# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Network and serial port discovery for CyberPower PDUs.

Network: scan subnets via SNMP using pysnmp-lextudio.
Serial: enumerate /dev/ttyUSB* ports and probe via serial CLI.
Works across the CyberPower product family.
"""

import argparse
import asyncio
import glob as glob_mod
import ipaddress
import logging
import os
import socket
import struct
import sys
from dataclasses import dataclass, field

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
    interface: str = ""


@dataclass
class NetworkInterface:
    """A detected network interface with its IPv4 subnet."""
    name: str
    ip: str
    subnet: str


@dataclass
class InterfaceScanResult:
    """Results from scanning a single network interface."""
    interface: str
    subnet: str
    ip: str
    pdus: list[DiscoveredPDU] = field(default_factory=list)
    error: str = ""


def get_all_interfaces() -> list[NetworkInterface]:
    """Enumerate all non-loopback IPv4 network interfaces.

    Reads /proc/net/route + /proc/net/fib_trie on Linux for reliable detection.
    Falls back to socket-based detection on other platforms.
    """
    interfaces = []
    seen_subnets = set()

    # Method 1: Parse /proc/net/if_inet6 is IPv6-only, so use /proc/net/route
    # to find interfaces, then read their IPs via SIOCGIFADDR ioctl.
    try:
        import fcntl
        import array

        # Get list of interface names from /proc/net/route
        iface_names = set()
        try:
            with open("/proc/net/route") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 1 and parts[0] != "Iface":
                        iface_names.add(parts[0])
        except (FileNotFoundError, PermissionError):
            pass

        # Also try to get interface list from SIOCGIFCONF
        if not iface_names:
            # Fallback: use SIOCGIFCONF ioctl
            SIOCGIFCONF = 0x8912
            buf = array.array('B', b'\0' * 4096)
            addr, length = buf.buffer_info()
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                result = fcntl.ioctl(s.fileno(), SIOCGIFCONF,
                                     struct.pack('iL', 4096, addr))
                outbytes = struct.unpack('iL', result)[0]
                data = buf.tobytes()[:outbytes]
                offset = 0
                while offset < len(data):
                    name = data[offset:offset + 16].split(b'\0')[0].decode('utf-8', errors='ignore')
                    if name:
                        iface_names.add(name)
                    offset += 40  # struct ifreq is 40 bytes
            finally:
                s.close()

        SIOCGIFADDR = 0x8915
        SIOCGIFNETMASK = 0x891b

        for iface in sorted(iface_names):
            # Skip loopback and Docker virtual bridges
            if iface == "lo" or iface.startswith("docker") or iface.startswith("br-"):
                continue
            # Also skip veth (container pairs) and virbr (libvirt)
            if iface.startswith("veth") or iface.startswith("virbr"):
                continue

            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    # Get IP address
                    ip_result = fcntl.ioctl(
                        s.fileno(), SIOCGIFADDR,
                        struct.pack('256s', iface.encode('utf-8')[:15]),
                    )
                    ip_addr = socket.inet_ntoa(ip_result[20:24])

                    # Get netmask
                    mask_result = fcntl.ioctl(
                        s.fileno(), SIOCGIFNETMASK,
                        struct.pack('256s', iface.encode('utf-8')[:15]),
                    )
                    netmask = socket.inet_ntoa(mask_result[20:24])
                finally:
                    s.close()

                network = ipaddress.IPv4Network(f"{ip_addr}/{netmask}", strict=False)
                subnet = str(network)

                # Skip link-local (169.254.x.x) and loopback ranges
                net_addr = network.network_address
                if net_addr.is_loopback or net_addr.is_link_local:
                    continue

                # Deduplicate subnets (e.g., two interfaces on same network)
                if subnet not in seen_subnets:
                    seen_subnets.add(subnet)
                    interfaces.append(NetworkInterface(
                        name=iface, ip=ip_addr, subnet=subnet,
                    ))

            except (OSError, struct.error):
                # Interface has no IPv4 address or is down
                continue

    except ImportError:
        # Not on Linux (no fcntl) — fall back to socket method
        pass

    # Method 2: Fallback — use the default-route socket trick
    if not interfaces:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            finally:
                s.close()
            network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
            interfaces.append(NetworkInterface(
                name="default", ip=local_ip, subnet=str(network),
            ))
        except Exception:
            pass

    # Last resort — a reasonable default
    if not interfaces:
        interfaces.append(NetworkInterface(
            name="unknown", ip="192.168.1.1", subnet="192.168.1.0/24",
        ))

    return interfaces


def get_local_subnets() -> list[str]:
    """Auto-detect local subnets from network interfaces.

    Backward-compatible wrapper around get_all_interfaces().
    """
    return [iface.subnet for iface in get_all_interfaces()]


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


async def scan_all_interfaces(
    community: str = "public",
    port: int = 161,
    timeout: float = 1.0,
    concurrency: int = 50,
    configured_hosts: set[str] | None = None,
) -> list[InterfaceScanResult]:
    """Scan all detected network interfaces for CyberPower PDUs.

    Returns one InterfaceScanResult per interface, each containing
    the PDUs found on that interface's subnet.  Already-configured
    PDUs are flagged but scanning always continues through all interfaces.
    """
    interfaces = get_all_interfaces()
    results: list[InterfaceScanResult] = []

    for iface in interfaces:
        result = InterfaceScanResult(
            interface=iface.name,
            subnet=iface.subnet,
            ip=iface.ip,
        )
        try:
            pdus = await scan_subnet(
                iface.subnet,
                community=community,
                port=port,
                timeout=timeout,
                concurrency=concurrency,
                configured_hosts=configured_hosts,
            )
            # Tag each PDU with the interface it was found on
            for pdu in pdus:
                pdu.interface = iface.name
            result.pdus = pdus
        except Exception as e:
            result.error = str(e)
            logger.warning("Scan failed on %s (%s): %s", iface.name, iface.subnet, e)

        results.append(result)

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


# ---------------------------------------------------------------------------
# Serial port discovery
# ---------------------------------------------------------------------------

@dataclass
class DiscoveredSerialPDU:
    """A CyberPower PDU found on a serial port."""
    port: str                       # e.g., "/dev/ttyUSB3"
    port_by_id: str = ""            # Stable name from /dev/serial/by-id/
    device_name: str = ""
    serial_number: str = ""
    model: str = ""
    outlet_count: int = 0
    already_configured: bool = False


def enumerate_serial_ports() -> list[str]:
    """Find all ttyUSB and ttyACM serial ports on the system."""
    ports = []
    for pattern in ["/dev/ttyUSB*", "/dev/ttyACM*"]:
        ports.extend(sorted(glob_mod.glob(pattern)))
    return ports


def get_stable_port_name(port: str) -> str:
    """Resolve a /dev/ttyUSBN path to its /dev/serial/by-id/ stable name.

    Returns empty string if no stable name found.
    """
    by_id_dir = "/dev/serial/by-id"
    if not os.path.isdir(by_id_dir):
        return ""

    try:
        real_port = os.path.realpath(port)
        for entry in os.listdir(by_id_dir):
            entry_path = os.path.join(by_id_dir, entry)
            if os.path.realpath(entry_path) == real_port:
                return entry_path
    except (OSError, PermissionError):
        pass
    return ""


async def probe_serial_port(
    port: str,
    username: str = "admin",
    password: str = "",
    timeout: float = 5.0,
) -> DiscoveredSerialPDU | None:
    """Probe a single serial port for a CyberPower PDU.

    Attempts to connect, login, and run 'sys show' to identify the device.
    Returns None if the port doesn't have a CyberPower PDU.
    """
    try:
        from .serial_client import SerialClient
        from .serial_parser import parse_sys_show, parse_oltsta_show
    except ImportError:
        logger.debug("Serial support not available (missing pyserial)")
        return None

    client = SerialClient(
        port=port,
        username=username,
        password=password,
        timeout=timeout,
    )

    try:
        await client.connect()
        sys_text = await client.execute("sys show")
        identity = parse_sys_show(sys_text)

        if not identity.name and not identity.model:
            # Not a CyberPower PDU
            return None

        # Get outlet count
        outlet_count = 0
        try:
            oltsta_text = await client.execute("oltsta show")
            outlets = parse_oltsta_show(oltsta_text)
            outlet_count = len(outlets)
        except Exception:
            pass

        stable_name = get_stable_port_name(port)

        return DiscoveredSerialPDU(
            port=port,
            port_by_id=stable_name,
            device_name=identity.name,
            serial_number=identity.serial,
            model=identity.model,
            outlet_count=outlet_count,
        )
    except Exception as e:
        logger.debug("Serial probe %s failed: %s", port, e)
        return None
    finally:
        client.close()


async def scan_serial_ports(
    configured_ports: set[str] | None = None,
    username: str = "admin",
    password: str = "",
    timeout: float = 5.0,
) -> list[DiscoveredSerialPDU]:
    """Enumerate and probe all serial ports for CyberPower PDUs.

    Returns a list of discovered PDUs with their port info.
    """
    ports = enumerate_serial_ports()
    if not ports:
        logger.info("No serial ports found to scan")
        return []

    logger.info("Scanning %d serial port(s): %s", len(ports), ", ".join(ports))
    results: list[DiscoveredSerialPDU] = []

    # Probe ports sequentially (each port is exclusive)
    for port in ports:
        result = await probe_serial_port(
            port, username=username, password=password, timeout=timeout,
        )
        if result:
            if configured_ports and port in configured_ports:
                result.already_configured = True
            results.append(result)
            logger.info(
                "Serial: found %s on %s (serial=%s, outlets=%d)",
                result.model or result.device_name,
                port, result.serial_number, result.outlet_count,
            )

    return results


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
