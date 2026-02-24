# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Entry point -- multi-PDU SNMP->MQTT bridge.

Architecture
------------
BridgeManager   -- orchestrates shared services (MQTT, history, web) and
                   launches one PDUPoller per configured device.
PDUPoller       -- handles a SINGLE PDU: SNMP discovery, poll loop,
                   automation engine, outlet-name overrides.
"""

__version__ = "1.0.0"

import asyncio
import enum
import ipaddress
import json
import logging
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from .automation import AutomationEngine
from .config import Config, ConfigError
from .discovery import (
    get_local_subnets, scan_all_interfaces, scan_for_serial, scan_serial_ports, scan_subnet,
)
from .history import HistoryStore
from .mock_pdu import MockPDU
from .mqtt_handler import MQTTHandler
from .pdu_config import PDUConfig, load_pdu_configs, next_device_id, save_pdu_configs
from .pdu_model import (
    ATS_SOURCE_MAP,
    OUTLET_CMD_MAP,
    DeviceIdentity,
    OID_DEVICE_NAME,
    OID_SYS_LOCATION,
    OID_SYS_NAME,
    PDUData,
)
from .serial_client import SerialClient
from .serial_transport import SerialTransport
from .snmp_client import SNMPClient
from .snmp_transport import SNMPTransport
from .report_generator import (
    generate_monthly_report,
    generate_weekly_report,
    get_report_path,
    list_reports,
)
from .web import RingBufferHandler, WebServer

logger = logging.getLogger("pdu_bridge")


def fmt_v(v) -> str:
    """Format voltage for event messages."""
    return f"{v:.1f}V" if v is not None else "?V"


# ---------------------------------------------------------------------------
# PDUPoller state machine for DHCP recovery
# ---------------------------------------------------------------------------

class PDUPollerState(enum.Enum):
    """Health state for a single PDU poller."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"         # 10+ consecutive SNMP failures
    RECOVERING = "recovering"     # actively scanning for PDU at new IP
    LOST = "lost"                 # 5 recovery scans failed


# ---------------------------------------------------------------------------
# PDUPoller -- one per physical PDU
# ---------------------------------------------------------------------------

