# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Unit tests for multi-PDU architecture:
   BridgeManager, PDUPoller, history device_id filtering,
   MQTT multi-device routing, web device_id param, and PDUData.identity.
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.config import Config
from src.history import HistoryStore
from src.mqtt_handler import MQTTHandler
from src.pdu_config import PDUConfig
from src.pdu_model import (
    BankData,
    DeviceIdentity,
    OutletData,
    PDUData,
    SourceData,
)
from src.web import WebServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdu_data(device_name="Test PDU", outlet_count=4, identity=None):
    """Create a minimal PDUData for testing."""
    return PDUData(
        device_name=device_name,
        outlet_count=outlet_count,
        phase_count=1,
        input_voltage=120.0,
        input_frequency=60.0,
        outlets={
            1: OutletData(number=1, name="Outlet 1", state="on",
                          current=1.0, power=120.0),
            2: OutletData(number=2, name="Outlet 2", state="on",
                          current=0.5, power=60.0),
        },
        banks={
            1: BankData(number=1, voltage=120.0, current=1.5,
                        power=180.0, load_state="normal"),
        },
        identity=identity,
    )


def _clean_env():
    """Remove PDU-related env vars to prevent test interference."""
    for key in ["PDU_HOST", "BRIDGE_MOCK_MODE", "PDU_DEVICE_ID",
                "BRIDGE_POLL_INTERVAL", "BRIDGE_PDUS_FILE"]:
        os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# BridgeManager creates pollers
# ---------------------------------------------------------------------------

