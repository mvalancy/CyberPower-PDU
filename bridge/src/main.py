# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""Entry point -- multi-PDU SNMP->MQTT bridge.

Architecture
------------
BridgeManager   -- orchestrates shared services (MQTT, history, web) and
                   launches one PDUPoller per configured device.
PDUPoller       -- handles a SINGLE PDU: SNMP discovery, poll loop,
                   automation engine, outlet-name overrides.
"""

import asyncio
import enum
import ipaddress
import json
import logging
import signal
import sys
import time
from pathlib import Path

from .automation import AutomationEngine
from .config import Config, ConfigError
from .discovery import scan_for_serial
from .history import HistoryStore
from .mock_pdu import MockPDU
from .mqtt_handler import MQTTHandler
from .pdu_config import PDUConfig, load_pdu_configs, save_pdu_configs
from .pdu_model import (
    BANK_LOAD_STATE_MAP,
    OUTLET_CMD_MAP,
    OUTLET_STATE_MAP,
    SOURCE_VOL_STATUS_MAP,
    BankData,
    DeviceIdentity,
    OID_ATS_AUTO_TRANSFER,
    OID_ATS_CURRENT_SOURCE,
    OID_ATS_PREFERRED_SOURCE,
    OID_DEVICE_NAME,
    OID_INPUT_FREQUENCY,
    OID_INPUT_VOLTAGE,
    OID_NUM_BANK_TABLE_ENTRIES,
    OID_OUTLET_COUNT,
    OID_PHASE_COUNT,
    OID_SOURCE_A_FREQUENCY,
    OID_SOURCE_A_STATUS,
    OID_SOURCE_A_VOLTAGE,
    OID_SOURCE_B_FREQUENCY,
    OID_SOURCE_B_STATUS,
    OID_SOURCE_B_VOLTAGE,
    OID_SOURCE_REDUNDANCY,
    OID_SYS_UPTIME,
    OutletData,
    PDUData,
    SourceData,
    oid_bank_active_power,
    oid_bank_apparent_power,
    oid_bank_current,
    oid_bank_energy,
    oid_bank_load_state,
    oid_bank_power_factor,
    oid_bank_timestamp,
    oid_bank_voltage,
    oid_outlet_bank_assignment,
    oid_outlet_command,
    oid_outlet_current,
    oid_outlet_energy,
    oid_outlet_max_load,
    oid_outlet_name,
    oid_outlet_power,
    oid_outlet_state,
)
from .snmp_client import SNMPClient
from .web import WebServer

logger = logging.getLogger("pdu_bridge")


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

        self.mock: MockPDU | None = None
        self.snmp: SNMPClient | None = None
        self._running = False

        # Auto-detected at startup from DeviceIdentity
        self._outlet_count: int | None = None
        self._phase_count: int = 1
        self._num_banks: int = pdu_cfg.num_banks  # default from config; overridden by SNMP
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

        # Initialize SNMP or mock
        if self.config.mock_mode:
            logger.info("[%s] Starting in MOCK mode", self.device_id)
            self.mock = MockPDU()
        else:
            logger.info(
                "[%s] Starting in REAL mode -- SNMP target %s:%d",
                self.device_id, self.pdu_cfg.host, self.pdu_cfg.snmp_port,
            )
            self.snmp = SNMPClient(
                pdu_config=self.pdu_cfg,
                global_config=self.config,
            )

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

    # -- Discovery --------------------------------------------------------

    async def _discover_identity(self) -> DeviceIdentity:
        """Query DeviceIdentity once at startup via snmp.get_identity().

        Auto-detects outlet_count, phase_count, and num_banks.
        Also handles serial validation and persistence.
        """
        if self.mock:
            return DeviceIdentity(
                name="CyberPower PDU44001 (Mock)",
                model="PDU44001",
                outlet_count=10,
                phase_count=1,
            )

        identity = await self.snmp.get_identity()
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
        """Detect bank count from SNMP (OID_NUM_BANK_TABLE_ENTRIES).

        Falls back to the PDUConfig default if the OID is unavailable.
        """
        if self.mock:
            return 2

        val = await self.snmp.get(OID_NUM_BANK_TABLE_ENTRIES)
        if val is not None:
            try:
                count = int(val)
                if count >= 1:
                    logger.info("[%s] PDU reports %d banks", self.device_id, count)
                    return count
            except (ValueError, TypeError):
                logger.warning(
                    "[%s] Invalid bank count value: %r", self.device_id, val,
                )

        logger.info(
            "[%s] Could not read bank count, using config default %d",
            self.device_id, self.pdu_cfg.num_banks,
        )
        return self.pdu_cfg.num_banks

    async def _query_startup_oids(self):
        """Query outlet bank_assignment and max_load once at startup."""
        if self.mock:
            return

        outlet_count = self._outlet_count or 0
        oids = []
        for n in range(1, outlet_count + 1):
            oids.append(oid_outlet_bank_assignment(n))
            oids.append(oid_outlet_max_load(n))

        if not oids:
            return

        values = await self.snmp.get_many(oids)

        for n in range(1, outlet_count + 1):
            raw_assign = values.get(oid_outlet_bank_assignment(n))
            if raw_assign is not None:
                try:
                    self._outlet_bank_assignments[n] = int(raw_assign)
                except (ValueError, TypeError):
                    pass

            raw_max = values.get(oid_outlet_max_load(n))
            if raw_max is not None:
                try:
                    self._outlet_max_loads[n] = int(raw_max) / 10.0
                except (ValueError, TypeError):
                    pass

        logger.info(
            "[%s] Startup OIDs: %d bank assignments, %d max loads",
            self.device_id,
            len(self._outlet_bank_assignments),
            len(self._outlet_max_loads),
        )

    # -- SNMP polling -----------------------------------------------------

    async def _poll_snmp(self) -> PDUData:
        """Poll all OIDs from the real PDU via SNMP."""
        snmp = self.snmp
        outlet_count = self._outlet_count

        # Build list of OIDs to query
        oids = [
            OID_DEVICE_NAME, OID_OUTLET_COUNT, OID_PHASE_COUNT,
            OID_INPUT_VOLTAGE, OID_INPUT_FREQUENCY,
            OID_ATS_PREFERRED_SOURCE, OID_ATS_CURRENT_SOURCE,
            OID_ATS_AUTO_TRANSFER,
            # Per-input source voltage/status (ePDU2 MIB)
            OID_SOURCE_A_VOLTAGE, OID_SOURCE_B_VOLTAGE,
            OID_SOURCE_A_FREQUENCY, OID_SOURCE_B_FREQUENCY,
            OID_SOURCE_A_STATUS, OID_SOURCE_B_STATUS,
            OID_SOURCE_REDUNDANCY,
            # System uptime for reboot detection
            OID_SYS_UPTIME,
        ]

        # Per-outlet OIDs
        for n in range(1, outlet_count + 1):
            oids.extend([
                oid_outlet_name(n),
                oid_outlet_state(n),
                oid_outlet_current(n),
                oid_outlet_power(n),
                oid_outlet_energy(n),
            ])

        # Per-bank OIDs (including new energy/timestamp)
        for idx in range(1, self._num_banks + 1):
            oids.extend([
                oid_bank_current(idx),
                oid_bank_load_state(idx),
                oid_bank_voltage(idx),
                oid_bank_active_power(idx),
                oid_bank_apparent_power(idx),
                oid_bank_power_factor(idx),
                oid_bank_energy(idx),
                oid_bank_timestamp(idx),
            ])

        values = await snmp.get_many(oids)

        def get_int(oid: str) -> int | None:
            v = values.get(oid)
            if v is None:
                return None
            try:
                return int(v)
            except (ValueError, TypeError):
                return None

        def get_str(oid: str) -> str:
            v = values.get(oid)
            return str(v) if v is not None else ""

        # -- Reboot detection via sysUptime --
        sys_uptime = get_int(OID_SYS_UPTIME)
        if sys_uptime is not None:
            if (
                self._last_sys_uptime is not None
                and sys_uptime < self._last_sys_uptime
            ):
                logger.warning(
                    "[%s] PDU reboot detected (uptime %d -> %d)",
                    self.device_id, self._last_sys_uptime, sys_uptime,
                )
            self._last_sys_uptime = sys_uptime

        # Parse device info
        device_name = get_str(OID_DEVICE_NAME)
        oc = get_int(OID_OUTLET_COUNT) or outlet_count
        phase_count = get_int(OID_PHASE_COUNT) or 1

        # Input -- PDU44001 returns tenths (1204 = 120.4V, 600 = 60.0Hz)
        raw_voltage = get_int(OID_INPUT_VOLTAGE)
        input_voltage = raw_voltage / 10.0 if raw_voltage is not None else None

        raw_freq = get_int(OID_INPUT_FREQUENCY)
        input_frequency = raw_freq / 10.0 if raw_freq is not None else None

        # Outlets
        outlets: dict[int, OutletData] = {}
        for n in range(1, outlet_count + 1):
            state_int = get_int(oid_outlet_state(n))
            state_str = (
                OUTLET_STATE_MAP.get(state_int, "unknown")
                if state_int is not None else "unknown"
            )

            raw_current = get_int(oid_outlet_current(n))
            current = raw_current / 10.0 if raw_current is not None else None
            # PDU reports 0.2A (raw 2) as metering floor for idle outlets
            if current is not None and raw_current <= 2:
                current = 0.0

            raw_power = get_int(oid_outlet_power(n))
            power = float(raw_power) if raw_power is not None else None
            # PDU reports 1W as metering floor for idle outlets
            if power is not None and raw_power <= 1:
                power = 0.0

            raw_energy = get_int(oid_outlet_energy(n))
            energy = raw_energy / 10.0 if raw_energy is not None else None

            outlets[n] = OutletData(
                number=n,
                name=get_str(oid_outlet_name(n)),
                state=state_str,
                current=current,
                power=power,
                energy=energy,
                bank_assignment=self._outlet_bank_assignments.get(n),
                max_load=self._outlet_max_loads.get(n),
            )

        # Banks
        banks: dict[int, BankData] = {}
        for idx in range(1, self._num_banks + 1):
            raw_bank_current = get_int(oid_bank_current(idx))
            bank_current = (
                raw_bank_current / 10.0 if raw_bank_current is not None else None
            )

            raw_bank_voltage = get_int(oid_bank_voltage(idx))
            bank_voltage = (
                raw_bank_voltage / 10.0 if raw_bank_voltage is not None else None
            )

            raw_power = get_int(oid_bank_active_power(idx))
            bank_power = float(raw_power) if raw_power is not None else None

            raw_apparent = get_int(oid_bank_apparent_power(idx))
            bank_apparent = float(raw_apparent) if raw_apparent is not None else None

            raw_pf = get_int(oid_bank_power_factor(idx))
            bank_pf = raw_pf / 100.0 if raw_pf is not None else None

            load_int = get_int(oid_bank_load_state(idx))
            load_state = (
                BANK_LOAD_STATE_MAP.get(load_int, "unknown")
                if load_int is not None else "unknown"
            )

            # New bank energy/timestamp OIDs
            raw_bank_energy = get_int(oid_bank_energy(idx))
            bank_energy = (
                raw_bank_energy / 10.0 if raw_bank_energy is not None else None
            )
            bank_timestamp = get_str(oid_bank_timestamp(idx))

            banks[idx] = BankData(
                number=idx,
                current=bank_current,
                voltage=bank_voltage,
                power=bank_power,
                apparent_power=bank_apparent,
                power_factor=bank_pf,
                load_state=load_state,
                energy=bank_energy,
                last_update=bank_timestamp,
            )

        # ATS
        ats_preferred = get_int(OID_ATS_PREFERRED_SOURCE)
        ats_current = get_int(OID_ATS_CURRENT_SOURCE)
        ats_auto_raw = get_int(OID_ATS_AUTO_TRANSFER)
        ats_auto = ats_auto_raw == 1 if ats_auto_raw is not None else True

        # Per-input source voltage/status (ePDU2 MIB)
        def parse_source(volt_oid, freq_oid, status_oid):
            raw_v = get_int(volt_oid)
            raw_f = get_int(freq_oid)
            raw_s = get_int(status_oid)
            return SourceData(
                voltage=raw_v / 10.0 if raw_v is not None else None,
                frequency=raw_f / 10.0 if raw_f is not None else None,
                voltage_status=SOURCE_VOL_STATUS_MAP.get(raw_s, "unknown"),
                voltage_status_raw=raw_s,
            )

        source_a = parse_source(
            OID_SOURCE_A_VOLTAGE, OID_SOURCE_A_FREQUENCY, OID_SOURCE_A_STATUS,
        )
        source_b = parse_source(
            OID_SOURCE_B_VOLTAGE, OID_SOURCE_B_FREQUENCY, OID_SOURCE_B_STATUS,
        )
        redundancy_raw = get_int(OID_SOURCE_REDUNDANCY)
        redundancy_ok = redundancy_raw == 2 if redundancy_raw is not None else None

        return PDUData(
            device_name=device_name,
            outlet_count=oc,
            phase_count=phase_count,
            input_voltage=input_voltage,
            input_frequency=input_frequency,
            outlets=outlets,
            banks=banks,
            ats_preferred_source=ats_preferred,
            ats_current_source=ats_current,
            ats_auto_transfer=ats_auto,
            source_a=source_a,
            source_b=source_b,
            redundancy_ok=redundancy_ok,
            identity=self._identity,
        )

    # -- Command handling -------------------------------------------------

    async def _handle_command(self, outlet: int, command_str: str):
        """Handle an outlet command from MQTT or web."""
        if command_str not in OUTLET_CMD_MAP:
            self.mqtt.publish_command_response(
                outlet, command_str, False, f"unknown command: {command_str}",
            )
            return

        cmd_val = OUTLET_CMD_MAP[command_str]

        if self.mock:
            success = await self.mock.command_outlet(outlet, cmd_val)
        else:
            oid = oid_outlet_command(outlet)
            success = await self.snmp.set(oid, cmd_val)

        error = None if success else "SNMP SET failed"
        self.mqtt.publish_command_response(outlet, command_str, success, error)
        logger.info(
            "[%s] Command outlet %d %s -> %s",
            self.device_id, outlet, command_str,
            "OK" if success else "FAILED",
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

        if self._state == PDUPollerState.DEGRADED and consecutive_failures >= 30:
            self._state = PDUPollerState.RECOVERING
            logger.warning(
                "[%s] State -> RECOVERING (%d consecutive failures)",
                self.device_id, consecutive_failures,
            )

    def _get_poll_interval(self) -> float:
        """Return the poll interval adjusted for current state."""
        if self._state == PDUPollerState.LOST:
            return 30.0  # Reduced rate when lost
        return self.config.poll_interval

    # -- Poll loop --------------------------------------------------------

    async def run(self):
        """Main poll loop for this PDU."""
        self._running = True

        # 1. Discover identity
        self._identity = await self._discover_identity()
        if self._serial_mismatch:
            logger.error("[%s] Aborting — serial mismatch", self.device_id)
            return

        self._outlet_count = self._identity.outlet_count or 10
        self._phase_count = self._identity.phase_count or 1

        # 2. Detect bank count from SNMP
        self._num_banks = await self._discover_num_banks()

        logger.info(
            "[%s] Monitoring %d outlets, %d banks, %d phase(s)",
            self.device_id, self._outlet_count, self._num_banks, self._phase_count,
        )

        # 3. Query startup-only OIDs (bank assignment, max load)
        await self._query_startup_oids()

        # 4. Publish Home Assistant MQTT Discovery
        self.mqtt.publish_ha_discovery(self._outlet_count, self._num_banks)

        # 5. Register web callbacks for the first (or single) poller
        # (In multi-PDU the web server uses the first poller's callbacks;
        # a future web refactor can make this per-device.)
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
                if self.mock:
                    data = await self.mock.poll()
                    data.identity = self._identity
                else:
                    data = await self._poll_snmp()

                # Apply custom outlet names
                self._apply_outlet_names(data)

                # Subsystem isolation: each subsystem call is independent
                self._safe_publish(data)
                self._safe_record(data)
                self.web.update_data(data)
                await self._safe_evaluate(data)

                self._poll_count += 1
                self._last_successful_poll = time.time()
                self._last_poll_duration = time.monotonic() - poll_start

                if self._poll_count % 60 == 1:
                    logger.info(
                        "[%s] Poll #%d [%s]: voltage=%.1fV, %d outlets, %d banks (%.0fms)",
                        self.device_id,
                        self._poll_count,
                        self._state.value,
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

            # Update recovery state machine
            if self.snmp and not self.mock:
                failures = self.snmp.consecutive_failures
                self._update_state(failures)

                # Trigger recovery scan
                if self._state == PDUPollerState.RECOVERING:
                    await self._attempt_recovery()
                elif self._state == PDUPollerState.LOST:
                    # Scan every 5 minutes when lost
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

    def stop(self):
        self._running = False
        if self.snmp:
            self.snmp.close()


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
        self._running = False

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
        )

        # Create pollers
        self.pollers: list[PDUPoller] = []
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

        logger.info(
            "BridgeManager: %d PDU(s) configured, %d poller(s) active",
            len(self._pdu_configs), len(self.pollers),
        )

    async def _report_scheduler(self):
        """Hourly task to generate weekly reports and run cleanup."""
        while self._running:
            try:
                self.history.generate_weekly_report()
                self.history.cleanup()
            except Exception:
                logger.exception("Error in report scheduler")
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

        # Start report scheduler
        asyncio.get_event_loop().create_task(self._report_scheduler())

        # Launch pollers with staggered starts (~100ms apart)
        tasks: list[asyncio.Task] = []
        for i, poller in enumerate(self.pollers):
            if i > 0:
                await asyncio.sleep(0.1)  # 100ms stagger
            task = asyncio.get_event_loop().create_task(
                poller.run(),
                name=f"poller-{poller.device_id}",
            )
            tasks.append(task)
            logger.info(
                "Launched poller for %s (%d/%d)",
                poller.device_id, i + 1, len(self.pollers),
            )

        # Wait for all pollers (they run forever until stopped)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _async_stop(self):
        await self.web.stop()

    def stop(self):
        if not self._running:
            return
        self._running = False

        # Stop all pollers
        for poller in self.pollers:
            poller.stop()

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

    manager = BridgeManager()

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
