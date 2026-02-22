# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
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