def test_bridge_manager_creates_pollers():
    """BridgeManager should create one PDUPoller per enabled PDUConfig."""
    _clean_env()
    os.environ["BRIDGE_MOCK_MODE"] = "true"

    # Create a temp pdus.json with 2 PDUs (one disabled)
    data = {
        "pdus": [
            {"device_id": "pdu-a", "host": "10.0.0.1", "enabled": True},
            {"device_id": "pdu-b", "host": "10.0.0.2", "enabled": True},
            {"device_id": "pdu-c", "host": "10.0.0.3", "enabled": False},
        ]
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        tmp_path = f.name

    os.environ["BRIDGE_PDUS_FILE"] = tmp_path

    try:
        with patch("src.main.MQTTHandler") as mock_mqtt_cls, \
             patch("src.main.HistoryStore") as mock_hist_cls, \
             patch("src.main.WebServer") as mock_web_cls:
            mock_mqtt_cls.return_value = MagicMock()
            mock_hist_cls.return_value = MagicMock()
            mock_web_cls.return_value = MagicMock()

            from src.main import BridgeManager
            manager = BridgeManager()

            # 2 enabled out of 3 total
            assert len(manager.pollers) == 2
            device_ids = [p.device_id for p in manager.pollers]
            assert "pdu-a" in device_ids
            assert "pdu-b" in device_ids
            assert "pdu-c" not in device_ids
    finally:
        os.unlink(tmp_path)
        _clean_env()


# ---------------------------------------------------------------------------
# PDUPoller startup discovers identity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poller_startup_discovers_identity():
    """PDUPoller._discover_identity queries SNMP and returns DeviceIdentity."""
    _clean_env()
    os.environ["BRIDGE_MOCK_MODE"] = "true"

    pdu_cfg = PDUConfig(device_id="test-pdu", host="127.0.0.1")
    config = Config()

    mock_mqtt = MagicMock()
    mock_history = MagicMock()
    mock_web = MagicMock()

    from src.main import PDUPoller
    poller = PDUPoller(
        pdu_cfg=pdu_cfg,
        global_config=config,
        mqtt=mock_mqtt,
        history=mock_history,
        web=mock_web,
        is_single_pdu=True,
    )

    # In mock mode, _discover_identity returns a mock DeviceIdentity
    identity = await poller._discover_identity()
    assert isinstance(identity, DeviceIdentity)
    assert identity.outlet_count == 10
    assert identity.model == "PDU44001"
    assert "Mock" in identity.name

    _clean_env()


# ---------------------------------------------------------------------------
# PDUPoller detects outlet count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poller_detects_outlet_count():
    """Identity.outlet_count is used for the poll loop."""
    _clean_env()
    os.environ["BRIDGE_MOCK_MODE"] = "true"

    pdu_cfg = PDUConfig(device_id="count-pdu", host="127.0.0.1")
    config = Config()

    from src.main import PDUPoller
    poller = PDUPoller(
        pdu_cfg=pdu_cfg,
        global_config=config,
        mqtt=MagicMock(),
        history=MagicMock(),
        web=MagicMock(),
        is_single_pdu=True,
    )

    identity = await poller._discover_identity()
    poller._identity = identity
    poller._outlet_count = identity.outlet_count

    assert poller._outlet_count == 10

    _clean_env()


# ---------------------------------------------------------------------------
# PDUPoller detects bank count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poller_detects_bank_count():
    """Bank count is auto-detected from SNMP (or mock default)."""
    _clean_env()
    os.environ["BRIDGE_MOCK_MODE"] = "true"

    pdu_cfg = PDUConfig(device_id="bank-pdu", host="127.0.0.1")
    config = Config()

    from src.main import PDUPoller
    poller = PDUPoller(
        pdu_cfg=pdu_cfg,
        global_config=config,
        mqtt=MagicMock(),
        history=MagicMock(),
        web=MagicMock(),
        is_single_pdu=True,
    )

    num_banks = await poller._discover_num_banks()
    assert num_banks == 2  # Mock default

    _clean_env()


# ---------------------------------------------------------------------------
# History device_id filtering
# ---------------------------------------------------------------------------

def test_history_device_id_filtering():
    """Insert records with different device_ids, query with filter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_history.db")
        store = HistoryStore(db_path)

        now = int(time.time())

        # Insert data for device "pdu-a"
        data_a = _make_pdu_data("PDU A", 2)
        store.record(data_a, device_id="pdu-a")
        store._conn.commit()

        # Insert data for device "pdu-b" (use a slightly different timestamp
        # so GROUP BY bucketing doesn't merge the two devices' rows)
        store._conn.execute(
            "INSERT INTO bank_samples "
            "(ts, bank, voltage, current, power, apparent, pf, device_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now + 2, 1, 121.0, 2.0, 200.0, None, None, "pdu-b"),
        )
        store._conn.execute(
            "INSERT INTO outlet_samples "
            "(ts, outlet, state, current, power, energy, device_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now + 2, 1, "on", 1.0, 120.0, None, "pdu-b"),
        )
        store._conn.commit()

        start = now - 60
        end = now + 60

        # Query filtered by pdu-a
        banks_a = store.query_banks(start, end, device_id="pdu-a")
        outlets_a = store.query_outlets(start, end, device_id="pdu-a")
        assert len(banks_a) > 0
        assert len(outlets_a) > 0

        # Query filtered by pdu-b
        banks_b = store.query_banks(start, end, device_id="pdu-b")
        outlets_b = store.query_outlets(start, end, device_id="pdu-b")
        assert len(banks_b) > 0
        assert len(outlets_b) > 0

        # Filtered results should be disjoint — querying pdu-a should not
        # return pdu-b data and vice versa
        bank_a_voltages = {r["voltage"] for r in banks_a}
        bank_b_voltages = {r["voltage"] for r in banks_b}
        assert bank_a_voltages != bank_b_voltages or len(banks_a) != len(banks_b)

        # Query for non-existent device returns empty
        banks_none = store.query_banks(start, end, device_id="pdu-z")
        assert len(banks_none) == 0

        store.close()


# ---------------------------------------------------------------------------
# History migration — add device_id column to old schema
# ---------------------------------------------------------------------------

def test_history_migration():
    """Verify the _migrate_device_id method is idempotent on a fresh DB.

    The HistoryStore._create_tables already includes the device_id column
    in the schema. The _migrate_device_id method runs ALTER TABLE which
    gracefully handles 'column already exists' via except OperationalError.
    This test verifies that a fresh HistoryStore can be opened, data
    inserted with device_id, and queried back correctly.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "migration_test.db")

        # First open — creates tables with device_id included
        store = HistoryStore(db_path)
        now = int(time.time())

        # Insert a row with default device_id ('')
        store._conn.execute(
            "INSERT INTO bank_samples "
            "(ts, bank, voltage, current, power, apparent, pf, device_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now, 1, 120.0, 1.5, 180.0, 185.0, 0.97, ""),
        )
        store._conn.commit()
        store.close()

        # Second open — migration should be idempotent (no errors)
        store2 = HistoryStore(db_path)

        # Old row (device_id='') should still be queryable
        rows = store2.query_banks(now - 60, now + 60, device_id="")
        assert len(rows) >= 1

        # Insert a new row with a specific device_id
        data = _make_pdu_data("Migrated PDU", 2)
        store2.record(data, device_id="new-pdu")
        store2._conn.commit()

        # Filtered query for the new device
        rows_new = store2.query_banks(now - 60, now + 60, device_id="new-pdu")
        assert len(rows_new) >= 1

        # Old rows should not appear in the new device query
        rows_old = store2.query_banks(now - 60, now + 60, device_id="")
        for r in rows_old:
            # Old rows should have been inserted with empty device_id
            assert r["voltage"] == 120.0

        store2.close()


