"""OID constants and data models for CyberPower PDU44001."""

from dataclasses import dataclass, field

# CyberPower ePDU MIB base
BASE_OID = "1.3.6.1.4.1.3808.1.1.3"

# Device identity
OID_DEVICE_NAME = f"{BASE_OID}.1.1.0"
OID_OUTLET_COUNT = f"{BASE_OID}.1.8.0"
OID_PHASE_COUNT = f"{BASE_OID}.1.9.0"

# Input (bus/output — NOT per-source on ATS models)
OID_INPUT_VOLTAGE = f"{BASE_OID}.5.7.0"
OID_INPUT_FREQUENCY = f"{BASE_OID}.5.8.0"

# Transfer switch (ATS) — ePDU MIB
OID_ATS_PREFERRED_SOURCE = f"{BASE_OID}.4.1.1.0"  # 1=A, 2=B
OID_ATS_CURRENT_SOURCE = f"{BASE_OID}.4.1.2.0"    # 1=A, 2=B
OID_ATS_AUTO_TRANSFER = f"{BASE_OID}.4.1.3.0"     # 1=enabled, 2=disabled

# ePDU2 Source Status — per-input voltage and status (ePDU2SourceStatusEntry)
EPDU2_SOURCE_ENTRY = "1.3.6.1.4.1.3808.1.1.6.9.4.1"
OID_SOURCE_A_VOLTAGE = f"{EPDU2_SOURCE_ENTRY}.5.1"      # 0.1V
OID_SOURCE_B_VOLTAGE = f"{EPDU2_SOURCE_ENTRY}.6.1"      # 0.1V
OID_SOURCE_A_FREQUENCY = f"{EPDU2_SOURCE_ENTRY}.7.1"    # 0.1Hz
OID_SOURCE_B_FREQUENCY = f"{EPDU2_SOURCE_ENTRY}.8.1"    # 0.1Hz
OID_SOURCE_A_STATUS = f"{EPDU2_SOURCE_ENTRY}.9.1"       # 1=normal,2=over,3=under
OID_SOURCE_B_STATUS = f"{EPDU2_SOURCE_ENTRY}.10.1"      # 1=normal,2=over,3=under
OID_SOURCE_REDUNDANCY = f"{EPDU2_SOURCE_ENTRY}.16.1"    # 1=lost,2=redundant


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


# Outlet command values
OUTLET_CMD_ON = 1
OUTLET_CMD_OFF = 2
OUTLET_CMD_REBOOT = 3

# Outlet state values
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


@dataclass
class OutletData:
    number: int
    name: str = ""
    state: str = "unknown"
    current: float | None = None  # amps
    power: float | None = None  # watts
    energy: float | None = None  # kWh


@dataclass
class BankData:
    number: int
    current: float | None = None  # amps
    voltage: float | None = None  # volts
    power: float | None = None  # watts
    apparent_power: float | None = None  # VA
    power_factor: float | None = None  # 0-1
    load_state: str = "unknown"


ATS_SOURCE_MAP = {1: "A", 2: "B"}

SOURCE_VOL_STATUS_MAP = {1: "normal", 2: "overVoltage", 3: "underVoltage"}


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
    # ATS fields
    ats_preferred_source: int | None = None  # 1=A, 2=B
    ats_current_source: int | None = None    # 1=A, 2=B
    ats_auto_transfer: bool = True
    # Per-input source data
    source_a: SourceData | None = None
    source_b: SourceData | None = None
    redundancy_ok: bool | None = None
