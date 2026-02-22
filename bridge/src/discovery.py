# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""Network discovery — scan subnets for CyberPower PDUs via SNMP.

Uses pysnmp-lextudio (already a dependency). No new packages required.
Works across the CyberPower product family by probing the ePDU MIB base OID.
"""

import argparse
import asyncio
import ipaddress
import logging
import socket
import struct
import sys
from dataclasses import dataclass

from .pdu_model import (
    BASE_OID,
    OID_DEVICE_NAME,
    OID_MODEL,
    OID_OUTLET_COUNT,
    OID_SERIAL_HW,
)

logger = logging.getLogger(__name__)

# Attempt to import pysnmp — fail gracefully if missing
try:
    from pysnmp.hlapi.asyncio import (
        CommunityData,
        ContextData,
        ObjectIdentity,
        ObjectType,
        SnmpEngine,
        UdpTransportTarget,
        getCmd,
    )
    HAS_PYSNMP = True
except ImportError:
    HAS_PYSNMP = False


@dataclass
class DiscoveredPDU:
    """A CyberPower PDU found on the network."""
    host: str
    device_name: str = ""
    serial: str = ""
    model: str = ""
    outlet_count: int = 0
    already_configured: bool = False


def get_local_subnets() -> list[str]:
    """Auto-detect local subnets from network interfaces."""
    subnets = []
    try:
        # Use socket to find local IPs
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Connect to a public IP to find our local interface
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        finally:
            s.close()

        # Assume /24 for the detected interface
        network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
        subnets.append(str(network))
    except Exception:
        pass

    # Also try common private subnets if nothing found
    if not subnets:
        subnets = ["192.168.1.0/24"]

    return subnets


async def _probe_host(engine: "SnmpEngine", host: str, community: str,
                      port: int, timeout: float) -> DiscoveredPDU | None:
    """Probe a single host for CyberPower PDU identity OIDs."""
    try:
        target = UdpTransportTarget(
            (host, port), timeout=timeout, retries=0,
        )
        comm = CommunityData(community)

        # Try to read device name — this is the quickest indicator
        oids_to_try = [
            OID_DEVICE_NAME,
            OID_MODEL,
            OID_SERIAL_HW,
            OID_OUTLET_COUNT,
        ]

        error_indication, error_status, error_index, var_binds = await getCmd(
            engine, comm, target, ContextData(),
            *[ObjectType(ObjectIdentity(oid)) for oid in oids_to_try],
        )

        if error_indication or error_status:
            return None

        values = {}
        for _oid, val in var_binds:
            oid_str = str(_oid)
            # Check if we got a real value (not noSuchObject/noSuchInstance)
            val_str = str(val)
            if "noSuch" in val_str or "No Such" in val_str:
                continue
            values[oid_str] = val_str

        # Must have at least a device name to count as a CyberPower PDU
        device_name = values.get(OID_DEVICE_NAME, "")
        if not device_name:
            return None

        model = values.get(OID_MODEL, "")
        serial = values.get(OID_SERIAL_HW, "")
        outlet_count = 0
        try:
            outlet_count = int(values.get(OID_OUTLET_COUNT, "0"))
        except (ValueError, TypeError):
            pass

        return DiscoveredPDU(
            host=host,
            device_name=device_name,
            serial=serial,
            model=model,
            outlet_count=outlet_count,
        )

    except Exception:
        return None


async def scan_subnet(subnet: str, community: str = "public",
                      port: int = 161, timeout: float = 1.0,
                      concurrency: int = 50,
                      configured_hosts: set[str] | None = None,
                      ) -> list[DiscoveredPDU]:
    """Scan a subnet for CyberPower PDUs.

    Args:
        subnet: CIDR notation, e.g., "192.168.1.0/24"
        community: SNMP community string
        port: SNMP port
        timeout: Per-host timeout in seconds
        concurrency: Max concurrent SNMP requests
        configured_hosts: Set of already-configured IP addresses
    """
    if not HAS_PYSNMP:
        raise RuntimeError("pysnmp-lextudio is required for discovery")

    network = ipaddress.IPv4Network(subnet, strict=False)
    hosts = [str(ip) for ip in network.hosts()]

    engine = SnmpEngine()
    semaphore = asyncio.Semaphore(concurrency)
    results: list[DiscoveredPDU] = []

    async def _probe_with_limit(host: str):
        async with semaphore:
            result = await _probe_host(engine, host, community, port, timeout)
            if result:
                if configured_hosts and host in configured_hosts:
                    result.already_configured = True
                results.append(result)

    tasks = [_probe_with_limit(host) for host in hosts]
    await asyncio.gather(*tasks)

    try:
        engine.close_dispatcher()
    except Exception:
        pass

    # Sort by IP address
    results.sort(key=lambda p: ipaddress.IPv4Address(p.host))
    return results


async def scan_for_serial(serial: str, subnet: str,
                          community: str = "public",
                          port: int = 161,
                          timeout: float = 1.0) -> DiscoveredPDU | None:
    """Scan a subnet for a PDU with a specific serial number.

    Returns the first match or None.  Used by the DHCP recovery system
    to relocate a PDU that changed IP addresses.
    """
    pdus = await scan_subnet(subnet, community=community, port=port,
                             timeout=timeout)
    for pdu in pdus:
        if pdu.serial and pdu.serial == serial:
            return pdu
    return None


def _format_table(pdus: list[DiscoveredPDU]) -> str:
    """Format discovered PDUs as an ASCII table."""
    if not pdus:
        return "  No CyberPower PDUs found."

    lines = []
    # Header
    lines.append(f"  {'Host':<18} {'Name':<25} {'Model':<15} {'Serial':<18} {'Outlets':>7}  {'Status'}")
    lines.append(f"  {'─' * 18} {'─' * 25} {'─' * 15} {'─' * 18} {'─' * 7}  {'─' * 12}")

    for pdu in pdus:
        status = "configured" if pdu.already_configured else "new"
        lines.append(
            f"  {pdu.host:<18} {pdu.device_name:<25} {pdu.model:<15} "
            f"{pdu.serial:<18} {pdu.outlet_count:>7}  {status}"
        )

    return "\n".join(lines)


async def _main_async(args):
    """Async entry point for CLI usage."""
    subnets = [args.subnet] if args.subnet else get_local_subnets()

    all_pdus = []
    for subnet in subnets:
        print(f"Scanning {subnet} (community={args.community}, timeout={args.timeout}s)...")
        pdus = await scan_subnet(
            subnet,
            community=args.community,
            port=args.port,
            timeout=args.timeout,
        )
        all_pdus.extend(pdus)

    print()
    if all_pdus:
        print(f"Found {len(all_pdus)} CyberPower PDU(s):\n")
        print(_format_table(all_pdus))
    else:
        print("No CyberPower PDUs found on the network.")
        print()
        print("Troubleshooting:")
        print("  - Verify the PDU is powered on and connected to the network")
        print("  - Check that the SNMP community string is correct (default: public)")
        print("  - Try specifying the subnet: --subnet 192.168.x.0/24")
        print("  - Ensure UDP port 161 is not blocked by a firewall")

    print()
    return all_pdus


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Discover CyberPower PDUs on the network"
    )
    parser.add_argument("--subnet", help="Subnet to scan (CIDR, e.g., 192.168.1.0/24)")
    parser.add_argument("--community", default="public", help="SNMP community string")
    parser.add_argument("--port", type=int, default=161, help="SNMP port")
    parser.add_argument("--timeout", type=float, default=1.0, help="Per-host timeout")
    args = parser.parse_args()

    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