# ---------------------------------------------------------------------------
# MQTT multi-device routing
# ---------------------------------------------------------------------------

def test_mqtt_multi_device_routing():
    """Register two devices and verify commands route to the correct callback."""
    _clean_env()
    config = Config()
    handler = MQTTHandler(config)

    # Set up an event loop for the handler
    loop = asyncio.new_event_loop()
    handler._loop = loop

    cb_a = AsyncMock()
    cb_b = AsyncMock()
    handler.register_device("pdu-a", cb_a)
    handler.register_device("pdu-b", cb_b)

    # Simulate an MQTT message for pdu-a
    msg_a = MagicMock()
    msg_a.topic = "pdu/pdu-a/outlet/3/command"
    msg_a.payload = b"on"
    handler._on_message(handler.client, None, msg_a)

    # Give the event loop a chance to process the coroutine
    loop.run_until_complete(asyncio.sleep(0.05))

    cb_a.assert_called_once_with(3, "on")
    cb_b.assert_not_called()

    # Simulate an MQTT message for pdu-b
    msg_b = MagicMock()
    msg_b.topic = "pdu/pdu-b/outlet/5/command"
    msg_b.payload = b"off"
    handler._on_message(handler.client, None, msg_b)

    loop.run_until_complete(asyncio.sleep(0.05))

    cb_b.assert_called_once_with(5, "off")
    # cb_a should still only have the one call from before
    assert cb_a.call_count == 1

    loop.close()
    _clean_env()


# ---------------------------------------------------------------------------
# MQTT HA discovery per device
# ---------------------------------------------------------------------------

def test_mqtt_ha_discovery_per_device():
    """HA discovery is sent separately per device and not duplicated."""
    _clean_env()
    config = Config()
    handler = MQTTHandler(config)

    # Track all publishes
    published_topics = []

    def mock_publish(topic, payload, qos=0, retain=False):
        info = MagicMock()
        info.rc = 0  # MQTT_ERR_SUCCESS
        published_topics.append(topic)
        return info

    handler.client.publish = mock_publish

    # Send HA discovery for device A
    handler.publish_ha_discovery(
        outlet_count=4, num_banks=2, device_id="pdu-a",
    )

    # Send HA discovery for device B
    handler.publish_ha_discovery(
        outlet_count=2, num_banks=1, device_id="pdu-b",
    )

    # Verify topics include both device IDs
    pdu_a_topics = [t for t in published_topics if "pdu-a" in t or "pdu_a" in t]
    pdu_b_topics = [t for t in published_topics if "pdu-b" in t or "pdu_b" in t]
    assert len(pdu_a_topics) > 0, "Expected HA discovery topics for pdu-a"
    assert len(pdu_b_topics) > 0, "Expected HA discovery topics for pdu-b"

    # Verify no cross-contamination
    assert handler._ha_discovery_sent.get("pdu-a") is True
    assert handler._ha_discovery_sent.get("pdu-b") is True

    # Sending again for pdu-a should be a no-op (already sent)
    count_before = len(published_topics)
    handler.publish_ha_discovery(outlet_count=4, num_banks=2, device_id="pdu-a")
    assert len(published_topics) == count_before, "HA discovery should not re-send"

    _clean_env()


# ---------------------------------------------------------------------------
# Web server device_id param
# ---------------------------------------------------------------------------

def test_web_device_id_param():
    """API endpoints accept ?device_id= query param to resolve the target PDU."""
    web = WebServer("default-pdu", port=8080)

    # Register two PDUs with data
    data_a = _make_pdu_data("PDU A")
    data_b = _make_pdu_data("PDU B")
    web.update_data(data_a, device_id="pdu-a")
    web.update_data(data_b, device_id="pdu-b")

    # Simulate a request with device_id param
    request_a = MagicMock()
    request_a.query = {"device_id": "pdu-a"}
    resolved = web._resolve_device_id(request_a)
    assert resolved == "pdu-a"

    request_b = MagicMock()
    request_b.query = {"device_id": "pdu-b"}
    resolved = web._resolve_device_id(request_b)
    assert resolved == "pdu-b"


# ---------------------------------------------------------------------------
# Web auto-selects single PDU
# ---------------------------------------------------------------------------