class PDUPoller:
    """Poll loop for a single PDU device.

    Owns its own SNMPClient (or MockPDU), AutomationEngine, and outlet-name
    overrides.  Shared services (MQTT, history, web) are passed in from
    BridgeManager.
    """

    def __init__(
        self,
        pdu_cfg: PDUConfig,
        global_config: Config,
        mqtt: MQTTHandler,
        history: HistoryStore,
        web: WebServer,
        *,
        is_single_pdu: bool = False,
    ):
        self.pdu_cfg = pdu_cfg
        self.config = global_config
        self.mqtt = mqtt
        self.history = history
        self.web = web
        self.device_id = pdu_cfg.device_id
        self._is_single_pdu = is_single_pdu

        # Transport abstraction — replaces direct SNMP/Mock references
        self.transport = None               # Primary PDUTransport
        self._fallback = None               # Optional failover transport
        self._active_transport_name = ""    # "snmp", "serial", or "mock"

        # Legacy aliases for backward compatibility
        self.mock: MockPDU | None = None
        self.snmp: SNMPClient | None = None
        self._running = False

        # Auto-detected at startup from DeviceIdentity
        self._outlet_count: int | None = None
        self._phase_count: int = 1
        self._num_banks: int = pdu_cfg.num_banks  # default from config; overridden
        self._identity: DeviceIdentity | None = None

        # Startup-only OID caches (queried once)
        self._outlet_bank_assignments: dict[int, int] = {}
        self._outlet_max_loads: dict[int, float] = {}

        # Reboot detection
        self._last_sys_uptime: int | None = None

        # Poll health tracking
        self._poll_count = 0
        self._poll_errors = 0
        self._last_poll_duration: float | None = None
        self._last_successful_poll: float | None = None

        # Recovery state machine
        self._state = PDUPollerState.HEALTHY
        self._serial_mismatch = False
        self._recovery_scan_count = 0
        self._last_recovery_scan: float = 0
        self._subsystem_errors: dict[str, int] = {
            "mqtt": 0, "history": 0, "automation": 0,
        }

        # Reference to all PDU configs for saving serial back
        self._all_pdu_configs: list[PDUConfig] = []

        # Default credential check result (None = unknown, True = at risk)
        self._default_creds_active: bool | None = None
        self._first_poll = True

        # Daily rollup tracking
        self._last_rollup_date: str = ""

        # Previous poll data for state-change detection (system events)
        self._prev_data: PDUData | None = None

        # Outlet name overrides
        self._outlet_names: dict[str, str] = {}
        self._load_outlet_names()

        # Per-device rules file.  Single-PDU mode keeps legacy path.
        if is_single_pdu:
            rules_path = self.config.rules_file
        else:
            rules_path = f"/data/rules_{self.device_id}.json"

        self.engine = AutomationEngine(
            rules_path,
            command_callback=self._handle_command,
        )

        # Initialize transports
        self._create_transports()

    # -- Outlet name persistence ------------------------------------------

    def _outlet_names_file(self) -> Path:
        """Return the path for this poller's outlet-name overrides."""
        if self._is_single_pdu:
            return Path(self.config.outlet_names_file)
        return Path(f"/data/outlet_names_{self.device_id}.json")

    def _load_outlet_names(self):
        path = self._outlet_names_file()
        if path.exists():
            try:
                self._outlet_names = json.loads(path.read_text())
                logger.info(
                    "[%s] Loaded %d outlet name overrides",
                    self.device_id, len(self._outlet_names),
                )
            except Exception:
                logger.exception(
                    "[%s] Failed to load outlet names from %s",
                    self.device_id, path,
                )

    def _save_outlet_names(self, names: dict[str, str]):
        self._outlet_names = names
        path = self._outlet_names_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(names, indent=2))
            logger.info("[%s] Saved outlet names to %s", self.device_id, path)
        except Exception:
            logger.exception(
                "[%s] Failed to save outlet names to %s",
                self.device_id, path,
            )

    def _apply_outlet_names(self, data: PDUData):
        """Override outlet names with custom names."""
        for n, outlet in data.outlets.items():
            key = str(n)
            if key in self._outlet_names:
                outlet.name = self._outlet_names[key]

    # -- Transport factory ------------------------------------------------

    def _create_transports(self):
        """Initialize primary and fallback transports based on config."""
        if self.config.mock_mode:
            logger.info("[%s] Starting in MOCK mode", self.device_id)
            self.mock = MockPDU()
            self.transport = self.mock
            self._active_transport_name = "mock"
            return

        transports: dict[str, object] = {}

        # SNMP transport
        if self.pdu_cfg.host:
            logger.info(
                "[%s] SNMP transport: %s:%d",
                self.device_id, self.pdu_cfg.host, self.pdu_cfg.snmp_port,
            )
            snmp_client = SNMPClient(
                pdu_config=self.pdu_cfg,
                global_config=self.config,
            )
            self.snmp = snmp_client  # Legacy alias
            transports["snmp"] = SNMPTransport(
                snmp_client, self.pdu_cfg, self.pdu_cfg.num_banks,
            )

        # Serial transport
        if self.pdu_cfg.serial_port:
            logger.info(
                "[%s] Serial transport: %s @ %d baud",
                self.device_id, self.pdu_cfg.serial_port, self.pdu_cfg.serial_baud,
            )
            serial_client = SerialClient(
                port=self.pdu_cfg.serial_port,
                username=self.pdu_cfg.serial_username,
                password=self.pdu_cfg.serial_password,
                baud=self.pdu_cfg.serial_baud,
            )
            transports["serial"] = SerialTransport(serial_client, self.pdu_cfg)

        if not transports:
            logger.error("[%s] No transports configured!", self.device_id)
            return

        # Select primary and fallback
        preferred = self.pdu_cfg.transport
        primary = transports.get(preferred) or next(iter(transports.values()))
        self._active_transport_name = preferred if preferred in transports else next(iter(transports))

        # Fallback is the other transport, if available
        fallback_key = "serial" if self._active_transport_name == "snmp" else "snmp"
        fallback = transports.get(fallback_key)
        if fallback is not primary:
            self._fallback = fallback
        else:
            self._fallback = None

        self.transport = primary

        if self._fallback:
            logger.info(
                "[%s] Primary transport: %s, Fallback: %s",
                self.device_id, self._active_transport_name, fallback_key,
            )
        else:
            logger.info("[%s] Transport: %s", self.device_id, self._active_transport_name)

    # -- Discovery --------------------------------------------------------

    async def _discover_identity(self) -> DeviceIdentity:
        """Query DeviceIdentity once at startup via transport.

        Auto-detects outlet_count, phase_count, and num_banks.
        Also handles serial validation and persistence.
        """
        identity = await self.transport.get_identity()
        logger.info(
            "[%s] Identity: model=%s serial=%s outlets=%d phases=%d",
            self.device_id,
            identity.model,
            identity.serial,
            identity.outlet_count,
            identity.phase_count,
        )

        # Serial validation / persistence
        self._validate_serial(identity)

        return identity

    def _validate_serial(self, identity: DeviceIdentity):
        """Compare discovered serial against saved config serial."""
        discovered = identity.serial
        saved = self.pdu_cfg.serial

        if saved and discovered and saved != discovered:
            logger.error(
                "[%s] SERIAL MISMATCH: config has '%s' but PDU reports '%s'. "
                "Stopping poller — wrong PDU at this address?",
                self.device_id, saved, discovered,
            )
            self._serial_mismatch = True
            return

        if not saved and discovered:
            logger.info(
                "[%s] First-run serial discovery: saving '%s' to config",
                self.device_id, discovered,
            )
            self.pdu_cfg.serial = discovered
            self._persist_configs()

        if saved and discovered and saved == discovered:
            logger.info("[%s] Serial verified: %s", self.device_id, discovered)

    def _persist_configs(self):
        """Save all PDU configs to disk (used after serial discovery or IP change)."""
        if self._all_pdu_configs:
            try:
                save_pdu_configs(self._all_pdu_configs)
            except Exception:
                logger.exception("[%s] Failed to persist PDU configs", self.device_id)

    async def _discover_num_banks(self) -> int:
        """Detect bank count via the active transport.

        Falls back to the PDUConfig default if unavailable.
        """
        count = await self.transport.discover_num_banks()
        logger.info("[%s] PDU reports %d banks", self.device_id, count)
        return count

    async def _query_startup_oids(self):
        """Query outlet bank_assignment and max_load once at startup."""
        outlet_count = self._outlet_count or 0
        if outlet_count == 0:
            return

        assignments, max_loads = await self.transport.query_startup_data(outlet_count)
        self._outlet_bank_assignments = assignments
        self._outlet_max_loads = max_loads

        logger.info(
            "[%s] Startup data: %d bank assignments, %d max loads",
            self.device_id,
            len(self._outlet_bank_assignments),
            len(self._outlet_max_loads),
        )

    # -- Command handling -------------------------------------------------

    async def _handle_command(self, outlet: int, command_str: str):
        """Handle an outlet command from MQTT or web."""
        valid_commands = set(OUTLET_CMD_MAP.keys()) | {"delayon", "delayoff", "cancel"}
        if command_str not in valid_commands:
            self.mqtt.publish_command_response(
                outlet, command_str, False, f"unknown command: {command_str}",
            )
            return

        success = await self.transport.command_outlet(outlet, command_str)

        error = None if success else f"{self._active_transport_name} command failed"
        self.mqtt.publish_command_response(outlet, command_str, success, error)
        logger.info(
            "[%s] Command outlet %d %s -> %s (via %s)",
            self.device_id, outlet, command_str,
            "OK" if success else "FAILED",
            self._active_transport_name,
        )

    # -- Recovery ---------------------------------------------------------

    def _get_recovery_subnet(self) -> str:
        """Determine the /24 subnet for recovery scanning."""
        if self.pdu_cfg.recovery_subnet:
            return self.pdu_cfg.recovery_subnet
        try:
            network = ipaddress.IPv4Network(
                f"{self.pdu_cfg.host}/24", strict=False
            )
            return str(network)
        except ValueError:
            return ""

    async def _attempt_recovery(self):
        """Scan the subnet for this PDU's serial number at a new IP."""
        if not self.config.recovery_enabled:
            return
        if not self.pdu_cfg.serial:
            logger.warning(
                "[%s] Cannot recover — no serial number saved",
                self.device_id,
            )
            return

        subnet = self._get_recovery_subnet()
        if not subnet:
            logger.warning("[%s] Cannot recover — no subnet to scan", self.device_id)
            return

        self._recovery_scan_count += 1
        self._last_recovery_scan = time.monotonic()
        logger.info(
            "[%s] Recovery scan #%d on %s for serial %s",
            self.device_id, self._recovery_scan_count, subnet,
            self.pdu_cfg.serial,
        )

        try:
            found = await scan_for_serial(
                serial=self.pdu_cfg.serial,
                subnet=subnet,
                community=self.pdu_cfg.community_read,
                port=self.pdu_cfg.snmp_port,
            )
        except Exception:
            logger.exception("[%s] Recovery scan failed", self.device_id)
            return

        if found and found.host != self.pdu_cfg.host:
            old_ip = self.pdu_cfg.host
            new_ip = found.host
            logger.info(
                "[%s] PDU found at new IP %s (was %s)",
                self.device_id, new_ip, old_ip,
            )
            self.pdu_cfg.host = new_ip
            self._persist_configs()

            # Update SNMP target
            if self.snmp:
                self.snmp.update_target(new_ip, self.pdu_cfg.snmp_port)
                self.snmp.reset_health()

            # Re-verify identity
            try:
                self._identity = await self._discover_identity()
            except Exception:
                logger.exception("[%s] Post-recovery identity check failed", self.device_id)

            if not self._serial_mismatch:
                self._state = PDUPollerState.HEALTHY
                self._recovery_scan_count = 0
                logger.info("[%s] Recovery successful — resumed polling", self.device_id)
        elif found and found.host == self.pdu_cfg.host:
            # Same IP, just came back online
            self._state = PDUPollerState.HEALTHY
            self._recovery_scan_count = 0
            if self.snmp:
                self.snmp.reset_health()
            logger.info("[%s] PDU back online at same IP", self.device_id)
        else:
            logger.warning(
                "[%s] Recovery scan #%d: PDU not found",
                self.device_id, self._recovery_scan_count,
            )
            if self._recovery_scan_count >= 5:
                self._state = PDUPollerState.LOST
                logger.error(
                    "[%s] PDU declared LOST after %d recovery scans",
                    self.device_id, self._recovery_scan_count,
                )

    def _update_state(self, consecutive_failures: int):
        """Update poller state based on SNMP failure count."""
        if consecutive_failures == 0:
            if self._state != PDUPollerState.HEALTHY:
                logger.info("[%s] State -> HEALTHY", self.device_id)
            self._state = PDUPollerState.HEALTHY
            self._recovery_scan_count = 0
            return

        if self._state == PDUPollerState.HEALTHY and consecutive_failures >= 10:
            self._state = PDUPollerState.DEGRADED
            logger.warning(
                "[%s] State -> DEGRADED (%d consecutive failures)",
                self.device_id, consecutive_failures,
            )
            self.web.add_system_event(
                self.device_id, "conn_degraded", "SNMP",
                f"Connection degraded — {consecutive_failures} consecutive failures")

        if self._state == PDUPollerState.DEGRADED and consecutive_failures >= 30:
            self._state = PDUPollerState.RECOVERING
            logger.warning(
                "[%s] State -> RECOVERING (%d consecutive failures)",
                self.device_id, consecutive_failures,
            )
            self.web.add_system_event(
                self.device_id, "conn_lost", "SNMP",
                f"Connection lost — attempting recovery scan")

    def _get_poll_interval(self) -> float:
        """Return the poll interval adjusted for current state."""
        if self._state == PDUPollerState.LOST:
            return 30.0  # Reduced rate when lost
        return self.config.poll_interval

    # -- System event detection --------------------------------------------

    def _detect_state_changes(self, data: PDUData):
        """Compare current poll data to previous and emit system events."""
        prev = self._prev_data
        if prev is None:
            return

        did = self.device_id
        emit = self.web.add_system_event

        # 1. ATS source transfer
        if (prev.ats_current_source is not None
                and data.ats_current_source is not None
                and prev.ats_current_source != data.ats_current_source):
            old_src = ATS_SOURCE_MAP.get(prev.ats_current_source, str(prev.ats_current_source))
            new_src = ATS_SOURCE_MAP.get(data.ats_current_source, str(data.ats_current_source))
            emit(did, "ats_transfer", "ATS",
                 f"Source transferred: {old_src} \u2192 {new_src}")

        # 2. Source A voltage status change
        if (prev.source_a and data.source_a
                and prev.source_a.voltage_status != data.source_a.voltage_status):
            old_s = prev.source_a.voltage_status
            new_s = data.source_a.voltage_status
            if new_s != "normal" and old_s == "normal":
                emit(did, "power_loss", "Source A",
                     f"Power problem: {old_s} \u2192 {new_s} ({fmt_v(data.source_a.voltage)})")
            elif new_s == "normal" and old_s != "normal":
                emit(did, "power_restore", "Source A",
                     f"Power restored: {old_s} \u2192 normal ({fmt_v(data.source_a.voltage)})")

        # 3. Source B voltage status change
        if (prev.source_b and data.source_b
                and prev.source_b.voltage_status != data.source_b.voltage_status):
            old_s = prev.source_b.voltage_status
            new_s = data.source_b.voltage_status
            if new_s != "normal" and old_s == "normal":
                emit(did, "power_loss", "Source B",
                     f"Power problem: {old_s} \u2192 {new_s} ({fmt_v(data.source_b.voltage)})")
            elif new_s == "normal" and old_s != "normal":
                emit(did, "power_restore", "Source B",
                     f"Power restored: {old_s} \u2192 normal ({fmt_v(data.source_b.voltage)})")

        # 4. Redundancy change
        if (prev.redundancy_ok is not None
                and data.redundancy_ok is not None
                and prev.redundancy_ok != data.redundancy_ok):
            if data.redundancy_ok:
                emit(did, "redundancy_ok", "ATS",
                     "Redundancy restored — both sources available")
            else:
                emit(did, "redundancy_lost", "ATS",
                     "Redundancy lost — only one source available")

        # 5. Outlet state changes
        for n, outlet in data.outlets.items():
            prev_outlet = prev.outlets.get(n)
            if prev_outlet and prev_outlet.state != outlet.state:
                name = outlet.name or f"Outlet {n}"
                emit(did, "outlet_change", name,
                     f"Outlet {n} ({name}): {prev_outlet.state} \u2192 {outlet.state}")

        # 6. Bank load state transitions (overload warnings)
        for idx, bank in data.banks.items():
            prev_bank = prev.banks.get(idx)
            if prev_bank and prev_bank.load_state != bank.load_state:
                if bank.load_state in ("nearOverload", "overload"):
                    emit(did, "load_warning", f"Bank {idx}",
                         f"Bank {idx} load: {prev_bank.load_state} \u2192 {bank.load_state}")
                elif prev_bank.load_state in ("nearOverload", "overload"):
                    emit(did, "load_normal", f"Bank {idx}",
                         f"Bank {idx} load returned to normal")

    def _detect_reboot(self, data: PDUData, sys_uptime: int | None):
        """Emit a reboot event when sysUptime wraps around."""
        if (sys_uptime is not None
                and self._last_sys_uptime is not None
                and sys_uptime < self._last_sys_uptime):
            self.web.add_system_event(
                self.device_id, "reboot", "PDU",
                f"PDU rebooted (uptime reset: {self._last_sys_uptime} \u2192 {sys_uptime})")

    # -- Default credential check -----------------------------------------

    def _has_serial_transport(self) -> bool:
        """Check if this poller has a serial transport (primary or fallback)."""
        return (
            isinstance(self.transport, (SerialTransport, MockPDU))
            or isinstance(self._fallback, (SerialTransport, MockPDU))
        )

    def _get_serial_transport(self) -> SerialTransport | MockPDU | None:
        """Get the serial transport (or MockPDU) if available."""
        if isinstance(self.transport, (SerialTransport, MockPDU)):
            return self.transport
        if isinstance(self._fallback, (SerialTransport, MockPDU)):
            return self._fallback
        return None

    async def _check_default_creds(self):
        """Check if default cyber/cyber credentials are still active."""
        if not self._has_serial_transport():
            return
        serial_t = self._get_serial_transport()
        if not serial_t:
            return
        try:
            self._default_creds_active = await serial_t.check_default_credentials()
            if self._default_creds_active:
                logger.warning(
                    "[%s] PDU is using factory default credentials (cyber/cyber) — security risk!",
                    self.device_id,
                )
                self.web.add_system_event(
                    self.device_id, "security_warning", "Security",
                    "PDU is using factory default credentials (cyber/cyber). "
                    "Change the password in Settings > Manage > Security.",
                )
        except Exception:
            logger.debug("[%s] Default credential check failed", self.device_id, exc_info=True)

    # -- Transport failover -----------------------------------------------

    def _check_failover(self):
        """Switch to fallback transport if primary has too many failures."""
        if not self._fallback:
            return
        if self.transport.consecutive_failures >= 10:
            old_name = self._active_transport_name
            # Swap primary and fallback
            self.transport, self._fallback = self._fallback, self.transport
            self._active_transport_name = (
                "serial" if old_name == "snmp" else "snmp"
            )
            logger.warning(
                "[%s] Transport failover: %s -> %s (primary had %d failures)",
                self.device_id, old_name, self._active_transport_name,
                self._fallback.consecutive_failures,
            )
            self.web.add_system_event(
                self.device_id, "transport_failover", "Transport",
                f"Switched from {old_name} to {self._active_transport_name}",
            )

    # -- Poll loop --------------------------------------------------------

    async def run(self):
        """Main poll loop for this PDU."""
        self._running = True

        if self.transport is None:
            logger.error("[%s] No transport configured — aborting", self.device_id)
            return

        # 0. Connect transport (serial needs explicit connect; SNMP is no-op)
        try:
            await self.transport.connect()
        except Exception:
            logger.exception("[%s] Transport connect failed", self.device_id)
            # If we have a fallback, try it
            if self._fallback:
                try:
                    await self._fallback.connect()
                    self.transport, self._fallback = self._fallback, self.transport
                    self._active_transport_name = (
                        "serial" if self._active_transport_name == "snmp" else "snmp"
                    )
                    logger.info("[%s] Fell back to %s", self.device_id, self._active_transport_name)
                except Exception:
                    logger.exception("[%s] Fallback connect also failed", self.device_id)
                    return
            else:
                return

        # 1. Discover identity
        self._identity = await self._discover_identity()
        if self._serial_mismatch:
            logger.error("[%s] Aborting — serial mismatch", self.device_id)
            return

        self._outlet_count = self._identity.outlet_count or 10
        self._phase_count = self._identity.phase_count or 1

        # 2. Detect bank count
        self._num_banks = await self._discover_num_banks()

        logger.info(
            "[%s] Monitoring %d outlets, %d banks, %d phase(s) via %s",
            self.device_id, self._outlet_count, self._num_banks,
            self._phase_count, self._active_transport_name,
        )

        # 3. Query startup-only data (bank assignment, max load)
        await self._query_startup_oids()

        # 4. Publish Home Assistant MQTT Discovery
        self.mqtt.publish_ha_discovery(
            self._outlet_count, self._num_banks,
            device_id=self.device_id, identity=self._identity,
        )

        # 5. Register web callbacks for the first (or single) poller
        self.web.set_command_callback(self._handle_command)
        self.web.set_outlet_names_callback(self._save_outlet_names)
        self.web.outlet_names = self._outlet_names

        # 6. Poll loop
        while self._running:
            if self._serial_mismatch:
                await asyncio.sleep(10)
                continue

            poll_start = time.monotonic()
            try:
                data = await self.transport.poll()
                data.identity = self._identity

                # Apply custom outlet names
                self._apply_outlet_names(data)

                # Detect state changes and emit system events
                try:
                    self._detect_state_changes(data)
                except Exception:
                    if self._poll_count <= 3:
                        logger.debug("[%s] State change detection error", self.device_id, exc_info=True)
                self._prev_data = data

                # Subsystem isolation: each subsystem call is independent
                self._safe_publish(data)
                self._safe_record(data)
                self._check_daily_rollup()
                self.web.update_data(data)
                await self._safe_evaluate(data)

                # Auto-check default credentials on first successful poll
                if self._first_poll:
                    self._first_poll = False
                    await self._check_default_creds()

                self._poll_count += 1
                self._last_successful_poll = time.time()
                self._last_poll_duration = time.monotonic() - poll_start

                # Publish device info at low rate (~every 30 polls)
                if self._identity and self._poll_count % 30 == 1:
                    self.mqtt.publish_device_info(
                        self._identity,
                        device_id=self.device_id,
                        transport=self._active_transport_name,
                        state=self._state.value,
                    )

                if self._poll_count % 60 == 1:
                    logger.info(
                        "[%s] Poll #%d [%s/%s]: voltage=%.1fV, %d outlets, %d banks (%.0fms)",
                        self.device_id,
                        self._poll_count,
                        self._state.value,
                        self._active_transport_name,
                        data.input_voltage or 0,
                        len(data.outlets),
                        len(data.banks),
                        self._last_poll_duration * 1000,
                    )

            except Exception:
                self._poll_errors += 1
                self._last_poll_duration = time.monotonic() - poll_start
                if self._poll_errors <= 5 or self._poll_errors % 30 == 0:
                    logger.exception(
                        "[%s] Error in poll loop (error %d)",
                        self.device_id, self._poll_errors,
                    )

            # Check transport failover
            self._check_failover()

            # Update recovery state machine (SNMP-specific)
            if self.snmp and not self.mock and self._active_transport_name == "snmp":
                failures = self.snmp.consecutive_failures
                self._update_state(failures)

                # Trigger recovery scan
                if self._state == PDUPollerState.RECOVERING:
                    await self._attempt_recovery()
                elif self._state == PDUPollerState.LOST:
                    elapsed = time.monotonic() - self._last_recovery_scan
                    if elapsed >= 300:
                        await self._attempt_recovery()

            await asyncio.sleep(self._get_poll_interval())

    # -- Subsystem isolation ----------------------------------------------

    def _safe_publish(self, data: PDUData):
        """Publish to MQTT, catching errors independently."""
        try:
            self.mqtt.publish_pdu_data(data)
        except Exception:
            self._subsystem_errors["mqtt"] += 1
            if self._subsystem_errors["mqtt"] <= 3:
                logger.exception("[%s] MQTT publish error", self.device_id)

    def _safe_record(self, data: PDUData):
        """Record to history, catching errors independently."""
        try:
            self.history.record(data, device_id=self.device_id)
        except Exception:
            self._subsystem_errors["history"] += 1
            if self._subsystem_errors["history"] <= 3:
                logger.exception("[%s] History record error", self.device_id)

    async def _safe_evaluate(self, data: PDUData):
        """Evaluate automation rules, catching errors independently."""
        try:
            new_events = await self.engine.evaluate(data)
            self.mqtt.publish_automation_status(self.engine.list_rules())
            for event in new_events:
                self.mqtt.publish_automation_event(event)
        except Exception:
            self._subsystem_errors["automation"] += 1
            if self._subsystem_errors["automation"] <= 3:
                logger.exception("[%s] Automation evaluation error", self.device_id)

    def _check_daily_rollup(self):
        """Trigger daily/monthly energy rollups once per day."""
        from datetime import datetime as dt
        today = dt.now().strftime("%Y-%m-%d")
        if today != self._last_rollup_date:
            try:
                self.history.compute_daily_rollups(device_id=self.device_id)
                self.history.compute_monthly_rollups(device_id=self.device_id)
            except Exception:
                logger.exception("[%s] Energy rollup error", self.device_id)
            self._last_rollup_date = today

    def get_status_detail(self) -> dict:
        """Return detailed status for this poller (exposed via API)."""
        now = time.time()
        detail = {
            "device_id": self.device_id,
            "state": self._state.value,
            "transport": self._active_transport_name,
            "poll_count": self._poll_count,
            "poll_errors": self._poll_errors,
            "last_poll_duration_ms": round(self._last_poll_duration * 1000, 1) if self._last_poll_duration else None,
            "last_successful_poll": self._last_successful_poll,
            "seconds_since_last_poll": round(now - self._last_successful_poll, 1) if self._last_successful_poll else None,
        }
        # Transport health
        if self.transport:
            health = self.transport.get_health()
            detail["transport_health"] = health
            detail["consecutive_failures"] = health.get("consecutive_failures", 0)
        # Fallback info
        if self._fallback:
            detail["fallback_transport"] = "serial" if self._active_transport_name == "snmp" else "snmp"
            detail["fallback_health"] = self._fallback.get_health()
        # Recovery state
        if self._state in (PDUPollerState.RECOVERING, PDUPollerState.LOST):
            detail["recovery_scans"] = self._recovery_scan_count
        # Serial mismatch
        if self._serial_mismatch:
            detail["serial_mismatch"] = True
        # Default credential warning
        if self._default_creds_active is not None:
            detail["default_credentials_active"] = self._default_creds_active
        # Subsystem errors
        if any(v > 0 for v in self._subsystem_errors.values()):
            detail["subsystem_errors"] = dict(self._subsystem_errors)
        return detail

    def stop(self):
        self._running = False
        if self.transport:
            self.transport.close()
        if self._fallback:
            self._fallback.close()


