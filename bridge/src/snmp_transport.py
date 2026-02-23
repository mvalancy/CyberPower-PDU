# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""SNMP transport — wraps SNMPClient into the PDUTransport interface.

Extracts poll logic (OID building, parsing) from PDUPoller into a
reusable transport class. SNMPClient itself remains unchanged.
"""

import logging

from .pdu_config import PDUConfig
from .pdu_model import (
    ATS_SOURCE_MAP,
    BANK_LOAD_STATE_MAP,
    OUTLET_CMD_MAP,
    OUTLET_STATE_MAP,
    SOURCE_VOL_STATUS_MAP,
    BankData,
    DeviceIdentity,
    EnvironmentalData,
    OID_ATS_AUTO_TRANSFER,
    OID_ATS_CURRENT_SOURCE,
    OID_ATS_PREFERRED_SOURCE,
    OID_DEVICE_NAME,
    OID_ENVIRO_CONTACT_1,
    OID_ENVIRO_CONTACT_2,
    OID_ENVIRO_CONTACT_3,
    OID_ENVIRO_CONTACT_4,
    OID_ENVIRO_HUMIDITY,
    OID_ENVIRO_TEMPERATURE,
    OID_ENVIRO_TEMP_UNIT,
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
    OID_SYS_LOCATION,
    OID_SYS_NAME,
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

logger = logging.getLogger(__name__)


class SNMPTransport:
    """PDUTransport implementation backed by SNMP.

    Wraps an SNMPClient instance and the poll/parse logic that was
    previously embedded in PDUPoller._poll_snmp().
    """

    def __init__(self, snmp: SNMPClient, pdu_cfg: PDUConfig,
                 num_banks: int = 2):
        self._snmp = snmp
        self._pdu_cfg = pdu_cfg
        self._num_banks = num_banks
        self._outlet_count = 0
        self._identity: DeviceIdentity | None = None
        self._outlet_bank_assignments: dict[int, int] = {}
        self._outlet_max_loads: dict[int, float] = {}
        # Environmental sensor: try 3 times at startup, then stop if absent
        self._enviro_supported: bool | None = None  # None=unknown, True/False=determined
        self._enviro_probe_count = 0

    @property
    def snmp(self) -> SNMPClient:
        """Direct access to underlying SNMPClient (for legacy code)."""
        return self._snmp

    async def connect(self) -> None:
        """SNMP is connectionless — this is a no-op."""
        pass

    async def poll(self) -> PDUData:
        """Poll all OIDs and return a PDUData snapshot."""
        snmp = self._snmp
        outlet_count = self._outlet_count

        oids = [
            OID_DEVICE_NAME, OID_OUTLET_COUNT, OID_PHASE_COUNT,
            OID_INPUT_VOLTAGE, OID_INPUT_FREQUENCY,
            OID_ATS_PREFERRED_SOURCE, OID_ATS_CURRENT_SOURCE,
            OID_ATS_AUTO_TRANSFER,
            OID_SOURCE_A_VOLTAGE, OID_SOURCE_B_VOLTAGE,
            OID_SOURCE_A_FREQUENCY, OID_SOURCE_B_FREQUENCY,
            OID_SOURCE_A_STATUS, OID_SOURCE_B_STATUS,
            OID_SOURCE_REDUNDANCY,
            OID_SYS_UPTIME,
        ]

        for n in range(1, outlet_count + 1):
            oids.extend([
                oid_outlet_name(n),
                oid_outlet_state(n),
                oid_outlet_current(n),
                oid_outlet_power(n),
                oid_outlet_energy(n),
            ])

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

        device_name = get_str(OID_DEVICE_NAME)
        oc = get_int(OID_OUTLET_COUNT) or outlet_count
        phase_count = get_int(OID_PHASE_COUNT) or 1

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
            if current is not None and raw_current <= 2:
                current = 0.0

            raw_power = get_int(oid_outlet_power(n))
            power = float(raw_power) if raw_power is not None else None
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
            bank_current = raw_bank_current / 10.0 if raw_bank_current is not None else None

            raw_bank_voltage = get_int(oid_bank_voltage(idx))
            bank_voltage = raw_bank_voltage / 10.0 if raw_bank_voltage is not None else None

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

            raw_bank_energy = get_int(oid_bank_energy(idx))
            bank_energy = raw_bank_energy / 10.0 if raw_bank_energy is not None else None
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

        # Environmental sensor (optional)
        environment = None
        if self._enviro_supported is not False:
            environment = await self._poll_environment()

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
            environment=environment,
            identity=self._identity,
        )

    async def _poll_environment(self) -> EnvironmentalData | None:
        """Attempt to read environmental sensor data. Graceful when absent."""
        try:
            env_oids = [
                OID_ENVIRO_TEMPERATURE, OID_ENVIRO_TEMP_UNIT,
                OID_ENVIRO_HUMIDITY,
                OID_ENVIRO_CONTACT_1, OID_ENVIRO_CONTACT_2,
                OID_ENVIRO_CONTACT_3, OID_ENVIRO_CONTACT_4,
            ]
            values = await self._snmp.get_many(env_oids)

            raw_temp = values.get(OID_ENVIRO_TEMPERATURE)
            raw_unit = values.get(OID_ENVIRO_TEMP_UNIT)
            raw_humidity = values.get(OID_ENVIRO_HUMIDITY)

            # If no temperature data, sensor is absent
            if raw_temp is None:
                self._enviro_probe_count += 1
                if self._enviro_probe_count >= 3:
                    self._enviro_supported = False
                    logger.info("No environmental sensor detected after %d probes",
                                self._enviro_probe_count)
                return None

            self._enviro_supported = True
            temp = int(raw_temp) / 10.0 if raw_temp is not None else None
            temp_unit = "F" if raw_unit is not None and int(raw_unit) == 2 else "C"
            humidity = int(raw_humidity) if raw_humidity is not None else None

            contacts = {}
            for i, oid in enumerate([OID_ENVIRO_CONTACT_1, OID_ENVIRO_CONTACT_2,
                                      OID_ENVIRO_CONTACT_3, OID_ENVIRO_CONTACT_4], 1):
                raw = values.get(oid)
                if raw is not None:
                    contacts[i] = int(raw) == 2  # 2=closed

            return EnvironmentalData(
                temperature=temp,
                temperature_unit=temp_unit,
                humidity=humidity,
                contacts=contacts,
                sensor_present=True,
            )
        except Exception:
            self._enviro_probe_count += 1
            if self._enviro_probe_count >= 3:
                self._enviro_supported = False
            return None

    async def get_identity(self) -> DeviceIdentity:
        """Query device identity via SNMP."""
        identity = await self._snmp.get_identity()
        self._identity = identity
        self._outlet_count = identity.outlet_count or 10
        return identity

    async def discover_num_banks(self) -> int:
        """Detect bank count from SNMP."""
        val = await self._snmp.get(OID_NUM_BANK_TABLE_ENTRIES)
        if val is not None:
            try:
                count = int(val)
                if count >= 1:
                    self._num_banks = count
                    return count
            except (ValueError, TypeError):
                pass
        return self._pdu_cfg.num_banks

    async def query_startup_data(self, outlet_count: int) -> tuple[dict, dict]:
        """Query outlet bank assignments and max loads."""
        self._outlet_count = outlet_count
        assignments: dict[int, int] = {}
        max_loads: dict[int, float] = {}

        oids = []
        for n in range(1, outlet_count + 1):
            oids.append(oid_outlet_bank_assignment(n))
            oids.append(oid_outlet_max_load(n))

        if not oids:
            return assignments, max_loads

        values = await self._snmp.get_many(oids)

        for n in range(1, outlet_count + 1):
            raw_assign = values.get(oid_outlet_bank_assignment(n))
            if raw_assign is not None:
                try:
                    assignments[n] = int(raw_assign)
                except (ValueError, TypeError):
                    pass

            raw_max = values.get(oid_outlet_max_load(n))
            if raw_max is not None:
                try:
                    max_loads[n] = int(raw_max) / 10.0
                except (ValueError, TypeError):
                    pass

        self._outlet_bank_assignments = assignments
        self._outlet_max_loads = max_loads
        return assignments, max_loads

    async def command_outlet(self, outlet: int, command: str) -> bool:
        """Execute an outlet command via SNMP SET.

        SNMP supports: on, off, reboot.
        Serial-only commands (delayon, delayoff, cancel) return False.
        """
        if command in ("delayon", "delayoff", "cancel"):
            logger.info("SNMP: delayed commands require serial transport (got '%s')", command)
            return False
        if command not in OUTLET_CMD_MAP:
            return False
        cmd_val = OUTLET_CMD_MAP[command]
        oid = oid_outlet_command(outlet)
        return await self._snmp.set(oid, cmd_val)

    async def set_preferred_source(self, source: str) -> bool:
        """Set ATS preferred source via SNMP SET. source: 'A' or 'B'."""
        from .pdu_model import ATS_SOURCE_REVERSE
        val = ATS_SOURCE_REVERSE.get(source.upper())
        if val is None:
            return False
        return await self._snmp.set(OID_ATS_PREFERRED_SOURCE, val)

    async def set_auto_transfer(self, enabled: bool) -> bool:
        """Set ATS auto-transfer via SNMP SET. 1=enabled, 2=disabled."""
        val = 1 if enabled else 2
        return await self._snmp.set(OID_ATS_AUTO_TRANSFER, val)

    async def set_device_field(self, field: str, value: str) -> bool:
        """Set a device field via SNMP SET."""
        from .pdu_model import OID_SYS_CONTACT
        oid_map = {
            "device_name": OID_DEVICE_NAME,
            "sys_name": OID_SYS_NAME,
            "sys_location": OID_SYS_LOCATION,
            "sys_contact": OID_SYS_CONTACT,
        }
        oid = oid_map.get(field)
        if not oid:
            return False
        return await self._snmp.set_string(oid, value)

    def get_health(self) -> dict:
        """Return SNMP health metrics."""
        health = self._snmp.get_health()
        health["transport"] = "snmp"
        return health

    @property
    def consecutive_failures(self) -> int:
        return self._snmp.consecutive_failures

    def reset_health(self) -> None:
        self._snmp.reset_health()

    def update_target(self, host: str, port: int | None = None):
        """Update SNMP target (delegate to SNMPClient)."""
        self._snmp.update_target(host, port)

    def close(self) -> None:
        self._snmp.close()
