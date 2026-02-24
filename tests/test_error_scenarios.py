# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Error scenario and edge-case tests for config, automation, and history."""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.automation import (
    AutomationEngine,
    AutomationRule,
    RuleState,
    VALID_CONDITIONS,
    VALID_ACTIONS,
    _validate_time_str,
)
from src.config import Config, ConfigError
from src.history import HistoryStore
from src.pdu_model import BankData, OutletData, PDUData, SourceData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_pdu_data(
    bank1_voltage=120.0, bank2_voltage=120.0,
    source_a_voltage=120.0, source_b_voltage=120.0,
    ats_current_source=1, ats_preferred_source=1,
):
    """Create PDUData with controllable voltages and ATS state."""
    return PDUData(
        device_name="Test PDU",
        outlet_count=10,
        phase_count=1,
        input_voltage=bank1_voltage,
        input_frequency=60.0,
        outlets={
            1: OutletData(number=1, name="Outlet 1", state="on",
                          current=0.8, power=50.0, energy=1.5),
            2: OutletData(number=2, name="Outlet 2", state="on",
                          current=0.2, power=24.0, energy=0.3),
        },
        banks={
            1: BankData(number=1, voltage=bank1_voltage, current=5.0,
                        power=100.0, apparent_power=110.0, power_factor=0.91,
                        load_state="normal"),
            2: BankData(number=2, voltage=bank2_voltage, current=3.0,
                        power=80.0, apparent_power=88.0, power_factor=0.91,
                        load_state="normal"),
        },
        source_a=SourceData(
            voltage=source_a_voltage, frequency=60.0,
            voltage_status="normal" if source_a_voltage > 10 else "underVoltage",
        ),
        source_b=SourceData(
            voltage=source_b_voltage, frequency=60.0,
            voltage_status="normal" if source_b_voltage > 10 else "underVoltage",
        ),
        ats_current_source=ats_current_source,
        ats_preferred_source=ats_preferred_source,
        redundancy_ok=(source_a_voltage > 10 and source_b_voltage > 10),
    )


# Track env vars we set so each test cleans up properly
_ENV_KEYS_TO_CLEAN = [
    "PDU_HOST", "PDU_SNMP_PORT", "PDU_COMMUNITY_READ", "PDU_COMMUNITY_WRITE",
    "PDU_DEVICE_ID", "MQTT_BROKER", "MQTT_PORT", "BRIDGE_POLL_INTERVAL",
    "BRIDGE_MOCK_MODE", "BRIDGE_LOG_LEVEL", "BRIDGE_SNMP_TIMEOUT",
    "BRIDGE_SNMP_RETRIES", "BRIDGE_RULES_FILE", "BRIDGE_WEB_PORT",
    "BRIDGE_HISTORY_DB", "HISTORY_RETENTION_DAYS", "HOUSE_MONTHLY_KWH",
    "BRIDGE_OUTLET_NAMES_FILE",
]


@pytest.fixture(autouse=True)
def clean_env():
    """Remove all config-related env vars before and after each test."""
    saved = {}
    for key in _ENV_KEYS_TO_CLEAN:
        if key in os.environ:
            saved[key] = os.environ.pop(key)
    yield
    # Restore original state
    for key in _ENV_KEYS_TO_CLEAN:
        os.environ.pop(key, None)
    for key, val in saved.items():
        os.environ[key] = val


# ===================================================================
# Config validation tests
# ===================================================================


class TestConfigDefaults:
    """Test 1: Default config loads correctly with all expected fields."""

    def test_defaults_load(self):
        cfg = Config()
        assert cfg.pdu_host == "192.168.20.177"
        assert cfg.pdu_snmp_port == 161
        assert cfg.pdu_community_read == "public"
        assert cfg.pdu_community_write == "private"
        assert cfg.device_id == ""
        assert cfg.mqtt_broker == "mosquitto"
        assert cfg.mqtt_port == 1883
        assert cfg.poll_interval == 1.0
        assert cfg.mock_mode is False
        assert cfg.log_level == "INFO"
        assert cfg.snmp_timeout == 2.0
        assert cfg.snmp_retries == 1
        assert cfg.web_port == 8080
        assert cfg.history_retention_days == 60
        assert cfg.house_monthly_kwh == 0.0

    def test_default_types(self):
        cfg = Config()
        assert isinstance(cfg.pdu_snmp_port, int)
        assert isinstance(cfg.mqtt_port, int)
        assert isinstance(cfg.poll_interval, float)
        assert isinstance(cfg.snmp_timeout, float)
        assert isinstance(cfg.snmp_retries, int)
        assert isinstance(cfg.mock_mode, bool)


