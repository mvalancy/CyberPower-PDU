# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
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
    _format_table,
    get_local_subnets,
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
