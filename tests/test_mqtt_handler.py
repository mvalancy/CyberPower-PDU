# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Unit tests for MQTT handler."""

import asyncio
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.pdu_model import BankData, OutletData, PDUData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**overrides):
    """Create a mock Config with default values."""
    config = MagicMock()
    config.device_id = overrides.get("device_id", "pdu44001")
    config.mqtt_broker = overrides.get("mqtt_broker", "mosquitto")
    config.mqtt_port = overrides.get("mqtt_port", 1883)
    config.pdu_host = overrides.get("pdu_host", "192.168.20.177")
    config.pdu_snmp_port = overrides.get("pdu_snmp_port", 161)
    config.pdu_community_read = overrides.get("pdu_community_read", "public")
    config.pdu_community_write = overrides.get("pdu_community_write", "private")
    config.snmp_timeout = overrides.get("snmp_timeout", 2.0)
    config.snmp_retries = overrides.get("snmp_retries", 1)
    config.poll_interval = overrides.get("poll_interval", 1.0)
    config.mock_mode = overrides.get("mock_mode", False)
    config.log_level = overrides.get("log_level", "INFO")
    config.rules_file = overrides.get("rules_file", "/data/rules.json")
    config.web_port = overrides.get("web_port", 8080)
    config.history_db = overrides.get("history_db", "/data/history.db")
    config.history_retention_days = overrides.get("history_retention_days", 60)
    config.house_monthly_kwh = overrides.get("house_monthly_kwh", 0.0)
    config.outlet_names_file = overrides.get("outlet_names_file", "/data/outlet_names.json")
    return config


def make_pdu_data(
    outlet_count=2,
    input_voltage=120.0,
    input_frequency=60.0,
    include_banks=True,
):
    """Create a PDUData with outlets and banks for testing publish."""
    outlets = {}
    for n in range(1, outlet_count + 1):
        outlets[n] = OutletData(
            number=n,
            name=f"Outlet {n}",
            state="on" if n % 2 == 1 else "off",
            current=0.5 * n,
            power=60.0 * n,
            energy=1.5 * n,
        )

    banks = {}
    if include_banks:
        banks[1] = BankData(
            number=1,
            current=5.0,
            voltage=120.0,
            power=600.0,
            apparent_power=650.0,
            power_factor=0.92,
            load_state="normal",
        )
        banks[2] = BankData(
            number=2,
            current=0.0,
            voltage=120.0,
            power=0.0,
            apparent_power=0.0,
            power_factor=1.0,
            load_state="normal",
        )

    return PDUData(
        device_name="CyberPower PDU44001",
        outlet_count=outlet_count,
        phase_count=1,
        input_voltage=input_voltage,
        input_frequency=input_frequency,
        outlets=outlets,
        banks=banks,
    )


def make_mqtt_message(topic, payload):
    """Create a mock MQTTMessage."""
    msg = MagicMock()
    msg.topic = topic
    msg.payload = payload.encode("utf-8") if isinstance(payload, str) else payload
    return msg


