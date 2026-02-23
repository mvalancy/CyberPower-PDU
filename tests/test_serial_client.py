# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Tests for SerialClient with mocked pyserial."""

import asyncio
import os
import sys
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.serial_client import SerialClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_serial(responses: list[bytes] | None = None):
    """Create a mock serial.Serial that feeds predefined responses."""
    mock = MagicMock()
    mock.is_open = True
    mock.timeout = 5.0

    if responses is None:
        responses = []

    _response_iter = iter(responses)
    _current = [b""]
    _offset = [0]

    def _load_next():
        try:
            _current[0] = next(_response_iter)
            _offset[0] = 0
        except StopIteration:
            _current[0] = b""
            _offset[0] = 0

    _load_next()

    def _read(size=1):
        if _offset[0] >= len(_current[0]):
            _load_next()
        if _offset[0] >= len(_current[0]):
            return b""
        end = min(_offset[0] + size, len(_current[0]))
        chunk = _current[0][_offset[0]:end]
        _offset[0] = end
        return chunk

    mock.read = _read
    mock.write = MagicMock()
    mock.reset_input_buffer = MagicMock()
    mock.close = MagicMock()

    return mock


# ---------------------------------------------------------------------------
# Construction tests
# ---------------------------------------------------------------------------

class TestSerialClientInit:
    def test_default_params(self):
        with patch("src.serial_client.HAS_PYSERIAL", True):
            client = SerialClient(port="/dev/ttyUSB0")
        assert client.port == "/dev/ttyUSB0"
        assert client.consecutive_failures == 0
        assert client.is_connected is False

    def test_custom_params(self):
        with patch("src.serial_client.HAS_PYSERIAL", True):
            client = SerialClient(
                port="/dev/ttyUSB3",
                username="admin",
                password="secret",
                baud=19200,
                timeout=10.0,
            )
        assert client.port == "/dev/ttyUSB3"
        assert client._baud == 19200
        assert client._timeout == 10.0

    def test_no_pyserial_raises(self):
        with patch("src.serial_client.HAS_PYSERIAL", False):
            with pytest.raises(RuntimeError, match="pyserial"):
                SerialClient(port="/dev/ttyUSB0")


# ---------------------------------------------------------------------------
# Health tracking tests
# ---------------------------------------------------------------------------

class TestSerialClientHealth:
    def test_initial_health(self):
        with patch("src.serial_client.HAS_PYSERIAL", True):
            client = SerialClient(port="/dev/ttyUSB0")
        health = client.get_health()
        assert health["port"] == "/dev/ttyUSB0"
        assert health["connected"] is False
        assert health["consecutive_failures"] == 0
        assert health["reachable"] is True

    def test_record_failure_increments(self):
        with patch("src.serial_client.HAS_PYSERIAL", True):
            client = SerialClient(port="/dev/ttyUSB0")
        client._record_failure("test error")
        assert client.consecutive_failures == 1
        assert client._failed_commands == 1
        assert client._last_error_msg == "test error"
        assert client._last_error_time is not None

    def test_record_success_resets(self):
        with patch("src.serial_client.HAS_PYSERIAL", True):
            client = SerialClient(port="/dev/ttyUSB0")
        client._record_failure("err1")
        client._record_failure("err2")
        assert client.consecutive_failures == 2
        client._record_success()
        assert client.consecutive_failures == 0
        assert client._last_success_time is not None

    def test_reset_health(self):
        with patch("src.serial_client.HAS_PYSERIAL", True):
            client = SerialClient(port="/dev/ttyUSB0")
        client._record_failure("err")
        client._record_failure("err")
        client.reset_health()
        assert client.consecutive_failures == 0
        assert client._failed_commands == 0
        assert client._last_error_msg is None

    def test_reachable_threshold(self):
        with patch("src.serial_client.HAS_PYSERIAL", True):
            client = SerialClient(port="/dev/ttyUSB0")
        for _ in range(9):
            client._record_failure("err")
        assert client.get_health()["reachable"] is True
        client._record_failure("err")
        assert client.get_health()["reachable"] is False


