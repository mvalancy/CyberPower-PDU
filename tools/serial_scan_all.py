#!/usr/bin/env python3
"""Scan all USB serial ports to find the CyberPower PDU console.

Sends a command and checks for CyberPower-specific responses.
Also tests different flow control and DTR/RTS configurations.
"""
import serial
import time
import sys
import glob


def read_all(ser, timeout=3):
    """Read all available data until timeout."""
    buf = b""
    start = time.monotonic()
    old_timeout = ser.timeout
    ser.timeout = 0.3
    try:
        while time.monotonic() - start < timeout:
            chunk = ser.read(256)
            if chunk:
                buf += chunk
                start = time.monotonic()
    finally:
        ser.timeout = old_timeout
    return buf


def test_port(port, baud=9600, rtscts=False, xonxoff=False):
    """Test a single port with given settings."""
    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=5,
            rtscts=rtscts,
            xonxoff=xonxoff,
        )
    except Exception as e:
        return f"OPEN FAILED: {e}"

    try:
        ser.reset_input_buffer()
        time.sleep(0.3)

        # Read any pending data
        pending = read_all(ser, timeout=1)

        # Send CR to wake
        ser.write(b"\r\n")
        time.sleep(0.5)

        # Read response
        resp = read_all(ser, timeout=3)
        text = resp.decode("utf-8", errors="replace")

        # Check for CyberPower markers
        markers = ["CyberPower", "Login Name", "Login Password", ">"]
        for m in markers:
            if m in text:
                return f"MATCH '{m}': {repr(text[:100])}"

        # Check for pure * responses (possible locked password prompt)
        if text and all(c in "*\r\n " for c in text):
            return f"STARS: {repr(text[:50])}"

        if text.strip():
            return f"DATA: {repr(text[:100])}"

        if pending:
            pending_text = pending.decode("utf-8", errors="replace")
            return f"PENDING: {repr(pending_text[:100])}"

        return "NO RESPONSE"

    finally:
        ser.close()


def main():
    ports = sorted(glob.glob("/dev/ttyUSB*"))
    if not ports:
        print("No /dev/ttyUSB* ports found")
        return

    print(f"=== Scanning {len(ports)} USB Serial Ports ===\n")

    # Test each port at 9600 baud (default for CyberPower)
    print("--- 9600 baud, no flow control ---")
    for port in ports:
        result = test_port(port, baud=9600)
        status = "✓" if "MATCH" in result else "★" if "STARS" in result else "·"
        print(f"  {status} {port}: {result}")

    # Test ports that responded with stars using RTS/CTS flow control
    print("\n--- 9600 baud, RTS/CTS flow control ---")
    for port in ports:
        result = test_port(port, baud=9600, rtscts=True)
        status = "✓" if "MATCH" in result else "★" if "STARS" in result else "·"
        print(f"  {status} {port}: {result}")

    # Try 115200 baud
    print("\n--- 115200 baud ---")
    for port in ports:
        result = test_port(port, baud=115200)
        status = "✓" if "MATCH" in result else "★" if "STARS" in result else "·"
        print(f"  {status} {port}: {result}")

    # Detailed probe on star-responding ports
    print("\n--- Detailed probe on star-responding ports ---")
    for port in ports:
        try:
            ser = serial.Serial(port, 9600, timeout=5)
            ser.reset_input_buffer()
            time.sleep(0.3)

            # Send various strings and log raw hex
            tests = [
                ("\\r", b"\r"),
                ("\\n", b"\n"),
                ("\\r\\n", b"\r\n"),
                ("test\\r\\n", b"test\r\n"),
                ("space", b" "),
            ]

            results = []
            for name, data in tests:
                ser.reset_input_buffer()
                time.sleep(0.2)
                ser.write(data)
                time.sleep(0.5)
                resp = read_all(ser, timeout=1)
                hex_str = " ".join(f"{b:02x}" for b in resp)
                results.append(f"{name}→[{hex_str}]")

            ser.close()

            # Only print if we got stars
            has_stars = any("2a" in r for r in results)
            if has_stars:
                print(f"  {port}: {', '.join(results)}")

        except Exception as e:
            print(f"  {port}: ERROR {e}")


if __name__ == "__main__":
    main()
