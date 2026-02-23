#!/usr/bin/env python3
"""Try multiple CyberPower PDU serial credentials."""
import serial
import time
import sys

PORT = "/dev/ttyUSB3"
BAUD = 9600

CREDENTIALS = [
    ("admin", "admin"),
    ("admin", ""),
    ("cyber", ""),
    ("admin", "password"),
    ("admin", "cyber"),
    ("cyber", "admin"),
    ("device", "cyber"),
    ("device", "admin"),
    ("readonly", "cyber"),
]


def read_for(ser, seconds=10, stop_on=None, quiet=True):
    buf = b""
    start = time.monotonic()
    old_timeout = ser.timeout
    ser.timeout = 0.5
    try:
        while time.monotonic() - start < seconds:
            chunk = ser.read(256)
            if chunk:
                buf += chunk
                if not quiet:
                    elapsed = time.monotonic() - start
                    text = chunk.decode("utf-8", errors="replace")
                    print(f"    [{elapsed:5.1f}s] {repr(text[:80])}")
                if stop_on:
                    full = buf.decode("utf-8", errors="replace")
                    for m in stop_on:
                        if m in full:
                            return full
    finally:
        ser.timeout = old_timeout
    return buf.decode("utf-8", errors="replace")


def try_credentials(username, password):
    """Try a single username/password combo. Returns True on success."""
    ser = serial.Serial(PORT, BAUD, timeout=5)
    ser.reset_input_buffer()
    time.sleep(0.5)

    try:
        # Check if already at a prompt from previous failed auth
        ser.write(b"\n")
        text = read_for(ser, seconds=3, stop_on=["Login Name", "Login Password", "CyberPower >"])

        if "CyberPower >" in text:
            print(f"  [{username}/{password}] Already logged in!")
            ser.close()
            return True

        # If not at any prompt, trigger login
        if not any(m in text for m in ["Login Name", "Login Password"]):
            ser.write(b"sys show\n")
            text = read_for(ser, seconds=15, stop_on=["Login Name", "Login Password", "CyberPower >"])

        if "Login Name" in text:
            ser.write(f"{username} ".encode())  # username + SPACE
            text = read_for(ser, seconds=20, stop_on=["Login Password", "CyberPower >"])

        if "Login Password" in text:
            if password:
                ser.write(f"{password} ".encode())  # password + SPACE
            else:
                ser.write(b" ")  # just SPACE for empty password
            text = read_for(ser, seconds=25,
                            stop_on=["CyberPower >", "Login Failed", "Login incorrect"])

        if "CyberPower >" in text:
            return True
        return False

    except Exception as e:
        print(f"  Error: {e}")
        return False
    finally:
        ser.close()
        time.sleep(1.0)


def main():
    print(f"=== CyberPower PDU Credential Scan ===")
    print(f"Port: {PORT}, Baud: {BAUD}\n")

    for username, password in CREDENTIALS:
        pw_display = f"'{password}'" if password else "(empty)"
        print(f"  Trying {username} / {pw_display}...", end=" ", flush=True)

        success = try_credentials(username, password)
        if success:
            print(f"SUCCESS!")
            print(f"\n=== VALID CREDENTIALS: {username} / {pw_display} ===")

            # Test commands
            ser = serial.Serial(PORT, BAUD, timeout=5)
            time.sleep(0.5)
            ser.write(b"sys show\n")
            text = read_for(ser, seconds=10, stop_on=["CyberPower >"])
            print(f"\nsys show:\n{text}")
            ser.close()
            return True
        else:
            print("FAILED")

    print(f"\nAll credentials failed. The password may have been changed.")
    print(f"Set PDU_SERIAL_USERNAME and PDU_SERIAL_PASSWORD env vars.")
    return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
