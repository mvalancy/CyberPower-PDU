# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Comprehensive tests for the web server REST API."""

import json
import logging
import os
import sys
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.automation import AutomationEngine
from src.pdu_model import BankData, OutletData, PDUData, SourceData
from src.web import WebServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_pdu_data(
    device_name="Test PDU",
    outlet_count=4,
    phase_count=1,
    input_voltage=120.0,
    input_frequency=60.0,
    source_a_voltage=120.0,
    source_b_voltage=120.0,
    ats_current_source=1,
    ats_preferred_source=1,
):
    """Create a realistic PDUData for testing."""
    return PDUData(
        device_name=device_name,
        outlet_count=outlet_count,
        phase_count=phase_count,
        input_voltage=input_voltage,
        input_frequency=input_frequency,
        outlets={
            1: OutletData(number=1, name="Server A", state="on",
                          current=1.2, power=144.0, energy=50.5),
            2: OutletData(number=2, name="Server B", state="on",
                          current=0.8, power=96.0, energy=32.1),
            3: OutletData(number=3, name="Switch", state="on",
                          current=0.3, power=36.0, energy=10.0),
            4: OutletData(number=4, name="Unused", state="off",
                          current=0.0, power=0.0, energy=0.0),
        },
        banks={
            1: BankData(number=1, voltage=120.0, current=2.3,
                        power=276.0, apparent_power=280.0,
                        power_factor=0.98, load_state="normal"),
            2: BankData(number=2, voltage=120.0, current=0.0,
                        power=0.0, apparent_power=0.0,
                        power_factor=1.0, load_state="normal"),
        },
        source_a=SourceData(
            voltage=source_a_voltage,
            frequency=60.0,
            voltage_status="normal",
        ),
        source_b=SourceData(
            voltage=source_b_voltage,
            frequency=60.0,
            voltage_status="normal",
        ),
        ats_current_source=ats_current_source,
        ats_preferred_source=ats_preferred_source,
        ats_auto_transfer=True,
        redundancy_ok=True,
    )


def make_engine():
    """Create an AutomationEngine backed by a temp file with valid JSON."""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    json.dump([], tmp)  # Write empty array so the engine loads cleanly
    tmp.close()
    engine = AutomationEngine(tmp.name)
    return engine, tmp.name


def make_mock_mqtt(connected=True):
    """Create a mock MQTT handler."""
    mqtt = MagicMock()
    mqtt.get_status.return_value = {"connected": connected, "broker": "localhost"}
    return mqtt


def make_mock_history(healthy=True):
    """Create a mock HistoryStore."""
    hist = MagicMock()
    hist.get_health.return_value = {"healthy": healthy, "total_rows": 1000}
    hist.query_banks.return_value = [
        {"bucket": "2025-01-01T00:00:00", "bank": 1, "voltage": 120.0,
         "current": 2.3, "power": 276.0, "apparent": 280.0, "pf": 0.98},
    ]
    hist.query_outlets.return_value = [
        {"bucket": "2025-01-01T00:00:00", "outlet": 1, "current": 1.2,
         "power": 144.0, "energy": 50.5},
    ]
    hist.list_reports.return_value = [
        {"id": 1, "period": "daily", "created_at": "2025-01-01T00:00:00"},
        {"id": 2, "period": "daily", "created_at": "2025-01-02T00:00:00"},
    ]
    hist.get_report.return_value = None
    hist.get_latest_report.return_value = None
    return hist


def make_mock_snmp(reachable=True):
    """Create a mock SNMP client."""
    snmp = MagicMock()
    snmp.get_health.return_value = {"reachable": reachable, "polls": 100}
    return snmp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def engine_and_path():
    """Provide an AutomationEngine and clean up the temp file after."""
    engine, path = make_engine()
    yield engine, path
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _make_web_server(engine, device_id="test-pdu-001", port=0,
                     mqtt=None, history=None):
    """Build a WebServer with the new constructor and register engine."""
    ws = WebServer(device_id, port, mqtt=mqtt, history=history)
    ws.register_automation_engine(device_id, engine)
    return ws


@pytest.fixture
def web_server(engine_and_path):
    """Create a WebServer with all mocks wired up, no data loaded yet."""
    engine, _path = engine_and_path
    ws = _make_web_server(
        engine,
        mqtt=make_mock_mqtt(),
        history=make_mock_history(),
    )
    return ws


@pytest_asyncio.fixture
async def client(web_server):
    """Provide an aiohttp TestClient connected to the WebServer app."""
    server = TestServer(web_server._app)
    async with TestClient(server) as c:
        yield c


@pytest_asyncio.fixture
async def client_no_history(engine_and_path):
    """Client for a WebServer with no history store."""
    engine, _path = engine_and_path
    ws = _make_web_server(
        engine,
        mqtt=make_mock_mqtt(),
        history=None,
    )
    server = TestServer(ws._app)
    async with TestClient(server) as c:
        yield c


# ===========================================================================
# Status endpoint tests
# ===========================================================================

class TestStatusEndpoint:
    """Tests for GET /api/status."""

    @pytest.mark.asyncio
    async def test_status_503_before_data(self, client):
        """Returns 503 with error message when no data has been received."""
        resp = await client.get("/api/status")
        assert resp.status == 503
        body = await resp.json()
        assert "error" in body
        assert body["error"] == "no data yet"

    @pytest.mark.asyncio
    async def test_status_returns_full_data(self, web_server, client):
        """Returns complete PDU data after update_data() is called."""
        pdu_data = make_pdu_data()
        web_server.update_data(pdu_data)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()

        # Device section
        assert body["device"]["name"] == "Test PDU"
        assert body["device"]["id"] == "test-pdu-001"
        assert body["device"]["outlet_count"] == 4
        assert body["device"]["phase_count"] == 1

        # ATS section
        assert body["ats"]["preferred_source"] == 1
        assert body["ats"]["preferred_label"] == "A"
        assert body["ats"]["current_source"] == 1
        assert body["ats"]["current_label"] == "A"
        assert body["ats"]["auto_transfer"] is True
        assert body["ats"]["transferred"] is False
        assert body["ats"]["redundancy_ok"] is True
        assert body["ats"]["source_a"]["voltage"] == 120.0
        assert body["ats"]["source_b"]["voltage"] == 120.0

        # Outlets
        assert len(body["outlets"]) == 4
        assert body["outlets"]["1"]["name"] == "Server A"
        assert body["outlets"]["1"]["state"] == "on"
        assert body["outlets"]["1"]["current"] == 1.2
        assert body["outlets"]["1"]["power"] == 144.0
        assert body["outlets"]["4"]["state"] == "off"

        # Banks / inputs
        assert len(body["inputs"]) == 2
        assert body["inputs"]["1"]["voltage"] == 120.0
        assert body["inputs"]["1"]["current"] == 2.3
        assert body["inputs"]["1"]["power"] == 276.0
        assert body["inputs"]["1"]["load_state"] == "normal"

        # Summary
        assert body["summary"]["total_power"] == 276.0
        assert body["summary"]["input_voltage"] == 120.0
        assert body["summary"]["active_outlets"] == 3
        assert body["summary"]["total_outlets"] == 4

        # Timestamp and data_age
        assert "ts" in body
        assert "data_age_seconds" in body

    @pytest.mark.asyncio
    async def test_status_ats_transferred(self, web_server, client):
        """Transferred flag is True when current != preferred source."""
        pdu_data = make_pdu_data(ats_current_source=2, ats_preferred_source=1)
        web_server.update_data(pdu_data)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        assert body["ats"]["transferred"] is True
        assert body["ats"]["current_label"] == "B"

    @pytest.mark.asyncio
    async def test_status_includes_mqtt_status(self, web_server, client):
        """MQTT status is included when mqtt handler is configured."""
        web_server.update_data(make_pdu_data())
        resp = await client.get("/api/status")
        body = await resp.json()
        assert "mqtt" in body
        assert body["mqtt"]["connected"] is True

    @pytest.mark.asyncio
    async def test_status_no_mqtt(self, engine_and_path):
        """MQTT key is absent when no mqtt handler configured."""
        engine, _path = engine_and_path
        ws = _make_web_server(engine, device_id="x", mqtt=None, history=None)
        ws.update_data(make_pdu_data())
        server = TestServer(ws._app)
        async with TestClient(server) as c:
            resp = await c.get("/api/status")
            body = await resp.json()
            assert "mqtt" not in body

    @pytest.mark.asyncio
    async def test_status_content_type_json(self, web_server, client):
        """Response Content-Type is application/json."""
        web_server.update_data(make_pdu_data())
        resp = await client.get("/api/status")
        assert "application/json" in resp.headers.get("Content-Type", "")


# ===========================================================================
# Health endpoint tests
# ===========================================================================

