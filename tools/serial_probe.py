#!/usr/bin/env python3
# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Interactive serial probe — exercise every CyberPower CLI command.

Connects to a real PDU via RS-232, runs each known CLI command, captures
raw output, validates parser results, and saves output files as test
fixtures for offline testing.

Usage:
    python3 tools/serial_probe.py /dev/ttyUSB3 admin Cyb3rPDU!
    python3 tools/serial_probe.py /dev/ttyUSB3 cyber cyber
"""

import asyncio
import argparse
import json
import os
import sys
import time
from pathlib import Path

# Add bridge source to path
BRIDGE_DIR = Path(__file__).resolve().parent.parent / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from src.serial_client import SerialClient
from src.serial_parser import (
    build_pdu_data,
    parse_devsta_show,
    parse_oltsta_show,
    parse_srccfg_show,
    parse_sys_show,
)

CAPTURE_DIR = Path(__file__).resolve().parent / "captured_output"


def banner(text: str):
    width = 60
    print(f"\n{'=' * width}")
    print(f"  {text}")
    print(f"{'=' * width}")


def section(text: str):
    print(f"\n--- {text} ---")


def save_capture(name: str, text: str):
    """Save raw CLI output to a file for use as a test fixture."""
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = CAPTURE_DIR / f"{name}.txt"
    path.write_text(text)
    print(f"  Saved: {path}")


def validate_range(label: str, value, min_val, max_val):
    """Check a numeric value is within expected range."""
    if value is None:
        print(f"  WARN: {label} is None")
        return False
    in_range = min_val <= value <= max_val
    status = "OK" if in_range else "FAIL"
    print(f"  [{status}] {label}: {value} (expected {min_val}-{max_val})")
    return in_range


async def probe_sys_show(client: SerialClient) -> dict:
    """Probe 'sys show' and validate identity fields."""
    section("sys show  (DeviceIdentity)")
    text = await client.execute("sys show")
    print(text)
    save_capture("sys_show", text)

    identity = parse_sys_show(text)
    print(f"\n  Parsed:")
    print(f"    Name:     {identity.name}")
    print(f"    Location: {identity.sys_location}")
    print(f"    Model:    {identity.model}")
    print(f"    Firmware: {identity.firmware_main}")
    print(f"    MAC:      {identity.mac_address}")
    print(f"    Serial:   {identity.serial}")
    print(f"    HW Rev:   {identity.hardware_rev}")

    checks = {
        "name": bool(identity.name),
        "model": bool(identity.model),
        "serial": bool(identity.serial),
    }
    for field, ok in checks.items():
        status = "OK" if ok else "WARN"
        print(f"  [{status}] {field} is {'non-empty' if ok else 'EMPTY'}")

    return {"identity": identity, "text": text}


async def probe_devsta_show(client: SerialClient) -> dict:
    """Probe 'devsta show' and validate device status ranges."""
    section("devsta show  (Device Status)")
    text = await client.execute("devsta show")
    print(text)
    save_capture("devsta_show", text)

    result = parse_devsta_show(text)
    print(f"\n  Parsed:")
    for k, v in result.items():
        print(f"    {k}: {v}")

    print(f"\n  Validation:")
    validate_range("Source A Voltage", result["source_a_voltage"], 80, 260)
    validate_range("Source B Voltage", result["source_b_voltage"], 0, 260)
    validate_range("Source A Frequency", result["source_a_frequency"], 49, 62)
    validate_range("Total Load (A)", result["total_load"], 0, 100)
    validate_range("Total Power (W)", result["total_power"], 0, 10000)
    validate_range("Total Energy (kWh)", result["total_energy"], 0, 999999)

    return {"devsta": result, "text": text}


async def probe_oltsta_show(client: SerialClient) -> dict:
    """Probe 'oltsta show' and validate outlet data."""
    section("oltsta show  (Outlet Status)")
    text = await client.execute("oltsta show")
    print(text)
    save_capture("oltsta_show", text)

    outlets = parse_oltsta_show(text)
    print(f"\n  Parsed {len(outlets)} outlets:")
    for n, o in sorted(outlets.items()):
        print(f"    Outlet {n}: name={o.name}, state={o.state}, "
              f"current={o.current}A, power={o.power}W")

    print(f"\n  Validation:")
    ok = len(outlets) > 0
    print(f"  [{'OK' if ok else 'FAIL'}] Outlet count: {len(outlets)}")
    for n, o in outlets.items():
        if o.state not in ("on", "off"):
            print(f"  [WARN] Outlet {n} unexpected state: {o.state}")
        if o.current is not None and o.current < 0:
            print(f"  [FAIL] Outlet {n} negative current: {o.current}")

    return {"outlets": outlets, "text": text}


async def probe_srccfg_show(client: SerialClient) -> dict:
    """Probe 'srccfg show' and validate source config."""
    section("srccfg show  (Source Configuration)")
    text = await client.execute("srccfg show")
    print(text)
    save_capture("srccfg_show", text)

    result = parse_srccfg_show(text)
    print(f"\n  Parsed:")
    for k, v in result.items():
        print(f"    {k}: {v}")

    print(f"\n  Validation:")
    pref = result.get("preferred_source")
    print(f"  [{'OK' if pref in ('A', 'B') else 'WARN'}] Preferred source: {pref}")
    validate_range("Transfer Voltage", result.get("transfer_voltage"), 50, 200)
    validate_range("Voltage Upper Limit", result.get("voltage_upper_limit"), 100, 300)

    return {"srccfg": result, "text": text}


async def probe_new_command(client: SerialClient, cmd: str, name: str) -> str:
    """Probe a new (unparsed) CLI command and save output."""
    section(f"{cmd}  ({name})")
    try:
        text = await client.execute(cmd)
        print(text)
        save_capture(name, text)
        return text
    except Exception as e:
        print(f"  ERROR: {e}")
        save_capture(name, f"ERROR: {e}")
        return ""


async def main():
    parser = argparse.ArgumentParser(
        description="CyberPower PDU Serial Probe — exercise all CLI commands"
    )
    parser.add_argument("port", help="Serial port (e.g., /dev/ttyUSB3)")
    parser.add_argument("username", nargs="?", default="cyber",
                        help="Login username (default: cyber)")
    parser.add_argument("password", nargs="?", default="cyber",
                        help="Login password (default: cyber)")
    parser.add_argument("--baud", type=int, default=9600,
                        help="Baud rate (default: 9600)")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="Command timeout in seconds (default: 10)")
    args = parser.parse_args()

    banner("CyberPower PDU Serial Probe")
    print(f"  Port:     {args.port}")
    print(f"  Username: {args.username}")
    print(f"  Baud:     {args.baud}")
    print(f"  Timeout:  {args.timeout}s")

    client = SerialClient(
        port=args.port,
        username=args.username,
        password=args.password,
        baud=args.baud,
        timeout=args.timeout,
    )

    try:
        banner("Connecting...")
        await client.connect()
        print("  Connected and logged in!")

        results = {}

        # 1. Commands with existing parsers
        banner("Phase 1: Commands with existing parsers")

        r = await probe_sys_show(client)
        results["sys_show"] = r

        r = await probe_devsta_show(client)
        results["devsta_show"] = r

        r = await probe_oltsta_show(client)
        results["oltsta_show"] = r

        r = await probe_srccfg_show(client)
        results["srccfg_show"] = r

        # 2. New commands (no parsers yet)
        banner("Phase 2: New commands (capture raw output)")

        results["oltcfg_show"] = await probe_new_command(
            client, "oltcfg show", "oltcfg_show")
        results["devcfg_show"] = await probe_new_command(
            client, "devcfg show", "devcfg_show")
        results["bankcfg_show"] = await probe_new_command(
            client, "bankcfg show", "bankcfg_show")
        results["oltloadcfg_show"] = await probe_new_command(
            client, "oltloadcfg show", "oltloadcfg_show")
        results["netcfg_show"] = await probe_new_command(
            client, "netcfg show", "netcfg_show")
        results["eventlog_show"] = await probe_new_command(
            client, "eventlog show", "eventlog_show")
        results["usercfg_show"] = await probe_new_command(
            client, "usercfg show", "usercfg_show")

        # 3. Help (capture full command list)
        banner("Phase 3: Help / command listing")
        results["help"] = await probe_new_command(client, "help", "help")

        # 4. Integration test: build_pdu_data
        banner("Integration: build_pdu_data()")
        try:
            devsta = results["devsta_show"]["devsta"]
            outlets = results["oltsta_show"]["outlets"]
            srccfg = results["srccfg_show"]["srccfg"]
            identity = results["sys_show"]["identity"]
            pdu_data = build_pdu_data(devsta, outlets, srccfg, identity)
            print(f"  device_name:     {pdu_data.device_name}")
            print(f"  outlet_count:    {pdu_data.outlet_count}")
            print(f"  input_voltage:   {pdu_data.input_voltage}")
            print(f"  input_frequency: {pdu_data.input_frequency}")
            print(f"  ats_current:     {pdu_data.ats_current_source}")
            print(f"  ats_preferred:   {pdu_data.ats_preferred_source}")
            print(f"  redundancy_ok:   {pdu_data.redundancy_ok}")
            print(f"  banks:           {len(pdu_data.banks)}")
            print(f"  outlets:         {len(pdu_data.outlets)}")
            print("  [OK] build_pdu_data() succeeded")
        except Exception as e:
            print(f"  [FAIL] build_pdu_data() error: {e}")

        # 5. Health check
        banner("Serial Client Health")
        health = client.get_health()
        for k, v in health.items():
            print(f"  {k}: {v}")

        # Summary
        banner("Probe Complete")
        captured = list(CAPTURE_DIR.glob("*.txt"))
        print(f"  Captured {len(captured)} output files in {CAPTURE_DIR}/")
        for f in sorted(captured):
            size = f.stat().st_size
            print(f"    {f.name} ({size} bytes)")

    except Exception as e:
        print(f"\n  FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        client.close()
        print("\n  Serial port closed.")


if __name__ == "__main__":
    asyncio.run(main())
