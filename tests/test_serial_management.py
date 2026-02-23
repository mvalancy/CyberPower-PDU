# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Tests for serial transport management commands (Phase 3-4)."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.serial_transport import SerialTransport
from src.serial_client import SerialClient
from src.pdu_config import PDUConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

OLTCFG_SHOW_RESPONSE = """\
Index  Name        On Delay(s)  Off Delay(s)  Reboot Duration(s)
1      Outlet1     0            0             10
2      Outlet2     5            0             10
3      Outlet3     10           5             15
"""

DEVCFG_SHOW_RESPONSE = """\
Overload Threshold : 80 %
Near Overload Threshold : 70 %
Low Load Threshold : 20 %
"""

BANKCFG_SHOW_RESPONSE = """\
Bank  Overload(%)  Near Overload(%)  Low Load(%)
1     80           70                20
2     85           75                25
"""

NETCFG_SHOW_RESPONSE = """\
IP Address     : 192.168.20.177
Subnet Mask    : 255.255.255.0
Gateway        : 192.168.20.1
DHCP           : Enabled
MAC Address    : 00:0C:15:AA:BB:CC
"""

EVENTLOG_SHOW_RESPONSE = """\
Index  Date        Time      Event
1      01/15/2026  14:23:05  Source A Power Restored
2      01/15/2026  14:22:30  Source A Power Lost
3      01/14/2026  09:00:00  System Started
"""


@pytest.fixture
def mock_serial_client():
    """Create a mocked SerialClient."""
    client = MagicMock(spec=SerialClient)
    client.port = "/dev/ttyUSB3"
    client.consecutive_failures = 0
    client.get_health.return_value = {
        "port": "/dev/ttyUSB3",
        "connected": True,
        "logged_in": True,
        "total_commands": 0,
        "failed_commands": 0,
        "consecutive_failures": 0,
        "reachable": True,
    }
    client.execute = AsyncMock()
    client.execute_interactive = AsyncMock()
    return client


@pytest.fixture
def pdu_config():
    return PDUConfig(
        device_id="test-pdu",
        host="192.168.20.177",
        serial_port="/dev/ttyUSB3",
        serial_username="cyber",
        serial_password="cyber",
    )


@pytest.fixture
def serial_transport(mock_serial_client, pdu_config):
    return SerialTransport(mock_serial_client, pdu_config)


# ---------------------------------------------------------------------------
# Outlet command tests (expanded)
# ---------------------------------------------------------------------------

class TestOutletCommands:
    def test_command_on(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "Command OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.command_outlet(1, "on")
        )
        assert result is True
        mock_serial_client.execute.assert_called_with("oltctrl index 1 act on")

    def test_command_off(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "Command OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.command_outlet(1, "off")
        )
        assert result is True

    def test_command_reboot(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "Command OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.command_outlet(1, "reboot")
        )
        assert result is True

    def test_command_delayon(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "Command OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.command_outlet(1, "delayon")
        )
        assert result is True
        mock_serial_client.execute.assert_called_with("oltctrl index 1 act delayon")

    def test_command_delayoff(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "Command OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.command_outlet(1, "delayoff")
        )
        assert result is True

    def test_command_cancel(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "Command OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.command_outlet(1, "cancel")
        )
        assert result is True

    def test_invalid_command(self, serial_transport, mock_serial_client):
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.command_outlet(1, "invalid")
        )
        assert result is False
        mock_serial_client.execute.assert_not_called()

    def test_command_error_response(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "Error: outlet not found"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.command_outlet(99, "on")
        )
        assert result is False

    def test_command_exception(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.side_effect = ConnectionError("Port closed")
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.command_outlet(1, "on")
        )
        assert result is False


# ---------------------------------------------------------------------------
# Outlet configuration tests
# ---------------------------------------------------------------------------

class TestConfigureOutlet:
    def test_set_name(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.configure_outlet(1, name="WebServer")
        )
        assert result is True
        mock_serial_client.execute.assert_called_with("oltcfg set 1 name WebServer")

    def test_set_on_delay(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.configure_outlet(2, on_delay=30)
        )
        assert result is True
        mock_serial_client.execute.assert_called_with("oltcfg set 2 ondelay 30")

    def test_set_multiple_fields(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.configure_outlet(
                1, name="DB", on_delay=10, off_delay=5, reboot_duration=20
            )
        )
        assert result is True
        assert mock_serial_client.execute.call_count == 4

    def test_error_response(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "Error: invalid parameter"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.configure_outlet(1, name="x")
        )
        assert result is False

    def test_exception(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.side_effect = ConnectionError("Port closed")
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.configure_outlet(1, name="x")
        )
        assert result is False


# ---------------------------------------------------------------------------
# Device threshold tests
# ---------------------------------------------------------------------------

class TestDeviceThresholds:
    def test_set_overload(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.set_device_threshold("overload", 85.0)
        )
        assert result is True
        mock_serial_client.execute.assert_called_with("devcfg overload 85")

    def test_set_nearover(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.set_device_threshold("nearover", 75.0)
        )
        assert result is True
        mock_serial_client.execute.assert_called_with("devcfg nearover 75")

    def test_set_lowload(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.set_device_threshold("lowload", 15.0)
        )
        assert result is True

    def test_invalid_type(self, serial_transport, mock_serial_client):
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.set_device_threshold("invalid", 50.0)
        )
        assert result is False
        mock_serial_client.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Bank threshold tests
