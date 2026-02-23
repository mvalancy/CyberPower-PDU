# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Tests for serial CLI text parsers using captured real PDU44001 output."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.serial_parser import (
    build_pdu_data,
    parse_bankcfg_show,
    parse_devcfg_show,
    parse_devsta_show,
    parse_emailcfg_show,
    parse_energywise_show,
    parse_eventlog_show,
    parse_netcfg_show,
    parse_oltcfg_show,
    parse_oltsta_show,
    parse_smtpcfg_show,
    parse_srccfg_show,
    parse_syslogcfg_show,
    parse_sys_show,
    parse_trapcfg_show,
    parse_usercfg_show,
)
from src.pdu_model import DeviceIdentity, OutletData


# ---------------------------------------------------------------------------
# Captured CLI output fixtures (from real PDU44001)
# ---------------------------------------------------------------------------

SYS_SHOW_OUTPUT = """\
CyberPower >

Name           : PDU44001
Location       : Server Room
Model Name     : PDU44001
Firmware Version : 1.3.4
MAC Address    : 00:0C:15:AA:BB:CC
Serial Number  : NLKQY7000136
Hardware Version : 3

CyberPower > """

DEVSTA_SHOW_OUTPUT = """\
CyberPower >

Active Source   : A
Source Voltage (A/B) : 119.7 /119.7 V
Source Frequency (A/B) : 60.0 /60.0 Hz
Source Status (A/B) : Normal /Normal
Total Load     : 0.3 A
Total Power    : 36 W
Total Energy   : 123.4 kWh
Bank 1 Current : 0.2 A
Bank 2 Current : 0.1 A

CyberPower > """

OLTSTA_SHOW_TABLE = """\
CyberPower >

Index  Name        Status  Current(A)  Power(W)
1      Outlet1     On      0.0         0
2      Outlet2     On      0.1         12
3      Outlet3     On      0.0         0
4      Outlet4     Off     0.0         0
5      Outlet5     On      0.0         0
6      Outlet6     On      0.1         12
7      Outlet7     On      0.0         0
8      Outlet8     On      0.0         0
9      Outlet9     On      0.0         0
10     Outlet10    On      0.1         12

CyberPower > """

OLTSTA_SHOW_KV = """\
Outlet 1 Name  : Outlet1
Outlet 1 Status : On
Outlet 1 Current : 0.0 A
Outlet 2 Name  : Outlet2
Outlet 2 Status : On
Outlet 2 Current : 0.1 A
Outlet 3 Name  : Outlet3
Outlet 3 Status : Off
"""

SRCCFG_SHOW_OUTPUT = """\
CyberPower >

Preferred Source : A
Voltage Sensitivity : Normal
Transfer Voltage : 88 V
Voltage Upper Limit : 148 V
Voltage Lower Limit : 88 V
Frequency Range : 47 - 63 Hz

CyberPower > """


# ---------------------------------------------------------------------------
# parse_sys_show tests
# ---------------------------------------------------------------------------

class TestParseSysShow:
    def test_basic_parse(self):
        identity = parse_sys_show(SYS_SHOW_OUTPUT)
        assert identity.name == "PDU44001"
        assert identity.sys_location == "Server Room"
        assert identity.model == "PDU44001"
        assert identity.firmware_main == "1.3.4"
        assert identity.mac_address == "00:0C:15:AA:BB:CC"
        assert identity.serial == "NLKQY7000136"
        assert identity.hardware_rev == 3

    def test_sys_name_matches_name(self):
        identity = parse_sys_show(SYS_SHOW_OUTPUT)
        assert identity.sys_name == "PDU44001"

    def test_empty_input(self):
        identity = parse_sys_show("")
        assert identity.name == ""
        assert identity.model == ""
        assert identity.serial == ""

    def test_partial_output(self):
        text = "Name : MyPDU\nModel Name : PDU30SWEV17FNET\n"
        identity = parse_sys_show(text)
        assert identity.name == "MyPDU"
        assert identity.model == "PDU30SWEV17FNET"
        assert identity.serial == ""

    def test_ansi_escape_stripped(self):
        text = "\x1b[0mName           : PDU44001\x1b[0m\nModel Name     : PDU44001\n"
        identity = parse_sys_show(text)
        assert identity.name == "PDU44001"
        assert identity.model == "PDU44001"

    def test_no_hardware_version(self):
        text = "Name : Test\nModel Name : PDU44001\n"
        identity = parse_sys_show(text)
        assert identity.hardware_rev == 0

    def test_model_fallback_key(self):
        """'Model' key works when 'Model Name' is absent."""
        text = "Name : Test\nModel : PDU15SWEV8FNET\n"
        identity = parse_sys_show(text)
        assert identity.model == "PDU15SWEV8FNET"


# ---------------------------------------------------------------------------
# parse_devsta_show tests
# ---------------------------------------------------------------------------

