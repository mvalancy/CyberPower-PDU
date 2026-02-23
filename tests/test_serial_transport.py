# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Tests for SerialTransport with mocked SerialClient."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.pdu_config import PDUConfig
from src.pdu_model import DeviceIdentity
from src.serial_transport import SerialTransport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SYS_SHOW_RESPONSE = """\
Name           : TestPDU
Location       : Lab
Model Name     : PDU44001
Firmware Version : 1.3.4
MAC Address    : 00:0C:15:AA:BB:CC
Serial Number  : NLKQY7000136
Hardware Version : 3
"""

DEVSTA_SHOW_RESPONSE = """\
Active Source   : A
Source Voltage (A/B) : 120.1 /119.8 V
Source Frequency (A/B) : 60.0 /60.0 Hz
Source Status (A/B) : Normal /Normal
Total Load     : 0.5 A
Total Power    : 60 W
Total Energy   : 50.0 kWh
Bank 1 Current : 0.3 A
Bank 2 Current : 0.2 A
"""

OLTSTA_SHOW_RESPONSE = """\
Index  Name        Status  Current(A)  Power(W)
1      Server1     On      0.3         36
2      Switch1     On      0.2         24
3      Monitor     Off     0.0         0
"""

SRCCFG_SHOW_RESPONSE = """\
Preferred Source : A
Voltage Sensitivity : Normal
Transfer Voltage : 88 V
"""


DEVCFG_SHOW_RESPONSE = """\
Overload Threshold : 80 %
Near Overload      : 70 %
Low Load           : 10 %
Coldstart Delay    : 0
Coldstart State    : allon
"""

@pytest.fixture()
def pdu_cfg():
    return PDUConfig(
        device_id="serial-pdu",
        host="",
        serial_port="/dev/ttyUSB3",
    )


@pytest.fixture()
def mock_serial():
    """Create a mock SerialClient."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.execute = AsyncMock()
    client.close = MagicMock()
    client.consecutive_failures = 0
    client.get_health.return_value = {
        "port": "/dev/ttyUSB3",
        "connected": True,
        "consecutive_failures": 0,
    }
    client.reset_health = MagicMock()
    return client


@pytest.fixture()
def transport(mock_serial, pdu_cfg):
    return SerialTransport(mock_serial, pdu_cfg)


# ---------------------------------------------------------------------------
# Connect tests
# ---------------------------------------------------------------------------

class TestSerialTransportConnect:
    @pytest.mark.asyncio
    async def test_connect_delegates(self, transport, mock_serial):
        await transport.connect()
        mock_serial.connect.assert_called_once()


# ---------------------------------------------------------------------------
# Identity tests
# ---------------------------------------------------------------------------

class TestSerialTransportIdentity:
    @pytest.mark.asyncio
    async def test_get_identity(self, transport, mock_serial):
        mock_serial.execute.side_effect = [
            SYS_SHOW_RESPONSE,
            OLTSTA_SHOW_RESPONSE,
        ]

        identity = await transport.get_identity()
        assert identity.name == "TestPDU"
        assert identity.model == "PDU44001"
        assert identity.serial == "NLKQY7000136"
        assert identity.outlet_count == 3

    @pytest.mark.asyncio
    async def test_identity_calls_sys_show_and_oltsta(self, transport, mock_serial):
        mock_serial.execute.side_effect = [
            SYS_SHOW_RESPONSE,
            OLTSTA_SHOW_RESPONSE,
        ]
        await transport.get_identity()
        assert mock_serial.execute.call_count == 2
        calls = [c[0][0] for c in mock_serial.execute.call_args_list]
        assert "sys show" in calls
        assert "oltsta show" in calls


# ---------------------------------------------------------------------------
# Discover banks tests
# ---------------------------------------------------------------------------

class TestSerialTransportDiscoverBanks:
    @pytest.mark.asyncio
    async def test_discover_from_devsta(self, transport, mock_serial):
        mock_serial.execute.return_value = DEVSTA_SHOW_RESPONSE
        count = await transport.discover_num_banks()
        assert count == 2

    @pytest.mark.asyncio
    async def test_discover_single_bank(self, transport, mock_serial):
        mock_serial.execute.return_value = """\
