# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""Live hardware validation suite — runs against a real CyberPower PDU.

Skipped automatically when PDU_HOST is not set.
Run with:  PDU_HOST=192.168.x.x pytest tests/test_hardware_validation.py -v
"""

import asyncio
import os
import sys
import time

import pytest

# Skip the entire module if no real PDU is available
PDU_HOST = os.environ.get("PDU_HOST", "")
PDU_PORT = int(os.environ.get("PDU_SNMP_PORT", "161"))
PDU_COMMUNITY = os.environ.get("PDU_COMMUNITY_READ", "public")
PDU_COMMUNITY_WRITE = os.environ.get("PDU_COMMUNITY_WRITE", "private")
PDU_TEST_OUTLET = int(os.environ.get("PDU_TEST_OUTLET", "0"))

pytestmark = pytest.mark.skipif(
    not PDU_HOST,
    reason="No PDU_HOST configured — set PDU_HOST to run hardware tests",
)

# Add bridge source to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))


@pytest.fixture(scope="module")
def snmp_client():
    """Create a real SNMPClient connected to the hardware PDU."""
    from src.pdu_config import PDUConfig
    from src.snmp_client import SNMPClient

    config = PDUConfig(
        device_id="hw-test",
        host=PDU_HOST,
        snmp_port=PDU_PORT,
        community_read=PDU_COMMUNITY,
        community_write=PDU_COMMUNITY_WRITE,
    )
    client = SNMPClient(pdu_config=config)
    yield client
    client.close()


@pytest.fixture(scope="module")
def event_loop():
    """Create a single event loop for the module."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def run(loop, coro):
    """Helper to run an async coroutine in the module event loop."""
    return loop.run_until_complete(coro)


# -----------------------------------------------------------------------
# Identity verification
# -----------------------------------------------------------------------

class TestIdentity:
    """Verify device identity OIDs return valid data."""

    def test_get_identity(self, snmp_client, event_loop):
        """Query all identity OIDs and verify key fields are non-empty."""
        identity = run(event_loop, snmp_client.get_identity())
        assert identity is not None, "get_identity() returned None"
        assert identity.name, "Device name is empty"
        print(f"  Name: {identity.name}")
        print(f"  Model: {identity.model}")
        print(f"  Serial: {identity.serial}")
        print(f"  Firmware: {identity.firmware_main}")

    def test_serial_non_empty(self, snmp_client, event_loop):
        """Serial number should be a non-empty string."""
        identity = run(event_loop, snmp_client.get_identity())
        assert identity.serial, "Serial number is empty"

    def test_model_non_empty(self, snmp_client, event_loop):
        """Model string should be non-empty."""
        identity = run(event_loop, snmp_client.get_identity())
        assert identity.model, "Model string is empty"

    def test_outlet_count_positive(self, snmp_client, event_loop):
        """Outlet count should be > 0."""
        identity = run(event_loop, snmp_client.get_identity())
        assert identity.outlet_count > 0, f"Outlet count is {identity.outlet_count}"
        print(f"  Outlets: {identity.outlet_count}")

    def test_phase_count(self, snmp_client, event_loop):
        """Phase count should be >= 1."""
        identity = run(event_loop, snmp_client.get_identity())
        assert identity.phase_count >= 1, f"Phase count is {identity.phase_count}"


# -----------------------------------------------------------------------
# Bank readings
# -----------------------------------------------------------------------

class TestBanks:
    """Verify bank sensor data is in reasonable ranges."""

    def test_bank_voltage_range(self, snmp_client, event_loop):
        """Bank voltage should be in 100-130V range (US) or 200-250V (EU)."""
        from src.pdu_model import oid_bank_voltage

        val = run(event_loop, snmp_client.get(oid_bank_voltage(1)))
        if val is None:
            pytest.skip("Bank voltage OID not supported")
        voltage = int(val) / 10.0
        assert 80 <= voltage <= 260, f"Bank 1 voltage {voltage}V out of range"
        print(f"  Bank 1 voltage: {voltage}V")

    def test_bank_current_non_negative(self, snmp_client, event_loop):
        """Bank current should be >= 0."""
        from src.pdu_model import oid_bank_current

        val = run(event_loop, snmp_client.get(oid_bank_current(1)))
        if val is None:
            pytest.skip("Bank current OID not supported")
        current = int(val) / 10.0
        assert current >= 0, f"Bank 1 current {current}A is negative"
        print(f"  Bank 1 current: {current}A")

    def test_bank_power_non_negative(self, snmp_client, event_loop):
        """Bank power should be >= 0."""
        from src.pdu_model import oid_bank_active_power

        val = run(event_loop, snmp_client.get(oid_bank_active_power(1)))
        if val is None:
            pytest.skip("Bank power OID not supported")
        power = int(val)
        assert power >= 0, f"Bank 1 power {power}W is negative"
        print(f"  Bank 1 power: {power}W")