class TestParseDevstaShow:
    def test_basic_parse(self):
        result = parse_devsta_show(DEVSTA_SHOW_OUTPUT)
        assert result["active_source"] == "A"
        assert result["source_a_voltage"] == 119.7
        assert result["source_b_voltage"] == 119.7
        assert result["source_a_frequency"] == 60.0
        assert result["source_b_frequency"] == 60.0
        assert result["source_a_status"] == "normal"
        assert result["source_b_status"] == "normal"
        assert result["total_load"] == 0.3
        assert result["total_power"] == 36.0
        assert result["total_energy"] == 123.4
        assert result["bank_currents"] == {1: 0.2, 2: 0.1}

    def test_empty_input(self):
        result = parse_devsta_show("")
        assert result["active_source"] is None
        assert result["source_a_voltage"] is None
        assert result["bank_currents"] == {}

    def test_single_bank(self):
        text = """\
Active Source   : A
Source Voltage (A/B) : 120.1 /0.0 V
Bank 1 Current : 1.5 A
"""
        result = parse_devsta_show(text)
        assert result["active_source"] == "A"
        assert result["source_a_voltage"] == 120.1
        assert result["source_b_voltage"] == 0.0
        assert result["bank_currents"] == {1: 1.5}

    def test_source_b_active(self):
        text = "Active Source   : B\n"
        result = parse_devsta_show(text)
        assert result["active_source"] == "B"

    def test_voltage_with_spaces(self):
        text = "Source Voltage (A/B) : 121.3 / 118.9 V\n"
        result = parse_devsta_show(text)
        assert result["source_a_voltage"] == 121.3
        assert result["source_b_voltage"] == 118.9

    def test_status_mixed_case(self):
        text = "Source Status (A/B) : Normal /UnderVoltage\n"
        result = parse_devsta_show(text)
        assert result["source_a_status"] == "normal"
        assert result["source_b_status"] == "undervoltage"

    def test_multiple_banks(self):
        text = """\
Bank 1 Current : 0.5 A
Bank 2 Current : 0.3 A
Bank 3 Current : 0.7 A
"""
        result = parse_devsta_show(text)
        assert result["bank_currents"] == {1: 0.5, 2: 0.3, 3: 0.7}


# ---------------------------------------------------------------------------
# parse_oltsta_show tests
# ---------------------------------------------------------------------------

class TestParseOltstaShow:
    def test_table_format(self):
        outlets = parse_oltsta_show(OLTSTA_SHOW_TABLE)
        assert len(outlets) == 10
        assert outlets[1].name == "Outlet1"
        assert outlets[1].state == "on"
        assert outlets[1].current == 0.0
        assert outlets[1].power == 0.0
        assert outlets[4].state == "off"
        assert outlets[2].current == 0.1
        assert outlets[2].power == 12.0
        assert outlets[10].current == 0.1
        assert outlets[10].power == 12.0

    def test_kv_format(self):
        outlets = parse_oltsta_show(OLTSTA_SHOW_KV)
        assert len(outlets) == 3
        assert outlets[1].name == "Outlet1"
        assert outlets[1].state == "on"
        assert outlets[2].state == "on"
        assert outlets[3].state == "off"
        assert outlets[2].current == 0.1

    def test_empty_input(self):
        outlets = parse_oltsta_show("")
        assert outlets == {}

    def test_single_outlet(self):
        text = "1      Server1     On      1.2         144\n"
        outlets = parse_oltsta_show(text)
        assert len(outlets) == 1
        assert outlets[1].name == "Server1"
        assert outlets[1].state == "on"
        assert outlets[1].current == 1.2
        assert outlets[1].power == 144.0

    def test_multi_word_name(self):
        text = "1      Web Server  On      0.5         60\n"
        outlets = parse_oltsta_show(text)
        assert len(outlets) == 1
        assert outlets[1].name == "Web Server"
        assert outlets[1].state == "on"

    def test_outlet_numbers_sequential(self):
        outlets = parse_oltsta_show(OLTSTA_SHOW_TABLE)
        for i in range(1, 11):
            assert i in outlets
            assert outlets[i].number == i


# ---------------------------------------------------------------------------
# parse_srccfg_show tests
# ---------------------------------------------------------------------------

class TestParseSrccfgShow:
    def test_basic_parse(self):
        result = parse_srccfg_show(SRCCFG_SHOW_OUTPUT)
        assert result["preferred_source"] == "A"
        assert result["voltage_sensitivity"] == "Normal"
        assert result["transfer_voltage"] == 88.0
        assert result["voltage_upper_limit"] == 148.0
        assert result["voltage_lower_limit"] == 88.0

    def test_empty_input(self):
        result = parse_srccfg_show("")
        assert result["preferred_source"] is None
        assert result["voltage_sensitivity"] == ""

    def test_preferred_source_b(self):
        text = "Preferred Source : B\n"
        result = parse_srccfg_show(text)
        assert result["preferred_source"] == "B"


# ---------------------------------------------------------------------------
# build_pdu_data tests
# ---------------------------------------------------------------------------