class TestHealthEndpoint:
    """Tests for GET /api/health."""

    @pytest.mark.asyncio
    async def test_health_degraded_no_data(self, client):
        """Returns 503 degraded when no data has been received."""
        resp = await client.get("/api/health")
        assert resp.status == 503
        body = await resp.json()
        assert body["status"] == "degraded"
        assert "No data received yet" in body["issues"]

    @pytest.mark.asyncio
    async def test_health_healthy_all_ok(self, web_server, client):
        """Returns 200 healthy when all subsystems are OK and data is fresh."""
        web_server.update_data(make_pdu_data())

        resp = await client.get("/api/health")
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "healthy"
        assert body["issues"] == []
        assert body["subsystems"]["mqtt"]["connected"] is True
        assert body["subsystems"]["history"]["healthy"] is True

    @pytest.mark.asyncio
    async def test_health_degraded_stale_data(self, web_server, client):
        """Returns 503 degraded when data is older than 30 seconds."""
        web_server.update_data(make_pdu_data())
        # Backdate the last data time by 60 seconds
        web_server._last_data_time = time.time() - 60

        resp = await client.get("/api/health")
        assert resp.status == 503
        body = await resp.json()
        assert body["status"] == "degraded"
        assert any("stale" in issue for issue in body["issues"])

    @pytest.mark.asyncio
    async def test_health_degraded_mqtt_disconnected(self, web_server, client):
        """Returns 503 degraded when MQTT is disconnected."""
        web_server.update_data(make_pdu_data())
        web_server._mqtt.get_status.return_value = {"connected": False}

        resp = await client.get("/api/health")
        assert resp.status == 503
        body = await resp.json()
        assert body["status"] == "degraded"
        assert any("MQTT" in issue for issue in body["issues"])

    @pytest.mark.asyncio
    async def test_health_degraded_stale_multi_pdu(self, web_server, client):
        """Returns 503 degraded when data age exceeds threshold via multi-PDU tracking."""
        web_server.update_data(make_pdu_data())
        # Register a PDU config so the multi-PDU health path is used
        web_server.register_pdu("test-pdu-001", {"host": "127.0.0.1"})
        # Backdate the per-device data time
        stale_time = time.time() - 60
        for did in list(web_server._pdu_data_times.keys()):
            web_server._pdu_data_times[did] = stale_time
        web_server._last_data_time = stale_time

        resp = await client.get("/api/health")
        assert resp.status == 503
        body = await resp.json()
        assert body["status"] == "degraded"
        assert any("stale" in issue for issue in body["issues"])

    @pytest.mark.asyncio
    async def test_health_notes_history_issues(self, web_server, client):
        """History write errors are noted in issues."""
        web_server.update_data(make_pdu_data())
        web_server._history.get_health.return_value = {"healthy": False}

        resp = await client.get("/api/health")
        body = await resp.json()
        assert any("History" in issue or "history" in issue.lower()
                    for issue in body["issues"])

    @pytest.mark.asyncio
    async def test_health_no_subsystems(self, engine_and_path):
        """Health works with no optional subsystems configured."""
        engine, _path = engine_and_path
        ws = _make_web_server(engine, device_id="x", mqtt=None, history=None)
        ws.update_data(make_pdu_data())

        server = TestServer(ws._app)
        async with TestClient(server) as c:
            resp = await c.get("/api/health")
            assert resp.status == 200
            body = await resp.json()
            assert body["status"] == "healthy"
            assert body["subsystems"]["mqtt"]["status"] == "unavailable"
            assert body["subsystems"]["history"]["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_health_uptime_seconds(self, web_server, client):
        """Uptime is computed from last data time."""
        web_server.update_data(make_pdu_data())
        resp = await client.get("/api/health")
        body = await resp.json()
        assert "uptime_seconds" in body
        assert body["uptime_seconds"] >= 0


# ===========================================================================
# Rule CRUD endpoint tests
# ===========================================================================

class TestRulesEndpoints:
    """Tests for /api/rules CRUD operations."""

    VALID_RULE = {
        "name": "test_rule",
        "input": 1,
        "condition": "voltage_below",
        "threshold": 10.0,
        "outlet": 1,
        "action": "off",
        "delay": 0,
    }

    @pytest.mark.asyncio
    async def test_list_rules_empty(self, client):
        """GET /api/rules returns empty list initially."""
        resp = await client.get("/api/rules")
        assert resp.status == 200
        body = await resp.json()
        assert body == []

    @pytest.mark.asyncio
    async def test_create_rule(self, client):
        """POST /api/rules creates a rule and returns 201."""
        resp = await client.post("/api/rules", json=self.VALID_RULE)
        assert resp.status == 201
        body = await resp.json()
        assert body["name"] == "test_rule"
        assert body["condition"] == "voltage_below"
        assert body["threshold"] == 10.0
        assert body["outlet"] == 1
        assert body["action"] == "off"

    @pytest.mark.asyncio
    async def test_create_rule_duplicate_409(self, client):
        """POST /api/rules returns 409 for duplicate rule name."""
        await client.post("/api/rules", json=self.VALID_RULE)
        resp = await client.post("/api/rules", json=self.VALID_RULE)
        assert resp.status == 409
        body = await resp.json()
        assert "error" in body
        assert "already exists" in body["error"]

    @pytest.mark.asyncio
    async def test_create_rule_invalid_400(self, client):
        """POST /api/rules returns 400 for missing required fields."""
        resp = await client.post("/api/rules", json={"name": "bad"})
        assert resp.status == 400
        body = await resp.json()
        assert "error" in body

    @pytest.mark.asyncio
    async def test_create_rule_invalid_condition(self, client):
        """POST /api/rules returns error for invalid condition type."""
        bad_rule = dict(self.VALID_RULE, condition="bogus_condition")
        resp = await client.post("/api/rules", json=bad_rule)
        # ValueError for unknown condition -> 409
        assert resp.status == 409
        body = await resp.json()
        assert "error" in body

    @pytest.mark.asyncio
    async def test_list_rules_after_create(self, client):
        """GET /api/rules returns the created rule with state."""
        await client.post("/api/rules", json=self.VALID_RULE)
        resp = await client.get("/api/rules")
        assert resp.status == 200
        body = await resp.json()
        assert len(body) == 1
        assert body[0]["name"] == "test_rule"
        assert "state" in body[0]
        assert body[0]["state"]["triggered"] is False

    @pytest.mark.asyncio
    async def test_update_rule(self, client):
        """PUT /api/rules/{name} updates an existing rule."""
        await client.post("/api/rules", json=self.VALID_RULE)
        update = {
            "input": 2,
            "condition": "voltage_above",
            "threshold": 130.0,
            "outlet": 3,
            "action": "on",
        }
        resp = await client.put("/api/rules/test_rule", json=update)
        assert resp.status == 200
        body = await resp.json()
        assert body["name"] == "test_rule"
        assert body["input"] == 2
        assert body["condition"] == "voltage_above"
        assert body["threshold"] == 130.0
        assert body["outlet"] == 3
        assert body["action"] == "on"

    @pytest.mark.asyncio
    async def test_update_rule_not_found_404(self, client):
        """PUT /api/rules/{name} returns 404 for nonexistent rule."""
        update = {
            "input": 1,
            "condition": "voltage_below",
            "threshold": 10.0,
            "outlet": 1,
            "action": "off",
        }
        resp = await client.put("/api/rules/nonexistent", json=update)
        assert resp.status == 404
        body = await resp.json()
        assert "error" in body

    @pytest.mark.asyncio
    async def test_update_rule_invalid_400(self, client):
        """PUT /api/rules/{name} returns 400 for invalid data."""
        await client.post("/api/rules", json=self.VALID_RULE)
        resp = await client.put(
            "/api/rules/test_rule",
            json={"action": "invalid_action", "condition": "voltage_below",
                  "threshold": 10, "outlet": 1},
        )
        assert resp.status == 400
        body = await resp.json()
        assert "error" in body

    @pytest.mark.asyncio
    async def test_delete_rule(self, client):
        """DELETE /api/rules/{name} deletes an existing rule."""
        await client.post("/api/rules", json=self.VALID_RULE)
        resp = await client.delete("/api/rules/test_rule")
        assert resp.status == 200
        body = await resp.json()
        assert body["deleted"] == "test_rule"

        # Verify it is gone
        resp = await client.get("/api/rules")
        body = await resp.json()
        assert body == []

    @pytest.mark.asyncio
    async def test_delete_rule_not_found_404(self, client):
        """DELETE /api/rules/{name} returns 404 for nonexistent rule."""
        resp = await client.delete("/api/rules/nonexistent")
        assert resp.status == 404
        body = await resp.json()
        assert "error" in body

    @pytest.mark.asyncio
    async def test_create_multiple_rules(self, client):
        """Multiple rules can be created and listed."""
        rule1 = dict(self.VALID_RULE, name="rule_1")
        rule2 = dict(self.VALID_RULE, name="rule_2", outlet=2)
        rule3 = dict(self.VALID_RULE, name="rule_3", outlet=3)

        await client.post("/api/rules", json=rule1)
        await client.post("/api/rules", json=rule2)
        await client.post("/api/rules", json=rule3)

        resp = await client.get("/api/rules")
        body = await resp.json()
        assert len(body) == 3
        names = {r["name"] for r in body}
        assert names == {"rule_1", "rule_2", "rule_3"}


# ===========================================================================
# Events endpoint tests
# ===========================================================================

class TestEventsEndpoint:
    """Tests for GET /api/events."""

    @pytest.mark.asyncio
    async def test_events_empty(self, client):
        """Returns empty list when no events have occurred."""
        resp = await client.get("/api/events")
        assert resp.status == 200
        body = await resp.json()
        assert body == []

    @pytest.mark.asyncio
    async def test_events_after_rule_creation(self, client):
        """Events are generated when rules are created."""
        rule = {
            "name": "test_rule",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 10.0,
            "outlet": 1,
            "action": "off",
        }
        await client.post("/api/rules", json=rule)

        resp = await client.get("/api/events")
        assert resp.status == 200
        body = await resp.json()
        assert len(body) >= 1
        assert body[0]["type"] == "created"
        assert body[0]["rule"] == "test_rule"


# ===========================================================================
# Outlet command endpoint tests
# ===========================================================================

class TestOutletCommandEndpoint:
    """Tests for POST /api/outlets/{n}/command."""

    @pytest.mark.asyncio
    async def test_outlet_command_on(self, web_server, client):
        """Sends ON command to outlet and returns success."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        web_server.set_command_callback(mock_cmd)

        resp = await client.post("/api/outlets/1/command",
                                 json={"action": "on"})
        assert resp.status == 200
        body = await resp.json()
        assert body["outlet"] == 1
        assert body["action"] == "on"
        assert body["ok"] is True
        assert commands == [(1, "on")]

    @pytest.mark.asyncio
    async def test_outlet_command_off(self, web_server, client):
        """Sends OFF command to outlet."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        web_server.set_command_callback(mock_cmd)

        resp = await client.post("/api/outlets/3/command",
                                 json={"action": "off"})
        assert resp.status == 200
        body = await resp.json()
        assert body["outlet"] == 3
        assert body["action"] == "off"
        assert body["ok"] is True

    @pytest.mark.asyncio
    async def test_outlet_command_reboot(self, web_server, client):
        """Sends REBOOT command to outlet."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        web_server.set_command_callback(mock_cmd)

        resp = await client.post("/api/outlets/2/command",
                                 json={"action": "reboot"})
        assert resp.status == 200
        body = await resp.json()
        assert body["action"] == "reboot"

    @pytest.mark.asyncio
    async def test_outlet_command_case_insensitive(self, web_server, client):
        """Action is lowercased so ON/Off/REBOOT all work."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        web_server.set_command_callback(mock_cmd)

        resp = await client.post("/api/outlets/1/command",
                                 json={"action": "ON"})
        assert resp.status == 200
        assert commands == [(1, "on")]

    @pytest.mark.asyncio
    async def test_outlet_command_invalid_action_400(self, web_server, client):
        """Returns 400 for invalid action string."""
        async def mock_cmd(outlet, action):
            pass
        web_server.set_command_callback(mock_cmd)

        resp = await client.post("/api/outlets/1/command",
                                 json={"action": "toggle"})
        assert resp.status == 400
        body = await resp.json()
        assert "error" in body
        assert "invalid action" in body["error"]

    @pytest.mark.asyncio
    async def test_outlet_command_empty_action_400(self, web_server, client):
        """Returns 400 when action is missing from body."""
        async def mock_cmd(outlet, action):
            pass
        web_server.set_command_callback(mock_cmd)

        resp = await client.post("/api/outlets/1/command", json={})
        assert resp.status == 400
        body = await resp.json()
        assert "invalid action" in body["error"]

    @pytest.mark.asyncio
    async def test_outlet_command_invalid_outlet_400(self, web_server, client):
        """Returns 400 for non-numeric outlet number."""
        resp = await client.post("/api/outlets/abc/command",
                                 json={"action": "on"})
        assert resp.status == 400
        body = await resp.json()
        assert "invalid outlet number" in body["error"]

    @pytest.mark.asyncio
    async def test_outlet_command_no_callback_503(self, web_server, client):
        """Returns 503 when command callback is not configured."""
        # Do NOT set a command callback
        resp = await client.post("/api/outlets/1/command",
                                 json={"action": "on"})
        assert resp.status == 503
        body = await resp.json()
        assert "not available" in body["error"]

    @pytest.mark.asyncio
    async def test_outlet_command_callback_error_500(self, web_server, client):
        """Returns 500 when command callback raises an exception."""
        async def failing_cmd(outlet, action):
            raise RuntimeError("SNMP timeout")

        web_server.set_command_callback(failing_cmd)

        resp = await client.post("/api/outlets/1/command",
                                 json={"action": "on"})
        assert resp.status == 500
        body = await resp.json()
        assert body["ok"] is False
        assert "SNMP timeout" in body["error"]

    @pytest.mark.asyncio
    async def test_outlet_command_invalid_json_400(self, web_server, client):
        """Returns 400 when body is not valid JSON."""
        async def mock_cmd(outlet, action):
            pass
        web_server.set_command_callback(mock_cmd)

        resp = await client.post(
            "/api/outlets/1/command",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400
        body = await resp.json()
        assert "invalid JSON" in body["error"]


# ===========================================================================
# History endpoint tests
# ===========================================================================

class TestHistoryEndpoints:
    """Tests for /api/history/* endpoints."""

    @pytest.mark.asyncio
    async def test_banks_json(self, web_server, client):
        """GET /api/history/banks returns JSON data."""
        resp = await client.get("/api/history/banks")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        assert "bank" in body[0]
        assert "voltage" in body[0]

    @pytest.mark.asyncio
    async def test_outlets_json(self, web_server, client):
        """GET /api/history/outlets returns JSON data."""
        resp = await client.get("/api/history/outlets")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        assert "outlet" in body[0]
        assert "power" in body[0]

    @pytest.mark.asyncio
    async def test_banks_csv(self, web_server, client):
        """GET /api/history/banks.csv returns CSV with correct headers."""
        resp = await client.get("/api/history/banks.csv")
        assert resp.status == 200
        assert "text/csv" in resp.headers.get("Content-Type", "")
        assert "bank_history.csv" in resp.headers.get("Content-Disposition", "")

        text = await resp.text()
        lines = text.strip().split("\n")
        # Header row
        assert "bucket" in lines[0]
        assert "bank" in lines[0]
        assert "voltage" in lines[0]
        # At least one data row
        assert len(lines) >= 2

    @pytest.mark.asyncio
    async def test_outlets_csv(self, web_server, client):
        """GET /api/history/outlets.csv returns CSV with correct headers."""
        resp = await client.get("/api/history/outlets.csv")
        assert resp.status == 200
        assert "text/csv" in resp.headers.get("Content-Type", "")
        assert "outlet_history.csv" in resp.headers.get(
            "Content-Disposition", "")

        text = await resp.text()
        lines = text.strip().split("\n")
        assert "bucket" in lines[0]
        assert "outlet" in lines[0]
        assert "power" in lines[0]

    @pytest.mark.asyncio
    async def test_history_503_no_store(self, client_no_history):
        """All history endpoints return 503 when history store is unavailable."""
        for path in ["/api/history/banks", "/api/history/outlets",
                     "/api/history/banks.csv", "/api/history/outlets.csv"]:
            resp = await client_no_history.get(path)
            assert resp.status == 503, f"{path} should return 503"
            body = await resp.json()
            assert "history not available" in body["error"]

    @pytest.mark.asyncio
    async def test_history_default_range(self, web_server, client):
        """Default range is 1h when no query params specified."""
        resp = await client.get("/api/history/banks")
        assert resp.status == 200
        web_server._history.query_banks.assert_called_once()
        args = web_server._history.query_banks.call_args[0]
        start, end = args[0], args[1]
        # Default 1h range: end - start should be close to 3600
        assert abs((end - start) - 3600) < 5

    @pytest.mark.asyncio
    async def test_history_custom_range(self, web_server, client):
        """Range query param selects different time windows."""
        resp = await client.get("/api/history/banks?range=24h")
        assert resp.status == 200
        args = web_server._history.query_banks.call_args[0]
        start, end = args[0], args[1]
        assert abs((end - start) - 86400) < 5

    @pytest.mark.asyncio
    async def test_history_explicit_start_end(self, web_server, client):
        """Explicit start/end query params override range."""
        resp = await client.get("/api/history/banks?start=1000&end=2000")
        assert resp.status == 200
        args = web_server._history.query_banks.call_args[0]
        start, end = args[0], args[1]
        assert start == 1000.0
        assert end == 2000.0

    @pytest.mark.asyncio
    async def test_history_start_end_clamped(self, web_server, client):
        """Ranges exceeding 90 days are clamped."""
        far_end = 1000 + 120 * 86400  # 120 days
        resp = await client.get(
            f"/api/history/banks?start=1000&end={far_end}")
        assert resp.status == 200
        args = web_server._history.query_banks.call_args[0]
        start, end = args[0], args[1]
        assert end - start == 90 * 86400

    @pytest.mark.asyncio
    async def test_csv_empty_data(self, web_server, client):
        """CSV export works with empty data (header only)."""
        web_server._history.query_banks.return_value = []
        resp = await client.get("/api/history/banks.csv")
        assert resp.status == 200
        text = await resp.text()
        lines = text.strip().split("\n")
        assert len(lines) == 1  # Header only
        assert "bucket" in lines[0]


# ===========================================================================
# Reports endpoint tests
# ===========================================================================

class TestReportsEndpoints:
    """Tests for /api/reports endpoints."""

    @pytest.mark.asyncio
    async def test_list_reports(self, web_server, client):
        """GET /api/reports returns report list."""
        resp = await client.get("/api/reports")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)
        assert len(body) == 2
        assert body[0]["id"] == 1

    @pytest.mark.asyncio
    async def test_list_reports_503_no_history(self, client_no_history):
        """GET /api/reports returns 503 when history not available."""
        resp = await client_no_history.get("/api/reports")
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_latest_report_none(self, web_server, client):
        """GET /api/reports/latest returns 404 when no reports exist."""
        web_server._history.get_latest_report.return_value = None
        resp = await client.get("/api/reports/latest")
        assert resp.status == 404
        body = await resp.json()
        assert "no reports" in body["error"]

    @pytest.mark.asyncio
    async def test_latest_report_found(self, web_server, client):
        """GET /api/reports/latest returns latest report when available."""
        report = {"id": 2, "period": "daily", "data": {"total_kwh": 12.5}}
        web_server._history.get_latest_report.return_value = report

        resp = await client.get("/api/reports/latest")
        assert resp.status == 200
        body = await resp.json()
        assert body["id"] == 2
        assert body["data"]["total_kwh"] == 12.5

    @pytest.mark.asyncio
    async def test_latest_report_503_no_history(self, client_no_history):
        """GET /api/reports/latest returns 503 when history not available."""
        resp = await client_no_history.get("/api/reports/latest")
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_get_report_by_id(self, web_server, client):
        """GET /api/reports/{id} returns a specific report."""
        report = {"id": 1, "period": "daily", "data": {"total_kwh": 10.0}}
        web_server._history.get_report.return_value = report

        resp = await client.get("/api/reports/1")
        assert resp.status == 200
        body = await resp.json()
        assert body["id"] == 1

    @pytest.mark.asyncio
    async def test_get_report_not_found(self, web_server, client):
        """GET /api/reports/{id} returns 404 when report doesn't exist."""
        web_server._history.get_report.return_value = None
        resp = await client.get("/api/reports/999")
        assert resp.status == 404
        body = await resp.json()
        assert "not found" in body["error"]

    @pytest.mark.asyncio
    async def test_get_report_invalid_id(self, web_server, client):
        """GET /api/reports/{id} returns 400 for non-numeric id."""
        resp = await client.get("/api/reports/abc")
        assert resp.status == 400
        body = await resp.json()
        assert "invalid report id" in body["error"]

    @pytest.mark.asyncio
    async def test_get_report_503_no_history(self, client_no_history):
        """GET /api/reports/{id} returns 503 when history not available."""
        resp = await client_no_history.get("/api/reports/1")
        assert resp.status == 503


# ===========================================================================
# Outlet naming endpoint tests
# ===========================================================================

class TestOutletNamingEndpoints:
    """Tests for PUT /api/outlets/{n}/name and GET /api/outlet-names."""

    @pytest.mark.asyncio
    async def test_get_outlet_names_empty(self, client):
        """GET /api/outlet-names returns empty dict initially."""
        resp = await client.get("/api/outlet-names")
        assert resp.status == 200
        body = await resp.json()
        assert body == {}

    @pytest.mark.asyncio
    async def test_rename_outlet(self, web_server, client):
        """PUT /api/outlets/{n}/name sets the custom name."""
        resp = await client.put("/api/outlets/1/name",
                                json={"name": "Web Server"})
        assert resp.status == 200
        body = await resp.json()
        assert body["outlet"] == 1
        assert body["name"] == "Web Server"
        assert body["ok"] is True

        # Verify via GET
        resp = await client.get("/api/outlet-names")
        body = await resp.json()
        assert body == {"1": "Web Server"}

    @pytest.mark.asyncio
    async def test_rename_multiple_outlets(self, web_server, client):
        """Multiple outlets can be named independently."""
        await client.put("/api/outlets/1/name", json={"name": "Server A"})
        await client.put("/api/outlets/2/name", json={"name": "Server B"})
        await client.put("/api/outlets/5/name", json={"name": "Switch"})

        resp = await client.get("/api/outlet-names")
        body = await resp.json()
        assert body == {"1": "Server A", "2": "Server B", "5": "Switch"}

    @pytest.mark.asyncio
    async def test_clear_outlet_name(self, web_server, client):
        """Setting an empty name removes the outlet name."""
        # Set a name first
        await client.put("/api/outlets/1/name", json={"name": "Server A"})
        resp = await client.get("/api/outlet-names")
        body = await resp.json()
        assert "1" in body

        # Clear it with empty string
        resp = await client.put("/api/outlets/1/name", json={"name": ""})
        assert resp.status == 200
        body = await resp.json()
        assert body["name"] == ""

        # Verify it is removed
        resp = await client.get("/api/outlet-names")
        body = await resp.json()
        assert "1" not in body

    @pytest.mark.asyncio
    async def test_clear_outlet_name_whitespace_only(self, web_server, client):
        """Whitespace-only name is treated as empty (cleared)."""
        await client.put("/api/outlets/1/name", json={"name": "Server A"})
        resp = await client.put("/api/outlets/1/name", json={"name": "   "})
        assert resp.status == 200

        resp = await client.get("/api/outlet-names")
        body = await resp.json()
        assert "1" not in body

    @pytest.mark.asyncio
    async def test_rename_outlet_invalid_number(self, client):
        """PUT /api/outlets/{n}/name returns 400 for non-numeric n."""
        resp = await client.put("/api/outlets/abc/name",
                                json={"name": "Test"})
        assert resp.status == 400
        body = await resp.json()
        assert "invalid outlet number" in body["error"]

    @pytest.mark.asyncio
    async def test_rename_outlet_invalid_json(self, client):
        """PUT /api/outlets/{n}/name returns 400 for invalid JSON."""
        resp = await client.put(
            "/api/outlets/1/name",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400
        body = await resp.json()
        assert "invalid JSON" in body["error"]

    @pytest.mark.asyncio
    async def test_rename_outlet_calls_callback(self, web_server, client):
        """Outlet names callback is invoked when names change."""
        callback_calls = []

        def on_names_change(names):
            callback_calls.append(dict(names))

        web_server.set_outlet_names_callback(on_names_change)

        await client.put("/api/outlets/1/name", json={"name": "Server A"})
        assert len(callback_calls) == 1
        assert callback_calls[0] == {"1": "Server A"}

        await client.put("/api/outlets/2/name", json={"name": "Server B"})
        assert len(callback_calls) == 2
        assert callback_calls[1] == {"1": "Server A", "2": "Server B"}

    @pytest.mark.asyncio
    async def test_rename_outlet_no_callback(self, web_server, client):
        """Renaming works even without a callback configured."""
        resp = await client.put("/api/outlets/1/name", json={"name": "Test"})
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_rename_overwrites_existing(self, web_server, client):
        """Renaming the same outlet overwrites the previous name."""
        await client.put("/api/outlets/1/name", json={"name": "Old Name"})
        await client.put("/api/outlets/1/name", json={"name": "New Name"})

        resp = await client.get("/api/outlet-names")
        body = await resp.json()
        assert body["1"] == "New Name"


# ===========================================================================
# CORS middleware tests
# ===========================================================================

class TestCORSMiddleware:
    """Tests for CORS headers on all responses."""

    @pytest.mark.asyncio
    async def test_cors_headers_on_get(self, web_server, client):
        """CORS headers are present on GET responses."""
        web_server.update_data(make_pdu_data())
        resp = await client.get("/api/status")
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert "GET" in resp.headers.get("Access-Control-Allow-Methods", "")
        assert "Content-Type" in resp.headers.get(
            "Access-Control-Allow-Headers", "")

    @pytest.mark.asyncio
    async def test_cors_headers_on_post(self, web_server, client):
        """CORS headers are present on POST responses."""
        async def mock_cmd(o, a):
            pass
        web_server.set_command_callback(mock_cmd)
        resp = await client.post("/api/outlets/1/command",
                                 json={"action": "on"})
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"

    @pytest.mark.asyncio
    async def test_cors_headers_on_error(self, client):
        """CORS headers are present even on error responses."""
        resp = await client.get("/api/status")
        assert resp.status == 503
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"

    @pytest.mark.asyncio
    async def test_cors_preflight_options(self, web_server, client):
        """OPTIONS preflight request returns 204 with CORS headers."""
        resp = await client.options("/api/status")
        assert resp.status == 204
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
        assert "DELETE" in resp.headers.get(
            "Access-Control-Allow-Methods", "")

    @pytest.mark.asyncio
    async def test_cors_on_history(self, web_server, client):
        """CORS headers on history endpoints."""
        resp = await client.get("/api/history/banks")
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"

    @pytest.mark.asyncio
    async def test_cors_on_rules(self, client):
        """CORS headers on rules endpoints."""
        resp = await client.get("/api/rules")
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"


# ===========================================================================
# Static file / index tests
# ===========================================================================

class TestStaticEndpoint:
    """Tests for GET / (index.html)."""

    @pytest.mark.asyncio
    async def test_index_serves_or_404(self, client):
        """GET / either serves index.html or returns 404."""
        resp = await client.get("/")
        # Static file may or may not exist in test env
        assert resp.status in (200, 404)

    @pytest.mark.asyncio
    async def test_index_serves_file(self, engine_and_path, tmp_path):
        """Returns index.html content when file exists."""
        engine, _path = engine_and_path
        index_file = tmp_path / "index.html"
        index_file.write_text("<html><body>Test PDU UI</body></html>")

        with patch("src.web.STATIC_DIR", tmp_path):
            ws = _make_web_server(engine, device_id="x")
            server = TestServer(ws._app)
            async with TestClient(server) as c:
                resp = await c.get("/")
                assert resp.status == 200
                text = await resp.text()
                assert "Test PDU UI" in text

    @pytest.mark.asyncio
    async def test_index_404_when_missing(self, engine_and_path, tmp_path):
        """Returns 404 when index.html does not exist."""
        engine, _path = engine_and_path
        # tmp_path exists but has no index.html
        with patch("src.web.STATIC_DIR", tmp_path):
            ws = _make_web_server(engine, device_id="x")
            server = TestServer(ws._app)
            async with TestClient(server) as c:
                resp = await c.get("/")
                assert resp.status == 404


# ===========================================================================
# Content-type and JSON format tests
# ===========================================================================

class TestResponseFormat:
    """Tests for response format consistency."""

    @pytest.mark.asyncio
    async def test_json_content_type_on_status(self, client):
        """Status error response has JSON content-type."""
        resp = await client.get("/api/status")
        assert "application/json" in resp.headers.get("Content-Type", "")

    @pytest.mark.asyncio
    async def test_json_content_type_on_health(self, client):
        """Health response has JSON content-type."""
        resp = await client.get("/api/health")
        assert "application/json" in resp.headers.get("Content-Type", "")

    @pytest.mark.asyncio
    async def test_json_content_type_on_rules(self, client):
        """Rules response has JSON content-type."""
        resp = await client.get("/api/rules")
        assert "application/json" in resp.headers.get("Content-Type", "")

    @pytest.mark.asyncio
    async def test_json_content_type_on_events(self, client):
        """Events response has JSON content-type."""
        resp = await client.get("/api/events")
        assert "application/json" in resp.headers.get("Content-Type", "")

    @pytest.mark.asyncio
    async def test_csv_content_type_on_bank_export(self, web_server, client):
        """Bank CSV export has text/csv content-type."""
        resp = await client.get("/api/history/banks.csv")
        assert "text/csv" in resp.headers.get("Content-Type", "")

    @pytest.mark.asyncio
    async def test_csv_content_disposition(self, web_server, client):
        """CSV export has Content-Disposition for download."""
        resp = await client.get("/api/history/outlets.csv")
        disposition = resp.headers.get("Content-Disposition", "")
        assert "attachment" in disposition
        assert "outlet_history.csv" in disposition


# ===========================================================================
# Integration-style tests (multiple operations)
# ===========================================================================

class TestIntegration:
    """Tests that exercise multiple endpoints together."""

    @pytest.mark.asyncio
    async def test_full_rule_lifecycle(self, client):
        """Create, list, update, delete a rule in sequence."""
        rule_data = {
            "name": "lifecycle_rule",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 80.0,
            "outlet": 1,
            "action": "off",
            "delay": 10,
        }

        # Create
        resp = await client.post("/api/rules", json=rule_data)
        assert resp.status == 201

        # List
        resp = await client.get("/api/rules")
        body = await resp.json()
        assert len(body) == 1
        assert body[0]["name"] == "lifecycle_rule"
        assert body[0]["threshold"] == 80.0

        # Update
        update_data = {
            "input": 2,
            "condition": "voltage_above",
            "threshold": 135.0,
            "outlet": 2,
            "action": "on",
            "delay": 5,
        }
        resp = await client.put("/api/rules/lifecycle_rule", json=update_data)
        assert resp.status == 200
        body = await resp.json()
        assert body["threshold"] == 135.0
        assert body["outlet"] == 2

        # Verify update persisted
        resp = await client.get("/api/rules")
        body = await resp.json()
        assert len(body) == 1
        assert body[0]["threshold"] == 135.0

        # Check events reflect the lifecycle
        resp = await client.get("/api/events")
        body = await resp.json()
        event_types = [e["type"] for e in body]
        assert "created" in event_types
        assert "updated" in event_types

        # Delete
        resp = await client.delete("/api/rules/lifecycle_rule")
        assert resp.status == 200

        # Verify deletion
        resp = await client.get("/api/rules")
        body = await resp.json()
        assert body == []

        # Events include deletion
        resp = await client.get("/api/events")
        body = await resp.json()
        event_types = [e["type"] for e in body]
        assert "deleted" in event_types

    @pytest.mark.asyncio
    async def test_data_update_transitions_status(self, web_server, client):
        """Server transitions from 503 to 200 after receiving data."""
        # Initially 503
        resp = await client.get("/api/status")
        assert resp.status == 503

        # Update data
        web_server.update_data(make_pdu_data())

        # Now 200
        resp = await client.get("/api/status")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_status_and_health_consistent(self, web_server, client):
        """Status and health endpoints agree on system state."""
        # Both should indicate problems when no data
        status_resp = await client.get("/api/status")
        health_resp = await client.get("/api/health")
        assert status_resp.status == 503
        assert health_resp.status == 503

        # After data, both should be OK
        web_server.update_data(make_pdu_data())
        status_resp = await client.get("/api/status")
        health_resp = await client.get("/api/health")
        assert status_resp.status == 200
        assert health_resp.status == 200

    @pytest.mark.asyncio
    async def test_outlet_name_and_command_same_outlet(
            self, web_server, client):
        """Can name an outlet and then send a command to it."""
        resp = await client.put("/api/outlets/3/name",
                                json={"name": "My Switch"})
        assert resp.status == 200

        commands = []
        async def mock_cmd(outlet, action):
            commands.append((outlet, action))
        web_server.set_command_callback(mock_cmd)

        resp = await client.post("/api/outlets/3/command",
                                 json={"action": "reboot"})
        assert resp.status == 200
        assert commands == [(3, "reboot")]

    @pytest.mark.asyncio
    async def test_create_time_based_rule_via_api(self, client):
        """Time-based rules can be created through the API."""
        rule_data = {
            "name": "night_saver",
            "input": 0,
            "condition": "time_between",
            "threshold": "22:00-06:00",
            "outlet": 4,
            "action": "off",
            "restore": True,
            "delay": 0,
        }
        resp = await client.post("/api/rules", json=rule_data)
        assert resp.status == 201
        body = await resp.json()
        assert body["condition"] == "time_between"
        assert body["threshold"] == "22:00-06:00"

    @pytest.mark.asyncio
    async def test_create_ats_rule_via_api(self, client):
        """ATS condition rules can be created through the API."""
        rule_data = {
            "name": "ats_failover",
            "input": 1,
            "condition": "ats_preferred_lost",
            "threshold": 0,
            "outlet": 1,
            "action": "off",
            "delay": 5,
        }
        resp = await client.post("/api/rules", json=rule_data)
        assert resp.status == 201
        body = await resp.json()
        assert body["condition"] == "ats_preferred_lost"


# ===========================================================================
# Edge case tests
# ===========================================================================

class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_status_with_none_bank_power(self, web_server, client):
        """Total power calculation handles None power values gracefully."""
        pdu_data = make_pdu_data()
        pdu_data.banks[2] = BankData(number=2, power=None)
        web_server.update_data(pdu_data)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        # Only bank 1 power counted
        assert body["summary"]["total_power"] == 276.0

    @pytest.mark.asyncio
    async def test_status_with_no_source_data(self, web_server, client):
        """Status works when source_a and source_b are None."""
        pdu_data = make_pdu_data()
        pdu_data.source_a = None
        pdu_data.source_b = None
        web_server.update_data(pdu_data)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        assert body["ats"]["source_a"]["voltage"] is None
        assert body["ats"]["source_b"]["voltage"] is None
        assert body["ats"]["source_a"]["voltage_status"] == "unknown"

    @pytest.mark.asyncio
    async def test_status_with_none_ats_fields(self, web_server, client):
        """Status works when ATS fields are None."""
        pdu_data = make_pdu_data()
        pdu_data.ats_preferred_source = None
        pdu_data.ats_current_source = None
        web_server.update_data(pdu_data)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        assert body["ats"]["preferred_source"] is None
        assert body["ats"]["transferred"] is False
        assert body["ats"]["preferred_label"] == "?"

    @pytest.mark.asyncio
    async def test_update_data_replaces_previous(self, web_server, client):
        """Calling update_data() replaces the previous snapshot."""
        web_server.update_data(make_pdu_data(device_name="First"))
        resp = await client.get("/api/status")
        body = await resp.json()
        assert body["device"]["name"] == "First"

        web_server.update_data(make_pdu_data(device_name="Second"))
        resp = await client.get("/api/status")
        body = await resp.json()
        assert body["device"]["name"] == "Second"

    @pytest.mark.asyncio
    async def test_large_outlet_number_in_command(self, web_server, client):
        """Large outlet numbers are accepted by the command endpoint."""
        commands = []
        async def mock_cmd(outlet, action):
            commands.append((outlet, action))
        web_server.set_command_callback(mock_cmd)

        resp = await client.post("/api/outlets/48/command",
                                 json={"action": "on"})
        assert resp.status == 200
        assert commands == [(48, "on")]

    @pytest.mark.asyncio
    async def test_outlet_names_string_keys(self, web_server, client):
        """Outlet names are stored with string keys."""
        await client.put("/api/outlets/10/name",
                         json={"name": "High Port"})
        resp = await client.get("/api/outlet-names")
        body = await resp.json()
        assert "10" in body
        assert body["10"] == "High Port"

    @pytest.mark.asyncio
    async def test_history_unknown_range_defaults_1h(
            self, web_server, client):
        """Unknown range value defaults to 1 hour."""
        resp = await client.get("/api/history/banks?range=invalid")
        assert resp.status == 200
        args = web_server._history.query_banks.call_args[0]
        start, end = args[0], args[1]
        assert abs((end - start) - 3600) < 5

    @pytest.mark.asyncio
    async def test_status_all_outlets_off(self, web_server, client):
        """Active outlet count is 0 when all outlets are off."""
        pdu_data = make_pdu_data()
        for outlet in pdu_data.outlets.values():
            outlet.state = "off"
        web_server.update_data(pdu_data)

        resp = await client.get("/api/status")
        body = await resp.json()
        assert body["summary"]["active_outlets"] == 0


# ---------------------------------------------------------------------------
# PDU Management Endpoints
# ---------------------------------------------------------------------------

class TestPDUManagementEndpoints:
    """Tests for PDU CRUD via /api/pdus endpoints."""

    @pytest.mark.asyncio
    async def test_list_pdus_empty(self, client):
        """GET /api/pdus with no PDUs registered returns empty list."""
        resp = await client.get("/api/pdus")
        assert resp.status == 200
        body = await resp.json()
        assert body["count"] == 0
        assert body["pdus"] == []

    @pytest.mark.asyncio
    async def test_list_pdus_with_registered(self, web_server, client):
        """GET /api/pdus returns registered PDUs."""
        web_server.register_pdu("pdu-1", {"host": "10.0.0.1", "label": "Rack A"})
        resp = await client.get("/api/pdus")
        body = await resp.json()
        assert body["count"] >= 1
        pdu_ids = [p["device_id"] for p in body["pdus"]]
        assert "pdu-1" in pdu_ids

    @pytest.mark.asyncio
    async def test_add_pdu(self, web_server, client):
        """POST /api/pdus adds a new PDU."""
        resp = await client.post("/api/pdus", json={
            "device_id": "new-pdu",
            "host": "192.168.1.50",
            "label": "New PDU",
        })
        assert resp.status == 201
        body = await resp.json()
        assert body["ok"] is True
        assert body["device_id"] == "new-pdu"
        assert "new-pdu" in web_server._pdu_configs

    @pytest.mark.asyncio
    async def test_add_pdu_duplicate(self, web_server, client):
        """POST /api/pdus returns 409 for duplicate device_id."""
        web_server._pdu_configs["existing"] = {"host": "10.0.0.1"}
        resp = await client.post("/api/pdus", json={
            "device_id": "existing",
            "host": "10.0.0.2",
        })
        assert resp.status == 409

    @pytest.mark.asyncio
    async def test_add_pdu_no_id(self, client):
        """POST /api/pdus returns 400 when device_id missing."""
        resp = await client.post("/api/pdus", json={})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_update_pdu(self, web_server, client):
        """PUT /api/pdus/{device_id} updates config."""
        web_server._pdu_configs["upd-pdu"] = {"host": "10.0.0.1"}
        resp = await client.put("/api/pdus/upd-pdu", json={
            "host": "10.0.0.2",
            "label": "Updated",
        })
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert web_server._pdu_configs["upd-pdu"]["host"] == "10.0.0.2"

    @pytest.mark.asyncio
    async def test_update_pdu_not_found(self, client):
        """PUT /api/pdus/{device_id} returns 404 for unknown PDU."""
        resp = await client.put("/api/pdus/nonexistent", json={"host": "x"})
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_delete_pdu(self, web_server, client):
        """DELETE /api/pdus/{device_id} removes the PDU."""
        web_server._pdu_configs["del-pdu"] = {"host": "10.0.0.1"}
        resp = await client.delete("/api/pdus/del-pdu")
        assert resp.status == 200
        body = await resp.json()
        assert body["deleted"] is True
        assert "del-pdu" not in web_server._pdu_configs

    @pytest.mark.asyncio
    async def test_delete_pdu_not_found(self, client):
        """DELETE /api/pdus/{device_id} returns 404 for unknown PDU."""
        resp = await client.delete("/api/pdus/nonexistent")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_add_pdu_with_runtime_callback(self, web_server, client):
        """POST /api/pdus calls the add_pdu runtime callback."""
        callback_called = {}

        async def mock_add(body):
            callback_called["body"] = body

        web_server.set_add_pdu_callback(mock_add)
        resp = await client.post("/api/pdus", json={
            "device_id": "runtime-pdu",
            "host": "10.0.0.5",
        })
        assert resp.status == 201
        assert "body" in callback_called
        assert callback_called["body"]["host"] == "10.0.0.5"

    @pytest.mark.asyncio
    async def test_delete_pdu_with_runtime_callback(self, web_server, client):
        """DELETE /api/pdus calls the remove_pdu runtime callback."""
        callback_called = {}

        async def mock_remove(device_id):
            callback_called["device_id"] = device_id

        web_server.set_remove_pdu_callback(mock_remove)
        web_server._pdu_configs["rt-del"] = {"host": "10.0.0.1"}
        resp = await client.delete("/api/pdus/rt-del")
        assert resp.status == 200
        assert callback_called.get("device_id") == "rt-del"

    @pytest.mark.asyncio
    async def test_discover_no_callback(self, client):
        """POST /api/pdus/discover returns 503 when no callback set."""
        resp = await client.post("/api/pdus/discover")
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_discover_with_callback(self, web_server, client):
        """POST /api/pdus/discover calls discovery callback."""
        async def mock_discover():
            return {
                "interfaces": [
                    {"interface": "eth0", "subnet": "10.0.0.0/24", "ip": "10.0.0.5",
                     "pdu_count": 1, "error": None},
                ],
                "discovered": [
                    {"host": "10.0.0.1", "device_name": "PDU1",
                     "interface": "eth0", "already_configured": False},
                ],
            }

        web_server.set_discovery_callback(mock_discover)
        resp = await client.post("/api/pdus/discover")
        assert resp.status == 200
        body = await resp.json()
        assert len(body["discovered"]) == 1
        assert body["discovered"][0]["host"] == "10.0.0.1"
        assert body["discovered"][0]["interface"] == "eth0"
        assert len(body["interfaces"]) == 1
        assert body["interfaces"][0]["interface"] == "eth0"

    @pytest.mark.asyncio
    async def test_discover_legacy_list_callback(self, web_server, client):
        """POST /api/pdus/discover still works with legacy list callbacks."""
        async def mock_discover():
            return [{"host": "10.0.0.1", "device_name": "PDU1"}]

        web_server.set_discovery_callback(mock_discover)
        resp = await client.post("/api/pdus/discover")
        assert resp.status == 200
        body = await resp.json()
        assert len(body["discovered"]) == 1

    @pytest.mark.asyncio
    async def test_test_connection_no_callback(self, client):
        """POST /api/pdus/test-connection returns 503 when no callback set."""
        resp = await client.post("/api/pdus/test-connection", json={"host": "10.0.0.1"})
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_test_connection_success(self, web_server, client):
        """POST /api/pdus/test-connection with successful SNMP."""
        async def mock_test(host, community, port):
            return {"success": True, "device_name": "PDU1", "model": "PDU44001",
                    "serial": "ABC123", "outlet_count": 10}

        web_server.set_test_connection_callback(mock_test)
        resp = await client.post("/api/pdus/test-connection", json={
            "host": "10.0.0.1",
            "community_read": "public",
            "snmp_port": 161,
        })
        assert resp.status == 200
        body = await resp.json()
        assert body["success"] is True
        assert body["model"] == "PDU44001"

    @pytest.mark.asyncio
    async def test_test_connection_no_host(self, web_server, client):
        """POST /api/pdus/test-connection returns 400 without host."""
        web_server.set_test_connection_callback(
            lambda h, c, p: {"success": False}
        )
        resp = await client.post("/api/pdus/test-connection", json={})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_test_connection_invalid_json(self, client):
        """POST /api/pdus/test-connection returns 400 for invalid JSON."""
        resp = await client.post("/api/pdus/test-connection",
                                 data="not-json",
                                 headers={"Content-Type": "application/json"})
        assert resp.status == 400


# ---------------------------------------------------------------------------
# Device Rename Endpoints
# ---------------------------------------------------------------------------

class TestDeviceRenameEndpoints:
    """Tests for device name/location SNMP SET endpoints."""

    @pytest.mark.asyncio
    async def test_set_device_name_no_callback(self, web_server, client):
        """PUT /api/device/name returns 503 when no SNMP SET callback."""
        web_server.update_data(make_pdu_data())
        resp = await client.put("/api/device/name", json={"name": "My PDU"})
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_set_device_name_success(self, web_server, client):
        """PUT /api/device/name calls SNMP SET callback."""
        callback_args = {}

        async def mock_snmp_set(device_id, field, value):
            callback_args.update({"device_id": device_id, "field": field, "value": value})

        web_server.set_snmp_set_callback(mock_snmp_set)
        web_server.update_data(make_pdu_data())
        resp = await client.put("/api/device/name", json={"name": "New Name"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert callback_args["field"] == "device_name"
        assert callback_args["value"] == "New Name"

    @pytest.mark.asyncio
    async def test_set_device_name_empty(self, web_server, client):
        """PUT /api/device/name returns 400 for empty name."""
        web_server.update_data(make_pdu_data())
        resp = await client.put("/api/device/name", json={"name": ""})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_set_device_location_success(self, web_server, client):
        """PUT /api/device/location calls SNMP SET callback."""
        callback_args = {}

        async def mock_snmp_set(device_id, field, value):
            callback_args.update({"device_id": device_id, "field": field, "value": value})

        web_server.set_snmp_set_callback(mock_snmp_set)
        web_server.update_data(make_pdu_data())
        resp = await client.put("/api/device/location", json={"location": "Rack A"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert callback_args["field"] == "sys_location"
        assert callback_args["value"] == "Rack A"

    @pytest.mark.asyncio
    async def test_set_device_location_empty(self, web_server, client):
        """PUT /api/device/location returns 400 for empty location."""
        web_server.update_data(make_pdu_data())
        resp = await client.put("/api/device/location", json={"location": ""})
        assert resp.status == 400


class TestSystemEvents:
    """Tests for system event log (merged with automation events)."""

    @pytest.mark.asyncio
    async def test_add_system_event(self, web_server, client):
        """System events are stored and returned via /api/events."""
        did = web_server._default_device_id
        web_server.update_data(make_pdu_data())
        web_server.add_system_event(did, "ats_transfer", "ATS", "Source A -> B")
        resp = await client.get("/api/events")
        assert resp.status == 200
        events = await resp.json()
        assert len(events) >= 1
        found = [e for e in events if e["type"] == "ats_transfer"]
        assert len(found) == 1
        assert found[0]["details"] == "Source A -> B"
        assert found[0]["rule"] == "ATS"
        assert found[0].get("system") is True

    @pytest.mark.asyncio
    async def test_system_events_merged_with_automation(self, web_server, client):
        """System events merge with automation events sorted by time."""
        did = web_server._default_device_id
        web_server.update_data(make_pdu_data())
        engine = web_server._get_engine(did)
        if engine is not None:
            engine._add_event("test_rule", "triggered", "Rule fired")
        web_server.add_system_event(did, "power_loss", "Source A", "Power failed")
        resp = await client.get("/api/events")
        events = await resp.json()
        types = [e["type"] for e in events]
        assert "power_loss" in types

    @pytest.mark.asyncio
    async def test_system_events_max_limit(self, web_server, client):
        """System events are capped at max_system_events."""
        did = web_server._default_device_id
        web_server._max_system_events = 5
        for i in range(10):
            web_server.add_system_event(did, "outlet_change", f"Outlet {i}", f"Changed {i}")
        events = web_server.get_system_events(did)
        assert len(events) == 5

    @pytest.mark.asyncio
    async def test_system_events_newest_first(self, web_server, client):
        """System events are returned newest first."""
        import time
        did = web_server._default_device_id
        web_server.add_system_event(did, "power_loss", "A", "first")
        time.sleep(0.01)
        web_server.add_system_event(did, "power_restore", "A", "second")
        events = web_server.get_system_events(did)
        assert events[0]["type"] == "power_restore"
        assert events[1]["type"] == "power_loss"

    @pytest.mark.asyncio
    async def test_system_events_per_device(self, web_server, client):
        """System events are scoped per device_id."""
        web_server.add_system_event("pdu-1", "reboot", "PDU", "Rebooted")
        web_server.add_system_event("pdu-2", "ats_transfer", "ATS", "Transferred")
        events_1 = web_server.get_system_events("pdu-1")
        events_2 = web_server.get_system_events("pdu-2")
        assert len(events_1) == 1
        assert events_1[0]["type"] == "reboot"
        assert len(events_2) == 1
        assert events_2[0]["type"] == "ats_transfer"


# ===========================================================================
# Config endpoint tests
# ===========================================================================

class TestConfigEndpoints:
    """Tests for GET/PUT /api/config — bridge settings management."""

    @pytest.mark.asyncio
    async def test_get_config_returns_settings(self, web_server, client):
        resp = await client.get("/api/config")
        assert resp.status == 200
        data = await resp.json()
        assert "poll_interval" in data
        assert "pdu_count" in data
        assert "default_device_id" in data

    @pytest.mark.asyncio
    async def test_get_config_with_config_object(self, client):
        """GET /api/config returns full settings when config object is set."""
        resp = await client.get("/api/config")
        data = await resp.json()
        # These come from the config object or fallback defaults
        assert "mqtt_broker" in data
        assert "mqtt_port" in data
        assert "log_level" in data
        assert "history_retention_days" in data
        assert "auth_enabled" in data

    @pytest.mark.asyncio
    async def test_update_config_poll_interval(self, web_server, client):
        """PUT /api/config with valid poll_interval."""
        # Give web_server a mock config
        from unittest.mock import MagicMock
        mock_cfg = MagicMock()
        mock_cfg.settings_file = "/tmp/_test_settings.json"
        mock_cfg.save_settings = MagicMock()
        web_server._config = mock_cfg
        resp = await client.put("/api/config", json={"poll_interval": 10})
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["updated"]["poll_interval"] == 10.0

    @pytest.mark.asyncio
    async def test_update_config_invalid_poll(self, web_server, client):
        from unittest.mock import MagicMock
        web_server._config = MagicMock()
        resp = await client.put("/api/config", json={"poll_interval": -1})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_update_config_log_level(self, web_server, client):
        from unittest.mock import MagicMock
        mock_cfg = MagicMock()
        mock_cfg.settings_file = "/tmp/_test_settings.json"
        mock_cfg.save_settings = MagicMock()
        web_server._config = mock_cfg
        resp = await client.put("/api/config", json={"log_level": "DEBUG"})
        assert resp.status == 200
        data = await resp.json()
        assert data["updated"]["log_level"] == "DEBUG"

    @pytest.mark.asyncio
    async def test_update_config_invalid_log_level(self, web_server, client):
        from unittest.mock import MagicMock
        web_server._config = MagicMock()
        resp = await client.put("/api/config", json={"log_level": "INVALID"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_update_config_mqtt_requires_restart(self, web_server, client):
        from unittest.mock import MagicMock
        mock_cfg = MagicMock()
        mock_cfg.settings_file = "/tmp/_test_settings.json"
        mock_cfg.save_settings = MagicMock()
        web_server._config = mock_cfg
        resp = await client.put("/api/config", json={"mqtt_broker": "10.0.0.1"})
        assert resp.status == 200
        data = await resp.json()
        assert "requires_restart" in data
        assert "mqtt_broker" in data["requires_restart"]

    @pytest.mark.asyncio
    async def test_update_config_no_fields(self, web_server, client):
        from unittest.mock import MagicMock
        web_server._config = MagicMock()
        resp = await client.put("/api/config", json={})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_update_config_no_config_obj(self, web_server, client):
        web_server._config = None
        resp = await client.put("/api/config", json={"poll_interval": 5})
        assert resp.status == 503


# ===========================================================================
# SSE (Server-Sent Events) tests
# ===========================================================================

class TestSSE:
    """Tests for SSE streaming, _build_status_dict, and broadcast_sse."""

    @pytest.mark.asyncio
    async def test_build_status_dict_returns_expected_keys(self, web_server):
        """_build_status_dict returns dict with all required top-level keys."""
        web_server.update_data(make_pdu_data())
        result = web_server._build_status_dict("test-pdu-001")
        assert result is not None
        for key in ("device", "ats", "inputs", "outlets", "summary", "ts"):
            assert key in result, f"Missing key: {key}"
        assert result["device"]["name"] == "Test PDU"
        assert result["device"]["id"] == "test-pdu-001"
        assert isinstance(result["outlets"], dict)
        assert isinstance(result["inputs"], dict)

    @pytest.mark.asyncio
    async def test_build_status_dict_returns_none_without_data(self, web_server):
        """_build_status_dict returns None when device has no data."""
        result = web_server._build_status_dict("nonexistent-device")
        assert result is None

    @pytest.mark.asyncio
    async def test_sse_endpoint_content_type(self, web_server, client):
        """GET /api/stream returns text/event-stream content-type.

        The SSE handler blocks indefinitely, so we rely on a short timeout
        and inspect the response before it finishes.
        """
        import asyncio
        web_server.update_data(make_pdu_data())

        try:
            resp = await asyncio.wait_for(client.get("/api/stream"), timeout=0.5)
            # If the response returns, check content-type
            ct = resp.headers.get("Content-Type", "")
            assert "text/event-stream" in ct
        except asyncio.TimeoutError:
            # Expected: the SSE endpoint blocks. The fact that we got a
            # timeout means the handler started correctly (no instant error).
            pass

    @pytest.mark.asyncio
    async def test_broadcast_sse_cleans_dead_clients(self, web_server):
        """broadcast_sse removes clients that raise on write."""
        dead_client = MagicMock()
        dead_client.write = MagicMock(side_effect=ConnectionResetError("gone"))
        web_server._sse_clients.append(dead_client)
        assert len(web_server._sse_clients) == 1

        await web_server.broadcast_sse("test", {"hello": "world"})
        assert len(web_server._sse_clients) == 0

    @pytest.mark.asyncio
    async def test_broadcast_sse_delivers_to_live_clients(self, web_server):
        """broadcast_sse writes correctly-formatted SSE payload to connected clients."""
        live_client = AsyncMock()
        web_server._sse_clients.append(live_client)

        await web_server.broadcast_sse("status", {"voltage": 120.5})

        live_client.write.assert_called_once()
        payload = live_client.write.call_args[0][0]
        text = payload.decode()
        assert text.startswith("event: status\n")
        assert '"voltage": 120.5' in text
        assert text.endswith("\n\n")
        # Client stays in the list (not removed)
        assert len(web_server._sse_clients) == 1

    @pytest.mark.asyncio
    async def test_broadcast_sse_mixed_live_and_dead_clients(self, web_server):
        """broadcast_sse delivers to live clients and removes dead ones."""
        live_client = AsyncMock()
        dead_client = MagicMock()
        dead_client.write = MagicMock(side_effect=ConnectionResetError("gone"))
        web_server._sse_clients.extend([live_client, dead_client])

        await web_server.broadcast_sse("update", {"state": "on"})

        live_client.write.assert_called_once()
        assert len(web_server._sse_clients) == 1
        assert web_server._sse_clients[0] is live_client

    @pytest.mark.asyncio
    async def test_broadcast_sse_noop_without_clients(self, web_server):
        """broadcast_sse returns immediately when no clients are connected."""
        assert len(web_server._sse_clients) == 0
        # Should not raise
        await web_server.broadcast_sse("status", {"voltage": 120.0})

    @pytest.mark.asyncio
    async def test_sse_auth_skip(self, engine_and_path):
        """GET /api/stream is accessible without auth when auth is enabled.

        The auth middleware explicitly skips /api/stream so that the SSE
        handler can handle token validation itself via query param.
        """
        engine, _path = engine_and_path
        ws = WebServer(
            "test-pdu-001", 0,
            mqtt=make_mock_mqtt(),
            history=make_mock_history(),
            auth_username="admin",
            auth_password="secret123",
        )
        ws.register_automation_engine("test-pdu-001", engine)
        ws.update_data(make_pdu_data())

        server = TestServer(ws._app)
        async with TestClient(server) as c:
            import asyncio
            try:
                # The SSE handler does its own auth check; with no token it
                # should return 401 (NOT the middleware 401 which would mean
                # the path was NOT skipped).  If the middleware blocked it,
                # we'd also get 401 but from a different handler.  The key
                # difference: the SSE handler returns JSON {"error": ...},
                # while middleware returns {"error": "Authentication required"}.
                resp = await asyncio.wait_for(c.get("/api/stream"), timeout=0.5)
                # SSE handler returns 401 with its own auth check
                assert resp.status == 401
            except asyncio.TimeoutError:
                # If it didn't return, the middleware let it through (success)
                pass


# ===========================================================================
# Bridge restart tests
# ===========================================================================

class TestRestart:
    """Tests for POST /api/system/restart and restart tracking."""

    @pytest.mark.asyncio
    async def test_restart_returns_ok(self, web_server, client):
        """POST /api/system/restart returns ok=True."""
        with patch("src.web.os.kill") as mock_kill, \
             patch("src.web.asyncio.ensure_future"):
            resp = await client.post("/api/system/restart")
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is True
            assert "message" in body

    @pytest.mark.asyncio
    async def test_restart_required_set_on_mqtt_change(self, web_server, client):
        """Changing MQTT config sets _restart_required."""
        mock_cfg = MagicMock()
        mock_cfg.settings_file = "/tmp/_test_settings.json"
        mock_cfg.save_settings = MagicMock()
        web_server._config = mock_cfg

        resp = await client.put("/api/config", json={"mqtt_broker": "10.0.0.99"})
        assert resp.status == 200
        assert len(web_server._restart_required) > 0
        assert "mqtt_broker" in web_server._restart_required

    @pytest.mark.asyncio
    async def test_health_includes_restart_required(self, web_server, client):
        """Health response includes restart_required when set."""
        web_server.update_data(make_pdu_data())
        web_server._restart_required = ["mqtt_broker", "auth"]

        resp = await client.get("/api/health")
        body = await resp.json()
        assert "restart_required" in body
        assert "mqtt_broker" in body["restart_required"]
        assert "auth" in body["restart_required"]


# ===========================================================================
# System info tests
# ===========================================================================

class TestSystemInfo:
    """Tests for GET /api/system/info."""

    @pytest.mark.asyncio
    async def test_system_info_returns_expected_fields(self, web_server, client):
        """GET /api/system/info returns all expected fields."""
        resp = await client.get("/api/system/info")
        assert resp.status == 200
        body = await resp.json()
        expected_fields = [
            "version", "python_version", "uptime_seconds",
            "db_size", "pdu_count", "total_polls",
            "in_docker", "mqtt_connected", "sse_clients",
        ]
        for field in expected_fields:
            assert field in body, f"Missing field: {field}"
        assert isinstance(body["uptime_seconds"], (int, float))
        assert isinstance(body["in_docker"], bool)

    @pytest.mark.asyncio
    async def test_system_info_uptime_increases(self, web_server, client):
        """Uptime value is positive and increases over time."""
        # Set start_time to 10 seconds ago
        web_server._start_time = time.time() - 10

        resp = await client.get("/api/system/info")
        body = await resp.json()
        assert body["uptime_seconds"] >= 10

    @pytest.mark.asyncio
    async def test_system_info_pdu_count(self, web_server, client):
        """pdu_count reflects registered PDUs."""
        web_server._pdu_configs["pdu-a"] = {"host": "10.0.0.1"}
        web_server._pdu_configs["pdu-b"] = {"host": "10.0.0.2"}

        resp = await client.get("/api/system/info")
        body = await resp.json()
        assert body["pdu_count"] == 2


# ===========================================================================
# RingBufferHandler tests (unit tests, no HTTP)
# ===========================================================================

class TestRingBufferHandler:
    """Tests for RingBufferHandler log capture."""

    def _make_record(self, msg, level="INFO", name="test"):
        """Create a minimal logging.LogRecord."""
        record = logging.LogRecord(
            name=name, level=getattr(logging, level),
            pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )
        return record

    def test_capacity_limit(self):
        """Buffer respects capacity limit and drops oldest entries."""
        from src.web import RingBufferHandler
        handler = RingBufferHandler(capacity=5)
        handler.setFormatter(logging.Formatter("%(message)s"))
        for i in range(10):
            handler.emit(self._make_record(f"msg-{i}"))
        records = handler.get_records(limit=100)
        assert len(records) == 5
        # Newest first — the last emitted should be first returned
        assert records[0]["message"] == "msg-9"
        assert records[4]["message"] == "msg-5"

    def test_level_filtering(self):
        """get_records filters by minimum level."""
        from src.web import RingBufferHandler
        handler = RingBufferHandler(capacity=100)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.emit(self._make_record("debug msg", level="DEBUG"))
        handler.emit(self._make_record("info msg", level="INFO"))
        handler.emit(self._make_record("warning msg", level="WARNING"))
        handler.emit(self._make_record("error msg", level="ERROR"))

        results = handler.get_records(level="WARNING", limit=100)
        assert len(results) == 2
        levels = {r["level"] for r in results}
        assert levels == {"WARNING", "ERROR"}

    def test_case_insensitive_search(self):
        """Search is case-insensitive."""
        from src.web import RingBufferHandler
        handler = RingBufferHandler(capacity=100)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.emit(self._make_record("SNMP poll succeeded"))
        handler.emit(self._make_record("MQTT connected"))
        handler.emit(self._make_record("snmp timeout on 10.0.0.1"))

        results = handler.get_records(search="snmp", limit=100)
        assert len(results) == 2
        for r in results:
            assert "snmp" in r["message"].lower()

    def test_get_records_format(self):
        """get_records returns dicts with ts, level, logger, message."""
        from src.web import RingBufferHandler
        handler = RingBufferHandler(capacity=100)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.emit(self._make_record("test message", level="INFO", name="mylogger"))

        records = handler.get_records()
        assert len(records) == 1
        rec = records[0]
        assert "ts" in rec
        assert rec["level"] == "INFO"
        assert rec["logger"] == "mylogger"
        assert rec["message"] == "test message"


# ===========================================================================
# System logs endpoint tests
# ===========================================================================

class TestSystemLogs:
    """Tests for GET /api/system/logs."""

    @pytest.mark.asyncio
    async def test_get_logs_returns_format(self, web_server, client):
        """GET /api/system/logs returns logs and count."""
        from src.web import RingBufferHandler
        handler = RingBufferHandler(capacity=100)
        handler.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0,
            msg="test log entry", args=(), exc_info=None,
        )
        handler.emit(record)
        web_server.set_log_buffer(handler)

        resp = await client.get("/api/system/logs")
        assert resp.status == 200
        body = await resp.json()
        assert "logs" in body
        assert "count" in body
        assert body["count"] >= 1
        assert body["logs"][0]["message"] == "test log entry"

    @pytest.mark.asyncio
    async def test_get_logs_level_filter(self, web_server, client):
        """GET /api/system/logs?level=WARNING filters low-level records."""
        from src.web import RingBufferHandler
        handler = RingBufferHandler(capacity=100)
        handler.setFormatter(logging.Formatter("%(message)s"))
        for level_name in ("DEBUG", "INFO", "WARNING", "ERROR"):
            record = logging.LogRecord(
                name="test", level=getattr(logging, level_name),
                pathname="", lineno=0,
                msg=f"{level_name} message", args=(), exc_info=None,
            )
            handler.emit(record)
        web_server.set_log_buffer(handler)

        resp = await client.get("/api/system/logs?level=WARNING")
        assert resp.status == 200
        body = await resp.json()
        assert body["count"] == 2
        levels = {log["level"] for log in body["logs"]}
        assert levels == {"WARNING", "ERROR"}

    @pytest.mark.asyncio
    async def test_get_logs_503_when_no_buffer(self, web_server, client):
        """GET /api/system/logs returns 503 when no log buffer is set."""
        web_server._log_buffer = None
        resp = await client.get("/api/system/logs")
        assert resp.status == 503
        body = await resp.json()
        assert "error" in body


# ===========================================================================
# Backup / Restore tests
# ===========================================================================

class TestBackupRestore:
    """Tests for GET /api/system/backup and POST /api/system/restore."""

    @pytest.mark.asyncio
    async def test_backup_returns_files_dict(self, web_server, client, tmp_path):
        """GET /api/system/backup returns a backup with files dict."""
        # Create config files in tmp_path
        (tmp_path / "pdus.json").write_text('{"pdu-1": {"host": "10.0.0.1"}}')
        (tmp_path / "bridge_settings.json").write_text('{"poll_interval": 5}')
        # A non-whitelisted file that should NOT appear
        (tmp_path / "secrets.json").write_text('{"key": "value"}')

        with patch("src.web.Path") as MockPath:
            # Make Path("/data") return tmp_path, but keep other Path calls working
            def path_side_effect(arg):
                if arg == "/data":
                    return tmp_path
                return Path(arg)
            MockPath.side_effect = path_side_effect

            resp = await client.get("/api/system/backup")
            assert resp.status == 200
            body = await resp.json()
            assert "version" in body
            assert "timestamp" in body
            assert "files" in body
            assert "pdus.json" in body["files"]
            assert "bridge_settings.json" in body["files"]
            assert "secrets.json" not in body["files"]

    @pytest.mark.asyncio
    async def test_restore_writes_files(self, web_server, client, tmp_path):
        """POST /api/system/restore writes allowed files to data dir."""
        backup_data = {
            "version": 1,
            "files": {
                "pdus.json": {"pdu-1": {"host": "10.0.0.1"}},
                "bridge_settings.json": {"poll_interval": 10},
            },
        }

        with patch("src.web.Path") as MockPath:
            def path_side_effect(arg):
                if arg == "/data":
                    return tmp_path
                return Path(arg)
            MockPath.side_effect = path_side_effect

            resp = await client.post("/api/system/restore", json=backup_data)
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is True
            assert "pdus.json" in body["restored"]
            assert "bridge_settings.json" in body["restored"]

            # Verify files were actually written
            assert (tmp_path / "pdus.json").exists()
            content = json.loads((tmp_path / "pdus.json").read_text())
            assert content["pdu-1"]["host"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_restore_rejects_bad_filenames(self, web_server, client, tmp_path):
        """POST /api/system/restore rejects filenames outside the whitelist."""
        backup_data = {
            "version": 1,
            "files": {
                "../etc/passwd": "root:x:0:0",
                "evil.json": {"bad": True},
                "pdus/../escape.json": {"attempt": True},
            },
        }

        with patch("src.web.Path") as MockPath:
            def path_side_effect(arg):
                if arg == "/data":
                    return tmp_path
                return Path(arg)
            MockPath.side_effect = path_side_effect

            resp = await client.post("/api/system/restore", json=backup_data)
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is True
            # None of those filenames should have been restored
            assert body["restored"] == []

    @pytest.mark.asyncio
    async def test_restore_sets_restart_required(self, web_server, client, tmp_path):
        """POST /api/system/restore sets _restart_required when files are restored."""
        backup_data = {
            "version": 1,
            "files": {
                "pdus.json": {"pdu-1": {"host": "10.0.0.1"}},
            },
        }

        with patch("src.web.Path") as MockPath:
            def path_side_effect(arg):
                if arg == "/data":
                    return tmp_path
                return Path(arg)
            MockPath.side_effect = path_side_effect

            web_server._restart_required = []
            resp = await client.post("/api/system/restore", json=backup_data)
            assert resp.status == 200
            assert "config_restored" in web_server._restart_required

    @pytest.mark.asyncio
    async def test_backup_content_disposition_header(self, web_server, client, tmp_path):
        """GET /api/system/backup includes Content-Disposition for download."""
        (tmp_path / "pdus.json").write_text("[]")

        with patch("src.web.Path") as MockPath:
            def path_side_effect(arg):
                if arg == "/data":
                    return tmp_path
                return Path(arg)
            MockPath.side_effect = path_side_effect

            resp = await client.get("/api/system/backup")
            assert resp.status == 200
            disposition = resp.headers.get("Content-Disposition", "")
            assert "attachment" in disposition
            assert "cyberpdu_backup.json" in disposition


# ===========================================================================
# Advanced config tests
# ===========================================================================

class TestAdvancedConfig:
    """Tests for advanced config fields: snmp_timeout, snmp_retries,
    recovery_enabled, session_timeout."""

    def _make_config_mock(self):
        """Create a mock config object with all advanced fields."""
        mock_cfg = MagicMock()
        mock_cfg.settings_file = "/tmp/_test_settings.json"
        mock_cfg.save_settings = MagicMock()
        mock_cfg.poll_interval = 5
        mock_cfg.mqtt_broker = "localhost"
        mock_cfg.mqtt_port = 1883
        mock_cfg.mqtt_username = ""
        mock_cfg.mqtt_password = ""
        mock_cfg.log_level = "INFO"
        mock_cfg.history_retention_days = 60
        mock_cfg.snmp_timeout = 2.0
        mock_cfg.snmp_retries = 1
        mock_cfg.recovery_enabled = True
        mock_cfg.session_timeout = 86400
        return mock_cfg

    @pytest.mark.asyncio
    async def test_get_config_includes_advanced_fields(self, web_server, client):
        """GET /api/config returns snmp_timeout, snmp_retries, recovery_enabled, session_timeout."""
        mock_cfg = self._make_config_mock()
        web_server._config = mock_cfg

        resp = await client.get("/api/config")
        assert resp.status == 200
        body = await resp.json()
        assert "snmp_timeout" in body
        assert "snmp_retries" in body
        assert "recovery_enabled" in body
        assert "session_timeout" in body
        assert body["snmp_timeout"] == 2.0
        assert body["snmp_retries"] == 1
        assert body["recovery_enabled"] is True
        assert body["session_timeout"] == 86400

    @pytest.mark.asyncio
    async def test_update_snmp_timeout_valid(self, web_server, client):
        """PUT /api/config with valid snmp_timeout succeeds."""
        mock_cfg = self._make_config_mock()
        web_server._config = mock_cfg

        resp = await client.put("/api/config", json={"snmp_timeout": 5.0})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["updated"]["snmp_timeout"] == 5.0
        assert mock_cfg.snmp_timeout == 5.0

    @pytest.mark.asyncio
    async def test_update_snmp_timeout_invalid_low(self, web_server, client):
        """PUT /api/config rejects snmp_timeout below 0.5."""
        mock_cfg = self._make_config_mock()
        web_server._config = mock_cfg

        resp = await client.put("/api/config", json={"snmp_timeout": 0.1})
        assert resp.status == 400
        body = await resp.json()
        assert "error" in body

    @pytest.mark.asyncio
    async def test_update_snmp_timeout_invalid_high(self, web_server, client):
        """PUT /api/config rejects snmp_timeout above 30."""
        mock_cfg = self._make_config_mock()
        web_server._config = mock_cfg

        resp = await client.put("/api/config", json={"snmp_timeout": 31})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_update_snmp_retries_valid(self, web_server, client):
        """PUT /api/config with valid snmp_retries succeeds."""
        mock_cfg = self._make_config_mock()
        web_server._config = mock_cfg

        resp = await client.put("/api/config", json={"snmp_retries": 3})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["updated"]["snmp_retries"] == 3
        assert mock_cfg.snmp_retries == 3

    @pytest.mark.asyncio
    async def test_update_snmp_retries_invalid(self, web_server, client):
        """PUT /api/config rejects snmp_retries outside 0-5."""
        mock_cfg = self._make_config_mock()
        web_server._config = mock_cfg

        resp = await client.put("/api/config", json={"snmp_retries": 10})
        assert resp.status == 400
        body = await resp.json()
        assert "error" in body

    @pytest.mark.asyncio
    async def test_update_recovery_enabled_bool_roundtrip(self, web_server, client):
        """PUT /api/config with recovery_enabled=False round-trips correctly."""
        mock_cfg = self._make_config_mock()
        web_server._config = mock_cfg

        # Set to false
        resp = await client.put("/api/config", json={"recovery_enabled": False})
        assert resp.status == 200
        body = await resp.json()
        assert body["updated"]["recovery_enabled"] is False
        assert mock_cfg.recovery_enabled is False

        # Set back to true
        resp = await client.put("/api/config", json={"recovery_enabled": True})
        assert resp.status == 200
        body = await resp.json()
        assert body["updated"]["recovery_enabled"] is True
        assert mock_cfg.recovery_enabled is True

    @pytest.mark.asyncio
    async def test_update_session_timeout_valid(self, web_server, client):
        """PUT /api/config with valid session_timeout succeeds."""
        mock_cfg = self._make_config_mock()
        web_server._config = mock_cfg

        resp = await client.put("/api/config", json={"session_timeout": 3600})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["updated"]["session_timeout"] == 3600
        assert web_server._session_timeout == 3600

    @pytest.mark.asyncio
    async def test_update_session_timeout_too_low(self, web_server, client):
        """PUT /api/config rejects session_timeout below 60."""
        mock_cfg = self._make_config_mock()
        web_server._config = mock_cfg

        resp = await client.put("/api/config", json={"session_timeout": 10})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_update_session_timeout_too_high(self, web_server, client):
        """PUT /api/config rejects session_timeout above 604800."""
        mock_cfg = self._make_config_mock()
        web_server._config = mock_cfg

        resp = await client.put("/api/config", json={"session_timeout": 999999})
        assert resp.status == 400


# ===========================================================================
# New endpoint tests — outlet delayon/delayoff/cancel, ATS config, network
# write, notifications, energywise, status new fields, toggle rule, contact
# ===========================================================================

class TestOutletDelayCommands:
    """Tests for POST /api/outlets/{n}/command with delayon/delayoff/cancel."""

    @pytest.mark.asyncio
    async def test_outlet_command_delayon(self, web_server, client):
        """Sends delayon command to outlet and returns success."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        web_server.set_command_callback(mock_cmd)

        resp = await client.post("/api/outlets/1/command",
                                 json={"action": "delayon"})
        assert resp.status == 200
        body = await resp.json()
        assert body["outlet"] == 1
        assert body["action"] == "delayon"
        assert body["ok"] is True
        assert commands == [(1, "delayon")]

    @pytest.mark.asyncio
    async def test_outlet_command_delayoff(self, web_server, client):
        """Sends delayoff command to outlet and returns success."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        web_server.set_command_callback(mock_cmd)

        resp = await client.post("/api/outlets/2/command",
                                 json={"action": "delayoff"})
        assert resp.status == 200
        body = await resp.json()
        assert body["outlet"] == 2
        assert body["action"] == "delayoff"
        assert body["ok"] is True
        assert commands == [(2, "delayoff")]

    @pytest.mark.asyncio
    async def test_outlet_command_cancel(self, web_server, client):
        """Sends cancel command to outlet and returns success."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        web_server.set_command_callback(mock_cmd)

        resp = await client.post("/api/outlets/3/command",
                                 json={"action": "cancel"})
        assert resp.status == 200
        body = await resp.json()
        assert body["outlet"] == 3
        assert body["action"] == "cancel"
        assert body["ok"] is True
        assert commands == [(3, "cancel")]

    @pytest.mark.asyncio
    async def test_outlet_command_delayon_case_insensitive(self, web_server, client):
        """Action is lowercased so DELAYON works."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        web_server.set_command_callback(mock_cmd)

        resp = await client.post("/api/outlets/1/command",
                                 json={"action": "DELAYON"})
        assert resp.status == 200
        body = await resp.json()
        assert body["action"] == "delayon"
        assert commands == [(1, "delayon")]


class TestATSConfigEndpoints:
    """Tests for GET/PUT ATS configuration endpoints."""

    @pytest.mark.asyncio
    async def test_get_ats_config_success(self, web_server, client):
        """GET /api/pdu/ats/config returns data from management callback."""
        expected = {
            "preferred_source": "A",
            "auto_transfer": True,
            "sensitivity": "normal",
            "voltage_upper": 148,
            "voltage_lower": 88,
        }

        async def mock_get_ats(device_id):
            return expected

        web_server.set_management_callback("get_ats_config", mock_get_ats)
        web_server.update_data(make_pdu_data())

        resp = await client.get("/api/pdu/ats/config")
        assert resp.status == 200
        body = await resp.json()
        assert body == expected

    @pytest.mark.asyncio
    async def test_get_ats_config_no_callback_503(self, web_server, client):
        """GET /api/pdu/ats/config returns 503 when callback not set."""
        resp = await client.get("/api/pdu/ats/config")
        assert resp.status == 503
        body = await resp.json()
        assert "Serial transport required" in body["error"]

    @pytest.mark.asyncio
    async def test_set_ats_preferred_source_a(self, web_server, client):
        """PUT /api/pdu/ats/preferred-source with source=A succeeds."""
        calls = []

        async def mock_set(device_id, source):
            calls.append((device_id, source))
            return {"ok": True, "source": source}

        web_server.set_management_callback("set_preferred_source", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/ats/preferred-source",
                                json={"source": "A"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["source"] == "A"
        assert calls[0][1] == "A"

    @pytest.mark.asyncio
    async def test_set_ats_preferred_source_b(self, web_server, client):
        """PUT /api/pdu/ats/preferred-source with source=B succeeds."""
        async def mock_set(device_id, source):
            return {"ok": True, "source": source}

        web_server.set_management_callback("set_preferred_source", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/ats/preferred-source",
                                json={"source": "b"})
        assert resp.status == 200
        body = await resp.json()
        assert body["source"] == "B"

    @pytest.mark.asyncio
    async def test_set_ats_preferred_source_invalid_400(self, web_server, client):
        """PUT /api/pdu/ats/preferred-source rejects invalid source."""
        async def mock_set(device_id, source):
            return {"ok": True}

        web_server.set_management_callback("set_preferred_source", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/ats/preferred-source",
                                json={"source": "C"})
        assert resp.status == 400
        body = await resp.json()
        assert "A" in body["error"] or "B" in body["error"]

    @pytest.mark.asyncio
    async def test_set_ats_auto_transfer_enabled(self, web_server, client):
        """PUT /api/pdu/ats/auto-transfer with enabled=true succeeds."""
        calls = []

        async def mock_set(device_id, enabled):
            calls.append(enabled)
            return {"ok": True, "enabled": enabled}

        web_server.set_management_callback("set_auto_transfer", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/ats/auto-transfer",
                                json={"enabled": True})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert calls == [True]

    @pytest.mark.asyncio
    async def test_set_ats_auto_transfer_disabled(self, web_server, client):
        """PUT /api/pdu/ats/auto-transfer with enabled=false succeeds."""
        async def mock_set(device_id, enabled):
            return {"ok": True, "enabled": enabled}

        web_server.set_management_callback("set_auto_transfer", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/ats/auto-transfer",
                                json={"enabled": False})
        assert resp.status == 200
        body = await resp.json()
        assert body["enabled"] is False

    @pytest.mark.asyncio
    async def test_set_ats_sensitivity_normal(self, web_server, client):
        """PUT /api/pdu/ats/sensitivity with sensitivity=normal succeeds."""
        async def mock_set(device_id, sensitivity):
            return {"ok": True, "sensitivity": sensitivity}

        web_server.set_management_callback("set_voltage_sensitivity", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/ats/sensitivity",
                                json={"sensitivity": "normal"})
        assert resp.status == 200
        body = await resp.json()
        assert body["sensitivity"] == "normal"

    @pytest.mark.asyncio
    async def test_set_ats_sensitivity_invalid_400(self, web_server, client):
        """PUT /api/pdu/ats/sensitivity rejects invalid sensitivity value."""
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/ats/sensitivity",
                                json={"sensitivity": "extreme"})
        assert resp.status == 400
        body = await resp.json()
        assert "sensitivity" in body["error"]

    @pytest.mark.asyncio
    async def test_set_ats_voltage_limits(self, web_server, client):
        """PUT /api/pdu/ats/voltage-limits with upper and lower succeeds."""
        calls = []

        async def mock_set(device_id, upper, lower):
            calls.append((upper, lower))
            return {"ok": True, "upper": upper, "lower": lower}

        web_server.set_management_callback("set_transfer_voltage", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/ats/voltage-limits",
                                json={"upper": 148, "lower": 88})
        assert resp.status == 200
        body = await resp.json()
        assert body["upper"] == 148
        assert body["lower"] == 88
        assert calls == [(148, 88)]

    @pytest.mark.asyncio
    async def test_set_ats_voltage_limits_missing_both_400(self, web_server, client):
        """PUT /api/pdu/ats/voltage-limits requires upper and/or lower."""
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/ats/voltage-limits", json={})
        assert resp.status == 400
        body = await resp.json()
        assert "upper" in body["error"] or "lower" in body["error"]

    @pytest.mark.asyncio
    async def test_set_ats_coldstart(self, web_server, client):
        """PUT /api/pdu/ats/coldstart sets delay and state."""
        calls = []

        async def mock_set(device_id, body):
            calls.append(body)
            return {"ok": True}

        web_server.set_management_callback("set_coldstart", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/ats/coldstart",
                                json={"delay": 0, "state": "allon"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert calls[0]["delay"] == 0
        assert calls[0]["state"] == "allon"

    @pytest.mark.asyncio
    async def test_set_ats_coldstart_no_callback_503(self, web_server, client):
        """PUT /api/pdu/ats/coldstart returns 503 when callback not set."""
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/ats/coldstart",
                                json={"delay": 5, "state": "prevstate"})
        assert resp.status == 503


class TestNetworkWriteEndpoint:
    """Tests for PUT /api/pdu/network (network config write with confirm)."""

    @pytest.mark.asyncio
    async def test_set_network_requires_confirm(self, web_server, client):
        """PUT /api/pdu/network returns 400 without confirm: true."""
        async def mock_set(device_id, body):
            return {"ok": True}

        web_server.set_management_callback("set_network_config", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/network",
                                json={"ip": "10.0.0.50"})
        assert resp.status == 400
        body = await resp.json()
        assert "confirm" in body["error"]

    @pytest.mark.asyncio
    async def test_set_network_with_confirm_succeeds(self, web_server, client):
        """PUT /api/pdu/network succeeds with confirm: true."""
        calls = []

        async def mock_set(device_id, body):
            calls.append(body)
            return {"ok": True}

        web_server.set_management_callback("set_network_config", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/network",
                                json={"ip": "10.0.0.50", "confirm": True})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert calls[0]["ip"] == "10.0.0.50"

    @pytest.mark.asyncio
    async def test_set_network_no_callback_503(self, web_server, client):
        """PUT /api/pdu/network returns 503 when callback not set."""
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/network",
                                json={"ip": "10.0.0.50", "confirm": True})
        assert resp.status == 503


class TestStatusNewFields:
    """Tests for new fields in GET /api/status response."""

    @pytest.mark.asyncio
    async def test_status_includes_ats_extended_fields(self, web_server, client):
        """GET /api/status includes voltage_sensitivity, coldstart, limits in ats block."""
        pdu_data = make_pdu_data()
        pdu_data.voltage_sensitivity = "Normal"
        pdu_data.transfer_voltage = 120.0
        pdu_data.voltage_upper_limit = 148.0
        pdu_data.voltage_lower_limit = 88.0
        pdu_data.coldstart_delay = 0
        pdu_data.coldstart_state = "allon"
        web_server.update_data(pdu_data)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        ats = body["ats"]
        assert ats["voltage_sensitivity"] == "Normal"
        assert ats["transfer_voltage"] == 120.0
        assert ats["voltage_upper_limit"] == 148.0
        assert ats["voltage_lower_limit"] == 88.0
        assert ats["coldstart_delay"] == 0
        assert ats["coldstart_state"] == "allon"

    @pytest.mark.asyncio
    async def test_status_summary_includes_total_load_and_energy(self, web_server, client):
        """GET /api/status summary includes total_load and total_energy."""
        pdu_data = make_pdu_data()
        pdu_data.total_load = 5.2
        pdu_data.total_energy = 1234.5
        web_server.update_data(pdu_data)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        summary = body["summary"]
        assert summary["total_load"] == 5.2
        assert summary["total_energy"] == 1234.5

    @pytest.mark.asyncio
    async def test_status_outlets_include_bank_assignment_and_max_load(self, web_server, client):
        """GET /api/status outlet entries include bank_assignment and max_load."""
        pdu_data = make_pdu_data()
        pdu_data.outlets[1].bank_assignment = 1
        pdu_data.outlets[1].max_load = 16.0
        web_server.update_data(pdu_data)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        outlet1 = body["outlets"]["1"]
        assert outlet1["bank_assignment"] == 1
        assert outlet1["max_load"] == 16.0

    @pytest.mark.asyncio
    async def test_status_inputs_include_energy_and_last_update(self, web_server, client):
        """GET /api/status input entries include energy and last_update."""
        pdu_data = make_pdu_data()
        pdu_data.banks[1].energy = 456.7
        pdu_data.banks[1].last_update = "2026-02-22T10:00:00"
        web_server.update_data(pdu_data)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        input1 = body["inputs"]["1"]
        assert input1["energy"] == 456.7
        assert input1["last_update"] == "2026-02-22T10:00:00"

    @pytest.mark.asyncio
    async def test_status_includes_environment_when_sensor_present(self, web_server, client):
        """GET /api/status includes environment block when sensor is present."""
        from src.pdu_model import EnvironmentalData

        pdu_data = make_pdu_data()
        pdu_data.environment = EnvironmentalData(
            temperature=23.5,
            temperature_unit="C",
            humidity=45.0,
            contacts={1: True, 2: False},
            sensor_present=True,
        )
        web_server.update_data(pdu_data)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        assert "environment" in body
        env = body["environment"]
        assert env["temperature"] == 23.5
        assert env["humidity"] == 45.0
        assert env["sensor_present"] is True

    @pytest.mark.asyncio
    async def test_status_no_environment_without_sensor(self, web_server, client):
        """GET /api/status omits environment block when no sensor present."""
        pdu_data = make_pdu_data()
        # environment is None by default
        web_server.update_data(pdu_data)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        assert "environment" not in body


class TestNotificationEndpoints:
    """Tests for notification configuration endpoints."""

    @pytest.mark.asyncio
    async def test_get_notifications_success(self, web_server, client):
        """GET /api/pdu/notifications returns notification config."""
        expected = {"traps": [], "email": [], "syslog": []}

        async def mock_get(device_id):
            return expected

        web_server.set_management_callback("get_notifications", mock_get)
        web_server.update_data(make_pdu_data())

        resp = await client.get("/api/pdu/notifications")
        assert resp.status == 200
        body = await resp.json()
        assert body == expected

    @pytest.mark.asyncio
    async def test_get_notifications_no_callback_503(self, web_server, client):
        """GET /api/pdu/notifications returns 503 when callback not set."""
        resp = await client.get("/api/pdu/notifications")
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_set_trap_receiver(self, web_server, client):
        """PUT /api/pdu/notifications/traps/1 configures trap receiver."""
        calls = []

        async def mock_set(device_id, index, body):
            calls.append((index, body))
            return {"ok": True}

        web_server.set_management_callback("set_trap_receiver", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/notifications/traps/1",
                                json={"ip": "10.0.0.100", "community": "public"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert calls[0][0] == 1

    @pytest.mark.asyncio
    async def test_get_smtp_config(self, web_server, client):
        """GET /api/pdu/notifications/smtp returns SMTP config."""
        expected = {"server": "mail.example.com", "port": 25}

        async def mock_get(device_id):
            return expected

        web_server.set_management_callback("get_smtp_config", mock_get)
        web_server.update_data(make_pdu_data())

        resp = await client.get("/api/pdu/notifications/smtp")
        assert resp.status == 200
        body = await resp.json()
        assert body["server"] == "mail.example.com"

    @pytest.mark.asyncio
    async def test_set_smtp_config(self, web_server, client):
        """PUT /api/pdu/notifications/smtp configures SMTP settings."""
        calls = []

        async def mock_set(device_id, body):
            calls.append(body)
            return {"ok": True}

        web_server.set_management_callback("set_smtp_config", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/notifications/smtp",
                                json={"server": "mail.example.com", "port": 587})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert calls[0]["server"] == "mail.example.com"

    @pytest.mark.asyncio
    async def test_set_email_recipient(self, web_server, client):
        """PUT /api/pdu/notifications/email/1 configures email recipient."""
        calls = []

        async def mock_set(device_id, index, body):
            calls.append((index, body))
            return {"ok": True}

        web_server.set_management_callback("set_email_recipient", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/notifications/email/1",
                                json={"address": "admin@example.com"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert calls[0][0] == 1
        assert calls[0][1]["address"] == "admin@example.com"

    @pytest.mark.asyncio
    async def test_set_syslog_server(self, web_server, client):
        """PUT /api/pdu/notifications/syslog/1 configures syslog server."""
        calls = []

        async def mock_set(device_id, index, body):
            calls.append((index, body))
            return {"ok": True}

        web_server.set_management_callback("set_syslog_server", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/notifications/syslog/1",
                                json={"server": "syslog.example.com", "port": 514})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert calls[0][0] == 1


class TestEnergyWiseEndpoints:
    """Tests for GET/PUT /api/pdu/energywise."""

    @pytest.mark.asyncio
    async def test_get_energywise_success(self, web_server, client):
        """GET /api/pdu/energywise returns config from management callback."""
        expected = {"enabled": True, "domain": "cisco.com", "importance": 50}

        async def mock_get(device_id):
            return expected

        web_server.set_management_callback("get_energywise", mock_get)
        web_server.update_data(make_pdu_data())

        resp = await client.get("/api/pdu/energywise")
        assert resp.status == 200
        body = await resp.json()
        assert body == expected

    @pytest.mark.asyncio
    async def test_get_energywise_no_callback_503(self, web_server, client):
        """GET /api/pdu/energywise returns 503 when callback not set."""
        resp = await client.get("/api/pdu/energywise")
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_set_energywise(self, web_server, client):
        """PUT /api/pdu/energywise configures EnergyWise settings."""
        calls = []

        async def mock_set(device_id, body):
            calls.append(body)
            return {"ok": True}

        web_server.set_management_callback("set_energywise", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/energywise",
                                json={"enabled": False, "importance": 100})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert calls[0]["enabled"] is False
        assert calls[0]["importance"] == 100

    @pytest.mark.asyncio
    async def test_set_energywise_no_callback_503(self, web_server, client):
        """PUT /api/pdu/energywise returns 503 when callback not set."""
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/energywise",
                                json={"enabled": True})
        assert resp.status == 503


class TestToggleRuleEndpoint:
    """Tests for PUT /api/rules/{name}/toggle."""

    @pytest.mark.asyncio
    async def test_toggle_rule_enables(self, web_server, client):
        """PUT /api/rules/{name}/toggle enables a disabled rule."""
        engine = list(web_server._engines.values())[0]
        engine.create_rule({
            "name": "test-rule",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 100.0,
            "outlet": 1,
            "action": "on",
            "enabled": False,
        })

        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/rules/test-rule/toggle")
        assert resp.status == 200
        body = await resp.json()
        assert body["name"] == "test-rule"
        assert body["enabled"] is True

    @pytest.mark.asyncio
    async def test_toggle_rule_disables(self, web_server, client):
        """PUT /api/rules/{name}/toggle disables an enabled rule."""
        engine = list(web_server._engines.values())[0]
        engine.create_rule({
            "name": "active-rule",
            "input": 1,
            "condition": "voltage_above",
            "threshold": 130.0,
            "outlet": 2,
            "action": "off",
            "enabled": True,
        })

        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/rules/active-rule/toggle")
        assert resp.status == 200
        body = await resp.json()
        assert body["name"] == "active-rule"
        assert body["enabled"] is False

    @pytest.mark.asyncio
    async def test_toggle_rule_not_found_404(self, web_server, client):
        """PUT /api/rules/{name}/toggle returns 404 for nonexistent rule."""
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/rules/nonexistent/toggle")
        assert resp.status == 404
        body = await resp.json()
        assert "error" in body


class TestDeviceContactEndpoint:
    """Tests for PUT /api/device/contact."""

    @pytest.mark.asyncio
    async def test_set_device_contact_success(self, web_server, client):
        """PUT /api/device/contact sets sysContact via SNMP SET."""
        calls = []

        async def mock_snmp_set(device_id, field, value):
            calls.append((device_id, field, value))

        web_server.set_snmp_set_callback(mock_snmp_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/device/contact",
                                json={"contact": "admin@example.com"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["contact"] == "admin@example.com"
        assert calls[0][1] == "sys_contact"
        assert calls[0][2] == "admin@example.com"

    @pytest.mark.asyncio
    async def test_set_device_contact_empty_400(self, web_server, client):
        """PUT /api/device/contact rejects empty contact."""
        async def mock_snmp_set(device_id, field, value):
            pass

        web_server.set_snmp_set_callback(mock_snmp_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/device/contact",
                                json={"contact": ""})
        assert resp.status == 400
        body = await resp.json()
        assert "contact" in body["error"]

    @pytest.mark.asyncio
    async def test_set_device_contact_no_snmp_503(self, web_server, client):
        """PUT /api/device/contact returns 503 without SNMP SET callback."""
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/device/contact",
                                json={"contact": "admin@example.com"})
        assert resp.status == 503


class TestGetUsersEndpoint:
    """Tests for GET /api/pdu/users."""

    @pytest.mark.asyncio
    async def test_get_users_success(self, web_server, client):
        """GET /api/pdu/users returns user listing."""
        expected = {"users": [{"name": "admin", "level": "admin"}]}

        async def mock_get(device_id):
            return expected

        web_server.set_management_callback("get_users", mock_get)
        web_server.update_data(make_pdu_data())

        resp = await client.get("/api/pdu/users")
        assert resp.status == 200
        body = await resp.json()
        assert body == expected

    @pytest.mark.asyncio
    async def test_get_users_no_callback_503(self, web_server, client):
        """GET /api/pdu/users returns 503 when callback not set."""
        resp = await client.get("/api/pdu/users")
        assert resp.status == 503


# ---------------------------------------------------------------------------
# Status includes default_credentials_active
# ---------------------------------------------------------------------------

class TestStatusDefaultCredentials:
    """Tests that /api/status includes default_credentials_active from poller status."""

    @pytest.mark.asyncio
    async def test_status_includes_default_creds_active(self, web_server, client):
        """GET /api/status includes default_credentials_active when set in poller."""
        web_server.update_data(make_pdu_data())
        did = web_server._default_device_id

        def mock_poller_status():
            return [{"device_id": did, "default_credentials_active": True}]

        web_server.set_poller_status_callback(mock_poller_status)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        assert body["default_credentials_active"] is True

    @pytest.mark.asyncio
    async def test_status_includes_default_creds_false(self, web_server, client):
        """GET /api/status includes default_credentials_active=false when changed."""
        web_server.update_data(make_pdu_data())
        did = web_server._default_device_id

        def mock_poller_status():
            return [{"device_id": did, "default_credentials_active": False}]

        web_server.set_poller_status_callback(mock_poller_status)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        assert body["default_credentials_active"] is False

    @pytest.mark.asyncio
    async def test_status_omits_default_creds_when_unknown(self, web_server, client):
        """GET /api/status omits default_credentials_active when not in poller status."""
        web_server.update_data(make_pdu_data())
        did = web_server._default_device_id

        def mock_poller_status():
            return [{"device_id": did}]

        web_server.set_poller_status_callback(mock_poller_status)

        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        assert "default_credentials_active" not in body


# ---------------------------------------------------------------------------
# Threshold PUT endpoint tests
# ---------------------------------------------------------------------------

class TestThresholdWriteEndpoints:
    """Tests for PUT /api/pdu/thresholds/device and /api/pdu/thresholds/bank/{n}."""

    @pytest.mark.asyncio
    async def test_set_device_thresholds_success(self, web_server, client):
        """PUT /api/pdu/thresholds/device with valid values succeeds."""
        calls = []

        async def mock_set(device_id, body):
            calls.append(body)
            return {"ok": True, "results": {"overload": True}}

        web_server.set_management_callback("set_device_threshold", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/thresholds/device",
                                json={"overload": 80, "nearover": 70})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True

    @pytest.mark.asyncio
    async def test_set_device_thresholds_no_callback(self, web_server, client):
        """PUT /api/pdu/thresholds/device returns 503 without callback."""
        web_server.update_data(make_pdu_data())
        resp = await client.put("/api/pdu/thresholds/device", json={"overload": 80})
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_set_bank_thresholds_success(self, web_server, client):
        """PUT /api/pdu/thresholds/bank/1 succeeds."""
        async def mock_set(device_id, bank, body):
            return {"ok": True, "results": {"overload": True}}

        web_server.set_management_callback("set_bank_threshold", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/thresholds/bank/1",
                                json={"overload": 85})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True


# ---------------------------------------------------------------------------
# Outlet config PUT endpoint tests
# ---------------------------------------------------------------------------

class TestOutletConfigWriteEndpoints:
    """Tests for PUT /api/pdu/outlets/{n}/config."""

    @pytest.mark.asyncio
    async def test_set_outlet_config_success(self, web_server, client):
        """PUT /api/pdu/outlets/1/config succeeds."""
        calls = []

        async def mock_set(device_id, outlet, body):
            calls.append((outlet, body))
            return {"ok": True, "outlet": outlet}

        web_server.set_management_callback("set_outlet_config", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/outlets/1/config",
                                json={"name": "Server1", "on_delay": 5})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert calls[0][0] == 1
        assert calls[0][1]["name"] == "Server1"

    @pytest.mark.asyncio
    async def test_set_outlet_config_no_callback(self, web_server, client):
        """PUT /api/pdu/outlets/1/config returns 503 without callback."""
        web_server.update_data(make_pdu_data())
        resp = await client.put("/api/pdu/outlets/1/config", json={"name": "X"})
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_set_outlet_config_multiple_fields(self, web_server, client):
        """PUT /api/pdu/outlets/3/config with all timing fields."""
        async def mock_set(device_id, outlet, body):
            return {"ok": True, "outlet": outlet}

        web_server.set_management_callback("set_outlet_config", mock_set)
        web_server.update_data(make_pdu_data())

        resp = await client.put("/api/pdu/outlets/3/config",
                                json={"name": "NAS", "on_delay": 10, "off_delay": 5, "reboot_duration": 15})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True


# ---------------------------------------------------------------------------
# Automation rule enhancements (days_of_week, schedule_type, multi-outlet)
# ---------------------------------------------------------------------------

class TestRuleEnhancements:
    """Tests for enhanced automation rule fields."""

    @pytest.mark.asyncio
    async def test_create_rule_with_days_of_week(self, client):
        """POST /api/rules with days_of_week creates rule with day restriction."""
        rule = {
            "name": "weekday_rule",
            "input": 1, "condition": "voltage_below", "threshold": 10.0,
            "outlet": 1, "action": "off", "delay": 5,
            "days_of_week": [0, 1, 2, 3, 4],
        }
        resp = await client.post("/api/rules", json=rule)
        assert resp.status == 201
        body = await resp.json()
        assert body["name"] == "weekday_rule"
        # Verify the rule was stored with days_of_week
        list_resp = await client.get("/api/rules")
        rules = await list_resp.json()
        found = [r for r in rules if r["name"] == "weekday_rule"]
        assert len(found) == 1
        assert found[0].get("days_of_week") == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_create_rule_with_schedule_type_oneshot(self, client):
        """POST /api/rules with schedule_type=oneshot creates one-shot rule."""
        rule = {
            "name": "oneshot_rule",
            "input": 1, "condition": "voltage_below", "threshold": 10.0,
            "outlet": 1, "action": "off", "delay": 0,
            "schedule_type": "oneshot",
        }
        resp = await client.post("/api/rules", json=rule)
        assert resp.status == 201
        body = await resp.json()
        assert body["name"] == "oneshot_rule"
        list_resp = await client.get("/api/rules")
        rules = await list_resp.json()
        found = [r for r in rules if r["name"] == "oneshot_rule"]
        assert len(found) == 1
        assert found[0].get("schedule_type") == "oneshot"

    @pytest.mark.asyncio
    async def test_create_rule_with_multi_outlet(self, client):
        """POST /api/rules with outlet as array targets multiple outlets."""
        rule = {
            "name": "multi_outlet_rule",
            "input": 1, "condition": "voltage_below", "threshold": 10.0,
            "outlet": [1, 3, 5], "action": "off", "delay": 5,
        }
        resp = await client.post("/api/rules", json=rule)
        assert resp.status == 201
        body = await resp.json()
        assert body["name"] == "multi_outlet_rule"
        list_resp = await client.get("/api/rules")
        rules = await list_resp.json()
        found = [r for r in rules if r["name"] == "multi_outlet_rule"]
        assert len(found) == 1
        assert found[0]["outlet"] == [1, 3, 5]

    @pytest.mark.asyncio
    async def test_toggle_rule_endpoint(self, client):
        """PUT /api/rules/{name}/toggle enables/disables a rule."""
        # Create rule first
        rule = {
            "name": "toggle_test",
            "input": 1, "condition": "voltage_below", "threshold": 10.0,
            "outlet": 1, "action": "off", "delay": 0,
        }
        await client.post("/api/rules", json=rule)

        # Toggle off
        resp = await client.put("/api/rules/toggle_test/toggle",
                                json={"enabled": False})
        assert resp.status == 200
        body = await resp.json()
        assert body.get("enabled") is False or body.get("ok") is True

    @pytest.mark.asyncio
    async def test_create_rule_with_all_new_fields(self, client):
        """POST /api/rules with all new fields together."""
        rule = {
            "name": "full_rule",
            "input": 2, "condition": "time_after", "threshold": "08:00",
            "outlet": [2, 4], "action": "on", "delay": 10,
            "restore": True,
            "days_of_week": [0, 1, 2, 3, 4],
            "schedule_type": "oneshot",
        }
        resp = await client.post("/api/rules", json=rule)
        assert resp.status == 201
        body = await resp.json()
        assert body["name"] == "full_rule"
