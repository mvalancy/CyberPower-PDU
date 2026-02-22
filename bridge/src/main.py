"""Entry point — poll loop for SNMP→MQTT bridge."""

import asyncio
import json
import logging
import signal
import sys
from pathlib import Path

from .automation import AutomationEngine
from .config import Config
from .history import HistoryStore
from .mock_pdu import MockPDU
from .mqtt_handler import MQTTHandler
from .web import WebServer
from .pdu_model import (
    BANK_LOAD_STATE_MAP,
    OUTLET_CMD_MAP,
    OUTLET_STATE_MAP,
    SOURCE_VOL_STATUS_MAP,
    BankData,
    OutletData,
    PDUData,
    SourceData,
    oid_bank_active_power,
    oid_bank_apparent_power,
    oid_bank_current,
    oid_bank_load_state,
    oid_bank_power_factor,
    oid_bank_voltage,
    oid_outlet_command,
    oid_outlet_current,
    oid_outlet_energy,
    oid_outlet_name,
    oid_outlet_power,
    oid_outlet_state,
    OID_ATS_PREFERRED_SOURCE,
    OID_ATS_CURRENT_SOURCE,
    OID_ATS_AUTO_TRANSFER,
    OID_DEVICE_NAME,
    OID_INPUT_FREQUENCY,
    OID_INPUT_VOLTAGE,
    OID_OUTLET_COUNT,
    OID_PHASE_COUNT,
    OID_SOURCE_A_VOLTAGE,
    OID_SOURCE_B_VOLTAGE,
    OID_SOURCE_A_FREQUENCY,
    OID_SOURCE_B_FREQUENCY,
    OID_SOURCE_A_STATUS,
    OID_SOURCE_B_STATUS,
    OID_SOURCE_REDUNDANCY,
)
from .snmp_client import SNMPClient

logger = logging.getLogger("pdu_bridge")