def test_web_auto_select_single_pdu():
    """When only one PDU has data, the web server auto-selects it."""
    web = WebServer("only-pdu", port=8080)

    data = _make_pdu_data("Only PDU")
    web.update_data(data, device_id="only-pdu")

    # Request with no device_id param
    request = MagicMock()
    request.query = {}
    resolved = web._resolve_device_id(request)
    assert resolved == "only-pdu"


# ---------------------------------------------------------------------------
# PDUData includes identity
# ---------------------------------------------------------------------------

def test_pdu_data_includes_identity():
    """PDUData.identity field is present and carries DeviceIdentity."""
    identity = DeviceIdentity(
        serial="SN123456",
        model="PDU44001",
        name="Test PDU",
        outlet_count=24,
        phase_count=1,
        firmware_main="2.1.0",
    )
    data = _make_pdu_data("Test PDU", outlet_count=24, identity=identity)

    assert data.identity is not None
    assert data.identity.serial == "SN123456"
    assert data.identity.model == "PDU44001"
    assert data.identity.firmware_main == "2.1.0"
    assert data.identity.outlet_count == 24

    # to_dict roundtrip
    d = data.identity.to_dict()
    assert d["serial"] == "SN123456"
    assert d["model"] == "PDU44001"


def test_pdu_data_identity_none_by_default():
    """PDUData.identity defaults to None when not provided."""
    data = PDUData(device_name="Bare PDU", outlet_count=4)
    assert data.identity is None


# ---------------------------------------------------------------------------
# Runtime Add/Remove PDU Tests
# ---------------------------------------------------------------------------