class TestBuildPduData:
    def test_full_build(self):
        devsta = parse_devsta_show(DEVSTA_SHOW_OUTPUT)
        outlets = parse_oltsta_show(OLTSTA_SHOW_TABLE)
        srccfg = parse_srccfg_show(SRCCFG_SHOW_OUTPUT)
        identity = parse_sys_show(SYS_SHOW_OUTPUT)

        data = build_pdu_data(devsta, outlets, srccfg, identity)

        assert data.device_name == "PDU44001"
        assert data.outlet_count == 10
        assert data.input_voltage == 119.7
        assert data.input_frequency == 60.0
        assert data.ats_current_source == 1  # A = 1
        assert data.ats_preferred_source == 1  # A = 1
        assert data.ats_auto_transfer is True
        assert data.source_a.voltage == 119.7
        assert data.source_b.voltage == 119.7
        assert data.redundancy_ok is True
        assert len(data.outlets) == 10
        assert len(data.banks) == 2
        assert data.identity is not None
        assert data.identity.serial == "NLKQY7000136"

    def test_source_b_active(self):
        devsta = {
            "active_source": "B",
            "source_a_voltage": 120.0,
            "source_b_voltage": 119.5,
            "source_a_frequency": 60.0,
            "source_b_frequency": 60.0,
            "source_a_status": "normal",
            "source_b_status": "normal",
            "total_load": 0.5,
            "total_power": 60,
            "total_energy": 10.0,
            "bank_currents": {1: 0.0, 2: 0.5},
        }
        outlets = {1: OutletData(number=1, name="Outlet1", state="on")}
        srccfg = {"preferred_source": "B"}
        data = build_pdu_data(devsta, outlets, srccfg)

        assert data.ats_current_source == 2  # B = 2
        assert data.ats_preferred_source == 2
        assert data.input_voltage == 119.5  # From source B

    def test_no_banks_creates_from_sources(self):
        devsta = {
            "active_source": "A",
            "source_a_voltage": 120.0,
            "source_b_voltage": 119.0,
            "source_a_frequency": 60.0,
            "source_b_frequency": 60.0,
            "source_a_status": "normal",
            "source_b_status": "normal",
            "total_load": None,
            "total_power": None,
            "total_energy": None,
            "bank_currents": {},
        }
        outlets = {}
        srccfg = {"preferred_source": "A"}
        data = build_pdu_data(devsta, outlets, srccfg)

        assert len(data.banks) == 2
        assert data.banks[1].voltage == 120.0
        assert data.banks[2].voltage == 119.0

    def test_empty_inputs(self):
        devsta = parse_devsta_show("")
        outlets = parse_oltsta_show("")
        srccfg = parse_srccfg_show("")
        data = build_pdu_data(devsta, outlets, srccfg)

        assert data.outlet_count == 0
        assert data.input_voltage is None
        assert data.ats_current_source is None

    def test_redundancy_lost(self):
        devsta = {
            "active_source": "A",
            "source_a_voltage": 120.0,
            "source_b_voltage": 0.0,
            "source_a_frequency": 60.0,
            "source_b_frequency": 0.0,
            "source_a_status": "normal",
            "source_b_status": "undervoltage",
            "total_load": None,
            "total_power": None,
            "total_energy": None,
            "bank_currents": {},
        }
        data = build_pdu_data(devsta, {}, {"preferred_source": "A"})
        assert data.redundancy_ok is False

    def test_bank_power_calculated(self):
        devsta = parse_devsta_show(DEVSTA_SHOW_OUTPUT)
        outlets = parse_oltsta_show(OLTSTA_SHOW_TABLE)
        srccfg = parse_srccfg_show(SRCCFG_SHOW_OUTPUT)
        data = build_pdu_data(devsta, outlets, srccfg)

        # Bank 1: current=0.2A, voltage=119.7V -> power=23.9W
        assert data.banks[1].current == 0.2
        assert data.banks[1].voltage == 119.7
        assert data.banks[1].power == pytest.approx(23.9, abs=0.1)


# ---------------------------------------------------------------------------
# Captured CLI output fixtures for new parsers
# ---------------------------------------------------------------------------

OLTCFG_SHOW_TABLE = """\
CyberPower >

Index  Name        On Delay(s)  Off Delay(s)  Reboot Duration(s)
1      Outlet1     0            0             10
2      Outlet2     5            0             10
3      Outlet3     10           5             15
4      Outlet4     0            0             10
5      Outlet5     0            0             10
6      Outlet6     0            0             10
7      Outlet7     0            0             10
8      Outlet8     0            0             10
9      Outlet9     0            0             10
10     Outlet10    0            0             10

CyberPower > """

OLTCFG_SHOW_KV = """\
Outlet 1 Name : Outlet1
Outlet 1 On Delay : 0 s
Outlet 1 Off Delay : 0 s
Outlet 1 Reboot Duration : 10 s
Outlet 2 Name : Outlet2
Outlet 2 On Delay : 5 s
Outlet 2 Off Delay : 0 s
Outlet 2 Reboot Duration : 10 s
"""

DEVCFG_SHOW_OUTPUT = """\
CyberPower >

Overload Threshold : 80 %
Near Overload Threshold : 70 %
Low Load Threshold : 20 %

CyberPower > """

BANKCFG_SHOW_TABLE = """\
CyberPower >

Bank  Overload(%)  Near Overload(%)  Low Load(%)
1     80           70                20
2     85           75                25

CyberPower > """

BANKCFG_SHOW_KV = """\
Bank 1 Overload Threshold : 80 %
Bank 1 Near Overload Threshold : 70 %
Bank 1 Low Load Threshold : 20 %
Bank 2 Overload Threshold : 85 %
Bank 2 Near Overload Threshold : 75 %
Bank 2 Low Load Threshold : 25 %
"""

