# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Tests for SNMPTransport with mocked SNMPClient."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.pdu_config import PDUConfig
from src.pdu_model import (
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
    oid_bank_active_power,
    oid_bank_apparent_power,
    oid_bank_current,
    oid_bank_energy,
    oid_bank_load_state,
    oid_bank_power_factor,
    oid_bank_timestamp,
    oid_bank_voltage,
    oid_outlet_current,
    oid_outlet_energy,
    oid_outlet_name,
    oid_outlet_power,
    oid_outlet_state,
)
from src.snmp_transport import SNMPTransport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def pdu_cfg():
    return PDUConfig(device_id="test-pdu", host="192.168.1.100")


@pytest.fixture()
def mock_snmp():
    """Create a mock SNMPClient."""
    snmp = MagicMock()
    snmp.get = AsyncMock()
    snmp.get_many = AsyncMock(return_value={})
    snmp.set = AsyncMock(return_value=True)
    snmp.set_string = AsyncMock(return_value=True)
    snmp.get_identity = AsyncMock(return_value=DeviceIdentity(
        serial="TEST123",
        model="PDU44001",
        name="Test PDU",
        outlet_count=10,
        phase_count=1,
    ))
    snmp.consecutive_failures = 0
    snmp.get_health.return_value = {"target": "192.168.1.100:161", "consecutive_failures": 0}
    snmp.reset_health = MagicMock()
    snmp.close = MagicMock()
    return snmp


@pytest.fixture()
def transport(mock_snmp, pdu_cfg):
    return SNMPTransport(mock_snmp, pdu_cfg, num_banks=2)


# ---------------------------------------------------------------------------
# Connect tests
# ---------------------------------------------------------------------------

class TestSNMPTransportConnect:
    @pytest.mark.asyncio
    async def test_connect_is_noop(self, transport):
        await transport.connect()  # Should not raise


# ---------------------------------------------------------------------------
# Identity tests
# ---------------------------------------------------------------------------

class TestSNMPTransportIdentity:
    @pytest.mark.asyncio
    async def test_get_identity(self, transport, mock_snmp):
        identity = await transport.get_identity()
        assert identity.serial == "TEST123"
        assert identity.model == "PDU44001"
        assert identity.outlet_count == 10
        mock_snmp.get_identity.assert_called_once()

    @pytest.mark.asyncio
    async def test_identity_sets_outlet_count(self, transport, mock_snmp):
        await transport.get_identity()
        assert transport._outlet_count == 10


# ---------------------------------------------------------------------------
# Discover num banks
# ---------------------------------------------------------------------------

class TestSNMPTransportDiscoverBanks:
    @pytest.mark.asyncio
    async def test_discover_from_snmp(self, transport, mock_snmp):
        mock_snmp.get.return_value = 3
        count = await transport.discover_num_banks()
        assert count == 3
        assert transport._num_banks == 3

    @pytest.mark.asyncio
    async def test_fallback_to_config(self, transport, mock_snmp):
        mock_snmp.get.return_value = None
        count = await transport.discover_num_banks()
        assert count == 2  # PDUConfig default


# ---------------------------------------------------------------------------
# Poll tests
# ---------------------------------------------------------------------------