# ---------------------------------------------------------------------------
# BridgeManager -- orchestrates shared services + multiple PDUPollers
# ---------------------------------------------------------------------------

class BridgeManager:
    """Top-level orchestrator.

    Creates shared MQTTHandler, HistoryStore, and WebServer, then launches
    one PDUPoller per configured PDU as concurrent asyncio tasks.
    """

    def __init__(self):
        self.config = Config()
        self.config.load_saved_settings(self.config.settings_file)
        self._running = False
        self._start_time = time.time()

        # Shared services
        self.mqtt = MQTTHandler(self.config)
        self.history = HistoryStore(
            self.config.history_db,
            retention_days=self.config.history_retention_days,
            house_monthly_kwh=self.config.house_monthly_kwh,
        )

        # Load PDU configs (backward compatible: single PDU from .env)
        self._pdu_configs = load_pdu_configs(
            pdus_file=self.config.pdus_file,
            env_host=self.config.pdu_host,
            env_port=self.config.pdu_snmp_port,
            env_community_read=self.config.pdu_community_read,
            env_community_write=self.config.pdu_community_write,
            env_device_id=self.config.device_id,
            mock_mode=self.config.mock_mode,
            env_serial_port=self.config.serial_port,
            env_serial_baud=self.config.serial_baud,
            env_serial_username=self.config.serial_username,
            env_serial_password=self.config.serial_password,
            env_transport=self.config.transport_primary,
        )

        is_single = len(self._pdu_configs) == 1

        # Build one AutomationEngine placeholder for the web server
        # (the first poller will overwrite the web callbacks once it starts)
        first_device_id = self._pdu_configs[0].device_id if self._pdu_configs else "pdu"
        first_rules_file = (
            self.config.rules_file
            if is_single
            else f"/data/rules_{first_device_id}.json"
        )
        _bootstrap_engine = AutomationEngine(first_rules_file)

        self.web = WebServer(
            first_device_id,
            self.config.web_port,
            mqtt=self.mqtt,
            history=self.history,
            config=self.config,
            auth_username=self.config.web_username,
            auth_password=self.config.web_password,
            session_secret=self.config.session_secret,
            session_timeout=self.config.session_timeout,
        )

        # Create pollers
        self.pollers: list[PDUPoller] = []
        # Running tasks keyed by device_id for hot-swap
        self._poller_tasks: dict[str, asyncio.Task] = {}

        for pdu_cfg in self._pdu_configs:
            if not pdu_cfg.enabled:
                logger.info("Skipping disabled PDU: %s", pdu_cfg.device_id)
                continue
            poller = PDUPoller(
                pdu_cfg=pdu_cfg,
                global_config=self.config,
                mqtt=self.mqtt,
                history=self.history,
                web=self.web,
                is_single_pdu=is_single,
            )
            self.pollers.append(poller)

        # Register each poller's automation engine with the web server
        # and pass config list reference for serial persistence
        for poller in self.pollers:
            poller._all_pdu_configs = self._pdu_configs
            self.web.register_automation_engine(poller.device_id, poller.engine)
            self.web.register_pdu(poller.device_id, poller.pdu_cfg.to_dict())

        # Wire web callbacks for runtime management
        self.web.set_pdu_config_callback(self._handle_config_update)
        self.web.set_discovery_callback(self._handle_discovery)
        self.web.set_snmp_set_callback(self._handle_snmp_set)
        self.web.set_add_pdu_callback(self._handle_add_pdu)
        self.web.set_remove_pdu_callback(self._handle_remove_pdu)
        self.web.set_test_connection_callback(self._handle_test_connection)
        self.web.set_test_serial_callback(self._handle_test_serial)
        self.web.set_poller_status_callback(self._get_poller_statuses)
        self.web.set_snmp_config_callback(self._handle_snmp_config_update)
        self.web.set_bridge_version(__version__)
        self.web.set_start_time(self._start_time)

        # Management callbacks (serial-specific operations)
        self.web.set_management_callback("get_network_config", self._handle_get_network_config)
        self.web.set_management_callback("get_thresholds", self._handle_get_thresholds)
        self.web.set_management_callback("set_device_threshold", self._handle_set_device_threshold)
        self.web.set_management_callback("set_bank_threshold", self._handle_set_bank_threshold)
        self.web.set_management_callback("get_outlet_config", self._handle_get_outlet_config)
        self.web.set_management_callback("set_outlet_config", self._handle_set_outlet_config)
        self.web.set_management_callback("get_eventlog", self._handle_get_eventlog)
        self.web.set_management_callback("check_credentials", self._handle_check_credentials)
        self.web.set_management_callback("change_password", self._handle_change_password)

        # ATS configuration callbacks
        self.web.set_management_callback("get_ats_config", self._handle_get_ats_config)
        self.web.set_management_callback("set_preferred_source", self._handle_set_preferred_source)
        self.web.set_management_callback("set_auto_transfer", self._handle_set_auto_transfer)
        self.web.set_management_callback("set_voltage_sensitivity", self._handle_set_voltage_sensitivity)
        self.web.set_management_callback("set_transfer_voltage", self._handle_set_transfer_voltage)
        self.web.set_management_callback("set_coldstart", self._handle_set_coldstart)

        # Network config write
        self.web.set_management_callback("set_network_config", self._handle_set_network_config)

        # User management
        self.web.set_management_callback("get_users", self._handle_get_users)

        # Notification callbacks
        self.web.set_management_callback("get_notifications", self._handle_get_notifications)
        self.web.set_management_callback("set_trap_receiver", self._handle_set_trap_receiver)
        self.web.set_management_callback("get_smtp_config", self._handle_get_smtp_config)
        self.web.set_management_callback("set_smtp_config", self._handle_set_smtp_config)
        self.web.set_management_callback("set_email_recipient", self._handle_set_email_recipient)
        self.web.set_management_callback("set_syslog_server", self._handle_set_syslog_server)

        # EnergyWise
        self.web.set_management_callback("get_energywise", self._handle_get_energywise)
        self.web.set_management_callback("set_energywise", self._handle_set_energywise)

        # Report generation callbacks
        self.web.set_report_list_callback(self._handle_list_reports)
        self.web.set_report_generate_callback(self._handle_generate_report)

        logger.info(
            "BridgeManager: %d PDU(s) configured, %d poller(s) active",
            len(self._pdu_configs), len(self.pollers),
        )

    # ------------------------------------------------------------------
    # Web callback handlers
    # ------------------------------------------------------------------

    async def _handle_config_update(self, configs_dict: dict):
        """Persist PDU configs when updated via web UI."""
        save_pdu_configs(self._pdu_configs)

    async def _handle_discovery(self) -> dict:
        """Run network AND serial port discovery scans in parallel."""
        configured_hosts = {p.host for p in self._pdu_configs if p.host}
        configured_ports = {p.serial_port for p in self._pdu_configs if p.serial_port}

        # Run network and serial scans concurrently
        network_task = scan_all_interfaces(configured_hosts=configured_hosts)
        serial_task = scan_serial_ports(configured_ports=configured_ports)
        iface_results, serial_results = await asyncio.gather(
            network_task, serial_task, return_exceptions=True,
        )

        # Process network results
        interfaces = []
        all_discovered = []
        if isinstance(iface_results, list):
            for ir in iface_results:
                iface_info = {
                    "interface": ir.interface,
                    "subnet": ir.subnet,
                    "ip": ir.ip,
                    "pdu_count": len(ir.pdus),
                    "error": ir.error or None,
                }
                interfaces.append(iface_info)
                for pdu in ir.pdus:
                    all_discovered.append({
                        "host": pdu.host,
                        "device_name": pdu.device_name,
                        "serial": pdu.serial,
                        "model": pdu.model,
                        "outlet_count": pdu.outlet_count,
                        "already_configured": pdu.already_configured,
                        "interface": pdu.interface,
                    })
        elif isinstance(iface_results, Exception):
            logger.exception("Network discovery failed: %s", iface_results)

        # Process serial results
        serial_discovered = []
        serial_ports_scanned = []
        if isinstance(serial_results, list):
            for spdu in serial_results:
                serial_discovered.append({
                    "port": spdu.port,
                    "port_by_id": spdu.port_by_id,
                    "device_name": spdu.device_name,
                    "serial": spdu.serial_number,
                    "model": spdu.model,
                    "outlet_count": spdu.outlet_count,
                    "already_configured": spdu.already_configured,
                })
        elif isinstance(serial_results, Exception):
            logger.exception("Serial discovery failed: %s", serial_results)

        return {
            "interfaces": interfaces,
            "discovered": all_discovered,
            "serial_ports": serial_ports_scanned,
            "serial_discovered": serial_discovered,
        }

    async def _handle_snmp_set(self, device_id: str, field: str, value: str):
        """Route device field SET to the correct poller's transport."""
        poller = self._find_poller(device_id)
        if not poller or not poller.transport:
            raise RuntimeError(f"No transport for device {device_id}")

        success = await poller.transport.set_device_field(field, value)
        if not success:
            raise RuntimeError(f"SET failed for {field}={value}")

    async def _handle_add_pdu(self, body: dict):
        """Add a PDU at runtime — create config, poller, and launch task."""
        # Auto-assign device_id if not provided
        if not body.get("device_id"):
            existing = {c.device_id for c in self._pdu_configs}
            body["device_id"] = next_device_id(existing)
        pdu_cfg = PDUConfig.from_dict(body)
        pdu_cfg.validate()

        self._pdu_configs.append(pdu_cfg)
        save_pdu_configs(self._pdu_configs)

        poller = PDUPoller(
            pdu_cfg=pdu_cfg,
            global_config=self.config,
            mqtt=self.mqtt,
            history=self.history,
            web=self.web,
            is_single_pdu=False,
        )
        poller._all_pdu_configs = self._pdu_configs
        self.pollers.append(poller)

        self.web.register_automation_engine(pdu_cfg.device_id, poller.engine)
        self.web.register_pdu(pdu_cfg.device_id, pdu_cfg.to_dict())
        self.mqtt.register_device(pdu_cfg.device_id, poller._handle_command)

        task = asyncio.get_event_loop().create_task(
            poller.run(), name=f"poller-{pdu_cfg.device_id}",
        )
        self._poller_tasks[pdu_cfg.device_id] = task
        logger.info("Runtime add: launched poller for %s", pdu_cfg.device_id)

    async def _handle_remove_pdu(self, device_id: str):
        """Remove a PDU at runtime — stop poller, cancel task, cleanup."""
        poller = self._find_poller(device_id)
        if poller:
            poller.stop()
            self.pollers.remove(poller)

        task = self._poller_tasks.pop(device_id, None)
        if task and not task.done():
            task.cancel()

        self.mqtt.unregister_device(device_id)

        # Remove from config list
        self._pdu_configs = [c for c in self._pdu_configs if c.device_id != device_id]
        save_pdu_configs(self._pdu_configs)
        logger.info("Runtime remove: stopped poller for %s", device_id)

    async def _handle_test_connection(self, host: str, community: str, port: int) -> dict:
        """Test SNMP connectivity to a host and return device info."""
        client = SNMPClient(pdu_config=PDUConfig(
            device_id="__test__", host=host,
            snmp_port=port, community_read=community,
        ), global_config=self.config)
        try:
            val = await client.get(OID_DEVICE_NAME)
            if val is None:
                return {"success": False, "error": "No response from host"}
            identity = await client.get_identity()
            return {
                "success": True,
                "device_name": str(val),
                "model": identity.model,
                "serial": identity.serial,
                "outlet_count": identity.outlet_count,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            client.close()

    async def _handle_test_serial(self, port: str, username: str, password: str) -> dict:
        """Test serial connectivity to a port and return device info."""
        client = SerialClient(
            port=port, username=username, password=password, timeout=5.0,
        )
        try:
            await client.connect()
            from .serial_parser import parse_sys_show, parse_oltsta_show
            sys_text = await client.execute("sys show")
            identity = parse_sys_show(sys_text)
            if not identity.name and not identity.model:
                return {"success": False, "error": "Not a CyberPower PDU"}
            outlet_count = 0
            try:
                oltsta_text = await client.execute("oltsta show")
                outlets = parse_oltsta_show(oltsta_text)
                outlet_count = len(outlets)
            except Exception:
                pass
            return {
                "success": True,
                "device_name": identity.name,
                "model": identity.model,
                "serial": identity.serial,
                "outlet_count": outlet_count,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Management handlers (serial-specific)
    # ------------------------------------------------------------------

    def _get_serial_transport(self, device_id: str):
        """Get SerialTransport (or MockPDU) for a device, or raise if not available."""
        poller = self._find_poller(device_id)
        if not poller or not poller.transport:
            raise RuntimeError(f"No transport for device {device_id}")
        # Check if transport is SerialTransport/MockPDU or if fallback is
        if isinstance(poller.transport, (SerialTransport, MockPDU)):
            return poller.transport
        if isinstance(poller._fallback, (SerialTransport, MockPDU)):
            return poller._fallback
        raise RuntimeError("Serial transport required for management operations")

    async def _handle_get_network_config(self, device_id: str) -> dict:
        transport = self._get_serial_transport(device_id)
        return await transport.get_network_config()

    async def _handle_get_thresholds(self, device_id: str) -> dict:
        transport = self._get_serial_transport(device_id)
        device = await transport.get_device_thresholds()
        banks = await transport.get_bank_thresholds()
        return {"device": device, "banks": {str(k): v for k, v in banks.items()}}

    async def _handle_set_device_threshold(self, device_id: str, body: dict) -> dict:
        transport = self._get_serial_transport(device_id)
        results = {}
        for threshold_type in ("overload", "nearover", "lowload"):
            if threshold_type in body:
                ok = await transport.set_device_threshold(threshold_type, body[threshold_type])
                results[threshold_type] = ok
        return {"ok": all(results.values()), "results": results}

    async def _handle_set_bank_threshold(self, device_id: str, bank: int, body: dict) -> dict:
        transport = self._get_serial_transport(device_id)
        results = {}
        for threshold_type in ("overload", "nearover", "lowload"):
            if threshold_type in body:
                ok = await transport.set_bank_threshold(bank, threshold_type, body[threshold_type])
                results[threshold_type] = ok
        return {"ok": all(results.values()), "results": results}

    async def _handle_get_outlet_config(self, device_id: str) -> dict:
        transport = self._get_serial_transport(device_id)
        config = await transport.get_outlet_config()
        return {str(k): v for k, v in config.items()}

    async def _handle_set_outlet_config(self, device_id: str, outlet: int, body: dict) -> dict:
        transport = self._get_serial_transport(device_id)
        ok = await transport.configure_outlet(
            outlet,
            name=body.get("name"),
            on_delay=body.get("on_delay"),
            off_delay=body.get("off_delay"),
            reboot_duration=body.get("reboot_duration"),
        )
        return {"ok": ok, "outlet": outlet}

    async def _handle_get_eventlog(self, device_id: str) -> list:
        transport = self._get_serial_transport(device_id)
        return await transport.get_event_log()

    async def _handle_check_credentials(self, device_id: str) -> dict:
        transport = self._get_serial_transport(device_id)
        default_active = await transport.check_default_credentials()
        return {
            "default_credentials_active": default_active,
            "security_risk": default_active,
        }

    async def _handle_change_password(self, device_id: str, account: str,
                                       password: str) -> dict:
        transport = self._get_serial_transport(device_id)
        ok = await transport.change_password(account, password)
        return {"ok": ok, "account": account}

    # ------------------------------------------------------------------
    # ATS configuration handlers
    # ------------------------------------------------------------------

    async def _handle_get_ats_config(self, device_id: str) -> dict:
        transport = self._get_serial_transport(device_id)
        srccfg = await transport.get_source_config()
        devcfg = await transport.get_device_config()
        return {
            "source_config": srccfg,
            "coldstart_delay": devcfg.get("coldstart_delay"),
            "coldstart_state": devcfg.get("coldstart_state", ""),
        }

    async def _handle_set_preferred_source(self, device_id: str, source: str) -> dict:
        """Set preferred source via serial, fall back to SNMP."""
        poller = self._find_poller(device_id)
        if not poller or not poller.transport:
            raise RuntimeError(f"No transport for {device_id}")
        # Try SNMP first (faster)
        if isinstance(poller.transport, SNMPTransport):
            ok = await poller.transport.set_preferred_source(source)
            return {"ok": ok, "transport": "snmp"}
        if isinstance(poller._fallback, SNMPTransport):
            ok = await poller._fallback.set_preferred_source(source)
            return {"ok": ok, "transport": "snmp"}
        # Fall back to serial
        transport = self._get_serial_transport(device_id)
        ok = await transport.set_preferred_source(source)
        return {"ok": ok, "transport": "serial"}

    async def _handle_set_auto_transfer(self, device_id: str, enabled: bool) -> dict:
        poller = self._find_poller(device_id)
        if not poller or not poller.transport:
            raise RuntimeError(f"No transport for {device_id}")
        if isinstance(poller.transport, SNMPTransport):
            ok = await poller.transport.set_auto_transfer(enabled)
            return {"ok": ok, "transport": "snmp"}
        if isinstance(poller._fallback, SNMPTransport):
            ok = await poller._fallback.set_auto_transfer(enabled)
            return {"ok": ok, "transport": "snmp"}
        return {"ok": False, "error": "SNMP transport required for auto-transfer"}

    async def _handle_set_voltage_sensitivity(self, device_id: str, sensitivity: str) -> dict:
        transport = self._get_serial_transport(device_id)
        ok = await transport.set_voltage_sensitivity(sensitivity)
        return {"ok": ok}

    async def _handle_set_transfer_voltage(self, device_id: str,
                                            upper: float | None, lower: float | None) -> dict:
        transport = self._get_serial_transport(device_id)
        ok = await transport.set_transfer_voltage(upper=upper, lower=lower)
        return {"ok": ok}

    async def _handle_set_coldstart(self, device_id: str, body: dict) -> dict:
        transport = self._get_serial_transport(device_id)
        results = {}
        if "delay" in body:
            results["delay"] = await transport.set_coldstart_delay(int(body["delay"]))
        if "state" in body:
            results["state"] = await transport.set_coldstart_state(body["state"])
        return {"ok": all(results.values()) if results else False, "results": results}

    # ------------------------------------------------------------------
    # Network config write
    # ------------------------------------------------------------------

    async def _handle_set_network_config(self, device_id: str, body: dict) -> dict:
        transport = self._get_serial_transport(device_id)
        ok = await transport.set_network_config(
            ip=body.get("ip"),
            subnet=body.get("subnet"),
            gateway=body.get("gateway"),
            dhcp=body.get("dhcp"),
        )
        return {"ok": ok}

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    async def _handle_get_users(self, device_id: str) -> dict:
        transport = self._get_serial_transport(device_id)
        return await transport.get_user_config()

    # ------------------------------------------------------------------
    # Notification configuration
    # ------------------------------------------------------------------

    async def _handle_get_notifications(self, device_id: str) -> dict:
        transport = self._get_serial_transport(device_id)
        traps = await transport.get_trap_config()
        smtp = await transport.get_smtp_config()
        email = await transport.get_email_config()
        syslog = await transport.get_syslog_config()
        return {
            "traps": traps,
            "smtp": smtp,
            "email": email,
            "syslog": syslog,
        }

    async def _handle_set_trap_receiver(self, device_id: str, index: int, body: dict) -> dict:
        transport = self._get_serial_transport(device_id)
        ok = await transport.set_trap_receiver(
            index,
            ip=body.get("ip"),
            community=body.get("community"),
            severity=body.get("severity"),
            enabled=body.get("enabled"),
        )
        return {"ok": ok}

    async def _handle_get_smtp_config(self, device_id: str) -> dict:
        transport = self._get_serial_transport(device_id)
        return await transport.get_smtp_config()

    async def _handle_set_smtp_config(self, device_id: str, body: dict) -> dict:
        transport = self._get_serial_transport(device_id)
        ok = await transport.set_smtp_config(
            server=body.get("server"),
            port=body.get("port"),
            from_addr=body.get("from_addr"),
            auth_user=body.get("auth_user"),
            auth_pass=body.get("auth_pass"),
        )
        return {"ok": ok}

    async def _handle_set_email_recipient(self, device_id: str, index: int, body: dict) -> dict:
        transport = self._get_serial_transport(device_id)
        ok = await transport.set_email_recipient(
            index,
            to=body.get("to"),
            enabled=body.get("enabled"),
        )
        return {"ok": ok}

    async def _handle_set_syslog_server(self, device_id: str, index: int, body: dict) -> dict:
        transport = self._get_serial_transport(device_id)
        ok = await transport.set_syslog_server(
            index,
            ip=body.get("ip"),
            facility=body.get("facility"),
            severity=body.get("severity"),
            enabled=body.get("enabled"),
        )
        return {"ok": ok}

    # ------------------------------------------------------------------
    # EnergyWise
    # ------------------------------------------------------------------

    async def _handle_get_energywise(self, device_id: str) -> dict:
        transport = self._get_serial_transport(device_id)
        return await transport.get_energywise_config()

    async def _handle_set_energywise(self, device_id: str, body: dict) -> dict:
        transport = self._get_serial_transport(device_id)
        ok = await transport.set_energywise_config(
            domain=body.get("domain"),
            port=body.get("port"),
            secret=body.get("secret"),
            enabled=body.get("enabled"),
        )
        return {"ok": ok}

    async def _handle_snmp_config_update(self, timeout: float, retries: int):
        """Apply SNMP timeout/retries to all active pollers."""
        for poller in self.pollers:
            if poller.snmp:
                try:
                    poller.snmp.update_snmp_params(timeout, retries)
                except Exception:
                    logger.exception("Failed to update SNMP params for %s", poller.device_id)
        logger.info("SNMP params updated: timeout=%.1f retries=%d", timeout, retries)

    def _get_poller_statuses(self) -> list[dict]:
        """Return status details for all active pollers."""
        return [p.get_status_detail() for p in self.pollers]

    def _find_poller(self, device_id: str) -> PDUPoller | None:
        """Find a poller by device_id."""
        for poller in self.pollers:
            if poller.device_id == device_id:
                return poller
        return None

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    async def _report_scheduler(self):
        """Background task — auto-generate weekly (Monday) and monthly (1st) reports."""
        await asyncio.sleep(60)  # Startup delay
        while self._running:
            try:
                if not self.config.reports_enabled:
                    await asyncio.sleep(3600)
                    continue

                now = datetime.now()
                # Weekly: Monday, first 2 hours
                if now.weekday() == 0 and now.hour < 2:
                    for poller in self.pollers:
                        self._generate_report_for_poller(poller, "weekly")

                # Monthly: 1st of month, first 2 hours
                if now.day == 1 and now.hour < 2:
                    for poller in self.pollers:
                        self._generate_report_for_poller(poller, "monthly")

            except Exception:
                logger.exception("Error in report scheduler")

            await asyncio.sleep(3600)

    def _generate_report_for_poller(
        self, poller: PDUPoller, report_type: str,
        week_start: str | None = None, month: str | None = None,
    ) -> str | None:
        """Generate a report for a specific poller. Returns path or None."""
        identity = poller._identity
        device_name = identity.name if identity else "PDU"
        model = identity.model if identity else ""
        reports_dir = self.config.reports_dir

        try:
            if report_type == "weekly":
                # Check idempotency — skip if file already exists
                if week_start is None:
                    today = datetime.now()
                    start = today - timedelta(days=today.weekday() + 7)
                    week_start = start.strftime("%Y-%m-%d")
                safe_id = poller.device_id or "default"
                expected = Path(reports_dir) / f"{safe_id}_weekly_{week_start}.pdf"
                if expected.exists():
                    return str(expected)
                return generate_weekly_report(
                    self.history, poller.device_id, device_name, model,
                    week_start=week_start, reports_dir=reports_dir,
                )
            elif report_type == "monthly":
                if month is None:
                    today = datetime.now()
                    prev = (today.replace(day=1) - timedelta(days=1))
                    month = prev.strftime("%Y-%m")
                safe_id = poller.device_id or "default"
                expected = Path(reports_dir) / f"{safe_id}_monthly_{month}.pdf"
                if expected.exists():
                    return str(expected)
                return generate_monthly_report(
                    self.history, poller.device_id, device_name, model,
                    month=month, reports_dir=reports_dir,
                )
        except Exception:
            logger.exception(
                "[%s] Failed to generate %s report", poller.device_id, report_type
            )
        return None

    async def _handle_list_reports(self, device_id: str | None = None) -> list[dict]:
        """List available PDF reports."""
        return list_reports(self.config.reports_dir, device_id)

    async def _handle_generate_report(self, body: dict) -> dict:
        """On-demand report generation from web UI."""
        device_id = body.get("device_id", "")
        report_type = body.get("type", "weekly")
        period = body.get("period")

        poller = self._find_poller(device_id)
        if not poller:
            # Try first poller if only one
            if len(self.pollers) == 1:
                poller = self.pollers[0]
            else:
                return {"error": f"Unknown device: {device_id}"}

        if report_type == "weekly":
            path = self._generate_report_for_poller(
                poller, "weekly", week_start=period,
            )
        elif report_type == "monthly":
            path = self._generate_report_for_poller(
                poller, "monthly", month=period,
            )
        else:
            return {"error": f"Unknown report type: {report_type}"}

        if path:
            return {"ok": True, "path": path, "filename": Path(path).name}
        return {"ok": False, "error": "No energy data for requested period"}

    async def _maintenance_scheduler(self):
        """Hourly task to run cleanup on old samples."""
        while self._running:
            try:
                self.history.cleanup()
            except Exception:
                logger.exception("Error in maintenance scheduler")
            await asyncio.sleep(3600)

    async def run(self):
        """Start all pollers, report scheduler, and web server."""
        self._running = True

        # Connect MQTT
        self.mqtt.set_command_callback(
            self.pollers[0]._handle_command if self.pollers else None,
        )
        self.mqtt.connect()

        # Start web UI
        await self.web.start()

        # Start maintenance scheduler (cleanup)
        asyncio.get_event_loop().create_task(self._maintenance_scheduler())

        # Start report scheduler (weekly/monthly PDF generation)
        if self.config.reports_enabled:
            asyncio.get_event_loop().create_task(self._report_scheduler())

        # Launch pollers with staggered starts (~100ms apart)
        for i, poller in enumerate(self.pollers):
            if i > 0:
                await asyncio.sleep(0.1)  # 100ms stagger
            task = asyncio.get_event_loop().create_task(
                poller.run(),
                name=f"poller-{poller.device_id}",
            )
            self._poller_tasks[poller.device_id] = task
            logger.info(
                "Launched poller for %s (%d/%d)",
                poller.device_id, i + 1, len(self.pollers),
            )

        # Wait for all pollers (they run forever until stopped)
        if self._poller_tasks:
            await asyncio.gather(*self._poller_tasks.values(), return_exceptions=True)

    async def _async_stop(self):
        await self.web.stop()

    def stop(self):
        if not self._running:
            return
        self._running = False

        # Stop all pollers
        for poller in self.pollers:
            poller.stop()

        # Cancel running tasks
        for task in self._poller_tasks.values():
            if not task.done():
                task.cancel()
        self._poller_tasks.clear()

        # Schedule web server cleanup
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._async_stop())
            else:
                loop.run_until_complete(self._async_stop())
        except Exception:
            pass

        self.mqtt.disconnect()
        self.history.close()


# ---------------------------------------------------------------------------
# Backward-compatible PDUBridge alias
# ---------------------------------------------------------------------------

PDUBridge = BridgeManager


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    try:
        config = Config()
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Set up ring buffer for web log viewer
    log_buffer = RingBufferHandler(1000)
    log_buffer.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(log_buffer)

    manager = BridgeManager()
    manager.web.set_log_buffer(log_buffer)

    loop = asyncio.new_event_loop()

    def _shutdown(sig, frame):
        logger.info("Received signal %s, shutting down...", sig)
        manager.stop()
        loop.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run_until_complete(manager.run())
    except KeyboardInterrupt:
        pass
    finally:
        manager.stop()
        loop.close()
        logger.info("Bridge stopped.")


if __name__ == "__main__":
    main()