class TestConfigFromEnv:
    """Test 2: Config reads correctly from environment variables."""

    def test_all_env_vars_applied(self):
        os.environ["PDU_HOST"] = "10.0.0.99"
        os.environ["PDU_SNMP_PORT"] = "1161"
        os.environ["PDU_COMMUNITY_READ"] = "myread"
        os.environ["PDU_COMMUNITY_WRITE"] = "mywrite"
        os.environ["PDU_DEVICE_ID"] = "rack7pdu"
        os.environ["MQTT_BROKER"] = "mqtt.local"
        os.environ["MQTT_PORT"] = "8883"
        os.environ["BRIDGE_POLL_INTERVAL"] = "5.0"
        os.environ["BRIDGE_MOCK_MODE"] = "true"
        os.environ["BRIDGE_LOG_LEVEL"] = "DEBUG"
        os.environ["BRIDGE_SNMP_TIMEOUT"] = "10.0"
        os.environ["BRIDGE_SNMP_RETRIES"] = "3"
        os.environ["BRIDGE_WEB_PORT"] = "9090"
        os.environ["HISTORY_RETENTION_DAYS"] = "30"
        os.environ["HOUSE_MONTHLY_KWH"] = "1200.5"

        cfg = Config()

        assert cfg.pdu_host == "10.0.0.99"
        assert cfg.pdu_snmp_port == 1161
        assert cfg.pdu_community_read == "myread"
        assert cfg.pdu_community_write == "mywrite"
        assert cfg.device_id == "rack7pdu"
        assert cfg.mqtt_broker == "mqtt.local"
        assert cfg.mqtt_port == 8883
        assert cfg.poll_interval == 5.0
        assert cfg.mock_mode is True
        assert cfg.log_level == "DEBUG"
        assert cfg.snmp_timeout == 10.0
        assert cfg.snmp_retries == 3
        assert cfg.web_port == 9090
        assert cfg.history_retention_days == 30
        assert cfg.house_monthly_kwh == pytest.approx(1200.5)


class TestConfigInvalidPort:
    """Test 3: ConfigError on invalid port (non-numeric, out of range)."""

    def test_non_numeric_snmp_port(self):
        os.environ["PDU_SNMP_PORT"] = "abc"
        with pytest.raises(ConfigError, match="not a valid integer"):
            Config()

    def test_non_numeric_mqtt_port(self):
        os.environ["MQTT_PORT"] = "not_a_number"
        with pytest.raises(ConfigError, match="not a valid integer"):
            Config()

    def test_non_numeric_web_port(self):
        os.environ["BRIDGE_WEB_PORT"] = "xyz"
        with pytest.raises(ConfigError, match="not a valid integer"):
            Config()

    def test_port_zero(self):
        os.environ["PDU_SNMP_PORT"] = "0"
        with pytest.raises(ConfigError, match="out of range"):
            Config()

    def test_port_negative(self):
        os.environ["MQTT_PORT"] = "-1"
        with pytest.raises(ConfigError, match="out of range"):
            Config()

    def test_port_above_65535(self):
        os.environ["MQTT_PORT"] = "65536"
        with pytest.raises(ConfigError, match="out of range"):
            Config()

    def test_port_boundary_1(self):
        os.environ["MQTT_PORT"] = "1"
        cfg = Config()
        assert cfg.mqtt_port == 1

    def test_port_boundary_65535(self):
        os.environ["MQTT_PORT"] = "65535"
        cfg = Config()
        assert cfg.mqtt_port == 65535

    def test_float_as_port(self):
        os.environ["PDU_SNMP_PORT"] = "161.5"
        with pytest.raises(ConfigError, match="not a valid integer"):
            Config()

    def test_empty_string_port(self):
        os.environ["PDU_SNMP_PORT"] = ""
        with pytest.raises(ConfigError, match="not a valid integer"):
            Config()


class TestConfigInvalidPollInterval:
    """Test 4: ConfigError on invalid poll interval (0, negative, too high)."""

    def test_zero_poll_interval(self):
        os.environ["BRIDGE_POLL_INTERVAL"] = "0"
        with pytest.raises(ConfigError, match="out of range"):
            Config()

    def test_negative_poll_interval(self):
        os.environ["BRIDGE_POLL_INTERVAL"] = "-1"
        with pytest.raises(ConfigError, match="out of range"):
            Config()

    def test_too_high_poll_interval(self):
        os.environ["BRIDGE_POLL_INTERVAL"] = "301"
        with pytest.raises(ConfigError, match="out of range"):
            Config()

    def test_non_numeric_poll_interval(self):
        os.environ["BRIDGE_POLL_INTERVAL"] = "fast"
        with pytest.raises(ConfigError, match="not a valid number"):
            Config()

    def test_poll_interval_minimum_boundary(self):
        os.environ["BRIDGE_POLL_INTERVAL"] = "0.1"
        cfg = Config()
        assert cfg.poll_interval == pytest.approx(0.1)

    def test_poll_interval_maximum_boundary(self):
        os.environ["BRIDGE_POLL_INTERVAL"] = "300"
        cfg = Config()
        assert cfg.poll_interval == pytest.approx(300.0)

    def test_poll_interval_below_minimum(self):
        os.environ["BRIDGE_POLL_INTERVAL"] = "0.05"
        with pytest.raises(ConfigError, match="out of range"):
            Config()