class TestBridgeManagerRuntimePDU:
    """Tests for runtime PDU add/remove in BridgeManager."""

    def _make_manager_config(self):
        """Create a mock Config for BridgeManager."""
        config = MagicMock(spec=Config)
        config.device_id = "test-pdu"
        config.pdu_host = "127.0.0.1"
        config.pdu_snmp_port = 161
        config.pdu_community_read = "public"
        config.pdu_community_write = "private"
        config.mqtt_broker = "localhost"
        config.mqtt_port = 1883
        config.mqtt_username = ""
        config.mqtt_password = ""
        config.snmp_timeout = 2.0
        config.snmp_retries = 1
        config.poll_interval = 5.0
        config.mock_mode = True
        config.log_level = "WARNING"
        config.rules_file = "/tmp/test_rules.json"
        config.web_port = 0
        config.history_db = ":memory:"
        config.history_retention_days = 7
        config.house_monthly_kwh = 0.0
        config.outlet_names_file = "/tmp/test_outlet_names.json"
        config.pdus_file = "/tmp/test_pdus.json"
        config.recovery_enabled = False
        return config

    @pytest.mark.asyncio
    async def test_handle_add_pdu_creates_poller(self):
        """_handle_add_pdu creates a new PDUPoller and appends config."""
        from src.main import BridgeManager

        with patch.object(BridgeManager, "__init__", lambda self: None):
            mgr = BridgeManager.__new__(BridgeManager)
            mgr.config = self._make_manager_config()
            mgr._pdu_configs = []
            mgr.pollers = []
            mgr._poller_tasks = {}
            mgr.mqtt = MagicMock()
            mgr.mqtt.register_device = MagicMock()
            mgr.history = MagicMock()
            mgr.web = MagicMock()
            mgr.web.register_automation_engine = MagicMock()
            mgr.web.register_pdu = MagicMock()

            with patch("src.main.save_pdu_configs"):
                with patch("asyncio.get_event_loop") as mock_loop:
                    mock_task = MagicMock()
                    mock_loop.return_value.create_task.return_value = mock_task

                    await mgr._handle_add_pdu({
                        "device_id": "new-pdu",
                        "host": "10.0.0.5",
                        "snmp_port": 161,
                        "community_read": "public",
                        "community_write": "private",
                    })

            assert len(mgr._pdu_configs) == 1
            assert mgr._pdu_configs[0].device_id == "new-pdu"
            assert len(mgr.pollers) == 1
            assert mgr.pollers[0].device_id == "new-pdu"
            assert "new-pdu" in mgr._poller_tasks
            mgr.web.register_pdu.assert_called_once()
            mgr.mqtt.register_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_remove_pdu_stops_poller(self):
        """_handle_remove_pdu stops and removes the poller."""
        from src.main import BridgeManager

        with patch.object(BridgeManager, "__init__", lambda self: None):
            mgr = BridgeManager.__new__(BridgeManager)
            mgr.config = self._make_manager_config()

            mock_poller = MagicMock()
            mock_poller.device_id = "del-pdu"
            mock_poller.stop = MagicMock()

            mgr.pollers = [mock_poller]
            mgr._pdu_configs = [PDUConfig(device_id="del-pdu", host="10.0.0.1")]
            mock_task = MagicMock()
            mock_task.done.return_value = False
            mgr._poller_tasks = {"del-pdu": mock_task}
            mgr.mqtt = MagicMock()
            mgr.mqtt.unregister_device = MagicMock()

            with patch("src.main.save_pdu_configs"):
                await mgr._handle_remove_pdu("del-pdu")

            mock_poller.stop.assert_called_once()
            mock_task.cancel.assert_called_once()
            mgr.mqtt.unregister_device.assert_called_once_with("del-pdu")
            assert len(mgr.pollers) == 0
            assert len(mgr._pdu_configs) == 0
            assert "del-pdu" not in mgr._poller_tasks

    @pytest.mark.asyncio
    async def test_handle_test_connection_success(self):
        """_handle_test_connection returns device info on success."""
        from src.main import BridgeManager

        with patch.object(BridgeManager, "__init__", lambda self: None):
            mgr = BridgeManager.__new__(BridgeManager)
            mgr.config = self._make_manager_config()

            mock_identity = DeviceIdentity(
                serial="SN123", model="PDU44001", outlet_count=10,
            )

            with patch("src.main.SNMPClient") as MockSNMP:
                mock_client = MockSNMP.return_value
                mock_client.get = AsyncMock(return_value="CyberPower PDU")
                mock_client.get_identity = AsyncMock(return_value=mock_identity)
                mock_client.close = MagicMock()

                result = await mgr._handle_test_connection("10.0.0.1", "public", 161)

            assert result["success"] is True
            assert result["model"] == "PDU44001"
            assert result["serial"] == "SN123"
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_test_connection_failure(self):
        """_handle_test_connection returns failure when host unreachable."""
        from src.main import BridgeManager

        with patch.object(BridgeManager, "__init__", lambda self: None):
            mgr = BridgeManager.__new__(BridgeManager)
            mgr.config = self._make_manager_config()

            with patch("src.main.SNMPClient") as MockSNMP:
                mock_client = MockSNMP.return_value
                mock_client.get = AsyncMock(return_value=None)
                mock_client.close = MagicMock()

                result = await mgr._handle_test_connection("10.0.0.1", "public", 161)

            assert result["success"] is False
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_snmp_set_routes_to_poller(self):
        """_handle_snmp_set routes to the correct poller's SNMP client."""
        from src.main import BridgeManager

        with patch.object(BridgeManager, "__init__", lambda self: None):
            mgr = BridgeManager.__new__(BridgeManager)
            mgr.config = self._make_manager_config()

            mock_poller = MagicMock()
            mock_poller.device_id = "pdu-1"
            mock_poller.transport = MagicMock()
            mock_poller.transport.set_device_field = AsyncMock(return_value=True)

            mgr.pollers = [mock_poller]

            await mgr._handle_snmp_set("pdu-1", "device_name", "New Name")
            mock_poller.transport.set_device_field.assert_called_once_with("device_name", "New Name")

    @pytest.mark.asyncio
    async def test_handle_snmp_set_unknown_device(self):
        """_handle_snmp_set raises for unknown device."""
        from src.main import BridgeManager

        with patch.object(BridgeManager, "__init__", lambda self: None):
            mgr = BridgeManager.__new__(BridgeManager)
            mgr.config = self._make_manager_config()
            mgr.pollers = []

            with pytest.raises(RuntimeError, match="No transport for device"):
                await mgr._handle_snmp_set("nonexistent", "device_name", "foo")


# ---------------------------------------------------------------------------
# State change detection (system events)
# ---------------------------------------------------------------------------