# ---------------------------------------------------------------------------
# Connection tests
# ---------------------------------------------------------------------------

class TestSerialClientConnect:
    @pytest.mark.asyncio
    async def test_connect_already_at_prompt(self):
        """When serial port is already at the CLI prompt, login succeeds."""
        mock_serial = _make_mock_serial([
            b"CyberPower > ",
        ])

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(port="/dev/ttyUSB0", timeout=1.0)
            await client.connect()

        assert client._logged_in is True
        assert client.is_connected is True

    @pytest.mark.asyncio
    async def test_connect_full_login_flow(self):
        """Full login: wakeup -> Login Name -> Password -> Prompt."""
        mock_serial = _make_mock_serial([
            b"Login Name:",
            b"Login Password:",
            b"\r\nCyberPower > ",
        ])

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(
                port="/dev/ttyUSB0",
                username="admin",
                password="Cyb3rPDU!",
                timeout=1.0,
            )
            await client.connect()

        assert client._logged_in is True
        # Verify username and password were sent
        calls = mock_serial.write.call_args_list
        sent = [c[0][0] for c in calls]
        assert any(b"admin" in s for s in sent)
        assert any(b"Cyb3rPDU!" in s for s in sent)

    @pytest.mark.asyncio
    async def test_login_uses_space_as_submit_key(self):
        """CyberPower PDU44001 uses SPACE (0x20) as submit for credentials, not CR/LF."""
        mock_serial = _make_mock_serial([
            b"Login Name:",
            b"Login Password:",
            b"\r\nCyberPower > ",
        ])

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(
                port="/dev/ttyUSB0",
                username="admin",
                password="secret",
                timeout=1.0,
            )
            await client.connect()

        # Verify credentials sent with SPACE terminator (not \r\n)
        calls = mock_serial.write.call_args_list
        sent = [c[0][0] for c in calls]
        # Username should be "admin " (with trailing space)
        assert b"admin " in sent
        # Password should be "secret " (with trailing space)
        assert b"secret " in sent
        # No \r\n should appear in credential sends
        assert b"admin\r\n" not in sent
        assert b"secret\r\n" not in sent

    @pytest.mark.asyncio
    async def test_login_trigger_uses_newline(self):
        """Login trigger command uses \\n (not \\r\\n) as terminator."""
        # Simulate: first _read_until_any_sync times out with no markers,
        # then after trigger, Login Name/Password/Prompt follow.
        # The first _read_until_any_sync with timeout=1.0 and ser.timeout=0.1
        # makes ~10 reads. Write calls reset the phase to deliver login data.
        phase = [0]  # 0=no data, 1=login flow
        responses = iter([
            b"Login Name:",
            b"Login Password:",
            b"\r\nCyberPower > ",
        ])

        mock_serial = MagicMock()
        mock_serial.is_open = True
        mock_serial.timeout = 5.0

        def _read(size=1):
            if phase[0] == 0:
                return b""
            try:
                return next(responses)
            except StopIteration:
                return b""

        def _write(data):
            # After "sys show\n" is sent, switch to login flow phase
            if b"sys show" in data:
                phase[0] = 1

        mock_serial.read = _read
        mock_serial.write = MagicMock(side_effect=_write)
        mock_serial.reset_input_buffer = MagicMock()
        mock_serial.close = MagicMock()

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(
                port="/dev/ttyUSB0",
                username="admin",
                password="pass",
                timeout=0.3,  # Short timeout to speed up test
            )
            await client.connect()

        calls = mock_serial.write.call_args_list
        sent = [c[0][0] for c in calls]
        # Trigger should use \n, not \r\n
        assert b"sys show\n" in sent
        assert b"sys show\r\n" not in sent

    @pytest.mark.asyncio
    async def test_login_handles_auth_wait(self):
        """Login handles 'Please wait for authentication....' phase."""
        mock_serial = _make_mock_serial([
            b"Login Name:",
            b"Login Password:",
            b"Please wait for authentication....",
            b"\r\nCyberPower > ",
        ])

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(
                port="/dev/ttyUSB0",
                username="admin",
                password="pass",
                timeout=1.0,
            )
            await client.connect()

        assert client._logged_in is True

    @pytest.mark.asyncio
    async def test_connect_bad_credentials(self):
        """Login failure raises ConnectionError."""
        mock_serial = _make_mock_serial([
            b"Login Name:",
            b"Login Password:",
            b"Login incorrect\r\nLogin Name:",
        ])

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(
                port="/dev/ttyUSB0",
                username="admin",
                password="wrong",
                timeout=1.0,
            )
            with pytest.raises(ConnectionError, match="invalid credentials"):
                await client.connect()