class TestSNMPTransportPoll:
    @pytest.mark.asyncio
    async def test_poll_basic(self, transport, mock_snmp):
        """Poll with minimal SNMP responses returns valid PDUData."""
        transport._outlet_count = 2
        transport._num_banks = 2

        mock_snmp.get_many.return_value = {
            OID_DEVICE_NAME: "Test PDU",
            OID_OUTLET_COUNT: 2,
            OID_PHASE_COUNT: 1,
            OID_INPUT_VOLTAGE: 1197,  # 119.7V
            OID_INPUT_FREQUENCY: 600,  # 60.0Hz
            OID_ATS_CURRENT_SOURCE: 1,
            OID_ATS_PREFERRED_SOURCE: 1,
            OID_ATS_AUTO_TRANSFER: 1,
            oid_outlet_name(1): "Server",
            oid_outlet_state(1): 1,  # ON
            oid_outlet_current(1): 5,  # 0.5A
            oid_outlet_power(1): 60,
            oid_outlet_energy(1): 100,
            oid_outlet_name(2): "Switch",
            oid_outlet_state(2): 2,  # OFF
            oid_outlet_current(2): 0,
            oid_outlet_power(2): 0,
            oid_outlet_energy(2): 0,
            oid_bank_current(1): 5,
            oid_bank_voltage(1): 1197,
            oid_bank_load_state(1): 1,
            oid_bank_active_power(1): 60,
            oid_bank_apparent_power(1): 65,
            oid_bank_power_factor(1): 92,
            oid_bank_current(2): 0,
            oid_bank_voltage(2): 1190,
            oid_bank_load_state(2): 1,
            OID_SOURCE_A_VOLTAGE: 1197,
            OID_SOURCE_B_VOLTAGE: 1190,
            OID_SOURCE_A_FREQUENCY: 600,
            OID_SOURCE_B_FREQUENCY: 600,
            OID_SOURCE_A_STATUS: 1,
            OID_SOURCE_B_STATUS: 1,
            OID_SOURCE_REDUNDANCY: 2,
        }

        data = await transport.poll()

        assert data.device_name == "Test PDU"
        assert data.input_voltage == 119.7
        assert data.input_frequency == 60.0
        assert data.outlet_count == 2
        assert len(data.outlets) == 2
        assert data.outlets[1].name == "Server"
        assert data.outlets[1].state == "on"
        assert data.outlets[1].current == 0.5
        assert data.outlets[2].state == "off"
        assert len(data.banks) == 2
        assert data.banks[1].voltage == 119.7
        assert data.ats_current_source == 1
        assert data.redundancy_ok is True

    @pytest.mark.asyncio
    async def test_poll_empty_response(self, transport, mock_snmp):
        """Poll with empty SNMP response returns defaults."""
        transport._outlet_count = 0
        transport._num_banks = 0
        mock_snmp.get_many.return_value = {}

        data = await transport.poll()
        assert data.device_name == ""
        assert data.input_voltage is None
        assert len(data.outlets) == 0


# ---------------------------------------------------------------------------
# Startup data tests
# ---------------------------------------------------------------------------

class TestSNMPTransportStartupData:
    @pytest.mark.asyncio
    async def test_query_startup_data(self, transport, mock_snmp):
        from src.pdu_model import oid_outlet_bank_assignment, oid_outlet_max_load
        mock_snmp.get_many.return_value = {
            oid_outlet_bank_assignment(1): 1,
            oid_outlet_max_load(1): 120,  # 12.0A
            oid_outlet_bank_assignment(2): 2,
            oid_outlet_max_load(2): 120,
        }

        assignments, max_loads = await transport.query_startup_data(2)
        assert assignments == {1: 1, 2: 2}
        assert max_loads == {1: 12.0, 2: 12.0}

    @pytest.mark.asyncio
    async def test_query_startup_data_empty(self, transport, mock_snmp):
        mock_snmp.get_many.return_value = {}
        assignments, max_loads = await transport.query_startup_data(0)
        assert assignments == {}
        assert max_loads == {}


# ---------------------------------------------------------------------------
# Command tests
# ---------------------------------------------------------------------------

class TestSNMPTransportCommand:
    @pytest.mark.asyncio
    async def test_command_on(self, transport, mock_snmp):
        result = await transport.command_outlet(1, "on")
        assert result is True
        mock_snmp.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_off(self, transport, mock_snmp):
        result = await transport.command_outlet(2, "off")
        assert result is True

    @pytest.mark.asyncio
    async def test_command_reboot(self, transport, mock_snmp):
        result = await transport.command_outlet(3, "reboot")
        assert result is True

    @pytest.mark.asyncio
    async def test_command_invalid(self, transport, mock_snmp):
        result = await transport.command_outlet(1, "invalid")
        assert result is False
        mock_snmp.set.assert_not_called()