Active Source   : A
Source Voltage (A/B) : 120.0 /0.0 V
Bank 1 Current : 0.5 A
"""
        count = await transport.discover_num_banks()
        assert count == 1

    @pytest.mark.asyncio
    async def test_discover_fallback_to_config(self, transport, mock_serial):
        mock_serial.execute.return_value = "No data\n"
        # No bank_currents and no dual voltages -> fallback
        count = await transport.discover_num_banks()
        assert count == 2  # PDUConfig default


# ---------------------------------------------------------------------------
# Poll tests
# ---------------------------------------------------------------------------

class TestSerialTransportPoll:
    @pytest.mark.asyncio
    async def test_poll_full(self, transport, mock_serial):
        mock_serial.execute.side_effect = [
            DEVSTA_SHOW_RESPONSE,
            OLTSTA_SHOW_RESPONSE,
            SRCCFG_SHOW_RESPONSE,
            DEVCFG_SHOW_RESPONSE,
        ]
        transport._identity = DeviceIdentity(name="TestPDU", model="PDU44001")

        data = await transport.poll()

        assert data.device_name == "TestPDU"
        assert data.outlet_count == 3
        assert data.input_voltage == 120.1
        assert data.input_frequency == 60.0
        assert data.ats_current_source == 1  # A
        assert len(data.outlets) == 3
        assert data.outlets[1].name == "Server1"
        assert data.outlets[1].state == "on"
        assert data.outlets[3].state == "off"
        assert data.source_a.voltage == 120.1
        assert data.source_b.voltage == 119.8
        assert data.redundancy_ok is True

    @pytest.mark.asyncio
    async def test_poll_calls_four_commands(self, transport, mock_serial):
        mock_serial.execute.side_effect = [
            DEVSTA_SHOW_RESPONSE,
            OLTSTA_SHOW_RESPONSE,
            SRCCFG_SHOW_RESPONSE,
            DEVCFG_SHOW_RESPONSE,
        ]

        await transport.poll()
        assert mock_serial.execute.call_count == 4
        calls = [c[0][0] for c in mock_serial.execute.call_args_list]
        assert calls == ["devsta show", "oltsta show", "srccfg show", "devcfg show"]


# ---------------------------------------------------------------------------
# Command tests
# ---------------------------------------------------------------------------

class TestSerialTransportCommand:
    @pytest.mark.asyncio
    async def test_command_on(self, transport, mock_serial):
        mock_serial.execute.return_value = "Command successful"
        result = await transport.command_outlet(1, "on")
        assert result is True
        mock_serial.execute.assert_called_with("oltctrl index 1 act on")

    @pytest.mark.asyncio
    async def test_command_off(self, transport, mock_serial):
        mock_serial.execute.return_value = "Command successful"
        result = await transport.command_outlet(5, "off")
        assert result is True
        mock_serial.execute.assert_called_with("oltctrl index 5 act off")

    @pytest.mark.asyncio
    async def test_command_reboot(self, transport, mock_serial):
        mock_serial.execute.return_value = "Command successful"
        result = await transport.command_outlet(3, "reboot")
        assert result is True
        mock_serial.execute.assert_called_with("oltctrl index 3 act reboot")

    @pytest.mark.asyncio
    async def test_command_invalid(self, transport, mock_serial):
        result = await transport.command_outlet(1, "explode")
        assert result is False
        mock_serial.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_command_error_response(self, transport, mock_serial):
        mock_serial.execute.return_value = "Error: outlet not found"
        result = await transport.command_outlet(99, "on")
        assert result is False

    @pytest.mark.asyncio
    async def test_command_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("port closed")
        result = await transport.command_outlet(1, "on")
        assert result is False


# ---------------------------------------------------------------------------
# Set field tests
# ---------------------------------------------------------------------------

class TestSerialTransportSetField:
    @pytest.mark.asyncio
    async def test_set_device_name(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_device_field("device_name", "NewPDU")
        assert result is True
        mock_serial.execute.assert_called_with("syscfg set name NewPDU")

    @pytest.mark.asyncio
    async def test_set_location(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_device_field("sys_location", "Rack 5")
        assert result is True

    @pytest.mark.asyncio
    async def test_set_unknown_field(self, transport, mock_serial):
        result = await transport.set_device_field("bogus", "value")
        assert result is False


# ---------------------------------------------------------------------------
# Health tests
# ---------------------------------------------------------------------------

class TestSerialTransportHealth:
    def test_get_health(self, transport):
        health = transport.get_health()
        assert health["transport"] == "serial"
        assert health["port"] == "/dev/ttyUSB3"

    def test_consecutive_failures(self, transport, mock_serial):
        mock_serial.consecutive_failures = 7
        assert transport.consecutive_failures == 7

    def test_reset_health(self, transport, mock_serial):
        transport.reset_health()
        mock_serial.reset_health.assert_called_once()

    def test_close(self, transport, mock_serial):
        transport.close()
        mock_serial.close.assert_called_once()


# ---------------------------------------------------------------------------
# Startup data tests
# ---------------------------------------------------------------------------

class TestSerialTransportStartupData:
    @pytest.mark.asyncio
    async def test_returns_empty(self, transport):
        """Serial doesn't support per-outlet bank assignment queries."""
        assignments, max_loads = await transport.query_startup_data(10)
        assert assignments == {}
        assert max_loads == {}