NETCFG_SHOW_OUTPUT = """\
CyberPower >

IP Address     : 192.168.20.177
Subnet Mask    : 255.255.255.0
Gateway        : 192.168.20.1
DHCP           : Enabled
MAC Address    : 00:0C:15:AA:BB:CC
IPv6           : Disabled

CyberPower > """

EVENTLOG_SHOW_TABLE = """\
CyberPower >

Index  Date        Time      Event
1      01/15/2026  14:23:05  Source A Power Restored
2      01/15/2026  14:22:30  Source A Power Lost
3      01/14/2026  09:00:00  System Started
4      01/13/2026  16:45:22  Outlet 5 Turned Off
5      01/13/2026  10:00:00  Overload Warning Bank 1

CyberPower > """

EVENTLOG_SHOW_COMPACT = """\
01/15/2026 14:23:05 Source A Power Restored
01/15/2026 14:22:30 Source A Power Lost
01/14/2026 09:00:00 System Started
"""


# ---------------------------------------------------------------------------
# parse_oltcfg_show tests
# ---------------------------------------------------------------------------

class TestParseOltcfgShow:
    def test_table_format(self):
        result = parse_oltcfg_show(OLTCFG_SHOW_TABLE)
        assert len(result) == 10
        assert result[1]["name"] == "Outlet1"
        assert result[1]["on_delay"] == 0
        assert result[1]["off_delay"] == 0
        assert result[1]["reboot_duration"] == 10
        assert result[2]["on_delay"] == 5
        assert result[3]["on_delay"] == 10
        assert result[3]["off_delay"] == 5
        assert result[3]["reboot_duration"] == 15

    def test_kv_format(self):
        result = parse_oltcfg_show(OLTCFG_SHOW_KV)
        assert len(result) == 2
        assert result[1]["name"] == "Outlet1"
        assert result[1]["on_delay"] == 0
        assert result[1]["reboot_duration"] == 10
        assert result[2]["on_delay"] == 5

    def test_empty_input(self):
        result = parse_oltcfg_show("")
        assert result == {}

    def test_single_outlet(self):
        text = "1      Server1     30           10            20\n"
        result = parse_oltcfg_show(text)
        assert len(result) == 1
        assert result[1]["name"] == "Server1"
        assert result[1]["on_delay"] == 30
        assert result[1]["off_delay"] == 10
        assert result[1]["reboot_duration"] == 20


# ---------------------------------------------------------------------------
# parse_devcfg_show tests
# ---------------------------------------------------------------------------

class TestParseDevcfgShow:
    def test_basic_parse(self):
        result = parse_devcfg_show(DEVCFG_SHOW_OUTPUT)
        assert result["overload_threshold"] == 80.0
        assert result["near_overload_threshold"] == 70.0
        assert result["low_load_threshold"] == 20.0

    def test_empty_input(self):
        result = parse_devcfg_show("")
        assert result["overload_threshold"] is None
        assert result["near_overload_threshold"] is None
        assert result["low_load_threshold"] is None

    def test_partial_output(self):
        text = "Overload Threshold : 90 %\n"
        result = parse_devcfg_show(text)
        assert result["overload_threshold"] == 90.0
        assert result["near_overload_threshold"] is None

    def test_decimal_values(self):
        text = "Overload Threshold : 80.5 %\nNear Overload Threshold : 70.2 %\n"
        result = parse_devcfg_show(text)
        assert result["overload_threshold"] == 80.5
        assert result["near_overload_threshold"] == 70.2


# ---------------------------------------------------------------------------
# parse_bankcfg_show tests
# ---------------------------------------------------------------------------

class TestParseBankcfgShow:
    def test_table_format(self):
        result = parse_bankcfg_show(BANKCFG_SHOW_TABLE)
        assert len(result) == 2
        assert result[1]["overload"] == 80.0
        assert result[1]["near_overload"] == 70.0
        assert result[1]["low_load"] == 20.0
        assert result[2]["overload"] == 85.0
        assert result[2]["near_overload"] == 75.0
        assert result[2]["low_load"] == 25.0

    def test_kv_format(self):
        result = parse_bankcfg_show(BANKCFG_SHOW_KV)
        assert len(result) == 2
        assert result[1]["overload"] == 80.0
        assert result[1]["near_overload"] == 70.0
        assert result[1]["low_load"] == 20.0
        assert result[2]["overload"] == 85.0

    def test_empty_input(self):
        result = parse_bankcfg_show("")
        assert result == {}

    def test_single_bank(self):
        text = "1     90           80                10\n"
        result = parse_bankcfg_show(text)
        assert len(result) == 1
        assert result[1]["overload"] == 90.0
        assert result[1]["near_overload"] == 80.0
        assert result[1]["low_load"] == 10.0


# ---------------------------------------------------------------------------
# parse_netcfg_show tests
# ---------------------------------------------------------------------------