# ---------------------------------------------------------------------------
# Set device field tests
# ---------------------------------------------------------------------------

class TestSNMPTransportSetField:
    @pytest.mark.asyncio
    async def test_set_device_name(self, transport, mock_snmp):
        result = await transport.set_device_field("device_name", "NewName")
        assert result is True
        mock_snmp.set_string.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_unknown_field(self, transport, mock_snmp):
        result = await transport.set_device_field("unknown_field", "value")
        assert result is False


# ---------------------------------------------------------------------------
# Health tests
# ---------------------------------------------------------------------------

class TestSNMPTransportHealth:
    def test_get_health(self, transport):
        health = transport.get_health()
        assert health["transport"] == "snmp"

    def test_consecutive_failures(self, transport, mock_snmp):
        mock_snmp.consecutive_failures = 5
        assert transport.consecutive_failures == 5

    def test_reset_health(self, transport, mock_snmp):
        transport.reset_health()
        mock_snmp.reset_health.assert_called_once()

    def test_close(self, transport, mock_snmp):
        transport.close()
        mock_snmp.close.assert_called_once()


# ---------------------------------------------------------------------------
# ATS configuration tests
# ---------------------------------------------------------------------------

class TestSNMPTransportATS:
    @pytest.mark.asyncio
    async def test_set_preferred_source_a(self, transport, mock_snmp):
        result = await transport.set_preferred_source("A")
        assert result is True
        mock_snmp.set.assert_called_once_with(OID_ATS_PREFERRED_SOURCE, 1)

    @pytest.mark.asyncio
    async def test_set_preferred_source_b(self, transport, mock_snmp):
        result = await transport.set_preferred_source("B")
        assert result is True
        mock_snmp.set.assert_called_once_with(OID_ATS_PREFERRED_SOURCE, 2)

    @pytest.mark.asyncio
    async def test_set_preferred_source_lowercase(self, transport, mock_snmp):
        result = await transport.set_preferred_source("b")
        assert result is True
        mock_snmp.set.assert_called_once_with(OID_ATS_PREFERRED_SOURCE, 2)

    @pytest.mark.asyncio
    async def test_set_preferred_source_invalid(self, transport, mock_snmp):
        result = await transport.set_preferred_source("C")
        assert result is False
        mock_snmp.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_auto_transfer_enabled(self, transport, mock_snmp):
        result = await transport.set_auto_transfer(True)
        assert result is True
        mock_snmp.set.assert_called_once_with(OID_ATS_AUTO_TRANSFER, 1)

    @pytest.mark.asyncio
    async def test_set_auto_transfer_disabled(self, transport, mock_snmp):
        result = await transport.set_auto_transfer(False)
        assert result is True
        mock_snmp.set.assert_called_once_with(OID_ATS_AUTO_TRANSFER, 2)


# ---------------------------------------------------------------------------
# Delayed/cancel command tests (SNMP limitations)
# ---------------------------------------------------------------------------

class TestSNMPTransportDelayedCommands:
    @pytest.mark.asyncio
    async def test_command_delayon_returns_false(self, transport, mock_snmp):
        """SNMP does not support delayon — serial-only."""
        result = await transport.command_outlet(1, "delayon")
        assert result is False
        mock_snmp.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_command_delayoff_returns_false(self, transport, mock_snmp):
        """SNMP does not support delayoff — serial-only."""
        result = await transport.command_outlet(1, "delayoff")
        assert result is False
        mock_snmp.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_command_cancel_returns_false(self, transport, mock_snmp):
        """SNMP does not support cancel — serial-only."""
        result = await transport.command_outlet(1, "cancel")
        assert result is False
        mock_snmp.set.assert_not_called()


