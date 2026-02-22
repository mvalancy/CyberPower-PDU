# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""MQTT pub/sub handler — publishes PDU data, subscribes for commands, HA discovery.

Supports multiple PDU devices on a single MQTT connection. Each device is
identified by a ``device_id`` string that forms part of the MQTT topic
hierarchy (``pdu/{device_id}/…``).  Per-device command callbacks are
registered via :meth:`register_device` and routed by the wildcard
subscription ``pdu/+/outlet/+/command``.
"""

import asyncio
import json
import logging
import time
from typing import Callable, Awaitable

import paho.mqtt.client as mqtt

from .config import Config
from .pdu_model import DeviceIdentity, PDUData

logger = logging.getLogger(__name__)

CommandCallback = Callable[[int, str], Awaitable[None]]


class MQTTHandler:
    def __init__(self, config: Config):
        self.config = config
        self.device = config.device_id
        # Legacy single-device callback (backward compat)
        self._command_callback: CommandCallback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # Per-device command callbacks: device_id -> callback
        self._device_callbacks: dict[str, CommandCallback] = {}

        # Connection status tracking
        self._connected: bool = False
        self._reconnect_count: int = 0
        self._last_connect_time: float | None = None
        self._last_disconnect_time: float | None = None
        self._publish_errors: int = 0
        self._total_publishes: int = 0
        # Per-device HA discovery tracking
        self._ha_discovery_sent: dict[str, bool] = {}

        # Pending publishes queued while disconnected (max 100)
        self._pending_publishes: list[tuple[str, str, bool, int]] = []
        self._max_pending = 100

        self.client = mqtt.Client(
            client_id=f"pdu-bridge-{self.device}",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self.client.will_set(
            f"pdu/{self.device}/bridge/status", "offline", qos=1, retain=True
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        # Auto-reconnect with backoff
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    # ------------------------------------------------------------------
    # Legacy single-device callback (backward compat)
    # ------------------------------------------------------------------

    def set_command_callback(self, callback: CommandCallback):
        """Set the legacy single-device command callback.

        For multi-PDU setups, prefer :meth:`register_device` instead.
        """
        self._command_callback = callback

    # ------------------------------------------------------------------
    # Multi-device registration
    # ------------------------------------------------------------------

    def register_device(self, device_id: str, callback: CommandCallback):
        """Register a per-device command callback.

        When a command arrives on ``pdu/{device_id}/outlet/{n}/command``,
        the corresponding *callback(outlet_num, command_str)* is invoked.
        """
        self._device_callbacks[device_id] = callback
        logger.info("Registered device %s for MQTT commands", device_id)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self):
        logger.info("Connecting to MQTT broker %s:%d", self.config.mqtt_broker, self.config.mqtt_port)
        self._loop = asyncio.get_event_loop()

        # MQTT authentication
        if self.config.mqtt_username:
            self.client.username_pw_set(
                self.config.mqtt_username, self.config.mqtt_password
            )
            logger.info("MQTT authentication configured for user %s", self.config.mqtt_username)

        try:
            self.client.connect(self.config.mqtt_broker, self.config.mqtt_port, keepalive=60)
            self.client.loop_start()
        except Exception:
            logger.exception("Failed to connect to MQTT broker")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        logger.info("MQTT connected (rc=%s)", reason_code)
        if self._last_connect_time is not None:
            self._reconnect_count += 1
            logger.info("MQTT reconnected (count=%d)", self._reconnect_count)
        self._connected = True
        self._last_connect_time = time.time()

        # Publish bridge online status for the primary device
        client.publish(
            f"pdu/{self.device}/bridge/status", "online", qos=1, retain=True
        )
        # Also publish online for any registered devices
        for dev_id in self._device_callbacks:
            if dev_id != self.device:
                client.publish(
                    f"pdu/{dev_id}/bridge/status", "online", qos=1, retain=True
                )

        # Subscribe to command topics for ALL devices using wildcard
        topic = "pdu/+/outlet/+/command"
        client.subscribe(topic, qos=1)
        logger.info("Subscribed to %s", topic)

        # Drain pending publishes queued during disconnect
        if self._pending_publishes:
            drained = len(self._pending_publishes)
            for topic, payload, retain, qos in self._pending_publishes:
                try:
                    client.publish(topic, payload, qos=qos, retain=retain)
                except Exception:
                    pass
            self._pending_publishes.clear()
            logger.info("Drained %d pending publishes after reconnect", drained)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        logger.warning("MQTT disconnected (rc=%s)", reason_code)
        self._connected = False
        self._last_disconnect_time = time.time()

    def get_status(self) -> dict:
        """Return MQTT connection health info."""
        return {
            "connected": self._connected,
            "reconnect_count": self._reconnect_count,
            "last_connect": self._last_connect_time,
            "last_disconnect": self._last_disconnect_time,
            "broker": self.config.mqtt_broker,
            "port": self.config.mqtt_port,
            "publish_errors": self._publish_errors,
            "total_publishes": self._total_publishes,
            "ha_discovery_sent": self._ha_discovery_sent,
            "registered_devices": list(self._device_callbacks.keys()),
        }

    def _publish(self, topic: str, payload, retain: bool = False, qos: int = 0):
        """Publish with error tracking. Queues critical messages on failure."""
        self._total_publishes += 1

        try:
            info = self.client.publish(topic, payload, qos=qos, retain=retain)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                self._publish_errors += 1
                if self._publish_errors % 100 == 1:
                    logger.warning("MQTT publish failed (rc=%s, topic=%s)", info.rc, topic)
                # Queue retained messages for retry on reconnect
                if retain and len(self._pending_publishes) < self._max_pending:
                    self._pending_publishes.append((topic, str(payload), retain, qos))
        except Exception:
            self._publish_errors += 1
            if self._publish_errors % 100 == 1:
                logger.exception("MQTT publish exception (topic=%s)", topic)
            # Queue retained messages for retry on reconnect
            if retain and len(self._pending_publishes) < self._max_pending:
                self._pending_publishes.append((topic, str(payload), retain, qos))

    # ------------------------------------------------------------------
    # Incoming message routing
    # ------------------------------------------------------------------

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        """Handle incoming command messages for any device."""
        try:
            # Parse topic: pdu/{device_id}/outlet/{n}/command
            parts = msg.topic.split("/")
            if len(parts) == 5 and parts[0] == "pdu" and parts[2] == "outlet" and parts[4] == "command":
                device_id = parts[1]
                outlet_num = int(parts[3])
                command = msg.payload.decode("utf-8").strip().lower()
                logger.info("Command received: device=%s outlet=%d -> %s", device_id, outlet_num, command)

                if not self._loop:
                    logger.warning("Event loop not set — cannot dispatch command")
                    return

                # Try per-device callback first
                cb = self._device_callbacks.get(device_id)
                if cb:
                    asyncio.run_coroutine_threadsafe(cb(outlet_num, command), self._loop)
                    return

                # Fall back to legacy single-device callback if device matches
                if device_id == self.device and self._command_callback:
                    asyncio.run_coroutine_threadsafe(
                        self._command_callback(outlet_num, command), self._loop
                    )
                    return

                logger.warning("No callback registered for device %s", device_id)
        except Exception:
            logger.exception("Error handling MQTT message on %s", msg.topic)

    # ------------------------------------------------------------------
    # Publishing — all methods accept optional device_id
    # ------------------------------------------------------------------

    def publish_pdu_data(self, data: PDUData, device_id: str | None = None):
        """Publish all PDU data to MQTT topics (retained)."""
        dev = device_id or self.device
        prefix = f"pdu/{dev}"

        # Full status JSON
        status: dict = {
            "device_name": data.device_name,
            "outlet_count": data.outlet_count,
            "phase_count": data.phase_count,
            "input_voltage": data.input_voltage,
            "input_frequency": data.input_frequency,
            "timestamp": time.time(),
        }
        # Include identity info if available
        if data.identity is not None:
            status["identity"] = {
                "serial": data.identity.serial,
                "model": data.identity.model,
                "firmware_main": data.identity.firmware_main,
                "firmware_secondary": data.identity.firmware_secondary,
                "hardware_rev": data.identity.hardware_rev,
            }
        self._publish(f"{prefix}/status", json.dumps(status), retain=True)

        # Input
        if data.input_voltage is not None:
            self._publish(f"{prefix}/input/voltage", str(data.input_voltage), retain=True)
        if data.input_frequency is not None:
            self._publish(f"{prefix}/input/frequency", str(data.input_frequency), retain=True)

        # Outlets
        for n, outlet in data.outlets.items():
            op = f"{prefix}/outlet/{n}"
            self._publish(f"{op}/state", outlet.state, retain=True)
            self._publish(f"{op}/name", outlet.name, retain=True)
            if outlet.current is not None:
                self._publish(f"{op}/current", str(outlet.current), retain=True)
            if outlet.power is not None:
                self._publish(f"{op}/power", str(outlet.power), retain=True)
            if outlet.energy is not None:
                self._publish(f"{op}/energy", str(outlet.energy), retain=True)

        # Banks
        for idx, bank in data.banks.items():
            bp = f"{prefix}/bank/{idx}"
            if bank.current is not None:
                self._publish(f"{bp}/current", str(bank.current), retain=True)
            if bank.voltage is not None:
                self._publish(f"{bp}/voltage", str(bank.voltage), retain=True)
            if bank.power is not None:
                self._publish(f"{bp}/power", str(bank.power), retain=True)
            if bank.apparent_power is not None:
                self._publish(f"{bp}/apparent_power", str(bank.apparent_power), retain=True)
            if bank.power_factor is not None:
                self._publish(f"{bp}/power_factor", str(bank.power_factor), retain=True)
            self._publish(f"{bp}/load_state", bank.load_state, retain=True)

    def publish_command_response(
        self, outlet: int, command: str, success: bool,
        error: str | None = None, device_id: str | None = None,
    ):
        """Publish a command response."""
        dev = device_id or self.device
        resp = {
            "success": success,
            "command": command,
            "outlet": outlet,
            "error": error,
            "ts": time.time(),
        }
        self._publish(
            f"pdu/{dev}/outlet/{outlet}/command/response",
            json.dumps(resp),
            qos=1,
        )

    def publish_automation_status(self, rules_data: list, device_id: str | None = None):
        """Publish automation rule states (retained, every poll)."""
        dev = device_id or self.device
        self._publish(
            f"pdu/{dev}/automation/status",
            json.dumps(rules_data),
            retain=True,
        )

    def publish_automation_event(self, event: dict, device_id: str | None = None):
        """Publish a single automation event (QoS 1, not retained)."""
        dev = device_id or self.device
        self._publish(
            f"pdu/{dev}/automation/event",
            json.dumps(event),
            qos=1,
        )

    # --- Home Assistant MQTT Discovery ---

    def publish_ha_discovery(
        self, outlet_count: int, num_banks: int = 2,
        device_id: str | None = None, identity: DeviceIdentity | None = None,
    ):
        """Publish Home Assistant MQTT auto-discovery configs.

        Args:
            outlet_count: Number of outlets to create switch entities for.
            num_banks: Number of banks to create sensor entities for.
            device_id: Target device identifier (defaults to self.device).
            identity: Optional DeviceIdentity for serial-based unique IDs
                      and accurate model information.
        """
        dev = device_id or self.device

        if self._ha_discovery_sent.get(dev, False):
            return

        base = f"pdu/{dev}"

        # Build HA device info — use identity when available
        if identity and identity.serial:
            identifiers = [f"cyberpdu_{identity.serial}"]
        else:
            identifiers = [f"cyberpdu_{dev}"]

        model = identity.model if (identity and identity.model) else "PDU44001"

        device_info = {
            "identifiers": identifiers,
            "name": f"CyberPower {dev.upper()}",
            "manufacturer": "CyberPower",
            "model": model,
        }
        # Include firmware version if available
        if identity and identity.firmware_main:
            device_info["sw_version"] = identity.firmware_main
        if identity and identity.hardware_rev:
            device_info["hw_version"] = str(identity.hardware_rev)

        avail = {
            "topic": f"{base}/bridge/status",
            "payload_available": "online",
            "payload_not_available": "offline",
        }

        # Outlet switches
        for n in range(1, outlet_count + 1):
            uid = f"{dev}_outlet_{n}"
            config = {
                "name": f"Outlet {n}",
                "unique_id": uid,
                "device": device_info,
                "availability": avail,
                "state_topic": f"{base}/outlet/{n}/state",
                "command_topic": f"{base}/outlet/{n}/command",
                "payload_on": "on",
                "payload_off": "off",
                "state_on": "on",
                "state_off": "off",
                "icon": "mdi:power-socket-us",
            }
            self._publish(
                f"homeassistant/switch/{uid}/config",
                json.dumps(config),
                retain=True,
            )

        # Bank sensors
        bank_metrics = [
            ("voltage", "V", "voltage", "mdi:flash-triangle"),
            ("current", "A", "current", "mdi:current-ac"),
            ("power", "W", "power", "mdi:flash"),
            ("apparent_power", "VA", None, "mdi:flash-outline"),
            ("power_factor", "", "power_factor", "mdi:angle-acute"),
            ("load_state", "", None, "mdi:gauge"),
        ]
        for idx in range(1, num_banks + 1):
            for metric, unit, dev_class, icon in bank_metrics:
                uid = f"{dev}_bank_{idx}_{metric}"
                config = {
                    "name": f"Bank {idx} {metric.replace('_', ' ').title()}",
                    "unique_id": uid,
                    "device": device_info,
                    "availability": avail,
                    "state_topic": f"{base}/bank/{idx}/{metric}",
                    "icon": icon,
                }
                if unit:
                    config["unit_of_measurement"] = unit
                if dev_class:
                    config["device_class"] = dev_class
                if metric != "load_state":
                    config["state_class"] = "measurement"
                self._publish(
                    f"homeassistant/sensor/{uid}/config",
                    json.dumps(config),
                    retain=True,
                )

        # Input sensors
        for metric, unit, dev_class, icon in [
            ("voltage", "V", "voltage", "mdi:flash-triangle"),
            ("frequency", "Hz", "frequency", "mdi:sine-wave"),
        ]:
            uid = f"{dev}_input_{metric}"
            config = {
                "name": f"Input {metric.title()}",
                "unique_id": uid,
                "device": device_info,
                "availability": avail,
                "state_topic": f"{base}/input/{metric}",
                "unit_of_measurement": unit,
                "device_class": dev_class,
                "state_class": "measurement",
                "icon": icon,
            }
            self._publish(
                f"homeassistant/sensor/{uid}/config",
                json.dumps(config),
                retain=True,
            )

        # Bridge status binary sensor
        uid = f"{dev}_bridge_status"
        config = {
            "name": "Bridge Status",
            "unique_id": uid,
            "device": device_info,
            "state_topic": f"{base}/bridge/status",
            "payload_on": "online",
            "payload_off": "offline",
            "device_class": "connectivity",
            "icon": "mdi:bridge",
        }
        self._publish(
            f"homeassistant/binary_sensor/{uid}/config",
            json.dumps(config),
            retain=True,
        )

        self._ha_discovery_sent[dev] = True
        logger.info(
            "Published HA MQTT Discovery configs for %s (%d outlets, %d banks)",
            dev, outlet_count, num_banks,
        )

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    def disconnect(self):
        """Publish offline status for all registered devices and disconnect."""
        # Publish offline for the primary device
        try:
            self._publish(
                f"pdu/{self.device}/bridge/status", "offline", qos=1, retain=True
            )
        except Exception:
            pass

        # Publish offline for every registered device
        for dev_id in self._device_callbacks:
            if dev_id != self.device:
                try:
                    self._publish(
                        f"pdu/{dev_id}/bridge/status", "offline", qos=1, retain=True
                    )
                except Exception:
                    pass

        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            logger.debug("Error during MQTT disconnect", exc_info=True)