class TestConfigInvalidDeviceId:
    """Test 5: ConfigError on invalid device_id with MQTT-unsafe characters."""

    @pytest.mark.parametrize("bad_id", [
        "pdu/rack1",      # forward slash
        "pdu#1",          # hash
        "pdu+backup",     # plus
        "pdu rack1",      # space
        "my/pdu#1",       # multiple bad chars
        "a b",            # just a space
        "/",              # single slash
        "#",              # single hash
        "+",              # single plus
        " ",              # single space
    ])
    def test_mqtt_unsafe_device_id(self, bad_id):
        os.environ["PDU_DEVICE_ID"] = bad_id
        with pytest.raises(ConfigError, match="invalid characters"):
            Config()

    @pytest.mark.parametrize("good_id", [
        "pdu44001",
        "rack7-pdu-a",
        "my_pdu.1",
        "PDU_A",
        "server-room-2_pdu",
    ])
    def test_valid_device_id(self, good_id):
        os.environ["PDU_DEVICE_ID"] = good_id
        cfg = Config()
        assert cfg.device_id == good_id


class TestConfigMockMode:
    """Test 6: mock_mode accepts 'true', '1', 'yes' (case insensitive)."""

    @pytest.mark.parametrize("truthy", [
        "true", "True", "TRUE", "tRuE",
        "1",
        "yes", "Yes", "YES", "yEs",
    ])
    def test_mock_mode_truthy(self, truthy):
        os.environ["BRIDGE_MOCK_MODE"] = truthy
        cfg = Config()
        assert cfg.mock_mode is True

    @pytest.mark.parametrize("falsy", [
        "false", "False", "FALSE",
        "0",
        "no", "No",
        "",
        "anything_else",
        "2",
    ])
    def test_mock_mode_falsy(self, falsy):
        os.environ["BRIDGE_MOCK_MODE"] = falsy
        cfg = Config()
        assert cfg.mock_mode is False


class TestConfigSnmpFields:
    """Test 7: snmp_timeout and snmp_retries have valid ranges."""

    def test_snmp_timeout_valid(self):
        os.environ["BRIDGE_SNMP_TIMEOUT"] = "5.0"
        cfg = Config()
        assert cfg.snmp_timeout == 5.0

    def test_snmp_timeout_too_low(self):
        os.environ["BRIDGE_SNMP_TIMEOUT"] = "0.1"
        with pytest.raises(ConfigError, match="out of range"):
            Config()

    def test_snmp_timeout_too_high(self):
        os.environ["BRIDGE_SNMP_TIMEOUT"] = "31"
        with pytest.raises(ConfigError, match="out of range"):
            Config()

    def test_snmp_timeout_minimum_boundary(self):
        os.environ["BRIDGE_SNMP_TIMEOUT"] = "0.5"
        cfg = Config()
        assert cfg.snmp_timeout == pytest.approx(0.5)

    def test_snmp_timeout_maximum_boundary(self):
        os.environ["BRIDGE_SNMP_TIMEOUT"] = "30"
        cfg = Config()
        assert cfg.snmp_timeout == pytest.approx(30.0)

    def test_snmp_timeout_non_numeric(self):
        os.environ["BRIDGE_SNMP_TIMEOUT"] = "slow"
        with pytest.raises(ConfigError, match="not a valid number"):
            Config()

    def test_snmp_retries_valid(self):
        os.environ["BRIDGE_SNMP_RETRIES"] = "3"
        cfg = Config()
        assert cfg.snmp_retries == 3

    def test_snmp_retries_zero(self):
        os.environ["BRIDGE_SNMP_RETRIES"] = "0"
        cfg = Config()
        assert cfg.snmp_retries == 0

    def test_snmp_retries_max(self):
        os.environ["BRIDGE_SNMP_RETRIES"] = "5"
        cfg = Config()
        assert cfg.snmp_retries == 5

    def test_snmp_retries_too_high(self):
        os.environ["BRIDGE_SNMP_RETRIES"] = "6"
        with pytest.raises(ConfigError, match="out of range"):
            Config()

    def test_snmp_retries_negative(self):
        os.environ["BRIDGE_SNMP_RETRIES"] = "-1"
        with pytest.raises(ConfigError, match="out of range"):
            Config()

    def test_snmp_retries_non_numeric(self):
        os.environ["BRIDGE_SNMP_RETRIES"] = "two"
        with pytest.raises(ConfigError, match="not a valid integer"):
            Config()


# ===================================================================
# Automation error scenario tests
# ===================================================================