# ---------------------------------------------------------------------------
# Environmental sensor poll tests
# ---------------------------------------------------------------------------

class TestSNMPTransportEnvironment:
    @pytest.mark.asyncio
    async def test_poll_environment_sensor_present(self, transport, mock_snmp):
        """When sensor is present, _poll_environment returns EnvironmentalData."""
        from src.pdu_model import (
            OID_ENVIRO_TEMPERATURE, OID_ENVIRO_TEMP_UNIT,
            OID_ENVIRO_HUMIDITY,
            OID_ENVIRO_CONTACT_1, OID_ENVIRO_CONTACT_2,
            OID_ENVIRO_CONTACT_3, OID_ENVIRO_CONTACT_4,
        )
        mock_snmp.get_many.return_value = {
            OID_ENVIRO_TEMPERATURE: 235,   # 23.5 degrees
            OID_ENVIRO_TEMP_UNIT: 1,       # Celsius
            OID_ENVIRO_HUMIDITY: 45,        # 45%
            OID_ENVIRO_CONTACT_1: 2,        # closed
            OID_ENVIRO_CONTACT_2: 1,        # open
            OID_ENVIRO_CONTACT_3: None,
            OID_ENVIRO_CONTACT_4: None,
        }

        env = await transport._poll_environment()
        assert env is not None
        assert env.sensor_present is True
        assert env.temperature == 23.5
        assert env.temperature_unit == "C"
        assert env.humidity == 45
        assert env.contacts[1] is True
        assert env.contacts[2] is False
        assert transport._enviro_supported is True

    @pytest.mark.asyncio
    async def test_poll_environment_fahrenheit(self, transport, mock_snmp):
        """Temperature unit 2 should map to Fahrenheit."""
        from src.pdu_model import (
            OID_ENVIRO_TEMPERATURE, OID_ENVIRO_TEMP_UNIT,
            OID_ENVIRO_HUMIDITY,
        )
        mock_snmp.get_many.return_value = {
            OID_ENVIRO_TEMPERATURE: 750,   # 75.0 degrees F
            OID_ENVIRO_TEMP_UNIT: 2,       # Fahrenheit
            OID_ENVIRO_HUMIDITY: 50,
        }

        env = await transport._poll_environment()
        assert env is not None
        assert env.temperature == 75.0
        assert env.temperature_unit == "F"

    @pytest.mark.asyncio
    async def test_poll_environment_sensor_absent_single_probe(self, transport, mock_snmp):
        """When sensor absent, returns None but doesn't disable after 1 probe."""
        mock_snmp.get_many.return_value = {}

        env = await transport._poll_environment()
        assert env is None
        assert transport._enviro_probe_count == 1
        assert transport._enviro_supported is not False  # None=unknown, still probing

    @pytest.mark.asyncio
    async def test_poll_environment_sensor_absent_after_3_probes(self, transport, mock_snmp):
        """After 3 probes with no sensor, _enviro_supported should be False."""
        mock_snmp.get_many.return_value = {}

        for _ in range(3):
            env = await transport._poll_environment()
            assert env is None

        assert transport._enviro_probe_count == 3
        assert transport._enviro_supported is False

    @pytest.mark.asyncio
    async def test_poll_skips_environment_when_unsupported(self, transport, mock_snmp):
        """Once _enviro_supported=False, poll should not call _poll_environment."""
        transport._enviro_supported = False
        transport._outlet_count = 0
        transport._num_banks = 0
        mock_snmp.get_many.return_value = {}

        data = await transport.poll()
        assert data.environment is None
        # Only one get_many call (the main poll), not a second for environment
        assert mock_snmp.get_many.call_count == 1

    @pytest.mark.asyncio
    async def test_poll_includes_environment_when_supported(self, transport, mock_snmp):
        """When _enviro_supported is None (unknown), poll calls _poll_environment."""
        from src.pdu_model import (
            OID_ENVIRO_TEMPERATURE, OID_ENVIRO_TEMP_UNIT,
            OID_ENVIRO_HUMIDITY,
        )
        transport._outlet_count = 0
        transport._num_banks = 0

        # First call: main poll OIDs, second call: environment OIDs
        mock_snmp.get_many.side_effect = [
            {},  # main poll
            {    # environment poll
                OID_ENVIRO_TEMPERATURE: 220,
                OID_ENVIRO_TEMP_UNIT: 1,
                OID_ENVIRO_HUMIDITY: 55,
            },
        ]

        data = await transport.poll()
        assert data.environment is not None
        assert data.environment.temperature == 22.0
        assert data.environment.humidity == 55
        assert mock_snmp.get_many.call_count == 2


