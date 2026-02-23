# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Pure-function parsers for CyberPower PDU serial CLI output.

Each parser takes raw CLI text and returns structured data.
Fully testable with no I/O dependencies.

CLI commands and their output formats (PDU44001):
  sys show       -> Name, Location, Model, Firmware, MAC, Serial
  devsta show    -> ATS source, voltages, frequencies, load, power, energy
  oltsta show    -> Outlet number/name/status table
  srccfg show    -> Preferred source, voltage/frequency config
  oltcfg show    -> Outlet config (names, delays, reboot duration)
  devcfg show    -> Device-level load thresholds
  bankcfg show   -> Per-bank load thresholds
  netcfg show    -> Network configuration (IP, subnet, gateway, DHCP)
  eventlog show  -> PDU event history
"""

import logging
import re

from .pdu_model import (
    BankData,
    DeviceIdentity,
    OutletData,
    PDUData,
    SourceData,
)

logger = logging.getLogger(__name__)


def _strip_cli(text: str) -> list[str]:
    """Strip ANSI escapes, blank lines, and the prompt from CLI output."""
    # Remove ANSI escape sequences
    ansi_re = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
    lines = []
    for line in text.splitlines():
        line = ansi_re.sub('', line).rstrip()
        # Skip empty lines and prompt lines
        if not line or line.strip().startswith('CyberPower >'):
            continue
        lines.append(line)
    return lines


def _parse_kv(text: str) -> dict[str, str]:
    """Parse 'Key : Value' lines into a dict."""
    result = {}
    for line in _strip_cli(text):
        # Match 'Key  : Value' or 'Key: Value'
        m = re.match(r'^(.+?)\s*:\s*(.+)$', line)
        if m:
            key = m.group(1).strip()
            value = m.group(2).strip()
            result[key] = value
    return result


def parse_sys_show(text: str) -> DeviceIdentity:
    """Parse 'sys show' output into DeviceIdentity.

    Example output:
        Name           : PDU44001
        Location       : Server Room
        Model Name     : PDU44001
        Firmware Version : 1.3.4
        MAC Address    : 00:0C:15:XX:XX:XX
        Serial Number  : NLKQY7000136
        Hardware Version : 3
    """
    kv = _parse_kv(text)

    hw_rev = 0
    hw_str = kv.get("Hardware Version", "")
    if hw_str:
        try:
            hw_rev = int(hw_str)
        except ValueError:
            pass

    return DeviceIdentity(
        name=kv.get("Name", ""),
        sys_location=kv.get("Location", ""),
        model=kv.get("Model Name", kv.get("Model", "")),
        firmware_main=kv.get("Firmware Version", ""),
        mac_address=kv.get("MAC Address", ""),
        serial=kv.get("Serial Number", ""),
        hardware_rev=hw_rev,
        sys_name=kv.get("Name", ""),
    )


def parse_devsta_show(text: str) -> dict:
    """Parse 'devsta show' output into a device status dict.

    Example output:
        Active Source   : A
        Source Voltage (A/B) : 119.7 /119.7 V
        Source Frequency (A/B) : 60.0 /60.0 Hz
        Source Status (A/B) : Normal /Normal
        Total Load     : 0.3 A
        Total Power    : 36 W
        Total Energy   : 123.4 kWh
        Bank 1 Current : 0.2 A
        Bank 2 Current : 0.1 A

    Returns dict with keys:
        active_source, source_a_voltage, source_b_voltage,
        source_a_frequency, source_b_frequency,
        source_a_status, source_b_status,
        total_load, total_power, total_energy,
        bank_currents: {1: 0.2, 2: 0.1}
    """
    kv = _parse_kv(text)
    result = {
        "active_source": None,
        "source_a_voltage": None,
        "source_b_voltage": None,
        "source_a_frequency": None,
        "source_b_frequency": None,
        "source_a_status": "unknown",
        "source_b_status": "unknown",
        "total_load": None,
        "total_power": None,
        "total_energy": None,
        "bank_currents": {},
    }

    # Active Source
    active = kv.get("Active Source", "").strip().upper()
    if active in ("A", "B"):
        result["active_source"] = active

    # Source Voltage (A/B) : 119.7 /119.7 V
    volt_str = kv.get("Source Voltage (A/B)", "")
    if volt_str:
        m = re.match(r'([\d.]+)\s*/\s*([\d.]+)', volt_str)
        if m:
            result["source_a_voltage"] = float(m.group(1))
            result["source_b_voltage"] = float(m.group(2))

    # Source Frequency (A/B) : 60.0 /60.0 Hz
    freq_str = kv.get("Source Frequency (A/B)", "")
    if freq_str:
        m = re.match(r'([\d.]+)\s*/\s*([\d.]+)', freq_str)
        if m:
            result["source_a_frequency"] = float(m.group(1))
            result["source_b_frequency"] = float(m.group(2))

    # Source Status (A/B) : Normal /Normal
    stat_str = kv.get("Source Status (A/B)", "")
    if stat_str:
        m = re.match(r'(\w+)\s*/\s*(\w+)', stat_str)
        if m:
            result["source_a_status"] = m.group(1).strip().lower()
            result["source_b_status"] = m.group(2).strip().lower()

    # Total Load : 0.3 A
    load_str = kv.get("Total Load", "")
    if load_str:
        m = re.match(r'([\d.]+)', load_str)
        if m:
            result["total_load"] = float(m.group(1))

    # Total Power : 36 W
    power_str = kv.get("Total Power", "")
    if power_str:
        m = re.match(r'([\d.]+)', power_str)
        if m:
            result["total_power"] = float(m.group(1))

    # Total Energy : 123.4 kWh
    energy_str = kv.get("Total Energy", "")
    if energy_str:
        m = re.match(r'([\d.]+)', energy_str)
        if m:
            result["total_energy"] = float(m.group(1))

    # Bank N Current : 0.2 A
    for key, val in kv.items():
        m = re.match(r'Bank\s+(\d+)\s+Current', key)
        if m:
            bank_num = int(m.group(1))
            val_m = re.match(r'([\d.]+)', val)
            if val_m:
                result["bank_currents"][bank_num] = float(val_m.group(1))

    return result


def parse_oltsta_show(text: str) -> dict[int, OutletData]:
    """Parse 'oltsta show' output into outlet data.

    Example output (table format):
        Index  Name        Status  Current(A)  Power(W)
        1      Outlet1     On      0.0         0
        2      Outlet2     On      0.1         12
        ...
        10     Outlet10    Off     0.0         0

    Also handles 'Key : Value' format:
        Outlet 1 Name  : Outlet1
        Outlet 1 Status : On
    """
    outlets: dict[int, OutletData] = {}

    lines = _strip_cli(text)

    # Try table format first: look for rows starting with a number
    table_pattern = re.compile(
        r'^\s*(\d+)\s+'          # index
        r'(\S+(?:\s+\S+)*?)\s+'  # name (possibly multi-word)
        r'(On|Off)\s*'           # status
        r'(?:([\d.]+)\s*)?'      # optional current
        r'(?:([\d.]+)\s*)?'      # optional power
    )

    for line in lines:
        m = table_pattern.match(line)
        if m:
            idx = int(m.group(1))
            name = m.group(2).strip()
            state = m.group(3).lower()
            current = float(m.group(4)) if m.group(4) else None
            power = float(m.group(5)) if m.group(5) else None
            outlets[idx] = OutletData(
                number=idx,
                name=name,
                state=state,
                current=current,
                power=power,
            )

    if outlets:
        return outlets

    # Fallback: Key-Value format
    kv = _parse_kv(text)
    outlet_data: dict[int, dict] = {}

    for key, val in kv.items():
        # Outlet N Name : ...
        m = re.match(r'Outlet\s+(\d+)\s+(\w+)', key)
        if m:
            idx = int(m.group(1))
            field = m.group(2).lower()
            if idx not in outlet_data:
                outlet_data[idx] = {"number": idx}
            if field == "name":
                outlet_data[idx]["name"] = val
            elif field == "status":
                outlet_data[idx]["state"] = val.lower()
            elif field == "current":
                vm = re.match(r'([\d.]+)', val)
                if vm:
                    outlet_data[idx]["current"] = float(vm.group(1))
            elif field == "power":
                vm = re.match(r'([\d.]+)', val)
                if vm:
                    outlet_data[idx]["power"] = float(vm.group(1))

    for idx, data in outlet_data.items():
        outlets[idx] = OutletData(
            number=data.get("number", idx),
            name=data.get("name", f"Outlet {idx}"),
            state=data.get("state", "unknown"),
            current=data.get("current"),
            power=data.get("power"),
        )

    return outlets


def parse_srccfg_show(text: str) -> dict:
    """Parse 'srccfg show' output into source config.

    Example output:
        Preferred Source : A
        Voltage Sensitivity : Normal
        Transfer Voltage : 88 V
        Voltage Upper Limit : 148 V
        Voltage Lower Limit : 88 V
        Frequency Range : 47 - 63 Hz
    """
    kv = _parse_kv(text)
    result = {
        "preferred_source": None,
        "voltage_sensitivity": "",
        "transfer_voltage": None,
        "voltage_upper_limit": None,
        "voltage_lower_limit": None,
    }

    pref = kv.get("Preferred Source", "").strip().upper()
    if pref in ("A", "B"):
        result["preferred_source"] = pref

    result["voltage_sensitivity"] = kv.get("Voltage Sensitivity", "")

    for key, target in [
        ("Transfer Voltage", "transfer_voltage"),
        ("Voltage Upper Limit", "voltage_upper_limit"),
        ("Voltage Lower Limit", "voltage_lower_limit"),
    ]:
        val = kv.get(key, "")
        m = re.match(r'([\d.]+)', val)
        if m:
            result[target] = float(m.group(1))

    return result


def parse_oltcfg_show(text: str) -> dict[int, dict]:
    """Parse 'oltcfg show' output into outlet configuration.

    Example table format:
        Index  Name        On Delay(s)  Off Delay(s)  Reboot Duration(s)
        1      Outlet1     0            0             10
        2      Outlet2     0            0             10

    Example KV format:
        Outlet 1 Name : Outlet1
        Outlet 1 On Delay : 0 s
        Outlet 1 Off Delay : 0 s
        Outlet 1 Reboot Duration : 10 s

    Returns: {outlet_num: {name, on_delay, off_delay, reboot_duration}}
    """
    lines = _strip_cli(text)
    result: dict[int, dict] = {}

    # Try table format: rows starting with a number
    table_re = re.compile(
        r'^\s*(\d+)\s+'            # index
        r'(\S+(?:\s+\S+)*?)\s+'   # name
        r'(\d+)\s+'               # on delay
        r'(\d+)\s+'               # off delay
        r'(\d+)\s*$'              # reboot duration
    )

    for line in lines:
        m = table_re.match(line)
        if m:
            idx = int(m.group(1))
            result[idx] = {
                "name": m.group(2).strip(),
                "on_delay": int(m.group(3)),
                "off_delay": int(m.group(4)),
                "reboot_duration": int(m.group(5)),
            }

    if result:
        return result

    # Fallback: Key-Value format
    kv = _parse_kv(text)
    outlet_data: dict[int, dict] = {}

    for key, val in kv.items():
        m = re.match(r'Outlet\s+(\d+)\s+(.+)', key)
        if m:
            idx = int(m.group(1))
            field = m.group(2).strip().lower()
            if idx not in outlet_data:
                outlet_data[idx] = {}
            if field == "name":
                outlet_data[idx]["name"] = val
            elif "on delay" in field:
                num = re.match(r'(\d+)', val)
                outlet_data[idx]["on_delay"] = int(num.group(1)) if num else 0
            elif "off delay" in field:
                num = re.match(r'(\d+)', val)
                outlet_data[idx]["off_delay"] = int(num.group(1)) if num else 0
            elif "reboot" in field:
                num = re.match(r'(\d+)', val)
                outlet_data[idx]["reboot_duration"] = int(num.group(1)) if num else 10

    for idx, data in outlet_data.items():
        result[idx] = {
            "name": data.get("name", f"Outlet {idx}"),
            "on_delay": data.get("on_delay", 0),
            "off_delay": data.get("off_delay", 0),
            "reboot_duration": data.get("reboot_duration", 10),
        }

    return result


def parse_devcfg_show(text: str) -> dict:
    """Parse 'devcfg show' output into device-level thresholds.

    Example output:
        Overload Threshold : 80 %
        Near Overload Threshold : 70 %
        Low Load Threshold : 20 %

    Returns: {overload_threshold, near_overload_threshold, low_load_threshold}
    """
    kv = _parse_kv(text)
    result = {
        "overload_threshold": None,
        "near_overload_threshold": None,
        "low_load_threshold": None,
    }

    for key, val in kv.items():
        num = re.match(r'([\d.]+)', val)
        key_lower = key.lower()

        if "coldstart" in key_lower and "delay" in key_lower:
            if num:
                result["coldstart_delay"] = int(float(num.group(1)))
            continue
        if "coldstart" in key_lower and "state" in key_lower:
            result["coldstart_state"] = val.strip().lower()
            continue

        if not num:
            continue
        value = float(num.group(1))
        if "near" in key_lower and "overload" in key_lower:
            result["near_overload_threshold"] = value
        elif "overload" in key_lower:
            result["overload_threshold"] = value
        elif "low" in key_lower and "load" in key_lower:
            result["low_load_threshold"] = value

    return result


def parse_bankcfg_show(text: str) -> dict[int, dict]:
    """Parse 'bankcfg show' output into per-bank thresholds.

    Example table format:
        Bank  Overload(%)  Near Overload(%)  Low Load(%)
        1     80           70                20
        2     80           70                20

    Example KV format:
        Bank 1 Overload Threshold : 80 %
        Bank 1 Near Overload Threshold : 70 %
        Bank 1 Low Load Threshold : 20 %

    Returns: {bank_num: {overload, near_overload, low_load}}
    """
    lines = _strip_cli(text)
    result: dict[int, dict] = {}

    # Try table format
    table_re = re.compile(
        r'^\s*(\d+)\s+'        # bank number
        r'([\d.]+)\s+'         # overload
        r'([\d.]+)\s+'         # near overload
        r'([\d.]+)\s*$'        # low load
    )

    for line in lines:
        m = table_re.match(line)
        if m:
            bank = int(m.group(1))
            result[bank] = {
                "overload": float(m.group(2)),
                "near_overload": float(m.group(3)),
                "low_load": float(m.group(4)),
            }

    if result:
        return result

    # Fallback: Key-Value format
    kv = _parse_kv(text)
    bank_data: dict[int, dict] = {}

    for key, val in kv.items():
        m = re.match(r'Bank\s+(\d+)\s+(.+)', key)
        if m:
            bank = int(m.group(1))
            field = m.group(2).strip().lower()
            if bank not in bank_data:
                bank_data[bank] = {}
            num = re.match(r'([\d.]+)', val)
            if not num:
                continue
            value = float(num.group(1))
            if "near" in field and "overload" in field:
                bank_data[bank]["near_overload"] = value
            elif "overload" in field:
                bank_data[bank]["overload"] = value
            elif "low" in field:
                bank_data[bank]["low_load"] = value

    for bank, data in bank_data.items():
        result[bank] = {
            "overload": data.get("overload", 80.0),
            "near_overload": data.get("near_overload", 70.0),
            "low_load": data.get("low_load", 20.0),
        }

    return result


def parse_netcfg_show(text: str) -> dict:
    """Parse 'netcfg show' output into network configuration.

    Example output:
        IP Address     : 192.168.20.177
        Subnet Mask    : 255.255.255.0
        Gateway        : 192.168.20.1
        DHCP           : Enabled
        MAC Address    : 00:0C:15:AA:BB:CC
        IPv6           : Disabled

    Returns: {ip, subnet, gateway, dhcp_enabled, mac_address}
    """
    kv = _parse_kv(text)
    result = {
        "ip": "",
        "subnet": "",
        "gateway": "",
        "dhcp_enabled": False,
        "mac_address": "",
    }

    result["ip"] = kv.get("IP Address", kv.get("IP", ""))
    result["subnet"] = kv.get("Subnet Mask", kv.get("Subnet", ""))
    result["gateway"] = kv.get("Gateway", kv.get("Default Gateway", ""))
    result["mac_address"] = kv.get("MAC Address", kv.get("MAC", ""))

    dhcp = kv.get("DHCP", "").lower()
    result["dhcp_enabled"] = dhcp in ("enabled", "on", "yes", "true")

    return result


def parse_eventlog_show(text: str) -> list[dict]:
    """Parse 'eventlog show' output into event list.

    Example output (table format):
        Index  Date        Time      Event
        1      01/15/2026  14:23:05  Source A Power Restored
        2      01/15/2026  14:22:30  Source A Power Lost
        3      01/14/2026  09:00:00  System Started

    Example output (compact format):
        01/15/2026 14:23:05 Source A Power Restored
        01/15/2026 14:22:30 Source A Power Lost

    Returns: [{timestamp, event_type, description}, ...]
    """
    lines = _strip_cli(text)
    events: list[dict] = []

    # Try indexed table format: Index Date Time Event
    indexed_re = re.compile(
        r'^\s*(\d+)\s+'                           # index
        r'(\d{1,2}/\d{1,2}/\d{2,4})\s+'          # date (MM/DD/YYYY)
        r'(\d{1,2}:\d{2}:\d{2})\s+'              # time (HH:MM:SS)
        r'(.+)$'                                   # event description
    )

    # Compact format: Date Time Event
    compact_re = re.compile(
        r'^\s*(\d{1,2}/\d{1,2}/\d{2,4})\s+'      # date
        r'(\d{1,2}:\d{2}:\d{2})\s+'               # time
        r'(.+)$'                                    # event description
    )

    for line in lines:
        # Skip header lines
        if re.match(r'^\s*(Index|Date|Time|Event|\-+)', line, re.IGNORECASE):
            continue

        m = indexed_re.match(line)
        if m:
            desc = m.group(4).strip()
            events.append({
                "timestamp": f"{m.group(2)} {m.group(3)}",
                "event_type": _classify_event(desc),
                "description": desc,
            })
            continue

        m = compact_re.match(line)
        if m:
            desc = m.group(3).strip()
            events.append({
                "timestamp": f"{m.group(1)} {m.group(2)}",
                "event_type": _classify_event(desc),
                "description": desc,
            })

    return events


def _classify_event(desc: str) -> str:
    """Classify an event description into a type category."""
    desc_lower = desc.lower()
    if "power restored" in desc_lower or "power normal" in desc_lower:
        return "power_restore"
    if "power lost" in desc_lower or "power fail" in desc_lower:
        return "power_loss"
    if "transfer" in desc_lower:
        return "ats_transfer"
    if "started" in desc_lower or "boot" in desc_lower:
        return "system_start"
    if "overload" in desc_lower:
        return "overload"
    if "outlet" in desc_lower:
        return "outlet_change"
    if "login" in desc_lower or "auth" in desc_lower:
        return "auth"
    if "config" in desc_lower or "setting" in desc_lower:
        return "config_change"
    return "info"


def parse_trapcfg_show(text: str) -> list[dict]:
    """Parse 'trapcfg show' output into trap receiver list.

    Example table format:
        Index  IP Address       Community   Severity   Status
        1      192.168.1.100    public      all        Enabled
        2      0.0.0.0          public      all        Disabled

    Example KV format:
        Trap Receiver 1 IP : 192.168.1.100
        Trap Receiver 1 Community : public
        Trap Receiver 1 Severity : all
        Trap Receiver 1 Status : Enabled

    Returns: [{index, ip, community, severity, enabled}]
    """
    lines = _strip_cli(text)
    result: list[dict] = []

    # Try table format
    table_re = re.compile(
        r'^\s*(\d+)\s+'                # index
        r'([\d.]+)\s+'                  # IP
        r'(\S+)\s+'                     # community
        r'(\S+)\s+'                     # severity
        r'(Enabled|Disabled)\s*$',      # status
        re.IGNORECASE,
    )
    for line in lines:
        m = table_re.match(line)
        if m:
            result.append({
                "index": int(m.group(1)),
                "ip": m.group(2),
                "community": m.group(3),
                "severity": m.group(4).lower(),
                "enabled": m.group(5).lower() == "enabled",
            })

    if result:
        return result

    # Fallback: KV format
    kv = _parse_kv(text)
    receivers: dict[int, dict] = {}
    for key, val in kv.items():
        m = re.match(r'Trap\s+Receiver\s+(\d+)\s+(.+)', key)
        if m:
            idx = int(m.group(1))
            field = m.group(2).strip().lower()
            if idx not in receivers:
                receivers[idx] = {"index": idx}
            if "ip" in field:
                receivers[idx]["ip"] = val
            elif "community" in field:
                receivers[idx]["community"] = val
            elif "severity" in field:
                receivers[idx]["severity"] = val.lower()
            elif "status" in field:
                receivers[idx]["enabled"] = val.lower() in ("enabled", "on")

    return list(receivers.values())


def parse_smtpcfg_show(text: str) -> dict:
    """Parse 'smtpcfg show' output into SMTP configuration.

    Example:
        SMTP Server    : mail.example.com
        SMTP Port      : 25
        From Address   : pdu@example.com
        Auth Username  : pduuser
    """
    kv = _parse_kv(text)
    result = {
        "server": kv.get("SMTP Server", kv.get("Server", "")),
        "port": 25,
        "from_addr": kv.get("From Address", kv.get("From", "")),
        "auth_user": kv.get("Auth Username", kv.get("Username", "")),
    }
    port_str = kv.get("SMTP Port", kv.get("Port", ""))
    if port_str:
        m = re.match(r'(\d+)', port_str)
        if m:
            result["port"] = int(m.group(1))
    return result


def parse_emailcfg_show(text: str) -> list[dict]:
    """Parse 'emailcfg show' output into email recipient list.

    Example table format:
        Index  To Address           Status
        1      admin@example.com    Enabled
        2                           Disabled

    Example KV format:
        Email Recipient 1 To : admin@example.com
        Email Recipient 1 Status : Enabled
    """
    lines = _strip_cli(text)
    result: list[dict] = []

    # Try table format
    table_re = re.compile(
        r'^\s*(\d+)\s+'                # index
        r'(\S+@\S+)?\s*'               # email (optional)
        r'(Enabled|Disabled)\s*$',      # status
        re.IGNORECASE,
    )
    for line in lines:
        m = table_re.match(line)
        if m:
            result.append({
                "index": int(m.group(1)),
                "to": m.group(2) or "",
                "enabled": m.group(3).lower() == "enabled",
            })

    if result:
        return result

    # Fallback: KV format
    kv = _parse_kv(text)
    recipients: dict[int, dict] = {}
    for key, val in kv.items():
        m = re.match(r'Email\s+Recipient\s+(\d+)\s+(.+)', key)
        if m:
            idx = int(m.group(1))
            field = m.group(2).strip().lower()
            if idx not in recipients:
                recipients[idx] = {"index": idx}
            if "to" in field or "address" in field:
                recipients[idx]["to"] = val
            elif "status" in field:
                recipients[idx]["enabled"] = val.lower() in ("enabled", "on")

    return list(recipients.values())


def parse_syslogcfg_show(text: str) -> list[dict]:
    """Parse 'syslog show' output into syslog server list.

    Example table format:
        Index  IP Address       Facility   Severity   Status
        1      192.168.1.50     local0     all        Enabled
        2      0.0.0.0          local0     all        Disabled

    Example KV format:
        Syslog Server 1 IP : 192.168.1.50
        Syslog Server 1 Facility : local0
        Syslog Server 1 Severity : all
        Syslog Server 1 Status : Enabled
    """
    lines = _strip_cli(text)
    result: list[dict] = []

    # Try table format
    table_re = re.compile(
        r'^\s*(\d+)\s+'                # index
        r'([\d.]+)\s+'                  # IP
        r'(\S+)\s+'                     # facility
        r'(\S+)\s+'                     # severity
        r'(Enabled|Disabled)\s*$',      # status
        re.IGNORECASE,
    )
    for line in lines:
        m = table_re.match(line)
        if m:
            result.append({
                "index": int(m.group(1)),
                "ip": m.group(2),
                "facility": m.group(3).lower(),
                "severity": m.group(4).lower(),
                "enabled": m.group(5).lower() == "enabled",
            })

    if result:
        return result

    # Fallback: KV format
    kv = _parse_kv(text)
    servers: dict[int, dict] = {}
    for key, val in kv.items():
        m = re.match(r'Syslog\s+Server\s+(\d+)\s+(.+)', key)
        if m:
            idx = int(m.group(1))
            field = m.group(2).strip().lower()
            if idx not in servers:
                servers[idx] = {"index": idx}
            if "ip" in field:
                servers[idx]["ip"] = val
            elif "facility" in field:
                servers[idx]["facility"] = val.lower()
            elif "severity" in field:
                servers[idx]["severity"] = val.lower()
            elif "status" in field:
                servers[idx]["enabled"] = val.lower() in ("enabled", "on")

    return list(servers.values())


def parse_usercfg_show(text: str) -> dict:
    """Parse 'usercfg show' output into user account info.

    Example:
        Admin Username : admin
        Admin Access   : Full
        Viewer Username : viewer
        Viewer Access   : Read-only

    Returns: {admin: {username, access}, viewer: {username, access}}
    """
    kv = _parse_kv(text)
    result: dict[str, dict] = {}

    for key, val in kv.items():
        key_lower = key.lower()
        if "admin" in key_lower:
            if "admin" not in result:
                result["admin"] = {}
            if "username" in key_lower or "name" in key_lower:
                result["admin"]["username"] = val
            elif "access" in key_lower:
                result["admin"]["access"] = val
        elif "viewer" in key_lower:
            if "viewer" not in result:
                result["viewer"] = {}
            if "username" in key_lower or "name" in key_lower:
                result["viewer"]["username"] = val
            elif "access" in key_lower:
                result["viewer"]["access"] = val

    return result


def parse_energywise_show(text: str) -> dict:
    """Parse 'energywise show' output into EnergyWise configuration.

    Example:
        Domain         : cisco.com
        Port           : 43440
        Shared Secret  : ********
        Status         : Disabled

    Returns: {domain, port, secret, enabled}
    """
    kv = _parse_kv(text)
    result = {
        "domain": kv.get("Domain", ""),
        "port": 43440,
        "secret": kv.get("Shared Secret", kv.get("Secret", "")),
        "enabled": False,
    }
    port_str = kv.get("Port", "")
    if port_str:
        m = re.match(r'(\d+)', port_str)
        if m:
            result["port"] = int(m.group(1))
    status = kv.get("Status", "").lower()
    result["enabled"] = status in ("enabled", "on")
    return result


def build_pdu_data(
    devsta: dict,
    outlets: dict[int, OutletData],
    srccfg: dict,
    identity: DeviceIdentity | None = None,
    devcfg: dict | None = None,
) -> PDUData:
    """Combine parsed CLI results into a PDUData snapshot.

    Maps the serial CLI's output structure into the same PDUData model
    that SNMP uses, so all downstream systems (MQTT, history, web,
    automation) work unchanged.
    """
    # ATS source mapping: "A" -> 1, "B" -> 2
    source_map = {"A": 1, "B": 2}

    active_source = devsta.get("active_source")
    ats_current = source_map.get(active_source) if active_source else None

    pref_source = srccfg.get("preferred_source")
    ats_preferred = source_map.get(pref_source) if pref_source else None

    # Source data
    src_a_voltage = devsta.get("source_a_voltage")
    src_b_voltage = devsta.get("source_b_voltage")
    src_a_freq = devsta.get("source_a_frequency")
    src_b_freq = devsta.get("source_b_frequency")

    source_a_status = devsta.get("source_a_status", "unknown")
    source_b_status = devsta.get("source_b_status", "unknown")

    source_a = SourceData(
        voltage=src_a_voltage,
        frequency=src_a_freq,
        voltage_status=source_a_status,
    )
    source_b = SourceData(
        voltage=src_b_voltage,
        frequency=src_b_freq,
        voltage_status=source_b_status,
    )

    # Determine input voltage from active source
    input_voltage = None
    if active_source == "A" and src_a_voltage is not None:
        input_voltage = src_a_voltage
    elif active_source == "B" and src_b_voltage is not None:
        input_voltage = src_b_voltage
    elif src_a_voltage is not None:
        input_voltage = src_a_voltage

    input_frequency = None
    if active_source == "A" and src_a_freq is not None:
        input_frequency = src_a_freq
    elif active_source == "B" and src_b_freq is not None:
        input_frequency = src_b_freq
    elif src_a_freq is not None:
        input_frequency = src_a_freq

    # Redundancy: both sources normal
    redundancy_ok = (
        source_a_status == "normal" and source_b_status == "normal"
    ) if source_a_status != "unknown" and source_b_status != "unknown" else None

    # Banks from devsta bank_currents
    banks: dict[int, BankData] = {}
    bank_currents = devsta.get("bank_currents", {})
    for bank_num, current in bank_currents.items():
        voltage = None
        if bank_num == 1 and src_a_voltage is not None:
            voltage = src_a_voltage
        elif bank_num == 2 and src_b_voltage is not None:
            voltage = src_b_voltage

        power = None
        if current is not None and voltage is not None:
            power = round(current * voltage, 1)

        banks[bank_num] = BankData(
            number=bank_num,
            current=current,
            voltage=voltage,
            power=power,
            load_state="normal",
        )

    # If no bank data, create banks from source voltages
    if not banks:
        if src_a_voltage is not None:
            banks[1] = BankData(number=1, voltage=src_a_voltage, load_state="normal")
        if src_b_voltage is not None:
            banks[2] = BankData(number=2, voltage=src_b_voltage, load_state="normal")

    return PDUData(
        device_name=identity.name if identity else "",
        outlet_count=len(outlets),
        phase_count=1,
        input_voltage=input_voltage,
        input_frequency=input_frequency,
        outlets=outlets,
        banks=banks,
        ats_preferred_source=ats_preferred,
        ats_current_source=ats_current,
        ats_auto_transfer=True,
        source_a=source_a,
        source_b=source_b,
        redundancy_ok=redundancy_ok,
        # ATS extended config from srccfg
        voltage_sensitivity=srccfg.get("voltage_sensitivity", ""),
        transfer_voltage=srccfg.get("transfer_voltage"),
        voltage_upper_limit=srccfg.get("voltage_upper_limit"),
        voltage_lower_limit=srccfg.get("voltage_lower_limit"),
        # Totals from devsta
        total_load=devsta.get("total_load"),
        total_power=devsta.get("total_power"),
        total_energy=devsta.get("total_energy"),
        # Coldstart config from devcfg
        coldstart_delay=devcfg.get("coldstart_delay") if devcfg else None,
        coldstart_state=devcfg.get("coldstart_state", "") if devcfg else "",
        identity=identity,
    )