# -----------------------------------------------------------------------
# Outlet enumeration
# -----------------------------------------------------------------------

class TestOutlets:
    """Verify outlet data can be read for all outlets."""

    def test_outlet_states(self, snmp_client, event_loop):
        """All outlets should report a valid state (on/off)."""
        from src.pdu_model import OUTLET_STATE_MAP, oid_outlet_state

        identity = run(event_loop, snmp_client.get_identity())
        for n in range(1, identity.outlet_count + 1):
            val = run(event_loop, snmp_client.get(oid_outlet_state(n)))
            if val is None:
                continue
            state = OUTLET_STATE_MAP.get(int(val), "unknown")
            assert state in ("on", "off"), f"Outlet {n} state '{state}' is unexpected"
            print(f"  Outlet {n}: {state}")

    def test_outlet_current_non_negative(self, snmp_client, event_loop):
        """All outlet currents should be >= 0."""
        from src.pdu_model import oid_outlet_current

        identity = run(event_loop, snmp_client.get_identity())
        for n in range(1, identity.outlet_count + 1):
            val = run(event_loop, snmp_client.get(oid_outlet_current(n)))
            if val is None:
                continue
            current = int(val) / 10.0
            assert current >= 0, f"Outlet {n} current {current}A is negative"


# -----------------------------------------------------------------------
# Outlet control cycle (opt-in via PDU_TEST_OUTLET env var)
# -----------------------------------------------------------------------

class TestOutletControl:
    """Test outlet on/off cycle. Only runs if PDU_TEST_OUTLET is set."""

    @pytest.mark.skipif(
        PDU_TEST_OUTLET == 0,
        reason="PDU_TEST_OUTLET not set — skipping outlet control test",
    )
    def test_outlet_off_on_cycle(self, snmp_client, event_loop):
        """Turn outlet off, verify, turn back on, verify."""
        from src.pdu_model import (
            OUTLET_CMD_MAP,
            OUTLET_STATE_MAP,
            oid_outlet_command,
            oid_outlet_state,
        )

        n = PDU_TEST_OUTLET
        print(f"  Testing outlet {n} control cycle...")

        # Turn off
        off_val = OUTLET_CMD_MAP.get("off", 2)
        ok = run(event_loop, snmp_client.set(oid_outlet_command(n), off_val))
        assert ok, f"Failed to send OFF command to outlet {n}"

        time.sleep(3)

        val = run(event_loop, snmp_client.get(oid_outlet_state(n)))
        state = OUTLET_STATE_MAP.get(int(val), "unknown") if val else "unknown"
        assert state == "off", f"Outlet {n} should be off but is '{state}'"
        print(f"  Outlet {n} confirmed OFF")

        # Turn on
        on_val = OUTLET_CMD_MAP.get("on", 1)
        ok = run(event_loop, snmp_client.set(oid_outlet_command(n), on_val))
        assert ok, f"Failed to send ON command to outlet {n}"

        time.sleep(3)

        val = run(event_loop, snmp_client.get(oid_outlet_state(n)))
        state = OUTLET_STATE_MAP.get(int(val), "unknown") if val else "unknown"
        assert state == "on", f"Outlet {n} should be on but is '{state}'"
        print(f"  Outlet {n} confirmed ON")


# -----------------------------------------------------------------------
# Full OID sweep
# -----------------------------------------------------------------------