# ---------------------------------------------------------------------------
# Command execution tests
# ---------------------------------------------------------------------------

class TestSerialClientExecute:
    @pytest.mark.asyncio
    async def test_execute_simple_command(self):
        """Execute a command and return the response text."""
        mock_serial = _make_mock_serial([
            # connect: already at prompt
            b"CyberPower > ",
            # execute: command response
            b"devsta show\r\nActive Source   : A\r\nCyberPower > ",
        ])

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(port="/dev/ttyUSB0", timeout=1.0)
            await client.connect()
            result = await client.execute("devsta show")

        assert "Active Source" in result
        assert client._total_commands == 1
        assert client.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_execute_uses_newline_terminator(self):
        """Commands sent via execute() use \\n terminator (not \\r\\n)."""
        mock_serial = _make_mock_serial([
            b"CyberPower > ",
            b"devsta show\r\nActive Source   : A\r\nCyberPower > ",
        ])

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(port="/dev/ttyUSB0", timeout=1.0)
            await client.connect()
            mock_serial.write.reset_mock()
            await client.execute("devsta show")

        calls = mock_serial.write.call_args_list
        sent = [c[0][0] for c in calls]
        assert b"devsta show\n" in sent
        assert b"devsta show\r\n" not in sent

    @pytest.mark.asyncio
    async def test_execute_tracks_duration(self):
        """Execute records command duration."""
        mock_serial = _make_mock_serial([
            b"CyberPower > ",
            b"OK\r\nCyberPower > ",
        ])

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(port="/dev/ttyUSB0", timeout=1.0)
            await client.connect()
            await client.execute("test")

        assert client._last_command_duration is not None
        assert client._last_command_duration >= 0


# ---------------------------------------------------------------------------
# Close tests
# ---------------------------------------------------------------------------

class TestSerialClientClose:
    def test_close(self):
        with patch("src.serial_client.HAS_PYSERIAL", True):
            client = SerialClient(port="/dev/ttyUSB0")
        mock_serial = MagicMock()
        mock_serial.is_open = True
        client._serial = mock_serial

        client.close()
        mock_serial.close.assert_called_once()
        assert client._serial is None
        assert client._logged_in is False

    def test_close_already_closed(self):
        with patch("src.serial_client.HAS_PYSERIAL", True):
            client = SerialClient(port="/dev/ttyUSB0")
        client.close()  # No serial object — should not raise
        assert client._serial is None

    def test_close_error_suppressed(self):
        with patch("src.serial_client.HAS_PYSERIAL", True):
            client = SerialClient(port="/dev/ttyUSB0")
        mock_serial = MagicMock()
        mock_serial.is_open = True
        mock_serial.close.side_effect = OSError("device gone")
        client._serial = mock_serial
        client.close()  # Should not raise
        assert client._serial is None


# ---------------------------------------------------------------------------
# Interactive command tests
# ---------------------------------------------------------------------------

