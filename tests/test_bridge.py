# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Unit tests for bridge components."""

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.config import Config
from src.pdu_model import (
    BASE_OID,
    OUTLET_CMD_MAP,
    OUTLET_CMD_OFF,
    OUTLET_CMD_ON,
    OUTLET_CMD_REBOOT,
    OUTLET_STATE_MAP,
    OutletData,
    BankData,
    PDUData,
    oid_outlet_state,
    oid_outlet_command,
    oid_bank_current,
)
from src.mock_pdu import MockPDU


def test_oid_functions():
    assert oid_outlet_state(1) == f"{BASE_OID}.3.5.1.1.4.1"
    assert oid_outlet_state(24) == f"{BASE_OID}.3.5.1.1.4.24"
    assert oid_outlet_command(5) == f"{BASE_OID}.3.3.1.1.4.5"
    assert oid_bank_current(2) == f"{BASE_OID}.2.3.1.1.2.2"


def test_outlet_cmd_map():
    assert OUTLET_CMD_MAP["on"] == OUTLET_CMD_ON
    assert OUTLET_CMD_MAP["off"] == OUTLET_CMD_OFF
    assert OUTLET_CMD_MAP["reboot"] == OUTLET_CMD_REBOOT


def test_outlet_state_map():
    assert OUTLET_STATE_MAP[1] == "on"
    assert OUTLET_STATE_MAP[2] == "off"


def test_config_defaults():
    os.environ.pop("PDU_HOST", None)
    os.environ.pop("BRIDGE_MOCK_MODE", None)
    config = Config()
    assert config.pdu_host == "192.168.20.177"
    assert config.mock_mode is False
    assert config.poll_interval == 1.0


def test_config_from_env():
    os.environ["PDU_HOST"] = "10.0.0.1"
    os.environ["BRIDGE_MOCK_MODE"] = "true"
    os.environ["BRIDGE_POLL_INTERVAL"] = "0.5"
    config = Config()
    assert config.pdu_host == "10.0.0.1"
    assert config.mock_mode is True
    assert config.poll_interval == 0.5
    # Clean up
    del os.environ["PDU_HOST"]
    del os.environ["BRIDGE_MOCK_MODE"]
    del os.environ["BRIDGE_POLL_INTERVAL"]


def test_data_classes():
    outlet = OutletData(number=1, name="Server", state="on", current=1.5, power=180.0)
    assert outlet.number == 1
    assert outlet.energy is None

    bank = BankData(number=1, current=5.0, voltage=120.0, load_state="normal")
    assert bank.power is None

    pdu = PDUData(device_name="Test", outlet_count=10)
    assert len(pdu.outlets) == 0
    assert len(pdu.banks) == 0


@pytest.mark.asyncio
async def test_mock_pdu_poll():
    mock = MockPDU()
    data = await mock.poll()

    assert data.device_name == "CyberPower PDU44001 (Mock)"
    assert data.outlet_count == 10
    assert len(data.outlets) == 10
    assert len(data.banks) == 2

    # All outlets start on
    assert data.outlets[1].state == "on"
    assert data.outlets[10].state == "on"

    # Bank 1 (active input) should have voltage and near-idle load
    assert data.banks[1].voltage > 100
    assert data.banks[1].power_factor >= 0.98
    assert data.banks[1].current < 1.0  # near-idle

    # Bank 2 (standby input) has voltage but no load
    assert data.banks[2].voltage > 100
    assert data.banks[2].current == 0.0


@pytest.mark.asyncio
async def test_mock_pdu_command():
    mock = MockPDU()

    # Turn off outlet 1
    assert await mock.command_outlet(1, OUTLET_CMD_OFF)
    data = await mock.poll()
    assert data.outlets[1].state == "off"

    # Turn it back on
    assert await mock.command_outlet(1, OUTLET_CMD_ON)
    data = await mock.poll()
    assert data.outlets[1].state == "on"

    # Invalid outlet
    assert not await mock.command_outlet(99, OUTLET_CMD_ON)


@pytest.mark.asyncio
async def test_mock_pdu_reboot():
    mock = MockPDU()

    assert await mock.command_outlet(1, OUTLET_CMD_REBOOT)
    data = await mock.poll()
    assert data.outlets[1].state == "off"  # Off during reboot


@pytest.mark.asyncio
async def test_mock_pdu_outlet_power_data():
    """Mock PDU should return non-None current/power/energy for ON outlets."""
    mock = MockPDU()
    data = await mock.poll()

    # All outlets start ON and should have realistic power data
    for n, outlet in data.outlets.items():
        assert outlet.current is not None, f"Outlet {n} current should not be None"
        assert outlet.power is not None, f"Outlet {n} power should not be None"
        assert outlet.energy is not None, f"Outlet {n} energy should not be None"
        assert outlet.current > 0, f"Outlet {n} should have nonzero current"
        assert outlet.power > 0, f"Outlet {n} should have nonzero power"


@pytest.mark.asyncio
async def test_mock_pdu_off_outlet_zero_power():
    """OFF outlets should have zero current and power."""
    mock = MockPDU()
    await mock.command_outlet(1, OUTLET_CMD_OFF)
    data = await mock.poll()

    assert data.outlets[1].current == 0.0
    assert data.outlets[1].power == 0.0


@pytest.mark.asyncio
async def test_mock_pdu_ats_source_transfer():
    """Mock PDU should simulate periodic ATS source transfers."""
    mock = MockPDU(num_banks=2)
    # Force a quick transfer by setting interval to 0
    mock._source_transfer_interval = 0
    mock._last_source_transfer = 0

    initial_source = mock._active_input
    data = await mock.poll()
    new_source = data.ats_current_source

    # Source should have switched
    assert new_source != initial_source
    assert new_source in (1, 2)