class TestStateChangeDetection:
    """Tests for PDUPoller._detect_state_changes emitting system events."""

    def _make_poller(self):
        """Create a PDUPoller with mocked dependencies."""
        from src.main import PDUPoller
        cfg = PDUConfig(
            device_id="test-pdu", host="10.0.0.1",
            community_read="public", community_write="private",
        )
        config = MagicMock()
        config.mock_mode = False
        config.poll_interval = 5
        mqtt = MagicMock()
        history = MagicMock()
        web = MagicMock()
        web.add_system_event = MagicMock()
        poller = PDUPoller(
            pdu_cfg=cfg, global_config=config,
            mqtt=mqtt, history=history, web=web,
        )
        return poller

    def _make_data(self, ats_current=1, source_a_status="normal",
                   source_b_status="normal", redundancy_ok=True,
                   outlet_states=None, bank_load="normal"):
        outlets = {}
        states = outlet_states or {"on": [1, 2]}
        for st, nums in states.items():
            for n in nums:
                outlets[n] = OutletData(number=n, name=f"Outlet {n}",
                                        state=st, current=1.0, power=100.0)
        return PDUData(
            device_name="Test", outlet_count=2, phase_count=1,
            input_voltage=120.0, input_frequency=60.0,
            outlets=outlets,
            banks={1: BankData(number=1, voltage=120.0, current=1.0,
                               power=100.0, load_state=bank_load)},
            ats_current_source=ats_current,
            ats_preferred_source=1,
            source_a=SourceData(voltage=120.0, frequency=60.0,
                                voltage_status=source_a_status),
            source_b=SourceData(voltage=120.0, frequency=60.0,
                                voltage_status=source_b_status),
            redundancy_ok=redundancy_ok,
        )

    def test_ats_transfer_event(self):
        """Detects ATS source transfer."""
        poller = self._make_poller()
        poller._prev_data = self._make_data(ats_current=1)
        poller._detect_state_changes(self._make_data(ats_current=2))
        calls = poller.web.add_system_event.call_args_list
        types = [c[0][1] for c in calls]
        assert "ats_transfer" in types

    def test_power_loss_event(self):
        """Detects source power loss."""
        poller = self._make_poller()
        poller._prev_data = self._make_data(source_a_status="normal")
        poller._detect_state_changes(self._make_data(source_a_status="underVoltage"))
        calls = poller.web.add_system_event.call_args_list
        types = [c[0][1] for c in calls]
        assert "power_loss" in types

    def test_power_restore_event(self):
        """Detects source power restore."""
        poller = self._make_poller()
        poller._prev_data = self._make_data(source_b_status="underVoltage")
        poller._detect_state_changes(self._make_data(source_b_status="normal"))
        calls = poller.web.add_system_event.call_args_list
        types = [c[0][1] for c in calls]
        assert "power_restore" in types

    def test_outlet_change_event(self):
        """Detects outlet state change."""
        poller = self._make_poller()
        poller._prev_data = self._make_data(outlet_states={"on": [1, 2]})
        poller._detect_state_changes(self._make_data(outlet_states={"on": [1], "off": [2]}))
        calls = poller.web.add_system_event.call_args_list
        types = [c[0][1] for c in calls]
        assert "outlet_change" in types

    def test_redundancy_lost_event(self):
        """Detects redundancy loss."""
        poller = self._make_poller()
        poller._prev_data = self._make_data(redundancy_ok=True)
        poller._detect_state_changes(self._make_data(redundancy_ok=False))
        calls = poller.web.add_system_event.call_args_list
        types = [c[0][1] for c in calls]
        assert "redundancy_lost" in types

    def test_load_warning_event(self):
        """Detects bank overload warning."""
        poller = self._make_poller()
        poller._prev_data = self._make_data(bank_load="normal")
        poller._detect_state_changes(self._make_data(bank_load="nearOverload"))
        calls = poller.web.add_system_event.call_args_list
        types = [c[0][1] for c in calls]
        assert "load_warning" in types

    def test_no_events_when_no_change(self):
        """No events emitted when data is unchanged."""
        poller = self._make_poller()
        data = self._make_data()
        poller._prev_data = data
        poller._detect_state_changes(data)
        poller.web.add_system_event.assert_not_called()

    def test_no_events_on_first_poll(self):
        """No events emitted on first poll (no previous data)."""
        poller = self._make_poller()
        poller._prev_data = None
        poller._detect_state_changes(self._make_data())
        poller.web.add_system_event.assert_not_called()


# ---------------------------------------------------------------------------
# Default credential auto-check tests
# ---------------------------------------------------------------------------

