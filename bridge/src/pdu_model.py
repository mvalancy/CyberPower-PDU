# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""OID constants and data models for CyberPower PDUs.

Designed to work across the CyberPower product family. OIDs are based on
the CyberPower ePDU MIB and ePDU2 MIB. Outlet counts, bank counts, and
capabilities are auto-detected from the device — not hardcoded.
"""

from dataclasses import dataclass, field

# --- CyberPower ePDU MIB base ---
BASE_OID = "1.3.6.1.4.1.3808.1.1.3"

# --- Device identity (section .1) ---
OID_DEVICE_NAME = f"{BASE_OID}.1.1.0"
OID_FW_MAIN = f"{BASE_OID}.1.2.0"            # Main firmware version
OID_FW_SECONDARY = f"{BASE_OID}.1.3.0"       # Secondary firmware version
OID_SERIAL_NUM = f"{BASE_OID}.1.4.0"         # Numeric serial number
OID_MODEL = f"{BASE_OID}.1.5.0"              # Model number (e.g., "PDU44001")
OID_SERIAL_HW = f"{BASE_OID}.1.6.0"          # Hardware serial — PRIMARY unique ID
OID_HW_REV = f"{BASE_OID}.1.7.0"             # Hardware revision
OID_OUTLET_COUNT = f"{BASE_OID}.1.8.0"
OID_PHASE_COUNT = f"{BASE_OID}.1.9.0"
OID_MAX_CURRENT = f"{BASE_OID}.1.15.0"       # Max input current rating (tenths A)

# --- Input (bus/output — NOT per-source on ATS models) ---
OID_INPUT_VOLTAGE = f"{BASE_OID}.5.7.0"
OID_INPUT_FREQUENCY = f"{BASE_OID}.5.8.0"

# --- Transfer switch (ATS) — ePDU MIB ---
OID_ATS_PREFERRED_SOURCE = f"{BASE_OID}.4.1.1.0"  # 1=A, 2=B
OID_ATS_CURRENT_SOURCE = f"{BASE_OID}.4.1.2.0"    # 1=A, 2=B
OID_ATS_AUTO_TRANSFER = f"{BASE_OID}.4.1.3.0"     # 1=enabled, 2=disabled

# --- ePDU2 Source Status — per-input voltage and status ---
EPDU2_SOURCE_ENTRY = "1.3.6.1.4.1.3808.1.1.6.9.4.1"
OID_SOURCE_A_VOLTAGE = f"{EPDU2_SOURCE_ENTRY}.5.1"      # 0.1V
OID_SOURCE_B_VOLTAGE = f"{EPDU2_SOURCE_ENTRY}.6.1"      # 0.1V
OID_SOURCE_A_FREQUENCY = f"{EPDU2_SOURCE_ENTRY}.7.1"    # 0.1Hz
OID_SOURCE_B_FREQUENCY = f"{EPDU2_SOURCE_ENTRY}.8.1"    # 0.1Hz
OID_SOURCE_A_STATUS = f"{EPDU2_SOURCE_ENTRY}.9.1"       # 1=normal,2=over,3=under
OID_SOURCE_B_STATUS = f"{EPDU2_SOURCE_ENTRY}.10.1"      # 1=normal,2=over,3=under
OID_SOURCE_REDUNDANCY = f"{EPDU2_SOURCE_ENTRY}.16.1"    # 1=lost,2=redundant

# --- Power distribution config (section .2.1) ---
OID_NUM_BANK_TABLE_ENTRIES = f"{BASE_OID}.2.1.2.0"
OID_OUTLETS_IN_DIST = f"{BASE_OID}.2.1.7.0"  # Outlets in power distribution

# --- Standard MIB-II ---
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
OID_SYS_CONTACT = "1.3.6.1.2.1.1.4.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
OID_SYS_LOCATION = "1.3.6.1.2.1.1.6.0"


# --- Per-outlet OID helpers ---

def oid_outlet_name(n: int) -> str:
    return f"{BASE_OID}.3.3.1.1.2.{n}"


def oid_outlet_command(n: int) -> str:
    return f"{BASE_OID}.3.3.1.1.4.{n}"


def oid_outlet_state(n: int) -> str:
    return f"{BASE_OID}.3.5.1.1.4.{n}"


def oid_outlet_current(n: int) -> str:
    return f"{BASE_OID}.3.5.1.1.5.{n}"


def oid_outlet_power(n: int) -> str:
    return f"{BASE_OID}.3.5.1.1.6.{n}"


def oid_outlet_energy(n: int) -> str:
    return f"{BASE_OID}.3.5.1.1.7.{n}"


def oid_outlet_bank_assignment(n: int) -> str:
    """Which bank outlet n belongs to (section .2.1.8.1.2)."""
    return f"{BASE_OID}.2.1.8.1.2.{n}"


def oid_outlet_max_load(n: int) -> str:
    """Max current rating for outlet n in tenths of amps (section .2.1.8.1.3)."""
    return f"{BASE_OID}.2.1.8.1.3.{n}"


# --- Per-bank OID helpers ---

def oid_bank_current(idx: int) -> str:
    return f"{BASE_OID}.2.3.1.1.2.{idx}"


def oid_bank_load_state(idx: int) -> str:
    return f"{BASE_OID}.2.3.1.1.3.{idx}"


def oid_bank_voltage(idx: int) -> str:
    return f"{BASE_OID}.2.3.1.1.6.{idx}"


def oid_bank_active_power(idx: int) -> str:
    return f"{BASE_OID}.2.3.1.1.7.{idx}"


def oid_bank_apparent_power(idx: int) -> str:
    return f"{BASE_OID}.2.3.1.1.8.{idx}"


def oid_bank_power_factor(idx: int) -> str:
    return f"{BASE_OID}.2.3.1.1.9.{idx}"


def oid_bank_energy(idx: int) -> str:
    """Bank energy — may be kWh counter (section .2.3.1.1.10)."""
    return f"{BASE_OID}.2.3.1.1.10.{idx}"


def oid_bank_timestamp(idx: int) -> str:
    """Bank last-update timestamp string (section .2.3.1.1.11)."""
    return f"{BASE_OID}.2.3.1.1.11.{idx}"


# --- Command / state constants ---

OUTLET_CMD_ON = 1
OUTLET_CMD_OFF = 2
OUTLET_CMD_REBOOT = 3

OUTLET_STATE_ON = 1
OUTLET_STATE_OFF = 2

OUTLET_STATE_MAP = {
    OUTLET_STATE_ON: "on",
    OUTLET_STATE_OFF: "off",
}

BANK_LOAD_STATE_MAP = {
    1: "normal",
    2: "low",
    3: "nearOverload",
    4: "overload",
}

OUTLET_CMD_MAP = {
    "on": OUTLET_CMD_ON,
    "off": OUTLET_CMD_OFF,
    "reboot": OUTLET_CMD_REBOOT,
}

ATS_SOURCE_MAP = {1: "A", 2: "B"}

SOURCE_VOL_STATUS_MAP = {1: "normal", 2: "overVoltage", 3: "underVoltage"}


# --- Data classes ---

@dataclass
class DeviceIdentity:
    """Device identity queried once at startup — works across CyberPower product family."""
    serial: str = ""                # Hardware serial (OID .1.6.0) — PRIMARY unique ID
    serial_numeric: str = ""        # Numeric serial (OID .1.4.0)
    model: str = ""                 # Model number (e.g., "PDU44001", "PDU30SWEV17FNET")
    name: str = ""                  # Device name (OID .1.1.0)
    firmware_main: str = ""         # Main firmware version
    firmware_secondary: str = ""    # Secondary firmware version
    hardware_rev: int = 0           # Hardware revision
    max_current: float = 0.0       # Max input current (amps, from tenths)
    outlet_count: int = 0           # Auto-detected from SNMP
    phase_count: int = 1            # Auto-detected from SNMP
    # Standard MIB-II (may or may not be supported)
    sys_description: str = ""
    sys_uptime: int = 0             # Hundredths of seconds
    sys_contact: str = ""
    sys_name: str = ""
    sys_location: str = ""
    mac_address: str = ""           # If discoverable via ifPhysAddress

    def to_dict(self) -> dict:
        return {
            "serial": self.serial,
            "serial_numeric": self.serial_numeric,
            "model": self.model,
            "name": self.name,
            "firmware_main": self.firmware_main,
            "firmware_secondary": self.firmware_secondary,
            "hardware_rev": self.hardware_rev,
            "max_current": self.max_current,
            "outlet_count": self.outlet_count,
            "phase_count": self.phase_count,
            "sys_description": self.sys_description,
            "sys_uptime": self.sys_uptime,
            "sys_contact": self.sys_contact,
            "sys_name": self.sys_name,
            "sys_location": self.sys_location,
            "mac_address": self.mac_address,
        }


@dataclass
class OutletData:
    number: int
    name: str = ""
    state: str = "unknown"
    current: float | None = None        # amps
    power: float | None = None          # watts
    energy: float | None = None         # kWh
    bank_assignment: int | None = None  # which bank this outlet belongs to
    max_load: float | None = None       # max current rating in amps


@dataclass
class BankData:
    number: int
    current: float | None = None        # amps
    voltage: float | None = None        # volts
    power: float | None = None          # watts
    apparent_power: float | None = None # VA
    power_factor: float | None = None   # 0-1
    load_state: str = "unknown"
    energy: float | None = None         # kWh (if supported)
    last_update: str = ""               # timestamp string (if supported)


@dataclass
class SourceData:
    """Per-input source data from ePDU2 Source Status table."""
    voltage: float | None = None        # volts
    frequency: float | None = None      # Hz
    voltage_status: str = "unknown"     # normal, overVoltage, underVoltage
    voltage_status_raw: int | None = None


@dataclass
class PDUData:
    device_name: str = ""
    outlet_count: int = 0
    phase_count: int = 0
    input_voltage: float | None = None
    input_frequency: float | None = None
    outlets: dict[int, OutletData] = field(default_factory=dict)
    banks: dict[int, BankData] = field(default_factory=dict)
    # ATS fields (may be absent on non-ATS models)
    ats_preferred_source: int | None = None  # 1=A, 2=B
    ats_current_source: int | None = None    # 1=A, 2=B
    ats_auto_transfer: bool = True
    # Per-input source data (may be absent on non-ATS models)
    source_a: SourceData | None = None
    source_b: SourceData | None = None
    redundancy_ok: bool | None = None
    # Device identity (queried at startup, included in every snapshot)
    identity: DeviceIdentity | None = None