class TestParseNetcfgShow:
    def test_basic_parse(self):
        result = parse_netcfg_show(NETCFG_SHOW_OUTPUT)
        assert result["ip"] == "192.168.20.177"
        assert result["subnet"] == "255.255.255.0"
        assert result["gateway"] == "192.168.20.1"
        assert result["dhcp_enabled"] is True
        assert result["mac_address"] == "00:0C:15:AA:BB:CC"

    def test_empty_input(self):
        result = parse_netcfg_show("")
        assert result["ip"] == ""
        assert result["subnet"] == ""
        assert result["dhcp_enabled"] is False

    def test_dhcp_disabled(self):
        text = "DHCP : Disabled\n"
        result = parse_netcfg_show(text)
        assert result["dhcp_enabled"] is False

    def test_alternative_keys(self):
        text = "IP : 10.0.0.5\nSubnet : 255.255.0.0\nDefault Gateway : 10.0.0.1\nMAC : AA:BB:CC:DD:EE:FF\n"
        result = parse_netcfg_show(text)
        assert result["ip"] == "10.0.0.5"
        assert result["subnet"] == "255.255.0.0"
        assert result["gateway"] == "10.0.0.1"
        assert result["mac_address"] == "AA:BB:CC:DD:EE:FF"

    def test_dhcp_on(self):
        text = "DHCP : On\n"
        result = parse_netcfg_show(text)
        assert result["dhcp_enabled"] is True


# ---------------------------------------------------------------------------
# parse_eventlog_show tests
# ---------------------------------------------------------------------------

class TestParseEventlogShow:
    def test_indexed_table(self):
        events = parse_eventlog_show(EVENTLOG_SHOW_TABLE)
        assert len(events) == 5
        assert events[0]["timestamp"] == "01/15/2026 14:23:05"
        assert events[0]["event_type"] == "power_restore"
        assert events[0]["description"] == "Source A Power Restored"
        assert events[1]["event_type"] == "power_loss"
        assert events[2]["event_type"] == "system_start"
        assert events[3]["event_type"] == "outlet_change"
        assert events[4]["event_type"] == "overload"

    def test_compact_format(self):
        events = parse_eventlog_show(EVENTLOG_SHOW_COMPACT)
        assert len(events) == 3
        assert events[0]["description"] == "Source A Power Restored"
        assert events[0]["event_type"] == "power_restore"
        assert events[1]["event_type"] == "power_loss"
        assert events[2]["event_type"] == "system_start"

    def test_empty_input(self):
        events = parse_eventlog_show("")
        assert events == []

    def test_header_only(self):
        text = "Index  Date        Time      Event\n---    ----        ----      -----\n"
        events = parse_eventlog_show(text)
        assert events == []

    def test_transfer_event(self):
        text = "1      02/20/2026  10:00:00  ATS Transfer A to B\n"
        events = parse_eventlog_show(text)
        assert len(events) == 1
        assert events[0]["event_type"] == "ats_transfer"

    def test_auth_event(self):
        text = "01/01/2026 12:00:00 Login Failed from 192.168.1.100\n"
        events = parse_eventlog_show(text)
        assert len(events) == 1
        assert events[0]["event_type"] == "auth"

    def test_config_event(self):
        text = "01/01/2026 12:00:00 Configuration Changed\n"
        events = parse_eventlog_show(text)
        assert len(events) == 1
        assert events[0]["event_type"] == "config_change"


# ---------------------------------------------------------------------------
# Captured CLI output fixtures for notification/config parsers
# ---------------------------------------------------------------------------

TRAPCFG_SHOW_TABLE = """\
CyberPower >

Index  IP Address       Community   Severity   Status
1      192.168.1.100    public      all        Enabled
2      0.0.0.0          public      all        Disabled

CyberPower > """

TRAPCFG_SHOW_KV = """\
Trap Receiver 1 IP : 192.168.1.100
Trap Receiver 1 Community : public
Trap Receiver 1 Severity : all
Trap Receiver 1 Status : Enabled
Trap Receiver 2 IP : 0.0.0.0
Trap Receiver 2 Community : private
Trap Receiver 2 Severity : critical
Trap Receiver 2 Status : Disabled
"""

SMTPCFG_SHOW_OUTPUT = """\
CyberPower >

SMTP Server    : mail.example.com
SMTP Port      : 25
From Address   : pdu@example.com
Auth Username  : pduuser

CyberPower > """

EMAILCFG_SHOW_TABLE = """\
CyberPower >

Index  To Address           Status
1      admin@example.com    Enabled
2                           Disabled

CyberPower > """

EMAILCFG_SHOW_KV = """\
Email Recipient 1 To : admin@example.com
Email Recipient 1 Status : Enabled
Email Recipient 2 To : ops@example.com
Email Recipient 2 Status : Disabled
"""

SYSLOGCFG_SHOW_TABLE = """\
CyberPower >

Index  IP Address       Facility   Severity   Status
1      192.168.1.50     local0     all        Enabled
2      0.0.0.0          local0     all        Disabled

CyberPower > """

SYSLOGCFG_SHOW_KV = """\
Syslog Server 1 IP : 192.168.1.50
Syslog Server 1 Facility : local0
Syslog Server 1 Severity : all
Syslog Server 1 Status : Enabled
Syslog Server 2 IP : 10.0.0.200
Syslog Server 2 Facility : local7
Syslog Server 2 Severity : critical
Syslog Server 2 Status : Disabled
"""

USERCFG_SHOW_OUTPUT = """\
CyberPower >

Admin Username : admin
Admin Access   : Full
Viewer Username : viewer
Viewer Access   : Read-only

CyberPower > """

