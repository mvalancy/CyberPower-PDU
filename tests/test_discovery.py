# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Unit tests for the network discovery module."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.discovery import (
    DiscoveredPDU,
    DiscoveredSerialPDU,
    InterfaceScanResult,
    NetworkInterface,
    _format_table,
    enumerate_serial_ports,
    get_all_interfaces,
    get_local_subnets,
    get_stable_port_name,
    scan_all_interfaces,
    scan_serial_ports,
    scan_subnet,
)
from src.pdu_model import OID_DEVICE_NAME, OID_MODEL, OID_OUTLET_COUNT, OID_SERIAL_HW


# ---------------------------------------------------------------------------
# get_local_subnets
# ---------------------------------------------------------------------------

def test_get_local_subnets():
    """get_local_subnets returns at least one subnet string."""
    subnets = get_local_subnets()
    assert isinstance(subnets, list)
    assert len(subnets) >= 1
    # Each entry should look like a CIDR subnet
    for s in subnets:
        assert "/" in s, f"Expected CIDR notation, got {s!r}"


# ---------------------------------------------------------------------------
# scan_subnet — empty result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_subnet_empty():
    """Scanning a non-routable /30 with a short timeout returns empty.

    Uses a TEST-NET address range (RFC 5737) to avoid hitting real devices.
    """
    # Mock pysnmp so we don't actually send SNMP packets
    with patch("src.discovery.HAS_PYSNMP", True), \
         patch("src.discovery.SnmpEngine") as mock_engine_cls, \
         patch("src.discovery._probe_host", new_callable=AsyncMock, return_value=None):
        mock_engine_cls.return_value = MagicMock()
        results = await scan_subnet("198.51.100.0/30", timeout=0.1)
    assert results == []


# ---------------------------------------------------------------------------
# DiscoveredPDU dataclass
# ---------------------------------------------------------------------------

def test_discovered_pdu_dataclass():
    """DiscoveredPDU fields work correctly."""
    pdu = DiscoveredPDU(
        host="192.168.1.100",
        device_name="CyberPower PDU44001",
        serial="ABC123",
        model="PDU44001",
        outlet_count=24,
        already_configured=False,
    )
    assert pdu.host == "192.168.1.100"
    assert pdu.device_name == "CyberPower PDU44001"
    assert pdu.serial == "ABC123"
    assert pdu.model == "PDU44001"
    assert pdu.outlet_count == 24
    assert pdu.already_configured is False


def test_discovered_pdu_defaults():
    """DiscoveredPDU defaults are sensible."""
    pdu = DiscoveredPDU(host="10.0.0.1")
    assert pdu.device_name == ""
    assert pdu.serial == ""
    assert pdu.model == ""
    assert pdu.outlet_count == 0
    assert pdu.already_configured is False


# ---------------------------------------------------------------------------
# _format_table
# ---------------------------------------------------------------------------

def test_format_table_empty():
    """Empty list prints 'No CyberPower PDUs found'."""
    output = _format_table([])
    assert "No CyberPower PDUs found" in output


def test_format_table_with_results():
    """Formats discovered PDUs as a readable ASCII table."""
    pdus = [
        DiscoveredPDU(
            host="192.168.1.10",
            device_name="Rack 1 PDU",
            serial="SN001",
            model="PDU44001",
            outlet_count=24,
            already_configured=False,
        ),
        DiscoveredPDU(
            host="192.168.1.20",
            device_name="Rack 2 PDU",
            serial="SN002",
            model="PDU30SWEV",
            outlet_count=10,
            already_configured=True,
        ),
    ]
    output = _format_table(pdus)

    # Table should contain both hosts
    assert "192.168.1.10" in output
    assert "192.168.1.20" in output

    # Table should contain device names
    assert "Rack 1 PDU" in output
    assert "Rack 2 PDU" in output

    # Status column
    assert "new" in output
    assert "configured" in output

    # Header elements
    assert "Host" in output
    assert "Name" in output