class TestAutomationCommandCallbackFailure:
    """Test 8: Command callback exception doesn't crash evaluate(), resets state."""

    def _make_engine(self, rules=None, command_callback=None):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        if rules:
            json.dump(rules, tmp)
        tmp.close()
        engine = AutomationEngine(tmp.name, command_callback=command_callback)
        return engine, tmp.name

    @pytest.mark.asyncio
    async def test_callback_exception_no_crash(self):
        """evaluate() continues when command callback raises."""
        async def failing_cmd(outlet, action):
            raise RuntimeError("SNMP timeout")

        engine, path = self._make_engine(command_callback=failing_cmd)
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data(source_a_voltage=0.0)
        # Should not raise
        events = await engine.evaluate(data)

        # Event was created (trigger attempt)
        assert len(events) == 1
        assert events[0]["type"] == "triggered"

        # State was NOT set to triggered — reset for retry
        assert engine._states["r1"].triggered is False
        assert engine._states["r1"].condition_since is None
        assert engine._command_failures == 1

        os.unlink(path)

    @pytest.mark.asyncio
    async def test_callback_failure_allows_retry(self):
        """After callback failure, rule retries on next evaluate cycle."""
        call_count = 0

        async def flaky_cmd(outlet, action):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("First attempt fails")
            # Second attempt succeeds

        engine, path = self._make_engine(command_callback=flaky_cmd)
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data(source_a_voltage=0.0)

        # First attempt — fails
        await engine.evaluate(data)
        assert engine._states["r1"].triggered is False
        assert call_count == 1

        # Second attempt — succeeds
        await engine.evaluate(data)
        assert engine._states["r1"].triggered is True
        assert call_count == 2

        os.unlink(path)

    @pytest.mark.asyncio
    async def test_callback_failure_increments_counter(self):
        """Each callback failure increments _command_failures."""
        async def always_fails(outlet, action):
            raise ConnectionError("Lost connection")

        engine, path = self._make_engine(command_callback=always_fails)
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data(source_a_voltage=0.0)
        for _ in range(3):
            await engine.evaluate(data)

        assert engine._command_failures == 3
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_restore_callback_failure(self):
        """Restore callback failure increments counter but still resets state."""
        calls = []

        async def fail_on_restore(outlet, action):
            calls.append((outlet, action))
            if action == "on":
                raise RuntimeError("Restore failed")

        engine, path = self._make_engine(command_callback=fail_on_restore)
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off",
            "restore": True, "delay": 0,
        })

        # Trigger
        await engine.evaluate(make_pdu_data(source_a_voltage=0.0))
        assert engine._states["r1"].triggered is True

        # Restore — callback fails but state resets anyway
        await engine.evaluate(make_pdu_data(source_a_voltage=120.0))
        assert engine._states["r1"].triggered is False
        assert engine._command_failures == 1
        os.unlink(path)


class TestAutomationInvalidCondition:
    """Test 9: Invalid condition type raises ValueError on from_dict."""

    def test_unknown_condition(self):
        d = {
            "name": "bad", "input": 1, "condition": "temperature_above",
            "threshold": 50, "outlet": 1, "action": "off",
        }
        with pytest.raises(ValueError, match="Unknown condition"):
            AutomationRule.from_dict(d)

    def test_empty_condition(self):
        d = {
            "name": "bad", "input": 1, "condition": "",
            "threshold": 50, "outlet": 1, "action": "off",
        }
        with pytest.raises(ValueError, match="Unknown condition"):
            AutomationRule.from_dict(d)

    def test_invalid_outlet_number(self):
        d = {
            "name": "bad", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 0, "action": "off",
        }
        with pytest.raises(ValueError, match="Outlet must be >= 1"):
            AutomationRule.from_dict(d)

    def test_negative_outlet_number(self):
        d = {
            "name": "bad", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": -1, "action": "off",
        }
        with pytest.raises(ValueError, match="Outlet must be >= 1"):
            AutomationRule.from_dict(d)


class TestAutomationInvalidAction:
    """Test 10: Invalid action raises ValueError on from_dict."""

    def test_unknown_action(self):
        d = {
            "name": "bad", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "reboot",
        }
        with pytest.raises(ValueError, match="Invalid action"):
            AutomationRule.from_dict(d)

    def test_empty_action(self):
        d = {
            "name": "bad", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "",
        }
        with pytest.raises(ValueError, match="Invalid action"):
            AutomationRule.from_dict(d)

    def test_capitalized_action(self):
        """Actions are case-sensitive, 'On' is not valid."""
        d = {
            "name": "bad", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "On",
        }
        with pytest.raises(ValueError, match="Invalid action"):
            AutomationRule.from_dict(d)