ENERGYWISE_SHOW_OUTPUT = """\
CyberPower >

Domain         : cisco.com
Port           : 43440
Shared Secret  : ********
Status         : Disabled

CyberPower > """

DEVCFG_SHOW_COLDSTART = """\
CyberPower >

Overload Threshold : 80 %
Near Overload Threshold : 70 %
Low Load Threshold : 20 %
Coldstart Delay : 0 s
Coldstart State : allon

CyberPower > """


# ---------------------------------------------------------------------------
# parse_trapcfg_show tests
# ---------------------------------------------------------------------------

class TestParseTrapcfgShow:
    def test_table_format(self):
        result = parse_trapcfg_show(TRAPCFG_SHOW_TABLE)
        assert len(result) == 2
        assert result[0]["index"] == 1
        assert result[0]["ip"] == "192.168.1.100"
        assert result[0]["community"] == "public"
        assert result[0]["severity"] == "all"
        assert result[0]["enabled"] is True
        assert result[1]["index"] == 2
        assert result[1]["ip"] == "0.0.0.0"
        assert result[1]["enabled"] is False

    def test_kv_format(self):
        result = parse_trapcfg_show(TRAPCFG_SHOW_KV)
        assert len(result) == 2
        assert result[0]["ip"] == "192.168.1.100"
        assert result[0]["community"] == "public"
        assert result[0]["severity"] == "all"
        assert result[0]["enabled"] is True
        assert result[1]["ip"] == "0.0.0.0"
        assert result[1]["community"] == "private"
        assert result[1]["severity"] == "critical"
        assert result[1]["enabled"] is False

    def test_empty_input(self):
        result = parse_trapcfg_show("")
        assert result == []

    def test_single_receiver(self):
        text = "1      10.0.0.5         mycomm      warning    Enabled\n"
        result = parse_trapcfg_show(text)
        assert len(result) == 1
        assert result[0]["ip"] == "10.0.0.5"
        assert result[0]["community"] == "mycomm"
        assert result[0]["severity"] == "warning"
        assert result[0]["enabled"] is True

    def test_header_only(self):
        text = "Index  IP Address       Community   Severity   Status\n"
        result = parse_trapcfg_show(text)
        assert result == []


# ---------------------------------------------------------------------------
# parse_smtpcfg_show tests
# ---------------------------------------------------------------------------

class TestParseSmtpcfgShow:
    def test_basic_parse(self):
        result = parse_smtpcfg_show(SMTPCFG_SHOW_OUTPUT)
        assert result["server"] == "mail.example.com"
        assert result["port"] == 25
        assert result["from_addr"] == "pdu@example.com"
        assert result["auth_user"] == "pduuser"

    def test_empty_input(self):
        result = parse_smtpcfg_show("")
        assert result["server"] == ""
        assert result["port"] == 25
        assert result["from_addr"] == ""
        assert result["auth_user"] == ""

    def test_custom_port(self):
        text = "SMTP Server : smtp.corp.local\nSMTP Port : 587\n"
        result = parse_smtpcfg_show(text)
        assert result["server"] == "smtp.corp.local"
        assert result["port"] == 587

    def test_alternative_keys(self):
        text = "Server : relay.example.com\nPort : 465\nFrom : noreply@pdu.local\nUsername : admin\n"
        result = parse_smtpcfg_show(text)
        assert result["server"] == "relay.example.com"
        assert result["port"] == 465
        assert result["from_addr"] == "noreply@pdu.local"
        assert result["auth_user"] == "admin"

    def test_no_auth(self):
        text = "SMTP Server : mail.example.com\nSMTP Port : 25\nFrom Address : pdu@example.com\n"
        result = parse_smtpcfg_show(text)
        assert result["server"] == "mail.example.com"
        assert result["auth_user"] == ""


# ---------------------------------------------------------------------------
# parse_emailcfg_show tests
# ---------------------------------------------------------------------------

class TestParseEmailcfgShow:
    def test_table_format(self):
        result = parse_emailcfg_show(EMAILCFG_SHOW_TABLE)
        assert len(result) == 2
        assert result[0]["index"] == 1
        assert result[0]["to"] == "admin@example.com"
        assert result[0]["enabled"] is True
        assert result[1]["index"] == 2
        assert result[1]["to"] == ""
        assert result[1]["enabled"] is False

    def test_kv_format(self):
        result = parse_emailcfg_show(EMAILCFG_SHOW_KV)
        assert len(result) == 2
        assert result[0]["to"] == "admin@example.com"
        assert result[0]["enabled"] is True
        assert result[1]["to"] == "ops@example.com"
        assert result[1]["enabled"] is False

    def test_empty_input(self):
        result = parse_emailcfg_show("")
        assert result == []

    def test_single_recipient(self):
        text = "1      ops@company.com      Enabled\n"
        result = parse_emailcfg_show(text)
        assert len(result) == 1
        assert result[0]["to"] == "ops@company.com"
        assert result[0]["enabled"] is True

    def test_header_only(self):
        text = "Index  To Address           Status\n"
        result = parse_emailcfg_show(text)
        assert result == []


# ---------------------------------------------------------------------------
# parse_syslogcfg_show tests
# ---------------------------------------------------------------------------