# ---------------------------------------------------------------------------
# _probe_host (mocked pysnmp)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_host_returns_discovered_pdu():
    """Mock the pysnmp getCmd call to simulate a successful probe."""
    from src.discovery import _probe_host

    # Create mock var_binds that mimic pysnmp output
    def make_var_bind(oid_str, value_str):
        oid_obj = MagicMock()
        oid_obj.__str__ = lambda self: oid_str
        val_obj = MagicMock()
        val_obj.__str__ = lambda self: value_str
        return (oid_obj, val_obj)

    var_binds = [
        make_var_bind(OID_DEVICE_NAME, "CyberPower PDU44001"),
        make_var_bind(OID_MODEL, "PDU44001"),
        make_var_bind(OID_SERIAL_HW, "ABC123456"),
        make_var_bind(OID_OUTLET_COUNT, "24"),
    ]

    mock_result = (None, None, 0, var_binds)
    mock_engine = MagicMock()

    with patch("src.discovery.getCmd", new_callable=AsyncMock, return_value=mock_result), \
         patch("src.discovery.UdpTransportTarget"), \
         patch("src.discovery.CommunityData"), \
         patch("src.discovery.ContextData"), \
         patch("src.discovery.ObjectType"), \
         patch("src.discovery.ObjectIdentity"):
        result = await _probe_host(mock_engine, "192.168.1.50", "public", 161, 1.0)

    assert result is not None
    assert isinstance(result, DiscoveredPDU)
    assert result.host == "192.168.1.50"
    assert result.device_name == "CyberPower PDU44001"
    assert result.model == "PDU44001"
    assert result.serial == "ABC123456"
    assert result.outlet_count == 24


@pytest.mark.asyncio
async def test_probe_host_error_returns_none():
    """When SNMP returns an error, _probe_host returns None."""
    from src.discovery import _probe_host

    mock_result = ("requestTimedOut", None, 0, [])
    mock_engine = MagicMock()

    with patch("src.discovery.getCmd", new_callable=AsyncMock, return_value=mock_result), \
         patch("src.discovery.UdpTransportTarget"), \
         patch("src.discovery.CommunityData"), \
         patch("src.discovery.ContextData"), \
         patch("src.discovery.ObjectType"), \
         patch("src.discovery.ObjectIdentity"):
        result = await _probe_host(mock_engine, "192.168.1.50", "public", 161, 1.0)

    assert result is None


@pytest.mark.asyncio
async def test_probe_host_no_device_name_returns_none():
    """If device name OID is empty/noSuch, _probe_host returns None."""
    from src.discovery import _probe_host

    def make_var_bind(oid_str, value_str):
        oid_obj = MagicMock()
        oid_obj.__str__ = lambda self: oid_str
        val_obj = MagicMock()
        val_obj.__str__ = lambda self: value_str
        return (oid_obj, val_obj)

    # Return noSuchObject for device name, real values for others
    var_binds = [
        make_var_bind(OID_DEVICE_NAME, "noSuchObject"),
        make_var_bind(OID_MODEL, "PDU44001"),
        make_var_bind(OID_SERIAL_HW, "ABC123"),
        make_var_bind(OID_OUTLET_COUNT, "24"),
    ]

    mock_result = (None, None, 0, var_binds)
    mock_engine = MagicMock()

    with patch("src.discovery.getCmd", new_callable=AsyncMock, return_value=mock_result), \
         patch("src.discovery.UdpTransportTarget"), \
         patch("src.discovery.CommunityData"), \
         patch("src.discovery.ContextData"), \
         patch("src.discovery.ObjectType"), \
         patch("src.discovery.ObjectIdentity"):
        result = await _probe_host(mock_engine, "192.168.1.50", "public", 161, 1.0)

    assert result is None


# ---------------------------------------------------------------------------
# NetworkInterface / get_all_interfaces
# ---------------------------------------------------------------------------

def test_network_interface_dataclass():
    """NetworkInterface fields work correctly."""
    iface = NetworkInterface(name="eth0", ip="192.168.1.10", subnet="192.168.1.0/24")
    assert iface.name == "eth0"
    assert iface.ip == "192.168.1.10"
    assert iface.subnet == "192.168.1.0/24"


def test_get_all_interfaces_returns_list():
    """get_all_interfaces returns at least one NetworkInterface."""
    interfaces = get_all_interfaces()
    assert isinstance(interfaces, list)
    assert len(interfaces) >= 1
    for iface in interfaces:
        assert isinstance(iface, NetworkInterface)
        assert iface.name
        assert "/" in iface.subnet


def test_get_all_interfaces_no_loopback():
    """get_all_interfaces should not include loopback."""
    interfaces = get_all_interfaces()
    for iface in interfaces:
        assert iface.name != "lo"
        assert not iface.ip.startswith("127.")


def test_get_all_interfaces_no_docker():
    """get_all_interfaces should skip Docker/veth interfaces."""
    interfaces = get_all_interfaces()
    for iface in interfaces:
        assert not iface.name.startswith("docker")
        assert not iface.name.startswith("veth")
        assert not iface.name.startswith("br-")
        assert not iface.name.startswith("virbr")


def test_get_local_subnets_uses_get_all_interfaces():
    """get_local_subnets() backward compat wrapper returns subnet strings."""
    subnets = get_local_subnets()
    interfaces = get_all_interfaces()
    assert len(subnets) == len(interfaces)
    for s, iface in zip(subnets, interfaces):
        assert s == iface.subnet


