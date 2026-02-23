# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Abstract transport protocol for CyberPower PDU communication.

Defines the PDUTransport interface that SNMP, Serial, and Mock
transports all implement. This allows the PDUPoller to be
transport-agnostic — it works with any transport that implements
this protocol.
"""

from typing import Protocol, runtime_checkable

from .pdu_model import DeviceIdentity, PDUData


@runtime_checkable
class PDUTransport(Protocol):
    """Protocol for PDU communication transports.

    Implementations: SNMPTransport, SerialTransport, MockPDU.
    """

    async def connect(self) -> None:
        """Establish connection to the PDU."""
        ...

    async def poll(self) -> PDUData:
        """Poll the PDU and return a data snapshot."""
        ...

    async def get_identity(self) -> DeviceIdentity:
        """Query device identity (called once at startup)."""
        ...

    async def discover_num_banks(self) -> int:
        """Detect the number of banks/inputs."""
        ...

    async def query_startup_data(self, outlet_count: int) -> tuple[dict, dict]:
        """Query startup-only data (bank assignments, max loads).

        Returns: (outlet_bank_assignments, outlet_max_loads)
        """
        ...

    async def command_outlet(self, outlet: int, command: str) -> bool:
        """Execute an outlet command ('on', 'off', 'reboot').

        Returns True on success.
        """
        ...

    async def set_device_field(self, field: str, value: str) -> bool:
        """Set a device field (name, location, etc.).

        Returns True on success.
        """
        ...

    def get_health(self) -> dict:
        """Return transport health metrics."""
        ...

    @property
    def consecutive_failures(self) -> int:
        """Current consecutive failure count."""
        ...

    def reset_health(self) -> None:
        """Zero out failure counters."""
        ...

    def close(self) -> None:
        """Close the transport connection."""
        ...