class TestParseSyslogcfgShow:
    def test_table_format(self):
        result = parse_syslogcfg_show(SYSLOGCFG_SHOW_TABLE)
        assert len(result) == 2
        assert result[0]["index"] == 1
        assert result[0]["ip"] == "192.168.1.50"
        assert result[0]["facility"] == "local0"
        assert result[0]["severity"] == "all"
        assert result[0]["enabled"] is True
        assert result[1]["index"] == 2
        assert result[1]["ip"] == "0.0.0.0"
        assert result[1]["enabled"] is False

    def test_kv_format(self):
        result = parse_syslogcfg_show(SYSLOGCFG_SHOW_KV)
        assert len(result) == 2
        assert result[0]["ip"] == "192.168.1.50"
        assert result[0]["facility"] == "local0"
        assert result[0]["severity"] == "all"
        assert result[0]["enabled"] is True
        assert result[1]["ip"] == "10.0.0.200"
        assert result[1]["facility"] == "local7"
        assert result[1]["severity"] == "critical"
        assert result[1]["enabled"] is False

    def test_empty_input(self):
        result = parse_syslogcfg_show("")
        assert result == []

    def test_single_server(self):
        text = "1      10.10.10.10      local3     warning    Enabled\n"
        result = parse_syslogcfg_show(text)
        assert len(result) == 1
        assert result[0]["ip"] == "10.10.10.10"
        assert result[0]["facility"] == "local3"
        assert result[0]["severity"] == "warning"
        assert result[0]["enabled"] is True

    def test_header_only(self):
        text = "Index  IP Address       Facility   Severity   Status\n"
        result = parse_syslogcfg_show(text)
        assert result == []


# ---------------------------------------------------------------------------
# parse_usercfg_show tests
# ---------------------------------------------------------------------------

class TestParseUsercfgShow:
    def test_basic_parse(self):
        result = parse_usercfg_show(USERCFG_SHOW_OUTPUT)
        assert "admin" in result
        assert result["admin"]["username"] == "admin"
        assert result["admin"]["access"] == "Full"
        assert "viewer" in result
        assert result["viewer"]["username"] == "viewer"
        assert result["viewer"]["access"] == "Read-only"

    def test_empty_input(self):
        result = parse_usercfg_show("")
        assert result == {}

    def test_admin_only(self):
        text = "Admin Username : root\nAdmin Access : Full\n"
        result = parse_usercfg_show(text)
        assert "admin" in result
        assert result["admin"]["username"] == "root"
        assert result["admin"]["access"] == "Full"
        assert "viewer" not in result

    def test_viewer_only(self):
        text = "Viewer Username : guest\nViewer Access : Read-only\n"
        result = parse_usercfg_show(text)
        assert "viewer" in result
        assert result["viewer"]["username"] == "guest"
        assert result["viewer"]["access"] == "Read-only"
        assert "admin" not in result


# ---------------------------------------------------------------------------
# parse_energywise_show tests
# ---------------------------------------------------------------------------

class TestParseEnergywiseShow:
    def test_basic_parse(self):
        result = parse_energywise_show(ENERGYWISE_SHOW_OUTPUT)
        assert result["domain"] == "cisco.com"
        assert result["port"] == 43440
        assert result["secret"] == "********"
        assert result["enabled"] is False

    def test_empty_input(self):
        result = parse_energywise_show("")
        assert result["domain"] == ""
        assert result["port"] == 43440
        assert result["secret"] == ""
        assert result["enabled"] is False

    def test_enabled_status(self):
        text = "Domain : mynet.local\nPort : 9999\nShared Secret : s3cret\nStatus : Enabled\n"
        result = parse_energywise_show(text)
        assert result["domain"] == "mynet.local"
        assert result["port"] == 9999
        assert result["secret"] == "s3cret"
        assert result["enabled"] is True

    def test_default_port(self):
        text = "Domain : test.com\nStatus : Disabled\n"
        result = parse_energywise_show(text)
        assert result["port"] == 43440


# ---------------------------------------------------------------------------
# parse_devcfg_show with coldstart fields tests
# ---------------------------------------------------------------------------

class TestParseDevcfgShowColdstart:
    def test_coldstart_fields(self):
        result = parse_devcfg_show(DEVCFG_SHOW_COLDSTART)
        assert result["overload_threshold"] == 80.0
        assert result["near_overload_threshold"] == 70.0
        assert result["low_load_threshold"] == 20.0
        assert result["coldstart_delay"] == 0
        assert result["coldstart_state"] == "allon"

    def test_coldstart_with_delay(self):
        text = """\
Overload Threshold : 80 %
Near Overload Threshold : 70 %
Low Load Threshold : 20 %
Coldstart Delay : 30 s
Coldstart State : prevstate
"""
        result = parse_devcfg_show(text)
        assert result["coldstart_delay"] == 30
        assert result["coldstart_state"] == "prevstate"

    def test_no_coldstart_fields(self):
        """Original devcfg without coldstart should not have those keys."""
        result = parse_devcfg_show(DEVCFG_SHOW_OUTPUT)
        assert result["overload_threshold"] == 80.0
        assert "coldstart_delay" not in result
        assert "coldstart_state" not in result

    def test_coldstart_delay_only(self):
        text = "Coldstart Delay : 15 s\n"
        result = parse_devcfg_show(text)
        assert result["coldstart_delay"] == 15
        assert "coldstart_state" not in result

    def test_coldstart_state_only(self):
        text = "Coldstart State : allon\n"
        result = parse_devcfg_show(text)
        assert result["coldstart_state"] == "allon"
        assert "coldstart_delay" not in result