# ---------------------------------------------------------------------------
# InterfaceScanResult
# ---------------------------------------------------------------------------

def test_interface_scan_result_dataclass():
    """InterfaceScanResult stores per-interface scan data."""
    result = InterfaceScanResult(
        interface="eth0", subnet="192.168.1.0/24", ip="192.168.1.10",
    )
    assert result.interface == "eth0"
    assert result.pdus == []
    assert result.error == ""


def test_interface_scan_result_with_pdus():
    """InterfaceScanResult can hold discovered PDUs."""
    pdu = DiscoveredPDU(host="192.168.1.50", device_name="PDU1", interface="eth0")
    result = InterfaceScanResult(
        interface="eth0", subnet="192.168.1.0/24", ip="192.168.1.10",
        pdus=[pdu],
    )
    assert len(result.pdus) == 1
    assert result.pdus[0].interface == "eth0"


# ---------------------------------------------------------------------------
# scan_all_interfaces (mocked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_all_interfaces_returns_per_interface():
    """scan_all_interfaces returns one result per interface."""
    mock_interfaces = [
        NetworkInterface(name="eth0", ip="192.168.1.10", subnet="192.168.1.0/24"),
        NetworkInterface(name="wlan0", ip="10.0.0.5", subnet="10.0.0.0/24"),
    ]
    pdu1 = DiscoveredPDU(host="192.168.1.50", device_name="PDU1")
    pdu2 = DiscoveredPDU(host="10.0.0.20", device_name="PDU2")

    async def mock_scan(subnet, **kw):
        if "192.168.1" in subnet:
            return [pdu1]
        return [pdu2]

    with patch("src.discovery.get_all_interfaces", return_value=mock_interfaces), \
         patch("src.discovery.scan_subnet", side_effect=mock_scan):
        results = await scan_all_interfaces()

    assert len(results) == 2
    assert results[0].interface == "eth0"
    assert len(results[0].pdus) == 1
    assert results[0].pdus[0].interface == "eth0"
    assert results[1].interface == "wlan0"
    assert len(results[1].pdus) == 1
    assert results[1].pdus[0].interface == "wlan0"


@pytest.mark.asyncio
async def test_scan_all_interfaces_configured_hosts():
    """Already-configured PDUs are flagged but scanning continues."""
    mock_interfaces = [
        NetworkInterface(name="eth0", ip="192.168.1.10", subnet="192.168.1.0/24"),
    ]
    pdu1 = DiscoveredPDU(host="192.168.1.50", device_name="PDU1", already_configured=True)
    pdu2 = DiscoveredPDU(host="192.168.1.60", device_name="PDU2")

    async def mock_scan(subnet, **kw):
        return [pdu1, pdu2]

    with patch("src.discovery.get_all_interfaces", return_value=mock_interfaces), \
         patch("src.discovery.scan_subnet", side_effect=mock_scan):
        results = await scan_all_interfaces(configured_hosts={"192.168.1.50"})

    assert len(results) == 1
    assert len(results[0].pdus) == 2


@pytest.mark.asyncio
async def test_scan_all_interfaces_error_handling():
    """If scanning one interface fails, others still complete."""
    mock_interfaces = [
        NetworkInterface(name="eth0", ip="192.168.1.10", subnet="192.168.1.0/24"),
        NetworkInterface(name="wlan0", ip="10.0.0.5", subnet="10.0.0.0/24"),
    ]
    pdu1 = DiscoveredPDU(host="10.0.0.20", device_name="PDU2")

    async def mock_scan(subnet, **kw):
        if "192.168.1" in subnet:
            raise RuntimeError("SNMP engine error")
        return [pdu1]

    with patch("src.discovery.get_all_interfaces", return_value=mock_interfaces), \
         patch("src.discovery.scan_subnet", side_effect=mock_scan):
        results = await scan_all_interfaces()

    assert len(results) == 2
    assert results[0].interface == "eth0"
    assert results[0].error
    assert len(results[0].pdus) == 0
    assert results[1].interface == "wlan0"
    assert len(results[1].pdus) == 1


def test_discovered_pdu_interface_field():
    """DiscoveredPDU has interface field."""
    pdu = DiscoveredPDU(host="192.168.1.50", interface="eth0")
    assert pdu.interface == "eth0"
    pdu2 = DiscoveredPDU(host="10.0.0.1")
    assert pdu2.interface == ""


# ---------------------------------------------------------------------------
# Serial port discovery
# ---------------------------------------------------------------------------