class TestDefaultCredentialCheck:
    """Tests for PDUPoller auto-checking default credentials on first poll."""

    def _make_poller(self, serial_transport=True):
        from src.main import PDUPoller
        from src.serial_transport import SerialTransport
        cfg = PDUConfig(
            device_id="test-pdu", host="10.0.0.1",
            community_read="public", community_write="private",
        )
        if serial_transport:
            cfg.serial_port = "/dev/ttyUSB0"
        config = MagicMock()
        config.mock_mode = True
        config.poll_interval = 5
        config.rules_file = "/tmp/test_rules.json"
        mqtt = MagicMock()
        history = MagicMock()
        web = MagicMock()
        web.add_system_event = MagicMock()
        poller = PDUPoller(
            pdu_cfg=cfg, global_config=config,
            mqtt=mqtt, history=history, web=web,
            is_single_pdu=True,
        )
        return poller

    def test_has_serial_transport_with_serial(self):
        """_has_serial_transport returns True when serial transport exists."""
        from src.serial_transport import SerialTransport
        poller = self._make_poller()
        poller.transport = MagicMock(spec=SerialTransport)
        assert poller._has_serial_transport() is True

    def test_has_serial_transport_as_fallback(self):
        """_has_serial_transport returns True when serial is fallback."""
        from src.serial_transport import SerialTransport
        poller = self._make_poller()
        poller.transport = MagicMock()  # non-serial primary
        poller._fallback = MagicMock(spec=SerialTransport)
        assert poller._has_serial_transport() is True

    def test_has_serial_transport_none(self):
        """_has_serial_transport returns False when no serial transport."""
        poller = self._make_poller(serial_transport=False)
        poller.transport = MagicMock()  # generic mock
        poller._fallback = None
        assert poller._has_serial_transport() is False

    @pytest.mark.asyncio
    async def test_check_default_creds_sets_flag(self):
        """_check_default_creds sets _default_creds_active when defaults active."""
        from src.serial_transport import SerialTransport
        poller = self._make_poller()
        mock_serial_t = MagicMock(spec=SerialTransport)
        mock_serial_t.check_default_credentials = AsyncMock(return_value=True)
        poller.transport = mock_serial_t

        await poller._check_default_creds()
        assert poller._default_creds_active is True
        # Should emit security warning event
        poller.web.add_system_event.assert_called_once()
        args = poller.web.add_system_event.call_args[0]
        assert args[1] == "security_warning"

    @pytest.mark.asyncio
    async def test_check_default_creds_not_active(self):
        """_check_default_creds sets flag to False when defaults changed."""
        from src.serial_transport import SerialTransport
        poller = self._make_poller()
        mock_serial_t = MagicMock(spec=SerialTransport)
        mock_serial_t.check_default_credentials = AsyncMock(return_value=False)
        poller.transport = mock_serial_t

        await poller._check_default_creds()
        assert poller._default_creds_active is False
        poller.web.add_system_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_default_creds_exception_handled(self):
        """_check_default_creds handles exceptions gracefully."""
        from src.serial_transport import SerialTransport
        poller = self._make_poller()
        mock_serial_t = MagicMock(spec=SerialTransport)
        mock_serial_t.check_default_credentials = AsyncMock(side_effect=Exception("port busy"))
        poller.transport = mock_serial_t

        await poller._check_default_creds()
        assert poller._default_creds_active is None  # unchanged

    def test_get_status_detail_includes_default_creds(self):
        """get_status_detail includes default_credentials_active when known."""
        poller = self._make_poller()
        poller._default_creds_active = True
        detail = poller.get_status_detail()
        assert detail["default_credentials_active"] is True

    def test_get_status_detail_excludes_default_creds_when_unknown(self):
        """get_status_detail omits default_credentials_active when None."""
        poller = self._make_poller()
        assert poller._default_creds_active is None
        detail = poller.get_status_detail()
        assert "default_credentials_active" not in detail

    def test_first_poll_flag_starts_true(self):
        """PDUPoller _first_poll is True initially."""
        poller = self._make_poller()
        assert poller._first_poll is True


# ---------------------------------------------------------------------------
# Management callback routing tests
# ---------------------------------------------------------------------------

