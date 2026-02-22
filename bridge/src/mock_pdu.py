# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""Simulated PDU for testing without real hardware.

Generates realistic data for any CyberPower PDU model.
Outlet count and bank count are configurable for testing
different product family members.
"""

import logging
import math
import random
import time

from .pdu_model import (
    BANK_LOAD_STATE_MAP,
    OUTLET_CMD_OFF,
    OUTLET_CMD_ON,
    OUTLET_CMD_REBOOT,
    OUTLET_STATE_MAP,
    OUTLET_STATE_OFF,
    OUTLET_STATE_ON,
    BankData,
    DeviceIdentity,
    OutletData,
    PDUData,
    SourceData,
)

logger = logging.getLogger(__name__)


class MockPDU:
    """Simulates a CyberPower PDU with realistic-looking data.

    Configurable outlet/bank counts to simulate different models in the
    CyberPower product family (PDU44001, PDU30SWEV17FNET, etc.).
    """

    def __init__(self, num_outlets: int = 10, num_banks: int = 2,
                 model: str = "PDU44001", device_name: str = ""):
        self._num_outlets = num_outlets
        self._num_banks = num_banks
        self._outlet_states: dict[int, int] = {}
        self._outlet_names: dict[int, str] = {}
        self._start_time = time.time()

        for n in range(1, num_outlets + 1):
            self._outlet_states[n] = OUTLET_STATE_ON
            self._outlet_names[n] = f"Outlet {n}"

        self._reboot_until: dict[int, float] = {}
        self._failed_banks: set[int] = set()
        self._active_input = 1

        # Mock identity
        self._identity = DeviceIdentity(
            serial=f"MOCK{random.randint(100000, 999999):06d}",
            serial_numeric=f"{random.randint(100000, 999999)}",
            model=model,
            name=device_name or f"CyberPower {model} (Mock)",
            firmware_main="1.2",
            firmware_secondary="1.3.4",
            hardware_rev=12,
            max_current=12.0,
            outlet_count=num_outlets,
            phase_count=1,
            sys_description=f"CyberPower {model} Switched ATS PDU",
            sys_uptime=0,
            sys_contact="admin@example.com",
            sys_name=f"mock-{model.lower()}",
            sys_location="Rack 1, Row A",
        )

    @property
    def identity(self) -> DeviceIdentity:
        return self._identity

    @property
    def num_outlets(self) -> int:
        return self._num_outlets

    @property
    def num_banks(self) -> int:
        return self._num_banks

    async def poll(self) -> PDUData:
        """Return a snapshot of the simulated PDU state."""
        now = time.time()
        elapsed = now - self._start_time

        # Update uptime
        self._identity.sys_uptime = int(elapsed * 100)

        # Handle rebooting outlets
        for n, until in list(self._reboot_until.items()):
            if now >= until:
                self._outlet_states[n] = OUTLET_STATE_ON
                del self._reboot_until[n]
                logger.info("Mock: outlet %d reboot complete, now ON", n)

        # Simulate slow voltage drift (utility mains)
        base_voltage = 120.0 + 2.0 * math.sin(elapsed / 60.0)
        frequency = 60.0 + 0.02 * math.sin(elapsed / 30.0)

        # Per-bank voltage override for failure simulation
        bank_voltages = {}
        for idx in range(1, self._num_banks + 1):
            if idx in self._failed_banks:
                bank_voltages[idx] = 0.0
            else:
                bank_voltages[idx] = base_voltage + random.uniform(-0.3, 0.3)

        # ATS logic: if the active input fails, transfer to the other
        if self._active_input in self._failed_banks:
            for other in range(1, self._num_banks + 1):
                if other != self._active_input and other not in self._failed_banks:
                    self._active_input = other
                    logger.info("Mock: ATS transferred to input %d", other)
                    break

        # Outlets
        outlets: dict[int, OutletData] = {}
        on_count = 0
        for n in range(1, self._num_outlets + 1):
            state_int = self._outlet_states[n]
            state_str = OUTLET_STATE_MAP.get(state_int, "unknown")
            if state_int == OUTLET_STATE_ON:
                on_count += 1

            bank_assignment = ((n - 1) % self._num_banks) + 1

            outlets[n] = OutletData(
                number=n,
                name=self._outlet_names[n],
                state=state_str,
                bank_assignment=bank_assignment,
                max_load=12.0,
            )

        # Bank-level metering
        total_current = on_count * 0.003 + random.uniform(0, 0.01)

        banks: dict[int, BankData] = {}
        for idx in range(1, self._num_banks + 1):
            voltage = bank_voltages[idx]
            is_active = (idx == self._active_input)

            if is_active and voltage > 10:
                current = round(total_current, 2)
                power = round(current * voltage, 1)
                apparent = round(current * voltage, 1)
                pf = 0.98 if current > 0.01 else 1.0
                load_state = "normal"
            else:
                current = 0.0
                power = 0.0
                apparent = 0.0
                pf = 1.0
                load_state = "normal" if voltage > 10 else "low"

            banks[idx] = BankData(
                number=idx,
                current=current,
                voltage=round(voltage, 1),
                power=power,
                apparent_power=apparent,
                power_factor=pf,
                load_state=load_state,
                energy=round(elapsed * power / 3600.0 / 1000.0, 3) if power > 0 else None,
                last_update=time.strftime("%m/%d/%Y %H:%M:%S") if power > 0 else "",
            )

        # Per-input source data
        source_a = SourceData(
            voltage=round(bank_voltages.get(1, 0.0), 1),
            frequency=round(frequency, 1) if 1 not in self._failed_banks else 0.0,
            voltage_status="underVoltage" if 1 in self._failed_banks else "normal",
        )
        source_b = None
        if self._num_banks >= 2:
            source_b = SourceData(
                voltage=round(bank_voltages.get(2, 0.0), 1),
                frequency=round(frequency, 1) if 2 not in self._failed_banks else 0.0,
                voltage_status="underVoltage" if 2 in self._failed_banks else "normal",
            )
        both_ok = not any(idx in self._failed_banks for idx in range(1, self._num_banks + 1))

        return PDUData(
            device_name=self._identity.name,
            outlet_count=self._num_outlets,
            phase_count=1,
            input_voltage=round(bank_voltages.get(self._active_input, 0.0), 1),
            input_frequency=round(frequency, 1),
            outlets=outlets,
            banks=banks,
            ats_preferred_source=1,
            ats_current_source=self._active_input,
            ats_auto_transfer=True,
            source_a=source_a,
            source_b=source_b,
            redundancy_ok=both_ok,
            identity=self._identity,
        )

    def simulate_input_failure(self, bank: int):
        """Simulate a power failure on the given bank."""
        if 1 <= bank <= self._num_banks:
            self._failed_banks.add(bank)
            logger.info("Mock: simulated power FAILURE on bank %d", bank)

    def simulate_input_restore(self, bank: int):
        """Restore power on the given bank."""
        self._failed_banks.discard(bank)
        logger.info("Mock: simulated power RESTORE on bank %d", bank)

    async def command_outlet(self, outlet: int, command: int) -> bool:
        """Execute a command on an outlet. Returns True on success."""
        if outlet < 1 or outlet > self._num_outlets:
            logger.error("Mock: invalid outlet %d", outlet)
            return False

        if command == OUTLET_CMD_ON:
            self._outlet_states[outlet] = OUTLET_STATE_ON
            logger.info("Mock: outlet %d -> ON", outlet)
        elif command == OUTLET_CMD_OFF:
            self._outlet_states[outlet] = OUTLET_STATE_OFF
            logger.info("Mock: outlet %d -> OFF", outlet)
        elif command == OUTLET_CMD_REBOOT:
            self._outlet_states[outlet] = OUTLET_STATE_OFF
            self._reboot_until[outlet] = time.time() + 5.0
            logger.info("Mock: outlet %d -> REBOOT (off for 5s)", outlet)
        else:
            logger.error("Mock: unknown command %d for outlet %d", command, outlet)
            return False

        return True
