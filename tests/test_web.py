# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""Comprehensive tests for the web server REST API."""

import json
import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

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
