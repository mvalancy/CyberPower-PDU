# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""Interactive setup wizard for first-time configuration.

Discovers CyberPower PDUs on the network and helps users create
a pdus.json config file. Works across the CyberPower product family.
"""

import asyncio
import json
import re
import sys
from pathlib import Path

from .discovery import DiscoveredPDU, get_local_subnets, scan_subnet, _format_table
from .pdu_config import PDUConfig, save_pdu_configs, DEFAULT_PDUS_FILE


def _input(prompt: str, default: str = "") -> str:
    """Prompt for input with default value."""
    if default:
        result = input(f"{prompt} [{default}]: ").strip()
        return result if result else default
    return input(f"{prompt}: ").strip()


def _sanitize_device_id(name: str) -> str:
    """Create a safe MQTT topic key from a device name."""
    # Lowercase, replace spaces/special chars with hyphens
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    return s.strip("-") or "pdu"


async def _test_connectivity(pdu: PDUConfig) -> bool:
    """Quick SNMP test to verify a PDU is reachable."""
    try:
        from .snmp_client import SNMPClient
        from .pdu_model import OID_DEVICE_NAME

        client = SNMPClient(pdu_config=pdu)
        result = await client.get(OID_DEVICE_NAME)
        client.close()
        return result is not None
    except Exception:
        return False


async def run_wizard():
    """Main wizard flow."""
    print()
    print("=" * 60)
    print("  CyberPower PDU Bridge — Setup Wizard")
    print("=" * 60)
    print()
    print("This wizard will help you discover CyberPower PDUs on your")
    print("network and create a configuration file.")
    print()

    # Step 1: Determine subnet
    detected_subnets = get_local_subnets()
    default_subnet = detected_subnets[0] if detected_subnets else "192.168.1.0/24"

    print(f"Step 1: Network scan")
    print(f"  Detected local subnet: {default_subnet}")
    subnet = _input("  Subnet to scan", default_subnet)
    community = _input("  SNMP community string", "public")

    # Step 2: Scan
    print()
    print(f"Scanning {subnet}...")
    pdus = await scan_subnet(subnet, community=community, timeout=1.5)

    if not pdus:
        print()
        print("No CyberPower PDUs found on the network.")
        print()
        print("Options:")
        print("  1. Try a different subnet or community string (re-run ./wizard)")
        print("  2. Configure manually — set PDU_HOST in .env")
        print("  3. Try mock mode — set BRIDGE_MOCK_MODE=true in .env")
        return

    # Step 3: Display results
    print()
    print(f"Found {len(pdus)} CyberPower PDU(s):")
    print()
    print(_format_table(pdus))
    print()

    # Step 4: Select PDUs
    if len(pdus) == 1:
        print("Only one PDU found — selecting it automatically.")
        selected = pdus
    else:
        print("Enter the numbers of the PDUs you want to monitor (comma-separated).")
        print("Press Enter for all.")
        for i, pdu in enumerate(pdus, 1):
            print(f"  {i}. {pdu.host} — {pdu.device_name} ({pdu.model})")

        choice = _input("  Select PDUs", ",".join(str(i) for i in range(1, len(pdus) + 1)))
        try:
            indices = [int(x.strip()) - 1 for x in choice.split(",")]
            selected = [pdus[i] for i in indices if 0 <= i < len(pdus)]
        except (ValueError, IndexError):
            selected = pdus

    if not selected:
        print("No PDUs selected. Exiting.")
        return

    # Step 5: Configure each PDU
    print()
    print("Step 2: Configure selected PDUs")
    configs: list[PDUConfig] = []

    for pdu in selected:
        print(f"\n  --- {pdu.host} ({pdu.device_name}) ---")

        # Suggest a device_id based on model + last octet
        suggested_id = _sanitize_device_id(pdu.model or pdu.device_name)
        if len(selected) > 1:
            # Add last IP octet for uniqueness
            last_octet = pdu.host.split(".")[-1]
            suggested_id = f"{suggested_id}-{last_octet}"

        device_id = _input("  Device ID (MQTT topic key)", suggested_id)
        label = _input("  Label (human-friendly name)", pdu.device_name)

        config = PDUConfig(
            device_id=device_id,
            host=pdu.host,
            community_read=community,
            community_write=_input("  SNMP write community", "private"),
            label=label,
        )
        configs.append(config)

    # Step 6: Test connectivity
    print()
    print("Step 3: Testing SNMP connectivity")
    all_ok = True
    for config in configs:
        ok = await _test_connectivity(config)
        status = "OK" if ok else "FAILED"
        symbol = "+" if ok else "!"
        print(f"  [{symbol}] {config.host} ({config.device_id}): {status}")
        if not ok:
            all_ok = False

    if not all_ok:
        print()
        print("  Some PDUs failed connectivity test.")
        proceed = _input("  Continue anyway? (y/n)", "y")
        if proceed.lower() != "y":
            print("Exiting.")
            return

    # Step 7: Write config
    print()
    config_path = _input("Config file path", DEFAULT_PDUS_FILE)
    save_pdu_configs(configs, config_path)
    print(f"  Wrote {len(configs)} PDU config(s) to {config_path}")

    # Step 8: Next steps
    print()
    print("=" * 60)
    print("  Setup complete!")
    print("=" * 60)
    print()
    print("Next steps:")
    print("  1. Review the config:  cat " + config_path)
    print("  2. Start the stack:    ./run")
    print("  3. Open the dashboard: http://localhost:8080")
    print()
    if len(configs) > 1:
        print(f"  Monitoring {len(configs)} PDUs — the dashboard will show a")
        print("  device selector to switch between them.")
        print()


def main():
    """CLI entry point."""
    asyncio.run(run_wizard())


if __name__ == "__main__":
    main()