# ---------------------------------------------------------------------------
# ATS configuration tests
# ---------------------------------------------------------------------------

class TestSerialTransportATS:
    @pytest.mark.asyncio
    async def test_set_preferred_source_a(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_preferred_source("A")
        assert result is True
        mock_serial.execute.assert_called_with("srccfg set preferred A")

    @pytest.mark.asyncio
    async def test_set_preferred_source_b(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_preferred_source("b")
        assert result is True
        mock_serial.execute.assert_called_with("srccfg set preferred B")

    @pytest.mark.asyncio
    async def test_set_preferred_source_invalid(self, transport, mock_serial):
        result = await transport.set_preferred_source("C")
        assert result is False
        mock_serial.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_voltage_sensitivity_normal(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_voltage_sensitivity("normal")
        assert result is True
        mock_serial.execute.assert_called_with("srccfg set sensitivity normal")

    @pytest.mark.asyncio
    async def test_set_voltage_sensitivity_high(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_voltage_sensitivity("High")
        assert result is True
        mock_serial.execute.assert_called_with("srccfg set sensitivity high")

    @pytest.mark.asyncio
    async def test_set_voltage_sensitivity_invalid(self, transport, mock_serial):
        result = await transport.set_voltage_sensitivity("extreme")
        assert result is False
        mock_serial.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_transfer_voltage_both(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_transfer_voltage(upper=148, lower=88)
        assert result is True
        calls = [c[0][0] for c in mock_serial.execute.call_args_list]
        assert "srccfg set uppervoltage 148" in calls
        assert "srccfg set lowervoltage 88" in calls

    @pytest.mark.asyncio
    async def test_set_transfer_voltage_upper_only(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_transfer_voltage(upper=150)
        assert result is True
        mock_serial.execute.assert_called_once_with("srccfg set uppervoltage 150")

    @pytest.mark.asyncio
    async def test_set_transfer_voltage_error(self, transport, mock_serial):
        mock_serial.execute.return_value = "Error: out of range"
        result = await transport.set_transfer_voltage(upper=999)
        assert result is False


# ---------------------------------------------------------------------------
# Coldstart configuration tests
# ---------------------------------------------------------------------------

class TestSerialTransportColdstart:
    @pytest.mark.asyncio
    async def test_set_coldstart_delay(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_coldstart_delay(5)
        assert result is True
        mock_serial.execute.assert_called_with("devcfg coldstadly 5")

    @pytest.mark.asyncio
    async def test_set_coldstart_state_allon(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_coldstart_state("allon")
        assert result is True
        mock_serial.execute.assert_called_with("devcfg coldstastate allon")

    @pytest.mark.asyncio
    async def test_set_coldstart_state_prevstate(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_coldstart_state("prevstate")
        assert result is True
        mock_serial.execute.assert_called_with("devcfg coldstastate prevstate")

    @pytest.mark.asyncio
    async def test_set_coldstart_state_invalid(self, transport, mock_serial):
        result = await transport.set_coldstart_state("randomstate")
        assert result is False
        mock_serial.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Source config query tests
# ---------------------------------------------------------------------------

class TestSerialTransportSourceConfig:
    @pytest.mark.asyncio
    async def test_get_source_config(self, transport, mock_serial):
        mock_serial.execute.return_value = SRCCFG_SHOW_RESPONSE
        config = await transport.get_source_config()
        assert isinstance(config, dict)
        mock_serial.execute.assert_called_with("srccfg show")


# ---------------------------------------------------------------------------
# Network configuration tests
# ---------------------------------------------------------------------------

class TestSerialTransportNetworkConfig:
    @pytest.mark.asyncio
    async def test_set_network_config_ip_and_subnet(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_network_config(
            ip="192.168.1.1", subnet="255.255.255.0"
        )
        assert result is True
        calls = [c[0][0] for c in mock_serial.execute.call_args_list]
        assert "netcfg set ip 192.168.1.1" in calls
        assert "netcfg set subnet 255.255.255.0" in calls

    @pytest.mark.asyncio
    async def test_set_network_config_dhcp(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_network_config(dhcp=True)
        assert result is True
        mock_serial.execute.assert_called_with("netcfg set dhcp enabled")

    @pytest.mark.asyncio
    async def test_set_network_config_error(self, transport, mock_serial):
        mock_serial.execute.return_value = "Error: invalid IP"
        result = await transport.set_network_config(ip="bad")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_network_config_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("port closed")
        result = await transport.set_network_config(ip="192.168.1.1")
        assert result is False


# ---------------------------------------------------------------------------
# SNMP trap configuration tests
# ---------------------------------------------------------------------------

class TestSerialTransportTrapConfig:
    @pytest.mark.asyncio
    async def test_get_trap_config(self, transport, mock_serial):
        mock_serial.execute.return_value = "Index  IP  Community\n1  10.0.0.1  public\n"
        result = await transport.get_trap_config()
        assert isinstance(result, list)
        mock_serial.execute.assert_called_with("trapcfg show")

    @pytest.mark.asyncio
    async def test_get_trap_config_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("timeout")
        result = await transport.get_trap_config()
        assert result == []

    @pytest.mark.asyncio
    async def test_set_trap_receiver(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_trap_receiver(
            index=1, ip="10.0.0.5", community="private", enabled=True
        )
        assert result is True
        calls = [c[0][0] for c in mock_serial.execute.call_args_list]
        assert "trapcfg set 1 ip 10.0.0.5" in calls
        assert "trapcfg set 1 community private" in calls
        assert "trapcfg set 1 status enabled" in calls

    @pytest.mark.asyncio
    async def test_set_trap_receiver_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("port closed")
        result = await transport.set_trap_receiver(index=1, ip="10.0.0.5")
        assert result is False


# ---------------------------------------------------------------------------
# SMTP configuration tests
# ---------------------------------------------------------------------------

class TestSerialTransportSMTPConfig:
    @pytest.mark.asyncio
    async def test_get_smtp_config(self, transport, mock_serial):
        mock_serial.execute.return_value = "Server: smtp.example.com\nPort: 25\n"
        result = await transport.get_smtp_config()
        assert isinstance(result, dict)
        mock_serial.execute.assert_called_with("smtpcfg show")

    @pytest.mark.asyncio
    async def test_get_smtp_config_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("timeout")
        result = await transport.get_smtp_config()
        assert result == {}

    @pytest.mark.asyncio
    async def test_set_smtp_config(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_smtp_config(
            server="smtp.example.com", port=587, from_addr="pdu@example.com"
        )
        assert result is True
        calls = [c[0][0] for c in mock_serial.execute.call_args_list]
        assert "smtpcfg set server smtp.example.com" in calls
        assert "smtpcfg set port 587" in calls
        assert "smtpcfg set from pdu@example.com" in calls

    @pytest.mark.asyncio
    async def test_set_smtp_config_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("port closed")
        result = await transport.set_smtp_config(server="smtp.test.com")
        assert result is False


# ---------------------------------------------------------------------------
# Email configuration tests
# ---------------------------------------------------------------------------

class TestSerialTransportEmailConfig:
    @pytest.mark.asyncio
    async def test_get_email_config(self, transport, mock_serial):
        mock_serial.execute.return_value = "Index  To  Status\n1  admin@test.com  Enabled\n"
        result = await transport.get_email_config()
        assert isinstance(result, list)
        mock_serial.execute.assert_called_with("emailcfg show")

    @pytest.mark.asyncio
    async def test_get_email_config_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("timeout")
        result = await transport.get_email_config()
        assert result == []

    @pytest.mark.asyncio
    async def test_set_email_recipient(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_email_recipient(
            index=1, to="ops@example.com", enabled=True
        )
        assert result is True
        calls = [c[0][0] for c in mock_serial.execute.call_args_list]
        assert "emailcfg set 1 to ops@example.com" in calls
        assert "emailcfg set 1 status enabled" in calls

    @pytest.mark.asyncio
    async def test_set_email_recipient_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("port closed")
        result = await transport.set_email_recipient(index=1, to="test@test.com")
        assert result is False


# ---------------------------------------------------------------------------
# Syslog configuration tests
# ---------------------------------------------------------------------------

class TestSerialTransportSyslogConfig:
    @pytest.mark.asyncio
    async def test_get_syslog_config(self, transport, mock_serial):
        mock_serial.execute.return_value = "Index  IP  Facility\n1  10.0.0.10  local0\n"
        result = await transport.get_syslog_config()
        assert isinstance(result, list)
        mock_serial.execute.assert_called_with("syslog show")

    @pytest.mark.asyncio
    async def test_get_syslog_config_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("timeout")
        result = await transport.get_syslog_config()
        assert result == []

    @pytest.mark.asyncio
    async def test_set_syslog_server(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_syslog_server(
            index=1, ip="10.0.0.10", facility="local0",
            severity="warning", enabled=True
        )
        assert result is True
        calls = [c[0][0] for c in mock_serial.execute.call_args_list]
        assert "syslog set 1 ip 10.0.0.10" in calls
        assert "syslog set 1 facility local0" in calls
        assert "syslog set 1 severity warning" in calls
        assert "syslog set 1 status enabled" in calls

    @pytest.mark.asyncio
    async def test_set_syslog_server_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("port closed")
        result = await transport.set_syslog_server(index=1, ip="10.0.0.10")
        assert result is False


# ---------------------------------------------------------------------------
# EnergyWise configuration tests
# ---------------------------------------------------------------------------

class TestSerialTransportEnergyWise:
    @pytest.mark.asyncio
    async def test_get_energywise_config(self, transport, mock_serial):
        mock_serial.execute.return_value = "Domain: factory\nPort: 43440\n"
        result = await transport.get_energywise_config()
        assert isinstance(result, dict)
        mock_serial.execute.assert_called_with("energywise show")

    @pytest.mark.asyncio
    async def test_get_energywise_config_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("timeout")
        result = await transport.get_energywise_config()
        assert result == {}

    @pytest.mark.asyncio
    async def test_set_energywise_config(self, transport, mock_serial):
        mock_serial.execute.return_value = "OK"
        result = await transport.set_energywise_config(
            domain="mynetwork", port=43440, secret="s3cret", enabled=True
        )
        assert result is True
        calls = [c[0][0] for c in mock_serial.execute.call_args_list]
        assert "energywise set domain mynetwork" in calls
        assert "energywise set port 43440" in calls
        assert "energywise set secret s3cret" in calls
        assert "energywise set status enabled" in calls

    @pytest.mark.asyncio
    async def test_set_energywise_config_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("port closed")
        result = await transport.set_energywise_config(domain="test")
        assert result is False


# ---------------------------------------------------------------------------
# User config tests
# ---------------------------------------------------------------------------

class TestSerialTransportUserConfig:
    @pytest.mark.asyncio
    async def test_get_user_config(self, transport, mock_serial):
        mock_serial.execute.return_value = "Admin: cyber\nViewer: viewer\n"
        result = await transport.get_user_config()
        assert isinstance(result, dict)
        mock_serial.execute.assert_called_with("usercfg show")

    @pytest.mark.asyncio
    async def test_get_user_config_exception(self, transport, mock_serial):
        mock_serial.execute.side_effect = ConnectionError("timeout")
        result = await transport.get_user_config()
        assert "error" in result


# ---------------------------------------------------------------------------
# Delayed command tests (serial supports them)
# ---------------------------------------------------------------------------

class TestSerialTransportDelayedCommands:
    @pytest.mark.asyncio
    async def test_command_delayon(self, transport, mock_serial):
        mock_serial.execute.return_value = "Command successful"
        result = await transport.command_outlet(1, "delayon")
        assert result is True
        mock_serial.execute.assert_called_with("oltctrl index 1 act delayon")

    @pytest.mark.asyncio
    async def test_command_delayoff(self, transport, mock_serial):
        mock_serial.execute.return_value = "Command successful"
        result = await transport.command_outlet(2, "delayoff")
        assert result is True
        mock_serial.execute.assert_called_with("oltctrl index 2 act delayoff")

    @pytest.mark.asyncio
    async def test_command_cancel(self, transport, mock_serial):
        mock_serial.execute.return_value = "Command successful"
        result = await transport.command_outlet(3, "cancel")
        assert result is True
        mock_serial.execute.assert_called_with("oltctrl index 3 act cancel")


# ---------------------------------------------------------------------------
# Password change terminator tests
# ---------------------------------------------------------------------------

class TestSerialTransportPasswordTerminators:
    @pytest.mark.asyncio
    async def test_change_password_uses_space_for_prompts(self, transport, mock_serial):
        """Password sub-prompts should use SPACE terminator, not \\n."""
        mock_serial.execute_interactive = AsyncMock(return_value="OK")
        result = await transport.change_password("admin", "newpass123")
        assert result is True
        mock_serial.execute_interactive.assert_called_once()
        exchanges = mock_serial.execute_interactive.call_args[0][0]
        # The password and confirm exchanges should have SPACE terminator
        assert len(exchanges) == 3
        assert exchanges[1] == ("newpass123", "Confirm Password:", " ")
        assert exchanges[2] == ("newpass123", "CyberPower >", " ")

    @pytest.mark.asyncio
    async def test_change_password_uses_newline_for_command(self, transport, mock_serial):
        """The initial CLI command should use default \\n terminator."""
        mock_serial.execute_interactive = AsyncMock(return_value="OK")
        result = await transport.change_password("admin", "newpass123")
        assert result is True
        exchanges = mock_serial.execute_interactive.call_args[0][0]
        # First exchange is a CLI command — 2-tuple means default \n
        assert len(exchanges[0]) == 2
        assert exchanges[0] == ("usercfg admin password", "New Password:")

    @pytest.mark.asyncio
    async def test_change_password_viewer_account(self, transport, mock_serial):
        """Viewer account password change also uses SPACE for sub-prompts."""
        mock_serial.execute_interactive = AsyncMock(return_value="OK")
        result = await transport.change_password("viewer", "viewpass")
        assert result is True
        exchanges = mock_serial.execute_interactive.call_args[0][0]
        assert exchanges[0][0] == "usercfg viewer password"
        assert exchanges[1][2] == " "  # SPACE terminator
        assert exchanges[2][2] == " "  # SPACE terminator

    @pytest.mark.asyncio
    async def test_change_password_invalid_account(self, transport, mock_serial):
        """Invalid account type returns False."""
        result = await transport.change_password("root", "pass")
        assert result is False
        mock_serial.execute_interactive.assert_not_called()

    @pytest.mark.asyncio
    async def test_change_password_error_response(self, transport, mock_serial):
        """Error in response returns False."""
        mock_serial.execute_interactive = AsyncMock(return_value="Error: failed")
        result = await transport.change_password("admin", "newpass")
        assert result is False