def make_publish_info(rc=0):
    """Create a mock publish info result."""
    info = MagicMock()
    info.rc = rc
    return info


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@patch("paho.mqtt.client.Client")
class TestMQTTHandlerInit:
    """Tests for MQTTHandler.__init__."""

    def test_creates_client_with_correct_id(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="mydevice")
        handler = MQTTHandler(config)

        MockClient.assert_called_once()
        call_kwargs = MockClient.call_args
        assert call_kwargs[1]["client_id"] == "pdu-bridge-mydevice"

    def test_sets_lwt_correctly(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        mock_client_instance.will_set.assert_called_once_with(
            "pdu/pdu44001/bridge/status", "offline", qos=1, retain=True
        )

    def test_sets_callbacks(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        config = make_config()
        handler = MQTTHandler(config)

        assert mock_client_instance.on_connect == handler._on_connect
        assert mock_client_instance.on_message == handler._on_message
        assert mock_client_instance.on_disconnect == handler._on_disconnect

    def test_sets_reconnect_delay(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        config = make_config()
        handler = MQTTHandler(config)

        mock_client_instance.reconnect_delay_set.assert_called_once_with(
            min_delay=1, max_delay=30
        )

    def test_initial_state(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config()
        handler = MQTTHandler(config)

        assert handler._connected is False
        assert handler._reconnect_count == 0
        assert handler._last_connect_time is None
        assert handler._last_disconnect_time is None
        assert handler._publish_errors == 0
        assert handler._total_publishes == 0
        assert handler._ha_discovery_sent == {}
        assert handler._command_callback is None


@patch("paho.mqtt.client.Client")
class TestConnect:
    """Tests for MQTTHandler.connect."""

    def test_connect_calls_broker(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        config = make_config(mqtt_broker="10.0.0.1", mqtt_port=1884)
        handler = MQTTHandler(config)

        with patch("asyncio.get_event_loop"):
            handler.connect()

        mock_client_instance.connect.assert_called_once_with(
            "10.0.0.1", 1884, keepalive=60
        )

    def test_connect_starts_loop(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        config = make_config()
        handler = MQTTHandler(config)

        with patch("asyncio.get_event_loop"):
            handler.connect()

        mock_client_instance.loop_start.assert_called_once()

    def test_connect_gets_event_loop(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config()
        handler = MQTTHandler(config)

        mock_loop = MagicMock()
        with patch("asyncio.get_event_loop", return_value=mock_loop) as mock_get_loop:
            handler.connect()

        mock_get_loop.assert_called_once()
        assert handler._loop is mock_loop

    def test_connect_handles_exception_gracefully(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.connect.side_effect = ConnectionRefusedError("refused")
        config = make_config()
        handler = MQTTHandler(config)

        # Should not raise
        with patch("asyncio.get_event_loop"):
            handler.connect()

        # loop_start should NOT be called since connect failed
        mock_client_instance.loop_start.assert_not_called()


@patch("paho.mqtt.client.Client")
class TestOnConnect:
    """Tests for MQTTHandler._on_connect callback."""

    def test_publishes_online_status(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        mock_client = MagicMock()
        handler._on_connect(mock_client, None, None, 0, None)

        mock_client.publish.assert_called_once_with(
            "pdu/pdu44001/bridge/status", "online", qos=1, retain=True
        )

    def test_subscribes_to_command_topic(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        mock_client = MagicMock()
        handler._on_connect(mock_client, None, None, 0, None)

        mock_client.subscribe.assert_called_once_with(
            "pdu/+/outlet/+/command", qos=1
        )

    def test_sets_connected_true(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config()
        handler = MQTTHandler(config)

        mock_client = MagicMock()
        handler._on_connect(mock_client, None, None, 0, None)

        assert handler._connected is True

    def test_sets_last_connect_time(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config()
        handler = MQTTHandler(config)

        before = time.time()
        mock_client = MagicMock()
        handler._on_connect(mock_client, None, None, 0, None)
        after = time.time()

        assert handler._last_connect_time is not None
        assert before <= handler._last_connect_time <= after

    def test_first_connect_does_not_increment_reconnect(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config()
        handler = MQTTHandler(config)

        # last_connect_time is None on first connect
        assert handler._last_connect_time is None

        mock_client = MagicMock()
        handler._on_connect(mock_client, None, None, 0, None)

        assert handler._reconnect_count == 0

    def test_reconnect_increments_count(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config()
        handler = MQTTHandler(config)

        mock_client = MagicMock()

        # First connect
        handler._on_connect(mock_client, None, None, 0, None)
        assert handler._reconnect_count == 0

        # Simulate disconnect/reconnect
        handler._on_connect(mock_client, None, None, 0, None)
        assert handler._reconnect_count == 1

        # Third connect
        handler._on_connect(mock_client, None, None, 0, None)
        assert handler._reconnect_count == 2

    def test_uses_custom_device_id_in_topics(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="rack3pdu")
        handler = MQTTHandler(config)

        mock_client = MagicMock()
        handler._on_connect(mock_client, None, None, 0, None)

        mock_client.publish.assert_called_with(
            "pdu/rack3pdu/bridge/status", "online", qos=1, retain=True
        )
        mock_client.subscribe.assert_called_with(
            "pdu/+/outlet/+/command", qos=1
        )


@patch("paho.mqtt.client.Client")
class TestOnDisconnect:
    """Tests for MQTTHandler._on_disconnect callback."""

    def test_sets_connected_false(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config()
        handler = MQTTHandler(config)
        handler._connected = True

        handler._on_disconnect(MagicMock(), None, None, 0)

        assert handler._connected is False

    def test_tracks_disconnect_time(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config()
        handler = MQTTHandler(config)

        before = time.time()
        handler._on_disconnect(MagicMock(), None, None, 0)
        after = time.time()

        assert handler._last_disconnect_time is not None
        assert before <= handler._last_disconnect_time <= after

    def test_disconnect_after_connect_cycle(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config()
        handler = MQTTHandler(config)

        mock_client = MagicMock()
        handler._on_connect(mock_client, None, None, 0, None)
        assert handler._connected is True

        handler._on_disconnect(mock_client, None, None, 0)
        assert handler._connected is False
        assert handler._last_disconnect_time is not None


@patch("paho.mqtt.client.Client")
class TestGetStatus:
    """Tests for MQTTHandler.get_status."""

    def test_returns_all_fields(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(mqtt_broker="10.0.0.5", mqtt_port=1884)
        handler = MQTTHandler(config)

        status = handler.get_status()

        assert "connected" in status
        assert "reconnect_count" in status
        assert "last_connect" in status
        assert "last_disconnect" in status
        assert "broker" in status
        assert "port" in status
        assert "publish_errors" in status
        assert "total_publishes" in status
        assert "ha_discovery_sent" in status

    def test_initial_status_values(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(mqtt_broker="10.0.0.5", mqtt_port=1884)
        handler = MQTTHandler(config)

        status = handler.get_status()

        assert status["connected"] is False
        assert status["reconnect_count"] == 0
        assert status["last_connect"] is None
        assert status["last_disconnect"] is None
        assert status["broker"] == "10.0.0.5"
        assert status["port"] == 1884
        assert status["publish_errors"] == 0
        assert status["total_publishes"] == 0
        assert status["ha_discovery_sent"] == {}

    def test_status_after_connect_and_publish(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        # Simulate connect
        mock_client = MagicMock()
        handler._on_connect(mock_client, None, None, 0, None)

        # Publish something
        handler._publish("test/topic", "payload")

        status = handler.get_status()
        assert status["connected"] is True
        assert status["last_connect"] is not None
        assert status["total_publishes"] == 1
        assert status["publish_errors"] == 0

    def test_status_reflects_reconnect_count(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config()
        handler = MQTTHandler(config)

        mock_client = MagicMock()
        # First connect
        handler._on_connect(mock_client, None, None, 0, None)
        # Reconnect
        handler._on_connect(mock_client, None, None, 0, None)

        status = handler.get_status()
        assert status["reconnect_count"] == 1


@patch("paho.mqtt.client.Client")
class TestPublish:
    """Tests for MQTTHandler._publish."""

    def test_successful_publish_increments_total(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        handler._publish("test/topic", "payload")

        assert handler._total_publishes == 1
        assert handler._publish_errors == 0

    def test_publish_passes_correct_args(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        handler._publish("my/topic", "data", retain=True, qos=1)

        mock_client_instance.publish.assert_called_once_with(
            "my/topic", "data", qos=1, retain=True
        )

    def test_publish_default_retain_and_qos(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        handler._publish("my/topic", "data")

        mock_client_instance.publish.assert_called_once_with(
            "my/topic", "data", qos=0, retain=False
        )

    def test_failed_rc_increments_errors(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=4)  # non-zero
        config = make_config()
        handler = MQTTHandler(config)

        handler._publish("test/topic", "payload")

        assert handler._total_publishes == 1
        assert handler._publish_errors == 1

    def test_exception_increments_errors(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.side_effect = RuntimeError("connection lost")
        config = make_config()
        handler = MQTTHandler(config)

        handler._publish("test/topic", "payload")

        assert handler._total_publishes == 1
        assert handler._publish_errors == 1

    def test_multiple_errors_tracked_correctly(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=4)
        config = make_config()
        handler = MQTTHandler(config)

        for _ in range(5):
            handler._publish("test/topic", "payload")

        assert handler._total_publishes == 5
        assert handler._publish_errors == 5

    def test_mixed_success_and_failure(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        config = make_config()
        handler = MQTTHandler(config)

        # 2 successes, then 1 failure
        mock_client_instance.publish.side_effect = [
            make_publish_info(rc=0),
            make_publish_info(rc=0),
            make_publish_info(rc=4),
        ]

        handler._publish("t", "p")
        handler._publish("t", "p")
        handler._publish("t", "p")

        assert handler._total_publishes == 3
        assert handler._publish_errors == 1


@patch("paho.mqtt.client.Client")
class TestOnMessage:
    """Tests for MQTTHandler._on_message callback."""

    def test_parses_valid_outlet_command(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler._command_callback = MagicMock(return_value=MagicMock())
        handler._loop = MagicMock()

        msg = make_mqtt_message("pdu/pdu44001/outlet/3/command", "on")

        with patch("asyncio.run_coroutine_threadsafe") as mock_rcts:
            handler._on_message(MagicMock(), None, msg)
            mock_rcts.assert_called_once()
            # Verify the callback was called with correct args
            handler._command_callback.assert_called_once_with(3, "on")

    def test_parses_outlet_number_correctly(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        handler._command_callback = MagicMock(return_value=MagicMock())
        handler._loop = MagicMock()

        for outlet_num in [1, 10, 24]:
            handler._command_callback.reset_mock()
            msg = make_mqtt_message(
                f"pdu/pdu44001/outlet/{outlet_num}/command", "off"
            )
            with patch("asyncio.run_coroutine_threadsafe"):
                handler._on_message(MagicMock(), None, msg)
            handler._command_callback.assert_called_once_with(outlet_num, "off")

    def test_normalizes_command_to_lowercase(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        handler._command_callback = MagicMock(return_value=MagicMock())
        handler._loop = MagicMock()

        msg = make_mqtt_message("pdu/pdu44001/outlet/1/command", "ON")
        with patch("asyncio.run_coroutine_threadsafe"):
            handler._on_message(MagicMock(), None, msg)
        handler._command_callback.assert_called_once_with(1, "on")

    def test_strips_whitespace_from_command(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        handler._command_callback = MagicMock(return_value=MagicMock())
        handler._loop = MagicMock()

        msg = make_mqtt_message("pdu/pdu44001/outlet/1/command", "  reboot  \n")
        with patch("asyncio.run_coroutine_threadsafe"):
            handler._on_message(MagicMock(), None, msg)
        handler._command_callback.assert_called_once_with(1, "reboot")

    def test_ignores_non_command_topic(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        handler._command_callback = MagicMock()
        handler._loop = MagicMock()

        # Not a command topic
        non_command_topics = [
            "pdu/pdu44001/outlet/1/state",
            "pdu/pdu44001/status",
            "pdu/pdu44001/bridge/status",
            "pdu/pdu44001/bank/1/current",
            "pdu/pdu44001/outlet/1/name",
            "homeassistant/switch/foo/config",
            "other/random/topic",
        ]

        for topic in non_command_topics:
            msg = make_mqtt_message(topic, "on")
            handler._on_message(MagicMock(), None, msg)

        handler._command_callback.assert_not_called()

    def test_ignores_topic_with_wrong_part_count(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        handler._command_callback = MagicMock()
        handler._loop = MagicMock()

        # Too few parts
        msg = make_mqtt_message("pdu/pdu44001/outlet/command", "on")
        handler._on_message(MagicMock(), None, msg)

        # Too many parts
        msg = make_mqtt_message("pdu/pdu44001/outlet/1/command/extra", "on")
        handler._on_message(MagicMock(), None, msg)

        handler._command_callback.assert_not_called()

    def test_no_callback_set_does_not_crash(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        handler._loop = MagicMock()
        # _command_callback is None

        msg = make_mqtt_message("pdu/pdu44001/outlet/1/command", "on")
        # Should not raise
        handler._on_message(MagicMock(), None, msg)

    def test_no_loop_set_does_not_crash(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        handler._command_callback = MagicMock()
        # _loop is None

        msg = make_mqtt_message("pdu/pdu44001/outlet/1/command", "on")
        # Should not raise (the if-guard checks both callback and loop)
        handler._on_message(MagicMock(), None, msg)
        handler._command_callback.assert_not_called()

    def test_invalid_outlet_number_does_not_crash(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        handler._command_callback = MagicMock()
        handler._loop = MagicMock()

        msg = make_mqtt_message("pdu/pdu44001/outlet/abc/command", "on")
        # Should not raise — caught by except block
        handler._on_message(MagicMock(), None, msg)

    def test_dispatches_via_run_coroutine_threadsafe(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        mock_loop = MagicMock()
        handler._loop = mock_loop
        mock_coro = MagicMock()
        handler._command_callback = MagicMock(return_value=mock_coro)

        msg = make_mqtt_message("pdu/pdu44001/outlet/5/command", "off")

        with patch("asyncio.run_coroutine_threadsafe") as mock_rcts:
            handler._on_message(MagicMock(), None, msg)
            mock_rcts.assert_called_once_with(mock_coro, mock_loop)


@patch("paho.mqtt.client.Client")
class TestPublishPDUData:
    """Tests for MQTTHandler.publish_pdu_data."""

    def test_publishes_status_json(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = make_pdu_data(outlet_count=2)
        handler.publish_pdu_data(data)

        # Find the status publish call
        calls = mock_client_instance.publish.call_args_list
        status_calls = [c for c in calls if c[0][0] == "pdu/pdu44001/status"]
        assert len(status_calls) == 1

        status_payload = json.loads(status_calls[0][0][1])
        assert status_payload["device_name"] == "CyberPower PDU44001"
        assert status_payload["outlet_count"] == 2
        assert status_payload["phase_count"] == 1
        assert status_payload["input_voltage"] == 120.0
        assert status_payload["input_frequency"] == 60.0
        assert "timestamp" in status_payload

    def test_publishes_input_voltage_and_frequency(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = make_pdu_data(input_voltage=121.5, input_frequency=59.9)
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        voltage_calls = [c for c in calls if c[0][0] == "pdu/pdu44001/input/voltage"]
        freq_calls = [c for c in calls if c[0][0] == "pdu/pdu44001/input/frequency"]

        assert len(voltage_calls) == 1
        assert voltage_calls[0][0][1] == "121.5"
        assert len(freq_calls) == 1
        assert freq_calls[0][0][1] == "59.9"

    def test_publishes_outlet_data(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = make_pdu_data(outlet_count=2)
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        topics = [c[0][0] for c in calls]

        # Check outlet 1 topics
        assert "pdu/pdu44001/outlet/1/state" in topics
        assert "pdu/pdu44001/outlet/1/name" in topics
        assert "pdu/pdu44001/outlet/1/current" in topics
        assert "pdu/pdu44001/outlet/1/power" in topics
        assert "pdu/pdu44001/outlet/1/energy" in topics

        # Check outlet 2 topics
        assert "pdu/pdu44001/outlet/2/state" in topics
        assert "pdu/pdu44001/outlet/2/name" in topics

    def test_publishes_outlet_state_correctly(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = make_pdu_data(outlet_count=2)
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        state1_calls = [c for c in calls if c[0][0] == "pdu/pdu44001/outlet/1/state"]
        state2_calls = [c for c in calls if c[0][0] == "pdu/pdu44001/outlet/2/state"]

        # Outlet 1 is "on" (odd), outlet 2 is "off" (even) per make_pdu_data
        assert state1_calls[0][0][1] == "on"
        assert state2_calls[0][0][1] == "off"

    def test_publishes_bank_data(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = make_pdu_data()
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        topics = [c[0][0] for c in calls]

        # Bank 1 topics
        assert "pdu/pdu44001/bank/1/current" in topics
        assert "pdu/pdu44001/bank/1/voltage" in topics
        assert "pdu/pdu44001/bank/1/power" in topics
        assert "pdu/pdu44001/bank/1/apparent_power" in topics
        assert "pdu/pdu44001/bank/1/power_factor" in topics
        assert "pdu/pdu44001/bank/1/load_state" in topics

        # Bank 2 topics
        assert "pdu/pdu44001/bank/2/current" in topics
        assert "pdu/pdu44001/bank/2/load_state" in topics

    def test_all_publishes_are_retained(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = make_pdu_data(outlet_count=1)
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        for c in calls:
            assert c[1].get("retain", False) is True or c[1] == {} and c[0][2:] == (), \
                f"Expected retain=True on {c[0][0]}"

    def test_skips_none_outlet_values(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = PDUData(
            device_name="Test",
            outlet_count=1,
            phase_count=1,
            input_voltage=None,
            input_frequency=None,
            outlets={
                1: OutletData(
                    number=1, name="O1", state="on",
                    current=None, power=None, energy=None,
                )
            },
            banks={},
        )
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        topics = [c[0][0] for c in calls]

        # State and name should always be published
        assert "pdu/pdu44001/outlet/1/state" in topics
        assert "pdu/pdu44001/outlet/1/name" in topics

        # None values should NOT be published
        assert "pdu/pdu44001/outlet/1/current" not in topics
        assert "pdu/pdu44001/outlet/1/power" not in topics
        assert "pdu/pdu44001/outlet/1/energy" not in topics
        assert "pdu/pdu44001/input/voltage" not in topics
        assert "pdu/pdu44001/input/frequency" not in topics

    def test_skips_none_bank_values(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = PDUData(
            device_name="Test",
            outlet_count=0,
            phase_count=1,
            outlets={},
            banks={
                1: BankData(
                    number=1,
                    current=None, voltage=None, power=None,
                    apparent_power=None, power_factor=None,
                    load_state="unknown",
                )
            },
        )
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        topics = [c[0][0] for c in calls]

        # load_state should always be published
        assert "pdu/pdu44001/bank/1/load_state" in topics

        # None values should NOT be published
        assert "pdu/pdu44001/bank/1/current" not in topics
        assert "pdu/pdu44001/bank/1/voltage" not in topics
        assert "pdu/pdu44001/bank/1/power" not in topics
        assert "pdu/pdu44001/bank/1/apparent_power" not in topics
        assert "pdu/pdu44001/bank/1/power_factor" not in topics


@patch("paho.mqtt.client.Client")
class TestPublishCommandResponse:
    """Tests for MQTTHandler.publish_command_response."""

    def test_publishes_success_response(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_command_response(outlet=3, command="on", success=True)

        calls = mock_client_instance.publish.call_args_list
        assert len(calls) == 1
        assert calls[0][0][0] == "pdu/pdu44001/outlet/3/command/response"

        payload = json.loads(calls[0][0][1])
        assert payload["success"] is True
        assert payload["command"] == "on"
        assert payload["outlet"] == 3
        assert payload["error"] is None
        assert "ts" in payload

    def test_publishes_error_response(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_command_response(
            outlet=5, command="off", success=False, error="SNMP timeout"
        )

        calls = mock_client_instance.publish.call_args_list
        payload = json.loads(calls[0][0][1])
        assert payload["success"] is False
        assert payload["error"] == "SNMP timeout"

    def test_uses_qos1(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_command_response(outlet=1, command="on", success=True)

        calls = mock_client_instance.publish.call_args_list
        assert calls[0][1]["qos"] == 1


@patch("paho.mqtt.client.Client")
class TestPublishAutomation:
    """Tests for publish_automation_status and publish_automation_event."""

    def test_publish_automation_status(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        rules_data = [{"name": "r1", "triggered": False}]
        handler.publish_automation_status(rules_data)

        calls = mock_client_instance.publish.call_args_list
        assert len(calls) == 1
        assert calls[0][0][0] == "pdu/pdu44001/automation/status"
        assert json.loads(calls[0][0][1]) == rules_data
        assert calls[0][1]["retain"] is True

    def test_publish_automation_event(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        event = {"type": "triggered", "rule": "r1", "outlet": 1}
        handler.publish_automation_event(event)

        calls = mock_client_instance.publish.call_args_list
        assert len(calls) == 1
        assert calls[0][0][0] == "pdu/pdu44001/automation/event"
        assert json.loads(calls[0][0][1]) == event
        assert calls[0][1]["qos"] == 1


@patch("paho.mqtt.client.Client")
class TestPublishHADiscovery:
    """Tests for MQTTHandler.publish_ha_discovery."""

    def test_publishes_outlet_switches(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=3, num_banks=2)

        calls = mock_client_instance.publish.call_args_list
        switch_calls = [
            c for c in calls if c[0][0].startswith("homeassistant/switch/")
        ]

        # One switch per outlet
        assert len(switch_calls) == 3

        # Check first outlet config
        config1 = json.loads(switch_calls[0][0][1])
        assert config1["name"] == "Outlet 1"
        assert config1["unique_id"] == "pdu44001_outlet_1"
        assert config1["state_topic"] == "pdu/pdu44001/outlet/1/state"
        assert config1["command_topic"] == "pdu/pdu44001/outlet/1/command"
        assert config1["payload_on"] == "on"
        assert config1["payload_off"] == "off"
        assert "device" in config1
        assert "availability" in config1

    def test_publishes_bank_sensors(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=1, num_banks=2)

        calls = mock_client_instance.publish.call_args_list
        sensor_calls = [
            c for c in calls
            if c[0][0].startswith("homeassistant/sensor/pdu44001_bank_")
        ]

        # 6 metrics per bank, 2 banks = 12
        assert len(sensor_calls) == 12

        # Check a specific bank sensor
        bank1_voltage_calls = [
            c for c in sensor_calls
            if "pdu44001_bank_1_voltage" in c[0][0]
        ]
        assert len(bank1_voltage_calls) == 1
        config1 = json.loads(bank1_voltage_calls[0][0][1])
        assert config1["state_topic"] == "pdu/pdu44001/bank/1/voltage"
        assert config1["unit_of_measurement"] == "V"
        assert config1["device_class"] == "voltage"

    def test_publishes_input_sensors(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=1, num_banks=1)

        calls = mock_client_instance.publish.call_args_list
        input_calls = [
            c for c in calls
            if c[0][0].startswith("homeassistant/sensor/pdu44001_input_")
        ]

        # voltage and frequency
        assert len(input_calls) == 2

        topics = [c[0][0] for c in input_calls]
        assert "homeassistant/sensor/pdu44001_input_voltage/config" in topics
        assert "homeassistant/sensor/pdu44001_input_frequency/config" in topics

    def test_publishes_bridge_binary_sensor(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=1, num_banks=1)

        calls = mock_client_instance.publish.call_args_list
        binary_calls = [
            c for c in calls
            if c[0][0].startswith("homeassistant/binary_sensor/")
        ]

        assert len(binary_calls) == 1
        config1 = json.loads(binary_calls[0][0][1])
        assert config1["unique_id"] == "pdu44001_bridge_status"
        assert config1["device_class"] == "connectivity"
        assert config1["state_topic"] == "pdu/pdu44001/bridge/status"
        assert config1["payload_on"] == "online"
        assert config1["payload_off"] == "offline"

    def test_sets_ha_discovery_sent_flag(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        assert handler._ha_discovery_sent == {}
        handler.publish_ha_discovery(outlet_count=1, num_banks=1)
        assert len(handler._ha_discovery_sent) > 0

    def test_only_runs_once(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=3, num_banks=2)
        first_call_count = mock_client_instance.publish.call_count

        # Second call should be a no-op
        handler.publish_ha_discovery(outlet_count=3, num_banks=2)
        second_call_count = mock_client_instance.publish.call_count

        assert second_call_count == first_call_count

    def test_idempotent_after_multiple_calls(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        for _ in range(5):
            handler.publish_ha_discovery(outlet_count=2, num_banks=1)

        # All publishes come from the first call only
        # 2 outlet switches + 6 bank sensors + 2 input sensors + 4 ATS sensors + 3 total sensors + 1 bridge binary = 18
        expected_count = 2 + 6 + 2 + 4 + 3 + 1
        assert mock_client_instance.publish.call_count == expected_count

    def test_device_info_shared_across_entities(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=1, num_banks=1)

        calls = mock_client_instance.publish.call_args_list
        ha_calls = [
            c for c in calls if c[0][0].startswith("homeassistant/")
        ]

        for c in ha_calls:
            payload = json.loads(c[0][1])
            device = payload.get("device", {})
            assert device.get("identifiers") == ["cyberpdu_pdu44001"]
            assert device.get("manufacturer") == "CyberPower"
            assert device.get("model") == "PDU44001"

    def test_all_discovery_messages_are_retained(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=1, num_banks=1)

        calls = mock_client_instance.publish.call_args_list
        for c in calls:
            assert c[1]["retain"] is True, f"Expected retain=True on {c[0][0]}"

    def test_bank_load_state_has_no_state_class(self, MockClient):
        """load_state is categorical, not a measurement; it should not have state_class."""
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=0, num_banks=1)

        calls = mock_client_instance.publish.call_args_list
        load_state_calls = [
            c for c in calls
            if "load_state" in c[0][0] and c[0][0].startswith("homeassistant/")
        ]

        assert len(load_state_calls) == 1
        payload = json.loads(load_state_calls[0][0][1])
        assert "state_class" not in payload


@patch("paho.mqtt.client.Client")
class TestDisconnect:
    """Tests for MQTTHandler.disconnect."""

    def test_publishes_offline_status(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.disconnect()

        # The first publish call should be the offline status
        first_call = mock_client_instance.publish.call_args_list[0]
        assert first_call[0][0] == "pdu/pdu44001/bridge/status"
        assert first_call[0][1] == "offline"

    def test_stops_loop_and_disconnects(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        handler.disconnect()

        mock_client_instance.loop_stop.assert_called_once()
        mock_client_instance.disconnect.assert_called_once()

    def test_handles_publish_error_gracefully(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.side_effect = RuntimeError("not connected")
        config = make_config()
        handler = MQTTHandler(config)

        # Should not raise
        handler.disconnect()

        # Should still attempt to stop loop and disconnect
        mock_client_instance.loop_stop.assert_called_once()
        mock_client_instance.disconnect.assert_called_once()

    def test_handles_loop_stop_error_gracefully(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        mock_client_instance.loop_stop.side_effect = RuntimeError("already stopped")
        config = make_config()
        handler = MQTTHandler(config)

        # Should not raise
        handler.disconnect()

    def test_handles_disconnect_error_gracefully(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        mock_client_instance.disconnect.side_effect = RuntimeError("already disconnected")
        config = make_config()
        handler = MQTTHandler(config)

        # Should not raise
        handler.disconnect()

    def test_handles_all_errors_gracefully(self, MockClient):
        """Both the offline publish and the disconnect/loop_stop can fail."""
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.side_effect = RuntimeError("no connection")
        mock_client_instance.loop_stop.side_effect = RuntimeError("already stopped")
        mock_client_instance.disconnect.side_effect = RuntimeError("already disconnected")
        config = make_config()
        handler = MQTTHandler(config)

        # Should not raise even when everything fails
        handler.disconnect()


@patch("paho.mqtt.client.Client")
class TestSetCommandCallback:
    """Tests for MQTTHandler.set_command_callback."""

    def test_stores_callback(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config()
        handler = MQTTHandler(config)

        async def my_callback(outlet, cmd):
            pass

        handler.set_command_callback(my_callback)
        assert handler._command_callback is my_callback


@patch("paho.mqtt.client.Client")
class TestIntegration:
    """Integration-style tests combining multiple operations."""

    def test_full_lifecycle(self, MockClient):
        """Test init -> connect -> publish -> disconnect."""
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        # Connect
        with patch("asyncio.get_event_loop"):
            handler.connect()

        # Simulate on_connect callback
        handler._on_connect(mock_client_instance, None, None, 0, None)
        assert handler._connected is True

        # Publish data
        data = make_pdu_data(outlet_count=2)
        handler.publish_pdu_data(data)

        # Check status
        status = handler.get_status()
        assert status["connected"] is True
        assert status["total_publishes"] > 0

        # Disconnect
        handler.disconnect()
        mock_client_instance.loop_stop.assert_called()
        mock_client_instance.disconnect.assert_called()

    def test_publish_errors_accumulate_in_status(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        config = make_config()
        handler = MQTTHandler(config)

        # 3 failures, 2 successes
        mock_client_instance.publish.side_effect = [
            make_publish_info(rc=4),
            make_publish_info(rc=0),
            make_publish_info(rc=4),
            make_publish_info(rc=0),
            make_publish_info(rc=4),
        ]

        for _ in range(5):
            handler._publish("t", "p")

        status = handler.get_status()
        assert status["total_publishes"] == 5
        assert status["publish_errors"] == 3

    def test_ha_discovery_flag_in_status(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        assert handler.get_status()["ha_discovery_sent"] == {}

        handler.publish_ha_discovery(outlet_count=1, num_banks=1)

        # After HA discovery, the dict should have entries
        assert len(handler.get_status()["ha_discovery_sent"]) > 0


# ---------------------------------------------------------------------------
# Unregister Device
# ---------------------------------------------------------------------------

@patch("paho.mqtt.client.Client")
class TestUnregisterDevice:
    """Tests for MQTTHandler.unregister_device()."""

    def test_unregister_removes_callback(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        async def dummy_cb(outlet, cmd):
            pass

        handler.register_device("dev1", dummy_cb)
        assert "dev1" in handler._device_callbacks

        handler.unregister_device("dev1")
        assert "dev1" not in handler._device_callbacks

    def test_unregister_removes_ha_discovery_state(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        handler._ha_discovery_sent["dev1"] = True
        handler.unregister_device("dev1")
        assert "dev1" not in handler._ha_discovery_sent

    def test_unregister_publishes_offline(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        handler.unregister_device("dev1")

        # Should have published offline status
        calls = mock_client_instance.publish.call_args_list
        offline_calls = [c for c in calls if "dev1/bridge/status" in str(c) and "offline" in str(c)]
        assert len(offline_calls) >= 1

    def test_unregister_nonexistent_device(self, MockClient):
        """Unregistering a device that doesn't exist should not raise."""
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        handler.unregister_device("nonexistent")  # Should not raise

    def test_unregister_allows_re_registration(self, MockClient):
        """After unregister, the same device_id can be re-registered."""
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config()
        handler = MQTTHandler(config)

        async def cb1(o, c): pass
        async def cb2(o, c): pass

        handler.register_device("dev1", cb1)
        handler.unregister_device("dev1")
        handler.register_device("dev1", cb2)
        assert handler._device_callbacks["dev1"] is cb2


# ---------------------------------------------------------------------------
# ATS config publish tests
# ---------------------------------------------------------------------------

@patch("paho.mqtt.client.Client")
class TestPublishATSConfig:
    """Tests for ATS config fields in publish_pdu_data."""

    def test_publishes_voltage_sensitivity(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = make_pdu_data()
        data.voltage_sensitivity = "Normal"
        data.transfer_voltage = 88.0
        data.voltage_upper_limit = 148.0
        data.voltage_lower_limit = 88.0
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        topics = {c[0][0]: c[0][1] for c in calls}

        assert topics.get("pdu/pdu44001/ats/voltage_sensitivity") == "Normal"
        assert topics.get("pdu/pdu44001/ats/transfer_voltage") == "88.0"
        assert topics.get("pdu/pdu44001/ats/voltage_upper_limit") == "148.0"
        assert topics.get("pdu/pdu44001/ats/voltage_lower_limit") == "88.0"

    def test_publishes_total_load_power_energy(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = make_pdu_data()
        data.total_load = 2.5
        data.total_power = 300.0
        data.total_energy = 150.5
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        topics = {c[0][0]: c[0][1] for c in calls}

        assert topics.get("pdu/pdu44001/total/load") == "2.5"
        assert topics.get("pdu/pdu44001/total/power") == "300.0"
        assert topics.get("pdu/pdu44001/total/energy") == "150.5"

    def test_skips_ats_fields_when_empty(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = make_pdu_data()
        # Default PDUData has empty ATS fields
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        topics = [c[0][0] for c in calls]

        assert "pdu/pdu44001/ats/voltage_sensitivity" not in topics
        assert "pdu/pdu44001/ats/transfer_voltage" not in topics
        assert "pdu/pdu44001/total/load" not in topics
        assert "pdu/pdu44001/total/power" not in topics
        assert "pdu/pdu44001/total/energy" not in topics


# ---------------------------------------------------------------------------
# Environment publish tests
# ---------------------------------------------------------------------------

@patch("paho.mqtt.client.Client")
class TestPublishEnvironment:
    """Tests for environment data in publish_pdu_data."""

    def test_publishes_environment_when_present(self, MockClient):
        from src.mqtt_handler import MQTTHandler
        from src.pdu_model import EnvironmentalData

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = make_pdu_data()
        data.environment = EnvironmentalData(
            temperature=23.5,
            temperature_unit="C",
            humidity=45,
            contacts={1: True, 2: False},
            sensor_present=True,
        )
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        topics = {c[0][0]: c[0][1] for c in calls}

        assert topics.get("pdu/pdu44001/environment/temperature") == "23.5"
        assert topics.get("pdu/pdu44001/environment/humidity") == "45"
        assert topics.get("pdu/pdu44001/environment/contact/1") == "closed"
        assert topics.get("pdu/pdu44001/environment/contact/2") == "open"

    def test_skips_environment_when_absent(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = make_pdu_data()
        data.environment = None
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        topics = [c[0][0] for c in calls]

        env_topics = [t for t in topics if "environment" in t]
        assert len(env_topics) == 0

    def test_skips_environment_when_sensor_not_present(self, MockClient):
        from src.mqtt_handler import MQTTHandler
        from src.pdu_model import EnvironmentalData

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        data = make_pdu_data()
        data.environment = EnvironmentalData(
            temperature=None,
            sensor_present=False,
        )
        handler.publish_pdu_data(data)

        calls = mock_client_instance.publish.call_args_list
        topics = [c[0][0] for c in calls]

        env_topics = [t for t in topics if "environment" in t]
        assert len(env_topics) == 0


# ---------------------------------------------------------------------------
# Extended MQTT command tests
# ---------------------------------------------------------------------------

@patch("paho.mqtt.client.Client")
class TestExtendedCommands:
    """Tests for extended MQTT commands (delayon, delayoff, cancel)."""

    def test_delayon_command_accepted(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        handler._command_callback = MagicMock(return_value=MagicMock())
        handler._loop = MagicMock()

        msg = make_mqtt_message("pdu/pdu44001/outlet/1/command", "delayon")
        with patch("asyncio.run_coroutine_threadsafe"):
            handler._on_message(MagicMock(), None, msg)
        handler._command_callback.assert_called_once_with(1, "delayon")

    def test_delayoff_command_accepted(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        handler._command_callback = MagicMock(return_value=MagicMock())
        handler._loop = MagicMock()

        msg = make_mqtt_message("pdu/pdu44001/outlet/5/command", "delayoff")
        with patch("asyncio.run_coroutine_threadsafe"):
            handler._on_message(MagicMock(), None, msg)
        handler._command_callback.assert_called_once_with(5, "delayoff")

    def test_cancel_command_accepted(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        handler._command_callback = MagicMock(return_value=MagicMock())
        handler._loop = MagicMock()

        msg = make_mqtt_message("pdu/pdu44001/outlet/3/command", "cancel")
        with patch("asyncio.run_coroutine_threadsafe"):
            handler._on_message(MagicMock(), None, msg)
        handler._command_callback.assert_called_once_with(3, "cancel")

    def test_unknown_command_rejected(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)
        handler._command_callback = MagicMock(return_value=MagicMock())
        handler._loop = MagicMock()

        msg = make_mqtt_message("pdu/pdu44001/outlet/1/command", "explode")
        handler._on_message(MagicMock(), None, msg)
        handler._command_callback.assert_not_called()


# ---------------------------------------------------------------------------
# HA discovery ATS and total sensor tests
# ---------------------------------------------------------------------------

@patch("paho.mqtt.client.Client")
class TestHADiscoveryATSAndTotal:
    """Tests for ATS and total sensors in HA discovery."""

    def test_publishes_ats_sensors(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=1, num_banks=1)

        calls = mock_client_instance.publish.call_args_list
        ats_calls = [
            c for c in calls
            if c[0][0].startswith("homeassistant/sensor/pdu44001_ats_")
        ]

        # 4 ATS sensors: voltage_sensitivity, transfer_voltage, voltage_upper_limit, voltage_lower_limit
        assert len(ats_calls) == 4

        ats_topics = [c[0][0] for c in ats_calls]
        assert "homeassistant/sensor/pdu44001_ats_voltage_sensitivity/config" in ats_topics
        assert "homeassistant/sensor/pdu44001_ats_transfer_voltage/config" in ats_topics
        assert "homeassistant/sensor/pdu44001_ats_voltage_upper_limit/config" in ats_topics
        assert "homeassistant/sensor/pdu44001_ats_voltage_lower_limit/config" in ats_topics

    def test_ats_sensor_config_structure(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=0, num_banks=0)

        calls = mock_client_instance.publish.call_args_list
        transfer_voltage_calls = [
            c for c in calls
            if "pdu44001_ats_transfer_voltage" in c[0][0]
        ]

        assert len(transfer_voltage_calls) == 1
        payload = json.loads(transfer_voltage_calls[0][0][1])
        assert payload["unique_id"] == "pdu44001_ats_transfer_voltage"
        assert payload["state_topic"] == "pdu/pdu44001/ats/transfer_voltage"
        assert payload["unit_of_measurement"] == "V"
        assert "device" in payload
        assert "availability" in payload

    def test_publishes_total_sensors(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=1, num_banks=1)

        calls = mock_client_instance.publish.call_args_list
        total_calls = [
            c for c in calls
            if c[0][0].startswith("homeassistant/sensor/pdu44001_total_")
        ]

        # 3 total sensors: load, power, energy
        assert len(total_calls) == 3

        total_topics = [c[0][0] for c in total_calls]
        assert "homeassistant/sensor/pdu44001_total_load/config" in total_topics
        assert "homeassistant/sensor/pdu44001_total_power/config" in total_topics
        assert "homeassistant/sensor/pdu44001_total_energy/config" in total_topics

    def test_total_energy_has_total_increasing_state_class(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=0, num_banks=0)

        calls = mock_client_instance.publish.call_args_list
        energy_calls = [
            c for c in calls
            if "pdu44001_total_energy" in c[0][0]
        ]

        assert len(energy_calls) == 1
        payload = json.loads(energy_calls[0][0][1])
        assert payload["state_class"] == "total_increasing"
        assert payload["device_class"] == "energy"
        assert payload["unit_of_measurement"] == "kWh"

    def test_total_load_sensor_config(self, MockClient):
        from src.mqtt_handler import MQTTHandler

        mock_client_instance = MockClient.return_value
        mock_client_instance.publish.return_value = make_publish_info(rc=0)
        config = make_config(device_id="pdu44001")
        handler = MQTTHandler(config)

        handler.publish_ha_discovery(outlet_count=0, num_banks=0)

        calls = mock_client_instance.publish.call_args_list
        load_calls = [
            c for c in calls
            if "pdu44001_total_load" in c[0][0]
        ]

        assert len(load_calls) == 1
        payload = json.loads(load_calls[0][0][1])
        assert payload["unique_id"] == "pdu44001_total_load"
        assert payload["state_topic"] == "pdu/pdu44001/total/load"
        assert payload["unit_of_measurement"] == "A"
        assert payload["device_class"] == "current"
        assert payload["state_class"] == "measurement"