class PDUBridge:
    def __init__(self):
        self.config = Config()
        self.mqtt = MQTTHandler(self.config)
        self.history = HistoryStore(
            self.config.history_db,
            retention_days=self.config.history_retention_days,
            house_monthly_kwh=self.config.house_monthly_kwh,
        )
        self.mock: MockPDU | None = None
        self.snmp: SNMPClient | None = None
        self._running = False
        self._outlet_count: int | None = None
        self._num_banks = 2  # PDU44001 has 2 banks

        # Outlet name overrides
        self._outlet_names: dict[str, str] = {}
        self._load_outlet_names()

        self.engine = AutomationEngine(
            self.config.rules_file,
            command_callback=self._handle_command,
        )
        self.web = WebServer(
            self.engine, self.config.device_id, self.config.web_port,
            mqtt=self.mqtt, history=self.history,
        )
        self.web.set_command_callback(self._handle_command)
        self.web.set_outlet_names_callback(self._save_outlet_names)
        self.web.outlet_names = self._outlet_names

        if self.config.mock_mode:
            logger.info("Starting in MOCK mode")
            self.mock = MockPDU()
        else:
            logger.info("Starting in REAL mode — SNMP target %s:%d",
                        self.config.pdu_host, self.config.pdu_snmp_port)
            self.snmp = SNMPClient(self.config)

    def _load_outlet_names(self):
        path = Path(self.config.outlet_names_file)
        if path.exists():
            try:
                self._outlet_names = json.loads(path.read_text())
                logger.info("Loaded %d outlet name overrides", len(self._outlet_names))
            except Exception:
                logger.exception("Failed to load outlet names from %s", path)

    def _save_outlet_names(self, names: dict[str, str]):
        self._outlet_names = names
        path = Path(self.config.outlet_names_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(names, indent=2))
        logger.info("Saved outlet names to %s", path)

    def _apply_outlet_names(self, data):
        """Override outlet names with custom names."""
        for n, outlet in data.outlets.items():
            key = str(n)
            if key in self._outlet_names:
                outlet.name = self._outlet_names[key]

    async def _discover_outlet_count(self) -> int:
        """Get outlet count from PDU or use default."""
        if self.mock:
            return 10

        val = await self.snmp.get(OID_OUTLET_COUNT)
        if val is not None:
            count = int(val)
            logger.info("PDU reports %d outlets", count)
            return count

        logger.warning("Could not read outlet count, defaulting to 10")
        return 10

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

        # Parse device info
        device_name = get_str(OID_DEVICE_NAME)
        oc = get_int(OID_OUTLET_COUNT) or outlet_count
        phase_count = get_int(OID_PHASE_COUNT) or 1

        # Input — PDU44001 returns tenths (1204 = 120.4V, 600 = 60.0Hz)
        raw_voltage = get_int(OID_INPUT_VOLTAGE)
        input_voltage = raw_voltage / 10.0 if raw_voltage is not None else None

        raw_freq = get_int(OID_INPUT_FREQUENCY)
        input_frequency = raw_freq / 10.0 if raw_freq is not None else None

        # Outlets
        outlets: dict[int, OutletData] = {}
        for n in range(1, outlet_count + 1):
            state_int = get_int(oid_outlet_state(n))
            state_str = OUTLET_STATE_MAP.get(state_int, "unknown") if state_int else "unknown"

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
            load_state = BANK_LOAD_STATE_MAP.get(load_int, "unknown") if load_int else "unknown"

            banks[idx] = BankData(
                number=idx,
                current=bank_current,
                voltage=bank_voltage,
                power=bank_power,
                apparent_power=bank_apparent,
                power_factor=bank_pf,
                load_state=load_state,
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
        )

    async def _handle_command(self, outlet: int, command_str: str):
        """Handle an outlet command from MQTT."""
        if command_str not in OUTLET_CMD_MAP:
            self.mqtt.publish_command_response(
                outlet, command_str, False, f"unknown command: {command_str}"
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
            "Command outlet %d %s → %s", outlet, command_str,
            "OK" if success else "FAILED"
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
        """Main poll loop."""
        self._running = True

        # Connect MQTT
        self.mqtt.set_command_callback(self._handle_command)
        self.mqtt.connect()

        # Start web UI
        await self.web.start()

        # Start report scheduler
        asyncio.get_event_loop().create_task(self._report_scheduler())

        # Discover outlet count
        self._outlet_count = await self._discover_outlet_count()
        logger.info("Monitoring %d outlets, %d banks", self._outlet_count, self._num_banks)

        poll_count = 0
        while self._running:
            try:
                if self.mock:
                    data = await self.mock.poll()
                else:
                    data = await self._poll_snmp()

                # Apply custom outlet names
                self._apply_outlet_names(data)

                self.mqtt.publish_pdu_data(data)

                # Record to history
                self.history.record(data)

                # Update web server with latest data
                self.web.update_data(data)

                # Evaluate automation rules
                new_events = await self.engine.evaluate(data)
                self.mqtt.publish_automation_status(self.engine.list_rules())
                for event in new_events:
                    self.mqtt.publish_automation_event(event)

                poll_count += 1

                if poll_count % 60 == 1:
                    logger.info(
                        "Poll #%d: voltage=%.1fV, %d outlets, %d banks",
                        poll_count,
                        data.input_voltage or 0,
                        len(data.outlets),
                        len(data.banks),
                    )

            except Exception:
                logger.exception("Error in poll loop")

            await asyncio.sleep(self.config.poll_interval)

    async def _async_stop(self):
        await self.web.stop()

    def stop(self):
        self._running = False
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
        if self.snmp:
            self.snmp.close()


def main():
    config = Config()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    bridge = PDUBridge()

    loop = asyncio.new_event_loop()

    def _shutdown(sig, frame):
        logger.info("Received signal %s, shutting down...", sig)
        bridge.stop()
        loop.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run_until_complete(bridge.run())
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        loop.close()
        logger.info("Bridge stopped.")


if __name__ == "__main__":
    main()