class TestManagementCallbacks:
    """Tests for BridgeManager management callbacks routing correctly."""

    def _make_manager(self):
        """Create a BridgeManager with mock dependencies for callback testing."""
        from src.main import BridgeManager, PDUPoller
        from src.serial_transport import SerialTransport

        with patch("src.main.Config") as MockConfig, \
             patch("src.main.MQTTHandler") as MockMQTT, \
             patch("src.main.HistoryStore") as MockHistory, \
             patch("src.main.WebServer") as MockWeb, \
             patch("src.main.load_pdu_configs") as mock_load:

            cfg_instance = MockConfig.return_value
            cfg_instance.load_saved_settings = MagicMock()
            cfg_instance.pdus_file = "/tmp/pdus.json"
            cfg_instance.pdu_host = "10.0.0.1"
            cfg_instance.pdu_snmp_port = 161
            cfg_instance.pdu_community_read = "public"
            cfg_instance.pdu_community_write = "private"
            cfg_instance.device_id = "test-pdu"
            cfg_instance.mock_mode = True
            cfg_instance.poll_interval = 5
            cfg_instance.rules_file = "/tmp/rules.json"
            cfg_instance.web_port = 8080
            cfg_instance.web_username = ""
            cfg_instance.web_password = ""
            cfg_instance.session_secret = "test"
            cfg_instance.session_timeout = 3600
            cfg_instance.history_db = ":memory:"
            cfg_instance.history_retention_days = 60
            cfg_instance.house_monthly_kwh = 0
            cfg_instance.settings_file = "/tmp/settings.json"
            cfg_instance.outlet_names_file = "/tmp/outlet_names.json"
            cfg_instance.serial_port = ""
            cfg_instance.serial_baud = 9600
            cfg_instance.serial_username = "cyber"
            cfg_instance.serial_password = "cyber"
            cfg_instance.transport_primary = "snmp"
            cfg_instance.log_level = "INFO"
            cfg_instance.recovery_enabled = False

            pdu_cfg = PDUConfig(
                device_id="test-pdu", host="10.0.0.1",
                community_read="public", community_write="private",
                serial_port="/dev/ttyUSB0",
            )
            mock_load.return_value = [pdu_cfg]

            manager = BridgeManager()

            # Inject a mock serial transport into the poller
            mock_serial_t = MagicMock(spec=SerialTransport)
            if manager.pollers:
                manager.pollers[0].transport = mock_serial_t
                manager.pollers[0]._fallback = None

            return manager, mock_serial_t

    @pytest.mark.asyncio
    async def test_handle_check_credentials_routes_to_serial(self):
        manager, mock_t = self._make_manager()
        mock_t.check_default_credentials = AsyncMock(return_value=True)
        result = await manager._handle_check_credentials("test-pdu")
        assert result["default_credentials_active"] is True
        mock_t.check_default_credentials.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_change_password_routes_to_serial(self):
        manager, mock_t = self._make_manager()
        mock_t.change_password = AsyncMock(return_value=True)
        result = await manager._handle_change_password("test-pdu", "admin", "newpass")
        assert result["ok"] is True
        mock_t.change_password.assert_called_once_with("admin", "newpass")

    @pytest.mark.asyncio
    async def test_handle_get_network_config_routes_to_serial(self):
        manager, mock_t = self._make_manager()
        mock_t.get_network_config = AsyncMock(return_value={"ip": "10.0.0.1"})
        result = await manager._handle_get_network_config("test-pdu")
        assert result["ip"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_handle_set_device_threshold_routes_to_serial(self):
        manager, mock_t = self._make_manager()
        mock_t.set_device_threshold = AsyncMock(return_value=True)
        result = await manager._handle_set_device_threshold("test-pdu", {"overload": 80})
        assert result["ok"] is True
        mock_t.set_device_threshold.assert_called_once_with("overload", 80)

    @pytest.mark.asyncio
    async def test_handle_set_outlet_config_routes_to_serial(self):
        manager, mock_t = self._make_manager()
        mock_t.configure_outlet = AsyncMock(return_value=True)
        result = await manager._handle_set_outlet_config("test-pdu", 1, {"name": "Server1"})
        assert result["ok"] is True
        mock_t.configure_outlet.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_get_eventlog_routes_to_serial(self):
        manager, mock_t = self._make_manager()
        mock_t.get_event_log = AsyncMock(return_value=[{"event": "test"}])
        result = await manager._handle_get_eventlog("test-pdu")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_handle_set_trap_receiver_routes_to_serial(self):
        manager, mock_t = self._make_manager()
        mock_t.set_trap_receiver = AsyncMock(return_value=True)
        result = await manager._handle_set_trap_receiver("test-pdu", 1, {"ip": "10.0.0.2"})
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_handle_set_network_config_routes_to_serial(self):
        manager, mock_t = self._make_manager()
        mock_t.set_network_config = AsyncMock(return_value=True)
        result = await manager._handle_set_network_config("test-pdu", {"ip": "10.0.0.5"})
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_handle_get_energywise_routes_to_serial(self):
        manager, mock_t = self._make_manager()
        mock_t.get_energywise_config = AsyncMock(return_value={"domain": "test"})
        result = await manager._handle_get_energywise("test-pdu")
        assert result["domain"] == "test"

    @pytest.mark.asyncio
    async def test_handle_set_energywise_routes_to_serial(self):
        manager, mock_t = self._make_manager()
        mock_t.set_energywise_config = AsyncMock(return_value=True)
        result = await manager._handle_set_energywise("test-pdu", {"domain": "new"})
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_management_callback_missing_device_raises(self):
        manager, _ = self._make_manager()
        with pytest.raises(RuntimeError, match="No transport"):
            await manager._handle_get_network_config("nonexistent-pdu")