class TestSerialClientInteractive:
    @pytest.mark.asyncio
    async def test_execute_interactive_default_terminator(self):
        """2-tuple exchanges use \\n as default terminator."""
        mock_serial = _make_mock_serial([
            b"CyberPower > ",                  # connect
            b"New Password:",                   # after CLI command
            b"Confirm Password:",               # after password
            b"CyberPower > ",                   # after confirm
        ])

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(port="/dev/ttyUSB0", timeout=1.0)
            await client.connect()
            mock_serial.write.reset_mock()

            await client.execute_interactive([
                ("usercfg admin password", "New Password:"),
                ("newpass", "Confirm Password:"),
                ("newpass", "CyberPower >"),
            ])

        calls = mock_serial.write.call_args_list
        sent = [c[0][0] for c in calls]
        # All three exchanges should use \n (default)
        assert b"usercfg admin password\n" in sent
        assert b"newpass\n" in sent

    @pytest.mark.asyncio
    async def test_execute_interactive_custom_terminator(self):
        """3-tuple exchanges use the specified terminator."""
        mock_serial = _make_mock_serial([
            b"CyberPower > ",                  # connect
            b"New Password:",                   # after CLI command
            b"Confirm Password:",               # after password with SPACE
            b"CyberPower > ",                   # after confirm with SPACE
        ])

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(port="/dev/ttyUSB0", timeout=1.0)
            await client.connect()
            mock_serial.write.reset_mock()

            await client.execute_interactive([
                ("usercfg admin password", "New Password:"),           # \n default
                ("newpass", "Confirm Password:", " "),                  # SPACE
                ("newpass", "CyberPower >", " "),                      # SPACE
            ])

        calls = mock_serial.write.call_args_list
        sent = [c[0][0] for c in calls]
        # First exchange: CLI command with \n
        assert b"usercfg admin password\n" in sent
        # Second and third: password with SPACE
        assert b"newpass " in sent
        # Should NOT have \n for password exchanges
        password_writes = [s for s in sent if s.startswith(b"newpass")]
        assert b"newpass " in password_writes
        assert b"newpass\n" not in password_writes

    @pytest.mark.asyncio
    async def test_execute_interactive_mixed_terminators(self):
        """Mix of 2-tuple (default \\n) and 3-tuple (custom) exchanges."""
        mock_serial = _make_mock_serial([
            b"CyberPower > ",                  # connect
            b"prompt1",                         # after cmd1
            b"prompt2",                         # after cmd2
            b"CyberPower > ",                   # after cmd3
        ])

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(port="/dev/ttyUSB0", timeout=1.0)
            await client.connect()
            mock_serial.write.reset_mock()

            await client.execute_interactive([
                ("cmd1", "prompt1"),               # 2-tuple: \n default
                ("val2", "prompt2", " "),           # 3-tuple: SPACE
                ("val3", "CyberPower >"),           # 2-tuple: \n default
            ])

        calls = mock_serial.write.call_args_list
        sent = [c[0][0] for c in calls]
        assert b"cmd1\n" in sent        # default \n
        assert b"val2 " in sent          # SPACE terminator
        assert b"val3\n" in sent         # default \n

    @pytest.mark.asyncio
    async def test_execute_interactive_health_tracking(self):
        """Interactive commands track health like regular execute."""
        mock_serial = _make_mock_serial([
            b"CyberPower > ",                  # connect
            b"prompt1",                         # exchange 1
            b"CyberPower > ",                   # exchange 2
        ])

        with patch("src.serial_client.HAS_PYSERIAL", True), \
             patch("src.serial_client.serial") as mock_serial_mod:
            mock_serial_mod.Serial.return_value = mock_serial
            client = SerialClient(port="/dev/ttyUSB0", timeout=1.0)
            await client.connect()

            await client.execute_interactive([
                ("cmd", "prompt1"),
                ("val", "CyberPower >"),
            ])

        assert client._total_commands == 1
        assert client.consecutive_failures == 0
        assert client._last_command_duration is not None
