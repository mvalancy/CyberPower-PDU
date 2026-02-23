# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Serial console client for CyberPower PDUs.

Manages RS-232 connection via pyserial, handles login/auth, command
execution, pagination, and session recovery. Commands are serialized
with an asyncio.Lock since the PDU CLI is single-threaded.

Typical hardware setup: PDU RJ45 console -> DB9 adapter -> USB serial
hub (e.g., Digi Edgeport) -> /dev/ttyUSBN
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import serial
    HAS_PYSERIAL = True
except ImportError:
    HAS_PYSERIAL = False


class SerialClient:
    """Low-level serial session manager for CyberPower PDU CLI.

    Uses pyserial (synchronous) run in an executor for async compatibility.
    Provides health tracking that mirrors SNMPClient's interface.
    """

    PROMPT = "CyberPower > "
    LOGIN_PROMPT = "Login Name"       # matches "Login Name:" and "Login Name :"
    PASSWORD_PROMPT = "Login Password"  # matches "Login Password:" and "Login Password :"
    PAGINATION_PROMPT = "press"  # "press <space> for next page"

    def __init__(
        self,
        port: str,
        username: str = "cyber",
        password: str = "cyber",
        baud: int = 9600,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        timeout: float = 5.0,
    ):
        if not HAS_PYSERIAL:
            raise RuntimeError("pyserial is required for serial transport: pip install pyserial")

        self._port = port
        self._username = username
        self._password = password
        self._baud = baud
        self._bytesize = bytesize
        self._parity = parity
        self._stopbits = stopbits
        self._timeout = timeout

        self._serial: Optional["serial.Serial"] = None
        self._lock = asyncio.Lock()
        self._logged_in = False

        # Health tracking (mirrors SNMPClient)
        self._total_commands = 0
        self._failed_commands = 0
        self._consecutive_failures = 0
        self._last_success_time: float | None = None
        self._last_error_time: float | None = None
        self._last_error_msg: str | None = None
        self._last_command_duration: float | None = None

    @property
    def port(self) -> str:
        return self._port

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def get_health(self) -> dict:
        """Return serial connection health metrics."""
        return {
            "port": self._port,
            "baud": self._baud,
            "connected": self.is_connected,
            "logged_in": self._logged_in,
            "total_commands": self._total_commands,
            "failed_commands": self._failed_commands,
            "consecutive_failures": self._consecutive_failures,
            "last_success": self._last_success_time,
            "last_error": self._last_error_time,
            "last_error_msg": self._last_error_msg,
            "last_command_duration_ms": (
                round(self._last_command_duration * 1000, 1)
                if self._last_command_duration is not None else None
            ),
            "reachable": self._consecutive_failures < 10,
        }

    def reset_health(self) -> None:
        """Zero out failure counters after recovery."""
        self._consecutive_failures = 0
        self._failed_commands = 0
        self._last_error_msg = None
        self._last_error_time = None

    def _record_success(self):
        self._consecutive_failures = 0
        self._last_success_time = time.time()

    def _record_failure(self, msg: str):
        self._failed_commands += 1
        self._consecutive_failures += 1
        self._last_error_time = time.time()
        self._last_error_msg = msg
        if self._consecutive_failures == 1:
            logger.warning("Serial: %s", msg)
        elif self._consecutive_failures <= 5:
            logger.error("Serial: %s (failure %d)", msg, self._consecutive_failures)
        elif self._consecutive_failures % 30 == 0:
            logger.error(
                "Serial: port unreachable for %d consecutive failures — %s",
                self._consecutive_failures, msg,
            )

    async def connect(self) -> None:
        """Open serial port and login."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._connect_sync)

    def _connect_sync(self) -> None:
        """Synchronous connect + login."""
        if self._serial and self._serial.is_open:
            self._serial.close()

        self._serial = serial.Serial(
            port=self._port,
            baudrate=self._baud,
            bytesize=self._bytesize,
            parity=self._parity,
            stopbits=self._stopbits,
            timeout=self._timeout,
        )
        self._logged_in = False
        logger.info("Serial: opened %s at %d baud", self._port, self._baud)

        self._login_sync()

    def _login_sync(self) -> None:
        """Handle the login sequence synchronously.

        CyberPower PDU serial console protocol (validated on PDU44001):
        - SPACE (0x20) is the submit/enter key for Login Name and Login Password
        - \\n is the command terminator for CLI commands and login trigger
        - \\r is treated as a regular input character (echoed as * at password prompt)
        - Auth processing takes 15-20 seconds ("Please wait for authentication....")
        - Login flow: sys show\\n → Login Name : → username SPACE → Login Password : → password SPACE → auth
        - Some firmware skips straight to "Login Password :" if username is cached
        """
        ser = self._serial
        if not ser or not ser.is_open:
            raise ConnectionError("Serial port not open")

        auth_timeout = max(self._timeout, 30.0)  # CyberPower auth takes 15-20s

        # Send a newline to check current state (don't use \r — it's treated as input)
        ser.write(b"\n")
        time.sleep(0.5)

        # Read whatever is waiting
        response = self._read_until_any_sync(
            [self.PROMPT, self.LOGIN_PROMPT, self.PASSWORD_PROMPT],
            timeout=self._timeout,
        )

        # If no login prompt appeared, send a recognized command to trigger it.
        # CyberPower PDUs only show the login prompt when auth is required.
        if not any(m in response for m in [self.PROMPT, self.LOGIN_PROMPT, self.PASSWORD_PROMPT]):
            ser.write(b"sys show\n")
            response = self._read_until_any_sync(
                [self.PROMPT, self.LOGIN_PROMPT, self.PASSWORD_PROMPT],
                timeout=auth_timeout,
            )

        if self.PROMPT in response:
            # Already logged in
            self._logged_in = True
            logger.info("Serial: already at CLI prompt")
            return

        if self.LOGIN_PROMPT in response:
            # Send username terminated by SPACE (the CyberPower submit key)
            ser.write(f"{self._username} ".encode())
            response = self._read_until_any_sync(
                [self.PASSWORD_PROMPT, self.PROMPT],
                timeout=auth_timeout,
            )

        if self.PASSWORD_PROMPT in response:
            # Send password terminated by SPACE (the CyberPower submit key)
            ser.write(f"{self._password} ".encode())
            response = self._read_until_any_sync(
                [self.PROMPT, "Login Failed", "Login incorrect",
                 "Please wait", self.LOGIN_PROMPT],
                timeout=auth_timeout,
            )

            # If we got "Please wait for authentication...." wait for final result
            if "Please wait" in response and self.PROMPT not in response:
                response += self._read_until_any_sync(
                    [self.PROMPT, "Login Failed", "Login incorrect", self.LOGIN_PROMPT],
                    timeout=auth_timeout,
                )

            if "Login Failed" in response or "Login incorrect" in response or self.LOGIN_PROMPT in response:
                raise ConnectionError("Serial: login failed — invalid credentials")

        if self.PROMPT in response:
            self._logged_in = True
            logger.info("Serial: logged in as %s", self._username)
        else:
            raise ConnectionError(
                f"Serial: unexpected response after login: {response[-200:]}"
            )

    def _read_until_any_sync(self, markers: list[str], timeout: float = 5.0) -> str:
        """Read serial data until any marker string is found or timeout."""
        ser = self._serial
        if not ser:
            return ""

        buf = b""
        start = time.monotonic()
        old_timeout = ser.timeout
        ser.timeout = 0.1  # Short read timeout for polling

        try:
            while time.monotonic() - start < timeout:
                chunk = ser.read(256)
                if chunk:
                    buf += chunk
                    text = buf.decode("utf-8", errors="replace")
                    for marker in markers:
                        if marker in text:
                            return text
                elif not chunk and buf:
                    # No new data but we have something — check one more time
                    text = buf.decode("utf-8", errors="replace")
                    for marker in markers:
                        if marker in text:
                            return text
        finally:
            ser.timeout = old_timeout

        return buf.decode("utf-8", errors="replace")

    async def execute(self, command: str) -> str:
        """Send a CLI command and return the text response.

        Serialized via asyncio.Lock (CLI is single-threaded).
        Handles pagination automatically.
        """
        async with self._lock:
            self._total_commands += 1
            start = time.monotonic()
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, self._execute_sync, command,
                )
                self._last_command_duration = time.monotonic() - start
                self._record_success()
                return result
            except Exception as e:
                self._last_command_duration = time.monotonic() - start
                self._record_failure(f"execute '{command}': {e}")
                # Try re-login on session timeout
                if "not open" in str(e).lower() or "login" in str(e).lower():
                    self._logged_in = False
                    try:
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, self._connect_sync)
                        result = await loop.run_in_executor(
                            None, self._execute_sync, command,
                        )
                        self._record_success()
                        return result
                    except Exception as retry_err:
                        self._record_failure(f"retry '{command}': {retry_err}")
                        raise
                raise

    async def execute_interactive(
        self, exchanges: list[tuple[str, str] | tuple[str, str, str]],
    ) -> str:
        """Execute an interactive command sequence.

        exchanges: [(send_text, wait_for_prompt), ...] or
                   [(send_text, wait_for_prompt, terminator), ...]
        Default terminator is "\\n" for CLI commands.
        Use " " (SPACE) for password/credential sub-prompts on CyberPower PDUs.

        Returns the full captured output.
        """
        async with self._lock:
            self._total_commands += 1
            start = time.monotonic()
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, self._execute_interactive_sync, exchanges,
                )
                self._last_command_duration = time.monotonic() - start
                self._record_success()
                return result
            except Exception as e:
                self._last_command_duration = time.monotonic() - start
                self._record_failure(f"interactive command: {e}")
                raise

    def _execute_interactive_sync(
        self, exchanges: list[tuple[str, str] | tuple[str, str, str]],
    ) -> str:
        """Synchronous interactive command execution."""
        ser = self._serial
        if not ser or not ser.is_open:
            raise ConnectionError("Serial port not open")
        if not self._logged_in:
            self._login_sync()

        ser.reset_input_buffer()
        full_output = ""

        for exchange in exchanges:
            send_text = exchange[0]
            wait_for = exchange[1]
            terminator = exchange[2] if len(exchange) > 2 else "\n"
            ser.write(f"{send_text}{terminator}".encode())
            response = self._read_until_any_sync(
                [wait_for, self.PROMPT, "error", "Error"],
                timeout=self._timeout,
            )
            full_output += response

        return full_output

    def _execute_sync(self, command: str) -> str:
        """Synchronous command execution."""
        ser = self._serial
        if not ser or not ser.is_open:
            raise ConnectionError("Serial port not open")

        if not self._logged_in:
            self._login_sync()

        # Clear input buffer
        ser.reset_input_buffer()

        # Send command (CyberPower uses \n as command terminator, not \r\n)
        ser.write(f"{command}\n".encode())

        # Read response until prompt
        response = ""
        while True:
            chunk = self._read_until_any_sync(
                [self.PROMPT, self.PAGINATION_PROMPT],
                timeout=self._timeout,
            )
            response += chunk

            # Handle pagination: send space to continue
            if self.PAGINATION_PROMPT in chunk and self.PROMPT not in chunk:
                ser.write(b" ")
                continue

            if self.PROMPT in chunk:
                break

            # Timeout with no markers — break
            if not chunk:
                break

        # Strip the command echo and trailing prompt
        lines = response.splitlines()
        cleaned = []
        for line in lines:
            stripped = line.strip()
            # Skip the echoed command
            if stripped == command:
                continue
            # Skip the prompt
            if stripped.startswith("CyberPower >"):
                continue
            cleaned.append(line)

        return "\n".join(cleaned)

    def close(self) -> None:
        """Close the serial connection."""
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
                logger.info("Serial: closed %s", self._port)
            except Exception:
                logger.debug("Error closing serial port", exc_info=True)
        self._serial = None
        self._logged_in = False