# ---------------------------------------------------------------------------
# build_pdu_data with new fields tests
# ---------------------------------------------------------------------------

class TestBuildPduDataNewFields:
    def test_srccfg_fields_propagated(self):
        """Verify voltage_sensitivity, transfer_voltage, limits are in PDUData."""
        devsta = parse_devsta_show(DEVSTA_SHOW_OUTPUT)
        outlets = parse_oltsta_show(OLTSTA_SHOW_TABLE)
        srccfg = parse_srccfg_show(SRCCFG_SHOW_OUTPUT)
        identity = parse_sys_show(SYS_SHOW_OUTPUT)

        data = build_pdu_data(devsta, outlets, srccfg, identity)

        assert data.voltage_sensitivity == "Normal"
        assert data.transfer_voltage == 88.0
        assert data.voltage_upper_limit == 148.0
        assert data.voltage_lower_limit == 88.0

    def test_total_fields_propagated(self):
        """Verify total_load, total_power, total_energy from devsta."""
        devsta = parse_devsta_show(DEVSTA_SHOW_OUTPUT)
        outlets = parse_oltsta_show(OLTSTA_SHOW_TABLE)
        srccfg = parse_srccfg_show(SRCCFG_SHOW_OUTPUT)

        data = build_pdu_data(devsta, outlets, srccfg)

        assert data.total_load == 0.3
        assert data.total_power == 36.0
        assert data.total_energy == 123.4

    def test_coldstart_from_devcfg(self):
        """Verify coldstart_delay and coldstart_state from devcfg param."""
        devsta = parse_devsta_show(DEVSTA_SHOW_OUTPUT)
        outlets = parse_oltsta_show(OLTSTA_SHOW_TABLE)
        srccfg = parse_srccfg_show(SRCCFG_SHOW_OUTPUT)
        devcfg = parse_devcfg_show(DEVCFG_SHOW_COLDSTART)

        data = build_pdu_data(devsta, outlets, srccfg, devcfg=devcfg)

        assert data.coldstart_delay == 0
        assert data.coldstart_state == "allon"

    def test_coldstart_prevstate(self):
        """Verify coldstart_state='prevstate' passes through."""
        devsta = parse_devsta_show("")
        outlets = parse_oltsta_show("")
        srccfg = parse_srccfg_show("")
        devcfg = {"coldstart_delay": 60, "coldstart_state": "prevstate"}

        data = build_pdu_data(devsta, outlets, srccfg, devcfg=devcfg)

        assert data.coldstart_delay == 60
        assert data.coldstart_state == "prevstate"

    def test_no_devcfg_defaults(self):
        """Without devcfg param, coldstart fields should be None/empty."""
        devsta = parse_devsta_show(DEVSTA_SHOW_OUTPUT)
        outlets = parse_oltsta_show(OLTSTA_SHOW_TABLE)
        srccfg = parse_srccfg_show(SRCCFG_SHOW_OUTPUT)

        data = build_pdu_data(devsta, outlets, srccfg)

        assert data.coldstart_delay is None
        assert data.coldstart_state == ""

    def test_empty_srccfg_defaults(self):
        """Empty srccfg should produce default/None values for ATS config."""
        devsta = parse_devsta_show("")
        outlets = {}
        srccfg = parse_srccfg_show("")

        data = build_pdu_data(devsta, outlets, srccfg)

        assert data.voltage_sensitivity == ""
        assert data.transfer_voltage is None
        assert data.voltage_upper_limit is None
        assert data.voltage_lower_limit is None
        assert data.total_load is None
        assert data.total_power is None
        assert data.total_energy is None

    def test_all_new_fields_together(self):
        """End-to-end: all new fields present in a single PDUData build."""
        devsta = parse_devsta_show(DEVSTA_SHOW_OUTPUT)
        outlets = parse_oltsta_show(OLTSTA_SHOW_TABLE)
        srccfg = parse_srccfg_show(SRCCFG_SHOW_OUTPUT)
        identity = parse_sys_show(SYS_SHOW_OUTPUT)
        devcfg = parse_devcfg_show(DEVCFG_SHOW_COLDSTART)

        data = build_pdu_data(devsta, outlets, srccfg, identity, devcfg=devcfg)

        # srccfg fields
        assert data.voltage_sensitivity == "Normal"
        assert data.transfer_voltage == 88.0
        assert data.voltage_upper_limit == 148.0
        assert data.voltage_lower_limit == 88.0
        # devsta totals
        assert data.total_load == 0.3
        assert data.total_power == 36.0
        assert data.total_energy == 123.4
        # devcfg coldstart
        assert data.coldstart_delay == 0
        assert data.coldstart_state == "allon"
        # existing fields still work
        assert data.device_name == "PDU44001"
        assert data.outlet_count == 10
        assert data.identity.serial == "NLKQY7000136"
