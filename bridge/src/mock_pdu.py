"""Simulated PDU for testing without real hardware."""

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
    OutletData,
    PDUData,
    SourceData,
)

logger = logging.getLogger(__name__)

NUM_OUTLETS = 10
NUM_BANKS = 2


class MockPDU:
    """Simulates a CyberPower PDU44001 with realistic-looking data.

    The PDU44001 is a Switched ATS with 2 inputs and 10 outlets.
    Input A (bank 1) is the preferred source; Input B (bank 2) is standby.
    All outlets are fed by whichever input is currently active.
    """

    def __init__(self):
        self._outlet_states: dict[int, int] = {}
        self._outlet_names: dict[int, str] = {}
        self._start_time = time.time()

        for n in range(1, NUM_OUTLETS + 1):
            self._outlet_states[n] = OUTLET_STATE_ON
            self._outlet_names[n] = f"Outlet {n}"

        self._reboot_until: dict[int, float] = {}
        self._failed_banks: set[int] = set()
        # Input A is preferred/active by default
        self._active_input = 1

    async def poll(self) -> PDUData:
        """Return a snapshot of the simulated PDU state."""
        now = time.time()
        elapsed = now - self._start_time

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
        bank_voltages = {
            1: 0.0 if 1 in self._failed_banks else base_voltage + random.uniform(-0.3, 0.3),
            2: 0.0 if 2 in self._failed_banks else base_voltage + random.uniform(-0.3, 0.3),
        }

        # ATS logic: if the active input fails, transfer to the other
        if self._active_input in self._failed_banks:
            other = 2 if self._active_input == 1 else 1
            if other not in self._failed_banks:
                self._active_input = other
                logger.info("Mock: ATS transferred to input %d", other)

        # Outlets
        outlets: dict[int, OutletData] = {}
        on_count = 0
        for n in range(1, NUM_OUTLETS + 1):
            state_int = self._outlet_states[n]
            state_str = OUTLET_STATE_MAP.get(state_int, "unknown")
            if state_int == OUTLET_STATE_ON:
                on_count += 1
            outlets[n] = OutletData(
                number=n,
                name=self._outlet_names[n],
                state=state_str,
            )

        # Bank-level metering
        # Nearly-idle PDU: tiny current from outlet relay coils and
        # whatever load is plugged in (very little right now).
        total_current = on_count * 0.003 + random.uniform(0, 0.01)

        banks: dict[int, BankData] = {}
        for idx in (1, 2):
            voltage = bank_voltages[idx]
            is_active = (idx == self._active_input)

            if is_active and voltage > 10:
                current = round(total_current, 2)
                power = round(current * voltage, 1)
                apparent = round(current * voltage, 1)
                pf = 0.98 if current > 0.01 else 1.0
                load_state = "normal"
            else:
                # Standby or failed input: voltage present but no load
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
            )

        # Per-input source data
        source_a = SourceData(
            voltage=round(bank_voltages[1], 1),
            frequency=round(frequency, 1) if 1 not in self._failed_banks else 0.0,
            voltage_status="underVoltage" if 1 in self._failed_banks else "normal",
        )
        source_b = SourceData(
            voltage=round(bank_voltages[2], 1),
            frequency=round(frequency, 1) if 2 not in self._failed_banks else 0.0,
            voltage_status="underVoltage" if 2 in self._failed_banks else "normal",
        )
        both_ok = 1 not in self._failed_banks and 2 not in self._failed_banks

        return PDUData(
            device_name="CyberPower PDU44001 (Mock)",
            outlet_count=NUM_OUTLETS,
            phase_count=1,
            input_voltage=round(bank_voltages[self._active_input], 1),
            input_frequency=round(frequency, 1),
            outlets=outlets,
            banks=banks,
            ats_preferred_source=1,
            ats_current_source=self._active_input,
            ats_auto_transfer=True,
            source_a=source_a,
            source_b=source_b,
            redundancy_ok=both_ok,
        )

    def simulate_input_failure(self, bank: int):
        """Simulate a power failure on the given bank (1 or 2)."""
        if bank in (1, 2):
            self._failed_banks.add(bank)
            logger.info("Mock: simulated power FAILURE on bank %d", bank)

    def simulate_input_restore(self, bank: int):
        """Restore power on the given bank."""
        self._failed_banks.discard(bank)
        logger.info("Mock: simulated power RESTORE on bank %d", bank)

    async def command_outlet(self, outlet: int, command: int) -> bool:
        """Execute a command on an outlet. Returns True on success."""
        if outlet < 1 or outlet > NUM_OUTLETS:
            logger.error("Mock: invalid outlet %d", outlet)
            return False

        if command == OUTLET_CMD_ON:
            self._outlet_states[outlet] = OUTLET_STATE_ON
            logger.info("Mock: outlet %d → ON", outlet)
        elif command == OUTLET_CMD_OFF:
            self._outlet_states[outlet] = OUTLET_STATE_OFF
            logger.info("Mock: outlet %d → OFF", outlet)
        elif command == OUTLET_CMD_REBOOT:
            self._outlet_states[outlet] = OUTLET_STATE_OFF
            self._reboot_until[outlet] = time.time() + 5.0
            logger.info("Mock: outlet %d → REBOOT (off for 5s)", outlet)
        else:
            logger.error("Mock: unknown command %d for outlet %d", command, outlet)
            return False

        return True