class TestDiscoveredSerialPDU:
    def test_dataclass(self):
        pdu = DiscoveredSerialPDU(
            port="/dev/ttyUSB3",
            device_name="PDU44001",
            serial_number="NLKQY7000136",
            model="PDU44001",
            outlet_count=10,
        )
        assert pdu.port == "/dev/ttyUSB3"
        assert pdu.device_name == "PDU44001"
        assert pdu.serial_number == "NLKQY7000136"
        assert pdu.already_configured is False

    def test_defaults(self):
        pdu = DiscoveredSerialPDU(port="/dev/ttyUSB0")
        assert pdu.device_name == ""
        assert pdu.serial_number == ""
        assert pdu.port_by_id == ""
        assert pdu.already_configured is False


class TestEnumerateSerialPorts:
    def test_returns_list(self):
        with patch("src.discovery.glob_mod.glob", return_value=[]):
            ports = enumerate_serial_ports()
        assert isinstance(ports, list)

    def test_finds_ttyusb(self):
        with patch("src.discovery.glob_mod.glob", side_effect=[
            ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB3"],
            [],
        ]):
            ports = enumerate_serial_ports()
        assert ports == ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB3"]

    def test_finds_ttyacm(self):
        with patch("src.discovery.glob_mod.glob", side_effect=[
            [],
            ["/dev/ttyACM0"],
        ]):
            ports = enumerate_serial_ports()
        assert ports == ["/dev/ttyACM0"]

    def test_combines_both(self):
        with patch("src.discovery.glob_mod.glob", side_effect=[
            ["/dev/ttyUSB0"],
            ["/dev/ttyACM0"],
        ]):
            ports = enumerate_serial_ports()
        assert ports == ["/dev/ttyUSB0", "/dev/ttyACM0"]


class TestGetStablePortName:
    def test_no_by_id_dir(self):
        with patch("os.path.isdir", return_value=False):
            result = get_stable_port_name("/dev/ttyUSB0")
        assert result == ""

    def test_finds_stable_name(self):
        with patch("os.path.isdir", return_value=True), \
             patch("os.listdir", return_value=["usb-Digi_Edgeport-port0"]), \
             patch("os.path.realpath", side_effect=lambda p: "/dev/ttyUSB0"):
            result = get_stable_port_name("/dev/ttyUSB0")
        assert "Digi_Edgeport" in result


class TestScanSerialPorts:
    @pytest.mark.asyncio
    async def test_no_ports(self):
        with patch("src.discovery.enumerate_serial_ports", return_value=[]):
            results = await scan_serial_ports()
        assert results == []

    @pytest.mark.asyncio
    async def test_probe_found(self):
        mock_pdu = DiscoveredSerialPDU(
            port="/dev/ttyUSB3",
            device_name="PDU44001",
            model="PDU44001",
            serial_number="NLKQY7000136",
            outlet_count=10,
        )
        with patch("src.discovery.enumerate_serial_ports", return_value=["/dev/ttyUSB3"]), \
             patch("src.discovery.probe_serial_port", new_callable=AsyncMock, return_value=mock_pdu):
            results = await scan_serial_ports()

        assert len(results) == 1
        assert results[0].port == "/dev/ttyUSB3"
        assert results[0].model == "PDU44001"

    @pytest.mark.asyncio
    async def test_already_configured(self):
        mock_pdu = DiscoveredSerialPDU(
            port="/dev/ttyUSB3",
            device_name="PDU44001",
            model="PDU44001",
        )
        with patch("src.discovery.enumerate_serial_ports", return_value=["/dev/ttyUSB3"]), \
             patch("src.discovery.probe_serial_port", new_callable=AsyncMock, return_value=mock_pdu):
            results = await scan_serial_ports(configured_ports={"/dev/ttyUSB3"})

        assert len(results) == 1
        assert results[0].already_configured is True

    @pytest.mark.asyncio
    async def test_probe_failure_skipped(self):
        with patch("src.discovery.enumerate_serial_ports", return_value=["/dev/ttyUSB0"]), \
             patch("src.discovery.probe_serial_port", new_callable=AsyncMock, return_value=None):
            results = await scan_serial_ports()
        assert results == []

    @pytest.mark.asyncio
    async def test_multiple_ports(self):
        pdu1 = DiscoveredSerialPDU(port="/dev/ttyUSB0", device_name="PDU1")
        pdu2 = None  # Not a PDU
        pdu3 = DiscoveredSerialPDU(port="/dev/ttyUSB3", device_name="PDU2")

        async def mock_probe(port, **kw):
            return {"/dev/ttyUSB0": pdu1, "/dev/ttyUSB1": pdu2, "/dev/ttyUSB3": pdu3}.get(port)

        with patch("src.discovery.enumerate_serial_ports",
                    return_value=["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB3"]), \
             patch("src.discovery.probe_serial_port", side_effect=mock_probe):
            results = await scan_serial_ports()

        assert len(results) == 2
        assert results[0].port == "/dev/ttyUSB0"
        assert results[1].port == "/dev/ttyUSB3"