class TestAutomationInvalidTimeFormat:
    """Test 11: Invalid time format raises ValueError on from_dict."""

    def test_bad_time_format_no_colon(self):
        d = {
            "name": "bad", "input": 0, "condition": "time_after",
            "threshold": "2200", "outlet": 1, "action": "off",
        }
        with pytest.raises(ValueError, match="Invalid time format"):
            AutomationRule.from_dict(d)

    def test_bad_time_format_extra_colon(self):
        d = {
            "name": "bad", "input": 0, "condition": "time_after",
            "threshold": "22:00:00", "outlet": 1, "action": "off",
        }
        with pytest.raises(ValueError, match="Invalid time format"):
            AutomationRule.from_dict(d)

    def test_bad_time_format_non_numeric(self):
        d = {
            "name": "bad", "input": 0, "condition": "time_after",
            "threshold": "ab:cd", "outlet": 1, "action": "off",
        }
        with pytest.raises(ValueError, match="non-numeric"):
            AutomationRule.from_dict(d)

    def test_bad_time_format_hour_out_of_range(self):
        d = {
            "name": "bad", "input": 0, "condition": "time_before",
            "threshold": "25:00", "outlet": 1, "action": "off",
        }
        with pytest.raises(ValueError, match="Invalid time"):
            AutomationRule.from_dict(d)

    def test_bad_time_format_minute_out_of_range(self):
        d = {
            "name": "bad", "input": 0, "condition": "time_before",
            "threshold": "22:60", "outlet": 1, "action": "off",
        }
        with pytest.raises(ValueError, match="Invalid time"):
            AutomationRule.from_dict(d)

    def test_bad_time_between_format(self):
        """time_between requires exactly two parts separated by '-'."""
        d = {
            "name": "bad", "input": 0, "condition": "time_between",
            "threshold": "22:00", "outlet": 1, "action": "off",
        }
        with pytest.raises(ValueError, match="HH:MM-HH:MM"):
            AutomationRule.from_dict(d)

    def test_bad_time_between_three_parts(self):
        d = {
            "name": "bad", "input": 0, "condition": "time_between",
            "threshold": "22:00-03:00-06:00", "outlet": 1, "action": "off",
        }
        with pytest.raises(ValueError, match="HH:MM-HH:MM"):
            AutomationRule.from_dict(d)

    def test_bad_time_between_invalid_start(self):
        d = {
            "name": "bad", "input": 0, "condition": "time_between",
            "threshold": "25:00-06:00", "outlet": 1, "action": "off",
        }
        with pytest.raises(ValueError, match="Invalid time"):
            AutomationRule.from_dict(d)

    def test_bad_time_between_invalid_end(self):
        d = {
            "name": "bad", "input": 0, "condition": "time_between",
            "threshold": "22:00-24:01", "outlet": 1, "action": "off",
        }
        with pytest.raises(ValueError, match="Invalid time"):
            AutomationRule.from_dict(d)

    def test_validate_time_str_directly(self):
        """Direct tests of the _validate_time_str helper."""
        # Valid
        _validate_time_str("00:00")
        _validate_time_str("23:59")
        _validate_time_str("12:30")

        # Invalid
        with pytest.raises(ValueError):
            _validate_time_str("24:00")
        with pytest.raises(ValueError):
            _validate_time_str("12:60")
        with pytest.raises(ValueError):
            _validate_time_str("noon")