# ---------------------------------------------------------------------------
# SNMP SET method tests
# ---------------------------------------------------------------------------

class TestSNMPTransportSET:
    """Tests for SNMP SET operations (preferred source, auto transfer, device field)."""

    @pytest.mark.asyncio
    async def test_set_preferred_source_a(self, transport, mock_snmp):
        """set_preferred_source('A') sends correct OID and value."""
        mock_snmp.set = AsyncMock(return_value=True)
        result = await transport.set_preferred_source("A")
        assert result is True
        mock_snmp.set.assert_called_once()
        call_args = mock_snmp.set.call_args[0]
        assert call_args[0] == OID_ATS_PREFERRED_SOURCE
        assert call_args[1] == 1  # ATS_SOURCE_REVERSE['A'] = 1

    @pytest.mark.asyncio
    async def test_set_preferred_source_b(self, transport, mock_snmp):
        """set_preferred_source('B') sends correct value."""
        mock_snmp.set = AsyncMock(return_value=True)
        result = await transport.set_preferred_source("B")
        assert result is True
        call_args = mock_snmp.set.call_args[0]
        assert call_args[1] == 2  # ATS_SOURCE_REVERSE['B'] = 2

    @pytest.mark.asyncio
    async def test_set_preferred_source_invalid(self, transport, mock_snmp):
        """set_preferred_source with invalid value returns False."""
        result = await transport.set_preferred_source("C")
        assert result is False
        mock_snmp.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_auto_transfer_enable(self, transport, mock_snmp):
        """set_auto_transfer(True) sends value 1 (enabled)."""
        mock_snmp.set = AsyncMock(return_value=True)
        result = await transport.set_auto_transfer(True)
        assert result is True
        call_args = mock_snmp.set.call_args[0]
        assert call_args[0] == OID_ATS_AUTO_TRANSFER
        assert call_args[1] == 1

    @pytest.mark.asyncio
    async def test_set_auto_transfer_disable(self, transport, mock_snmp):
        """set_auto_transfer(False) sends value 2 (disabled)."""
        mock_snmp.set = AsyncMock(return_value=True)
        result = await transport.set_auto_transfer(False)
        assert result is True
        call_args = mock_snmp.set.call_args[0]
        assert call_args[1] == 2

    @pytest.mark.asyncio
    async def test_set_device_field_name(self, transport, mock_snmp):
        """set_device_field('device_name', ...) sends SNMP SET with correct OID."""
        mock_snmp.set_string = AsyncMock(return_value=True)
        result = await transport.set_device_field("device_name", "MyPDU")
        assert result is True
        mock_snmp.set_string.assert_called_once_with(OID_DEVICE_NAME, "MyPDU")

    @pytest.mark.asyncio
    async def test_set_device_field_unknown(self, transport, mock_snmp):
        """set_device_field with unknown field returns False."""
        result = await transport.set_device_field("unknown_field", "val")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_preferred_source_snmp_failure(self, transport, mock_snmp):
        """set_preferred_source returns False when SNMP SET fails."""
        mock_snmp.set = AsyncMock(return_value=False)
        result = await transport.set_preferred_source("A")
        assert result is False