# ---------------------------------------------------------------------------

class TestBankThresholds:
    def test_set_bank_overload(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.set_bank_threshold(1, "overload", 90.0)
        )
        assert result is True
        mock_serial_client.execute.assert_called_with("bankcfg index b1 overload 90")

    def test_set_bank2_nearover(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = "OK"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.set_bank_threshold(2, "nearover", 80.0)
        )
        assert result is True
        mock_serial_client.execute.assert_called_with("bankcfg index b2 nearover 80")

    def test_invalid_type(self, serial_transport, mock_serial_client):
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.set_bank_threshold(1, "invalid", 50.0)
        )
        assert result is False


# ---------------------------------------------------------------------------
# Read-only management query tests
# ---------------------------------------------------------------------------

class TestManagementQueries:
    def test_get_outlet_config(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = OLTCFG_SHOW_RESPONSE
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.get_outlet_config()
        )
        assert len(result) == 3
        assert result[1]["name"] == "Outlet1"
        assert result[2]["on_delay"] == 5
        assert result[3]["reboot_duration"] == 15

    def test_get_device_thresholds(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = DEVCFG_SHOW_RESPONSE
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.get_device_thresholds()
        )
        assert result["overload_threshold"] == 80.0
        assert result["near_overload_threshold"] == 70.0
        assert result["low_load_threshold"] == 20.0

    def test_get_bank_thresholds(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = BANKCFG_SHOW_RESPONSE
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.get_bank_thresholds()
        )
        assert len(result) == 2
        assert result[1]["overload"] == 80.0
        assert result[2]["near_overload"] == 75.0

    def test_get_network_config(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = NETCFG_SHOW_RESPONSE
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.get_network_config()
        )
        assert result["ip"] == "192.168.20.177"
        assert result["subnet"] == "255.255.255.0"
        assert result["gateway"] == "192.168.20.1"
        assert result["dhcp_enabled"] is True

    def test_get_event_log(self, serial_transport, mock_serial_client):
        mock_serial_client.execute.return_value = EVENTLOG_SHOW_RESPONSE
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.get_event_log()
        )
        assert len(result) == 3
        assert result[0]["event_type"] == "power_restore"
        assert result[1]["event_type"] == "power_loss"
        assert result[2]["event_type"] == "system_start"


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------

class TestSecurityCommands:
    def test_change_password_admin(self, serial_transport, mock_serial_client):
        mock_serial_client.execute_interactive.return_value = "Password changed"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.change_password("admin", "newpass123")
        )
        assert result is True
        mock_serial_client.execute_interactive.assert_called_once()
        args = mock_serial_client.execute_interactive.call_args[0][0]
        assert args[0][0] == "usercfg admin password"
        assert args[1][0] == "newpass123"
        assert args[2][0] == "newpass123"

    def test_change_password_viewer(self, serial_transport, mock_serial_client):
        mock_serial_client.execute_interactive.return_value = "Password changed"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.change_password("viewer", "viewpass")
        )
        assert result is True

    def test_change_password_invalid_type(self, serial_transport, mock_serial_client):
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.change_password("root", "pass")
        )
        assert result is False
        mock_serial_client.execute_interactive.assert_not_called()

    def test_change_password_error(self, serial_transport, mock_serial_client):
        mock_serial_client.execute_interactive.return_value = "Error: password too short"
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.change_password("admin", "x")
        )
        assert result is False

    def test_change_password_exception(self, serial_transport, mock_serial_client):
        mock_serial_client.execute_interactive.side_effect = ConnectionError("Port closed")
        result = asyncio.get_event_loop().run_until_complete(
            serial_transport.change_password("admin", "pass")
        )
        assert result is False


# ---------------------------------------------------------------------------
# Health and properties tests
# ---------------------------------------------------------------------------

class TestHealthAndProperties:
    def test_get_health(self, serial_transport, mock_serial_client):
        health = serial_transport.get_health()
        assert health["transport"] == "serial"
        assert health["connected"] is True

    def test_consecutive_failures(self, serial_transport, mock_serial_client):
        mock_serial_client.consecutive_failures = 5
        assert serial_transport.consecutive_failures == 5

    def test_reset_health(self, serial_transport, mock_serial_client):
        serial_transport.reset_health()
        mock_serial_client.reset_health.assert_called_once()

    def test_serial_client_property(self, serial_transport, mock_serial_client):
        assert serial_transport.serial_client is mock_serial_client

    def test_close(self, serial_transport, mock_serial_client):
        serial_transport.close()
        mock_serial_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# Default credential tests
# ---------------------------------------------------------------------------

class TestDefaultCredentials:
    def test_pdu_config_defaults(self):
        """PDUConfig should default to cyber/cyber."""
        config = PDUConfig(device_id="test")
        assert config.serial_username == "cyber"
        assert config.serial_password == "cyber"

    def test_from_dict_defaults(self):
        """from_dict with no serial fields should default to cyber/cyber."""
        config = PDUConfig.from_dict({"device_id": "test", "host": "1.2.3.4"})
        assert config.serial_username == "cyber"
        assert config.serial_password == "cyber"