class TestAutomationAtomicSave:
    """Test 12: Atomic file save — writes to temp then renames."""

    def test_save_creates_file(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        tmp.close()
        engine = AutomationEngine(tmp.name)
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off",
        })

        # Verify final file exists and has valid JSON
        data = json.loads(Path(tmp.name).read_text())
        assert len(data) == 1
        assert data[0]["name"] == "r1"

        # Verify no temp file left behind
        tmp_file = Path(tmp.name).with_suffix(".tmp")
        assert not tmp_file.exists()

        os.unlink(tmp.name)

    def test_save_failure_cleans_temp(self):
        """When rename fails, temp file is cleaned up."""
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        tmp.close()
        engine = AutomationEngine(tmp.name)

        # Add a rule to the engine manually (bypass save)
        rule = AutomationRule(
            name="r1", input=1, condition="voltage_below",
            threshold=10.0, outlet=1, action="off",
        )
        engine._rules["r1"] = rule
        engine._states["r1"] = RuleState()

        # Patch rename to fail
        with patch.object(Path, 'rename', side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                engine._save()

        # Temp file should be cleaned up
        tmp_file = Path(tmp.name).with_suffix(".tmp")
        assert not tmp_file.exists()

        os.unlink(tmp.name)

    def test_save_writes_to_temp_first(self):
        """Verify that _save creates a .tmp file which is then renamed."""
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        tmp.close()
        engine = AutomationEngine(tmp.name)

        rule = AutomationRule(
            name="r1", input=1, condition="voltage_below",
            threshold=10.0, outlet=1, action="off",
        )
        engine._rules["r1"] = rule
        engine._states["r1"] = RuleState()

        # After save, the final file should exist with correct content
        # and the .tmp file should NOT exist (was renamed)
        engine._save()

        final_path = Path(tmp.name)
        tmp_path = final_path.with_suffix(".tmp")

        assert final_path.exists()
        assert not tmp_path.exists()

        data = json.loads(final_path.read_text())
        assert len(data) == 1
        assert data[0]["name"] == "r1"

        os.unlink(tmp.name)


class TestAutomationConditionException:
    """Test 13: Condition check exception is caught per-rule, doesn't skip others."""

    def _make_engine(self, command_callback=None):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        tmp.close()
        engine = AutomationEngine(tmp.name, command_callback=command_callback)
        return engine, tmp.name

    @pytest.mark.asyncio
    async def test_exception_in_one_rule_doesnt_block_others(self):
        """If _check_condition raises for one rule, remaining rules still evaluate."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)

        # Create two rules
        engine.create_rule({
            "name": "rule_bad", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 0,
        })
        engine.create_rule({
            "name": "rule_good", "input": 2, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 2, "action": "off", "delay": 0,
        })

        data = make_pdu_data(source_a_voltage=0.0, source_b_voltage=0.0)

        # Patch _check_condition to fail only on first call
        original_check = engine._check_condition
        call_count = 0

        def patched_check(rule, data_arg):
            nonlocal call_count
            call_count += 1
            if rule.name == "rule_bad":
                raise RuntimeError("Simulated condition check error")
            return original_check(rule, data_arg)

        with patch.object(engine, '_check_condition', side_effect=patched_check):
            events = await engine.evaluate(data)

        # rule_good should still have fired
        assert any(e["rule"] == "rule_good" for e in events)
        assert (2, "off") in commands
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_condition_exception_logged_and_skipped(self):
        """Exception in condition check is handled gracefully (no crash)."""
        engine, path = self._make_engine()
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data()

        with patch.object(engine, '_check_condition', side_effect=TypeError("bad")):
            # Should not raise
            events = await engine.evaluate(data)

        assert events == []
        os.unlink(path)

    def test_load_skips_invalid_rules_in_file(self):
        """Invalid rules in the JSON file are skipped without crashing."""
        rules_data = [
            # Valid rule
            {"name": "good", "input": 1, "condition": "voltage_below",
             "threshold": 10.0, "outlet": 1, "action": "off"},
            # Invalid rule — bad condition
            {"name": "bad", "input": 1, "condition": "nonexistent",
             "threshold": 10.0, "outlet": 1, "action": "off"},
            # Invalid rule — missing fields
            {"name": "incomplete"},
        ]
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump(rules_data, tmp)
        tmp.close()

        engine = AutomationEngine(tmp.name)
        rules = engine.list_rules()
        assert len(rules) == 1
        assert rules[0]["name"] == "good"
        os.unlink(tmp.name)


# ===================================================================
# History error scenario tests
# ===================================================================


class TestHistoryRecordErrors:
    """Test 14: record() catches SQLite errors and increments write_errors."""

    def _make_store(self, **kwargs):
        tmp = tempfile.mktemp(suffix=".db")
        store = HistoryStore(tmp, **kwargs)
        return store, tmp

    def test_sqlite_error_caught_and_counted(self):
        store, path = self._make_store()
        data = make_pdu_data()

        # Make the execute call fail by dropping the table
        store._conn.execute("DROP TABLE bank_samples")
        store._conn.commit()

        # record() should not raise
        store.record(data)

        assert store._write_errors == 1
        assert store._total_writes == 1

        store.close()
        os.unlink(path)

    def test_multiple_write_errors_counted(self):
        store, path = self._make_store()
        data = make_pdu_data()

        store._conn.execute("DROP TABLE bank_samples")
        store._conn.commit()

        for _ in range(5):
            store.record(data)

        assert store._write_errors == 5
        assert store._total_writes == 5

        store.close()
        os.unlink(path)

    def test_record_rollback_on_error(self):
        """After a write error, the transaction is rolled back cleanly."""
        store, path = self._make_store()
        data = make_pdu_data()

        # Record one good sample
        store.record(data)
        store._conn.commit()

        # Now break things
        store._conn.execute("DROP TABLE bank_samples")
        store._conn.commit()
        store.record(data)

        assert store._write_errors == 1

        # Recreate the table and verify we can write again
        store._conn.execute(
            "CREATE TABLE bank_samples ("
            "ts INTEGER, bank INTEGER, voltage REAL, current REAL, "
            "power REAL, apparent REAL, pf REAL, "
            "device_id TEXT NOT NULL DEFAULT '', active_source INTEGER)"
        )
        store._conn.commit()
        store.record(data)
        store._conn.commit()

        # The new write should succeed
        rows = store._conn.execute("SELECT COUNT(*) as c FROM bank_samples").fetchone()
        assert rows["c"] >= 1

        store.close()
        os.unlink(path)


class TestHistoryCloseSafety:
    """Test 15: close() is safe even when commit fails."""

    def _make_store(self, **kwargs):
        tmp = tempfile.mktemp(suffix=".db")
        store = HistoryStore(tmp, **kwargs)
        return store, tmp

    def test_close_after_normal_operation(self):
        store, path = self._make_store()
        data = make_pdu_data()
        store.record(data)
        # Should not raise
        store.close()
        os.unlink(path)

    def test_close_with_commit_failure(self):
        """close() does not raise even if commit fails."""
        store, path = self._make_store()

        # sqlite3.Connection.commit is a C-level attribute and can't be
        # patched directly. Instead, wrap _conn with a proxy that fails
        # on commit but delegates close() properly.
        real_conn = store._conn

        class FailingCommitConn:
            """Proxy that raises on commit but delegates everything else."""
            def commit(self):
                raise sqlite3.OperationalError("disk I/O error")

            def close(self):
                real_conn.close()

            def __getattr__(self, name):
                return getattr(real_conn, name)

        store._conn = FailingCommitConn()

        # close() should not raise — it catches the commit error
        store.close()

        # Verify the underlying connection was actually closed
        with pytest.raises(Exception):
            real_conn.execute("SELECT 1")

        os.unlink(path)

    def test_double_close(self):
        """Calling close() twice should not crash."""
        store, path = self._make_store()
        store.close()

        # Second close — connection is already closed, should handle gracefully
        store.close()

        os.unlink(path)

    def test_close_commits_pending_data(self):
        """close() commits any pending (uncommitted) data."""
        store, path = self._make_store()
        data = make_pdu_data()

        # Write fewer than 10 samples so auto-commit hasn't triggered
        store.record(data)
        assert store._write_count == 1  # Not yet committed

        store.close()

        # Reopen and verify the data was committed
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT COUNT(*) as c FROM bank_samples").fetchone()
        assert rows["c"] >= 1
        conn.close()
        os.unlink(path)


class TestHistoryEnergyRollupErrors:
    """Test 16: energy rollup error handling."""

    def _make_store(self, **kwargs):
        tmp = tempfile.mktemp(suffix=".db")
        store = HistoryStore(tmp, **kwargs)
        return store, tmp

    def test_rollup_no_data_is_noop(self):
        """compute_daily_rollups with no samples produces no rows."""
        store, path = self._make_store()
        store.compute_daily_rollups(device_id="")
        count = store._conn.execute("SELECT COUNT(*) as c FROM energy_daily").fetchone()["c"]
        assert count == 0
        store.close()
        os.unlink(path)

    def test_rollup_idempotent(self):
        """Running compute_daily_rollups twice should not duplicate rows."""
        from datetime import datetime, timedelta
        store, path = self._make_store()

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        day_start = int(datetime.strptime(yesterday, "%Y-%m-%d").timestamp())

        for i in range(10):
            store._conn.execute(
                "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf, device_id, active_source) "
                "VALUES (?, 1, 120.0, 1.0, 100.0, 110.0, 0.91, '', 1)",
                (day_start + i,),
            )
        store._conn.commit()

        store.compute_daily_rollups(device_id="")
        count1 = store._conn.execute("SELECT COUNT(*) as c FROM energy_daily").fetchone()["c"]

        store.compute_daily_rollups(device_id="")
        count2 = store._conn.execute("SELECT COUNT(*) as c FROM energy_daily").fetchone()["c"]

        assert count1 == count2
        store.close()
        os.unlink(path)

    def test_monthly_rollup_recomputes_cleanly(self):
        """compute_monthly_rollups should replace, not accumulate."""
        from datetime import datetime
        store, path = self._make_store()

        now = datetime.now()
        current_month = now.strftime("%Y-%m")
        store._conn.execute(
            "INSERT INTO energy_daily (date, device_id, source, outlet, kwh, peak_power_w, avg_power_w, samples) "
            "VALUES (?, '', NULL, NULL, 5.0, 500.0, 250.0, 3600)",
            (f"{current_month}-01",),
        )
        store._conn.commit()

        store.compute_monthly_rollups(device_id="")
        store.compute_monthly_rollups(device_id="")

        rows = store._conn.execute(
            "SELECT * FROM energy_monthly WHERE month = ? AND source IS NULL AND outlet IS NULL",
            (current_month,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["kwh"] == pytest.approx(5.0, abs=0.01)

        store.close()
        os.unlink(path)

    def test_energy_summary_empty_db(self):
        """get_energy_summary on empty DB returns all zeros."""
        store, path = self._make_store()
        summary = store.get_energy_summary(device_id="")
        assert summary["today"]["total_kwh"] == 0
        assert summary["this_month"]["total_kwh"] == 0
        store.close()
        os.unlink(path)


class TestHistoryHealthMetrics:
    """Test 17: get_health returns accurate metrics."""

    def _make_store(self, **kwargs):
        tmp = tempfile.mktemp(suffix=".db")
        store = HistoryStore(tmp, **kwargs)
        return store, tmp

    def test_initial_health(self):
        store, path = self._make_store()
        health = store.get_health()
        assert health["total_writes"] == 0
        assert health["write_errors"] == 0
        assert health["retention_days"] == 60
        assert health["healthy"] is True  # 0 errors => healthy
        assert health["db_path"] == path
        store.close()
        os.unlink(path)

    def test_health_after_successful_writes(self):
        store, path = self._make_store()
        data = make_pdu_data()
        for _ in range(5):
            store.record(data)

        health = store.get_health()
        assert health["total_writes"] == 5
        assert health["write_errors"] == 0
        assert health["healthy"] is True
        store.close()
        os.unlink(path)

    def test_health_with_errors_below_threshold(self):
        """Healthy when error rate < 10%."""
        store, path = self._make_store()
        # Simulate 100 writes with 5 errors (5% < 10%)
        store._total_writes = 100
        store._write_errors = 5

        health = store.get_health()
        assert health["total_writes"] == 100
        assert health["write_errors"] == 5
        assert health["healthy"] is True
        store.close()
        os.unlink(path)

    def test_health_with_errors_above_threshold(self):
        """Unhealthy when error rate >= 10%."""
        store, path = self._make_store()
        # Simulate 100 writes with 15 errors (15% >= 10%)
        store._total_writes = 100
        store._write_errors = 15

        health = store.get_health()
        assert health["healthy"] is False
        store.close()
        os.unlink(path)

    def test_health_with_errors_at_boundary(self):
        """Exactly 10% error rate is unhealthy."""
        store, path = self._make_store()
        store._total_writes = 100
        store._write_errors = 10

        health = store.get_health()
        assert health["healthy"] is False
        store.close()
        os.unlink(path)

    def test_health_custom_retention(self):
        store, path = self._make_store(retention_days=30)
        health = store.get_health()
        assert health["retention_days"] == 30
        store.close()
        os.unlink(path)


class TestHistoryIntervalClamping:
    """Test 18: Interval clamped to minimum 1 (prevents division by zero)."""

    def _make_store(self, **kwargs):
        tmp = tempfile.mktemp(suffix=".db")
        store = HistoryStore(tmp, **kwargs)
        return store, tmp

    def test_zero_interval_clamped(self):
        """Passing interval=0 should be clamped to 1."""
        store, path = self._make_store()
        now = int(time.time())

        # Insert some test data
        store._conn.execute(
            "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now, 1, 120.0, 0.8, 100.0, 110.0, 0.91),
        )
        store._conn.commit()

        # Should not raise ZeroDivisionError
        rows = store.query_banks(now - 60, now, interval=0)
        assert isinstance(rows, list)

        store.close()
        os.unlink(path)

    def test_negative_interval_clamped(self):
        """Passing a negative interval should be clamped to 1."""
        store, path = self._make_store()
        now = int(time.time())

        store._conn.execute(
            "INSERT INTO outlet_samples (ts, outlet, state, current, power, energy) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now, 1, "on", 0.5, 50.0, 1.0),
        )
        store._conn.commit()

        # Should not raise
        rows = store.query_outlets(now - 60, now, interval=-5)
        assert isinstance(rows, list)

        store.close()
        os.unlink(path)

    def test_zero_interval_outlets(self):
        """query_outlets also clamps interval to 1."""
        store, path = self._make_store()
        now = int(time.time())

        store._conn.execute(
            "INSERT INTO outlet_samples (ts, outlet, state, current, power, energy) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now, 1, "on", 0.5, 50.0, 1.0),
        )
        store._conn.commit()

        rows = store.query_outlets(now - 60, now, interval=0)
        assert isinstance(rows, list)

        store.close()
        os.unlink(path)

    def test_auto_pick_interval_never_zero(self):
        """_pick_interval always returns >= 1 for any span."""
        store, path = self._make_store()
        now = int(time.time())

        # Even a 0-second span should return >= 1
        interval = store._pick_interval(now, now)
        assert interval >= 1

        # Very short span
        interval = store._pick_interval(now - 1, now)
        assert interval >= 1

        store.close()
        os.unlink(path)


# ===================================================================
# Additional edge-case tests
# ===================================================================


class TestConfigIntFloatHelpers:
    """Directly test the _int and _float static methods."""

    def test_int_valid(self):
        os.environ["_TEST_INT"] = "42"
        val = Config._int("_TEST_INT", "0", 0, 100)
        assert val == 42
        del os.environ["_TEST_INT"]

    def test_int_uses_default(self):
        os.environ.pop("_TEST_MISSING", None)
        val = Config._int("_TEST_MISSING", "7", 0, 100)
        assert val == 7

    def test_float_valid(self):
        os.environ["_TEST_FLOAT"] = "3.14"
        val = Config._float("_TEST_FLOAT", "0", 0, 10)
        assert val == pytest.approx(3.14)
        del os.environ["_TEST_FLOAT"]

    def test_float_uses_default(self):
        os.environ.pop("_TEST_MISSING_F", None)
        val = Config._float("_TEST_MISSING_F", "1.5", 0, 10)
        assert val == pytest.approx(1.5)


class TestConfigSettingsPersistence:
    """Test save/load of bridge settings to JSON file."""

    def test_save_and_load_settings(self):
        cfg = Config()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            cfg.mqtt_broker = "mqtt.example.com"
            cfg.mqtt_port = 8883
            cfg.poll_interval = 10.0
            cfg.log_level = "DEBUG"
            cfg.history_retention_days = 30
            cfg.save_settings(path)

            cfg2 = Config()
            cfg2.load_saved_settings(path)
            assert cfg2.mqtt_broker == "mqtt.example.com"
            assert cfg2.mqtt_port == 8883
            assert cfg2.poll_interval == 10.0
            assert cfg2.log_level == "DEBUG"
            assert cfg2.history_retention_days == 30
        finally:
            os.unlink(path)

    def test_load_missing_file(self):
        cfg = Config()
        original_broker = cfg.mqtt_broker
        cfg.load_saved_settings("/tmp/nonexistent_settings.json")
        assert cfg.mqtt_broker == original_broker  # unchanged

    def test_settings_dict(self):
        cfg = Config()
        d = cfg.settings_dict
        assert "mqtt_broker" in d
        assert "poll_interval" in d
        assert "log_level" in d
        assert "history_retention_days" in d

    def test_load_corrupt_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("not json")
            path = f.name
        try:
            cfg = Config()
            original_broker = cfg.mqtt_broker
            cfg.load_saved_settings(path)  # Should not raise
            assert cfg.mqtt_broker == original_broker  # unchanged
        finally:
            os.unlink(path)


class TestAutomationLoadCorruptFile:
    """Engine handles corrupt rules file gracefully."""

    def test_load_non_json_file(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        tmp.write("this is not json")
        tmp.close()

        # Should not raise — logs error and starts empty
        engine = AutomationEngine(tmp.name)
        assert engine.list_rules() == []
        os.unlink(tmp.name)

    def test_load_json_not_a_list(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump({"not": "a list"}, tmp)
        tmp.close()

        # Should not raise — the TypeError from iterating a dict is caught
        engine = AutomationEngine(tmp.name)
        # Behavior depends on iteration of dict (iterates keys), but shouldn't crash
        os.unlink(tmp.name)
