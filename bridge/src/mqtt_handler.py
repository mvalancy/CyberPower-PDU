"""MQTT pub/sub handler — publishes PDU data, subscribes for commands."""

import asyncio
import json
import logging
import time
from typing import Callable, Awaitable

import paho.mqtt.client as mqtt

from .config import Config
from .pdu_model import PDUData

logger = logging.getLogger(__name__)

CommandCallback = Callable[[int, str], Awaitable[None]]


class MQTTHandler:
    def __init__(self, config: Config):
        self.config = config
        self.device = config.device_id
        self._command_callback: CommandCallback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # Connection status tracking
        self._connected: bool = False
        self._reconnect_count: int = 0
        self._last_connect_time: float | None = None
        self._last_disconnect_time: float | None = None

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

    def set_command_callback(self, callback: CommandCallback):
        self._command_callback = callback

    def connect(self):
        logger.info("Connecting to MQTT broker %s:%d", self.config.mqtt_broker, self.config.mqtt_port)
        self._loop = asyncio.get_event_loop()
        self.client.connect(self.config.mqtt_broker, self.config.mqtt_port, keepalive=60)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        logger.info("MQTT connected (rc=%s)", reason_code)
        if self._connected:
            self._reconnect_count += 1
        self._connected = True
        self._last_connect_time = time.time()
        # Publish bridge online status
        client.publish(
            f"pdu/{self.device}/bridge/status", "online", qos=1, retain=True
        )
        # Subscribe to command topics for all outlets
        topic = f"pdu/{self.device}/outlet/+/command"
        client.subscribe(topic, qos=1)
        logger.info("Subscribed to %s", topic)

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
        }

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        """Handle incoming command messages."""
        try:
            # Parse topic: pdu/{device}/outlet/{n}/command
            parts = msg.topic.split("/")
            if len(parts) == 5 and parts[2] == "outlet" and parts[4] == "command":
                outlet_num = int(parts[3])
                command = msg.payload.decode("utf-8").strip().lower()
                logger.info("Command received: outlet %d → %s", outlet_num, command)

                if self._command_callback and self._loop:
                    asyncio.run_coroutine_threadsafe(
                        self._command_callback(outlet_num, command), self._loop
                    )
        except Exception:
            logger.exception("Error handling MQTT message on %s", msg.topic)

    def publish_pdu_data(self, data: PDUData):
        """Publish all PDU data to MQTT topics (retained)."""
        prefix = f"pdu/{self.device}"

        # Full status JSON
        status = {
            "device_name": data.device_name,
            "outlet_count": data.outlet_count,
            "phase_count": data.phase_count,
            "input_voltage": data.input_voltage,
            "input_frequency": data.input_frequency,
            "timestamp": time.time(),
        }
        self.client.publish(f"{prefix}/status", json.dumps(status), retain=True)

        # Input
        if data.input_voltage is not None:
            self.client.publish(
                f"{prefix}/input/voltage", str(data.input_voltage), retain=True
            )
        if data.input_frequency is not None:
            self.client.publish(
                f"{prefix}/input/frequency", str(data.input_frequency), retain=True
            )

        # Outlets
        for n, outlet in data.outlets.items():
            op = f"{prefix}/outlet/{n}"
            self.client.publish(f"{op}/state", outlet.state, retain=True)
            self.client.publish(f"{op}/name", outlet.name, retain=True)
            if outlet.current is not None:
                self.client.publish(f"{op}/current", str(outlet.current), retain=True)
            if outlet.power is not None:
                self.client.publish(f"{op}/power", str(outlet.power), retain=True)
            if outlet.energy is not None:
                self.client.publish(f"{op}/energy", str(outlet.energy), retain=True)

        # Banks
        for idx, bank in data.banks.items():
            bp = f"{prefix}/bank/{idx}"
            if bank.current is not None:
                self.client.publish(f"{bp}/current", str(bank.current), retain=True)
            if bank.voltage is not None:
                self.client.publish(f"{bp}/voltage", str(bank.voltage), retain=True)
            if bank.power is not None:
                self.client.publish(f"{bp}/power", str(bank.power), retain=True)
            if bank.apparent_power is not None:
                self.client.publish(
                    f"{bp}/apparent_power", str(bank.apparent_power), retain=True
                )
            if bank.power_factor is not None:
                self.client.publish(
                    f"{bp}/power_factor", str(bank.power_factor), retain=True
                )
            self.client.publish(f"{bp}/load_state", bank.load_state, retain=True)

    def publish_command_response(
        self, outlet: int, command: str, success: bool, error: str | None = None
    ):
        """Publish a command response."""
        resp = {
            "success": success,
            "command": command,
            "outlet": outlet,
            "error": error,
            "ts": time.time(),
        }
        self.client.publish(
            f"pdu/{self.device}/outlet/{outlet}/command/response",
            json.dumps(resp),
            qos=1,
        )

    def publish_automation_status(self, rules_data: list):
        """Publish automation rule states (retained, every poll)."""
        self.client.publish(
            f"pdu/{self.device}/automation/status",
            json.dumps(rules_data),
            retain=True,
        )

    def publish_automation_event(self, event: dict):
        """Publish a single automation event (QoS 1, not retained)."""
        self.client.publish(
            f"pdu/{self.device}/automation/event",
            json.dumps(event),
            qos=1,
            retain=False,
        )

    def disconnect(self):
        self.client.publish(
            f"pdu/{self.device}/bridge/status", "offline", qos=1, retain=True
        )
        self.client.loop_stop()
        self.client.disconnect()