class TestOIDSweep:
    """Query every known OID and log which return data vs timeout."""

    def test_full_oid_sweep(self, snmp_client, event_loop):
        """Sweep all known OIDs and report coverage."""
        from src.pdu_model import (
            OID_ATS_AUTO_TRANSFER,
            OID_ATS_CURRENT_SOURCE,
            OID_ATS_PREFERRED_SOURCE,
            OID_DEVICE_NAME,
            OID_FW_MAIN,
            OID_FW_SECONDARY,
            OID_HW_REV,
            OID_INPUT_FREQUENCY,
            OID_INPUT_VOLTAGE,
            OID_MAX_CURRENT,
            OID_MODEL,
            OID_NUM_BANK_TABLE_ENTRIES,
            OID_OUTLET_COUNT,
            OID_PHASE_COUNT,
            OID_SERIAL_HW,
            OID_SERIAL_NUM,
            OID_SOURCE_A_FREQUENCY,
            OID_SOURCE_A_STATUS,
            OID_SOURCE_A_VOLTAGE,
            OID_SOURCE_B_FREQUENCY,
            OID_SOURCE_B_STATUS,
            OID_SOURCE_B_VOLTAGE,
            OID_SOURCE_REDUNDANCY,
            OID_SYS_CONTACT,
            OID_SYS_DESCR,
            OID_SYS_LOCATION,
            OID_SYS_NAME,
            OID_SYS_UPTIME,
        )

        oids = {
            "DEVICE_NAME": OID_DEVICE_NAME,
            "MODEL": OID_MODEL,
            "SERIAL_HW": OID_SERIAL_HW,
            "SERIAL_NUM": OID_SERIAL_NUM,
            "FW_MAIN": OID_FW_MAIN,
            "FW_SECONDARY": OID_FW_SECONDARY,
            "HW_REV": OID_HW_REV,
            "MAX_CURRENT": OID_MAX_CURRENT,
            "OUTLET_COUNT": OID_OUTLET_COUNT,
            "PHASE_COUNT": OID_PHASE_COUNT,
            "INPUT_VOLTAGE": OID_INPUT_VOLTAGE,
            "INPUT_FREQUENCY": OID_INPUT_FREQUENCY,
            "NUM_BANK_TABLE_ENTRIES": OID_NUM_BANK_TABLE_ENTRIES,
            "ATS_PREFERRED_SOURCE": OID_ATS_PREFERRED_SOURCE,
            "ATS_CURRENT_SOURCE": OID_ATS_CURRENT_SOURCE,
            "ATS_AUTO_TRANSFER": OID_ATS_AUTO_TRANSFER,
            "SOURCE_A_VOLTAGE": OID_SOURCE_A_VOLTAGE,
            "SOURCE_A_FREQUENCY": OID_SOURCE_A_FREQUENCY,
            "SOURCE_A_STATUS": OID_SOURCE_A_STATUS,
            "SOURCE_B_VOLTAGE": OID_SOURCE_B_VOLTAGE,
            "SOURCE_B_FREQUENCY": OID_SOURCE_B_FREQUENCY,
            "SOURCE_B_STATUS": OID_SOURCE_B_STATUS,
            "SOURCE_REDUNDANCY": OID_SOURCE_REDUNDANCY,
            "SYS_DESCR": OID_SYS_DESCR,
            "SYS_UPTIME": OID_SYS_UPTIME,
            "SYS_CONTACT": OID_SYS_CONTACT,
            "SYS_NAME": OID_SYS_NAME,
            "SYS_LOCATION": OID_SYS_LOCATION,
        }

        responded = 0
        total = len(oids)
        for name, oid in oids.items():
            val = run(event_loop, snmp_client.get(oid))
            status = "OK" if val is not None else "TIMEOUT"
            if val is not None:
                responded += 1
            print(f"  {name:<30} {status}  {val}")

        coverage = responded / total * 100 if total else 0
        print(f"\n  OID coverage: {responded}/{total} ({coverage:.0f}%)")
        assert responded >= 5, f"Too few OIDs responded ({responded}/{total})"


# -----------------------------------------------------------------------
# SNMP health after sweep
# -----------------------------------------------------------------------

class TestHealthAfterSweep:
    """Verify SNMP health is good after all the queries."""

    def test_health_endpoint(self, snmp_client, event_loop):
        """SNMP client should report healthy after the sweep."""
        health = snmp_client.get_health()
        print(f"  Total GETs: {health['total_gets']}")
        print(f"  Failed GETs: {health['failed_gets']}")
        print(f"  Consecutive failures: {health['consecutive_failures']}")
        assert health["reachable"], "SNMP client reports unreachable after sweep"
