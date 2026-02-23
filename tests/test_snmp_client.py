# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Unit tests for SNMP client with mocked pysnmp calls."""

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.config import Config
from src.snmp_client import SNMPClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config():
    """Return a Config with safe defaults (no env side-effects)."""
    os.environ.pop("PDU_HOST", None)
    os.environ.pop("BRIDGE_MOCK_MODE", None)
    return Config()


@pytest.fixture()
def client(config):
    """Return an SNMPClient wrapping a default Config."""
    return SNMPClient(config)


def _make_var_bind(oid_str="1.3.6.1", value=42):
    """Create a (oid, value) var-bind tuple mimicking pysnmp output."""
    oid_obj = MagicMock()
    oid_obj.__str__ = lambda self: oid_str
    return (oid_obj, value)


# ---------------------------------------------------------------------------
# GET — success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_success(client):
    """Successful SNMP GET returns the value from var_binds."""
    var_binds = [_make_var_bind("1.3.6.1.4.1.3808", 99)]
    mock_result = (None, None, 0, var_binds)

    with patch("src.snmp_client.getCmd", new_callable=AsyncMock, return_value=mock_result):
        result = await client.get("1.3.6.1.4.1.3808")

    assert result == 99
    assert client._total_gets == 1
    assert client._failed_gets == 0
    assert client._consecutive_failures == 0
    assert client._last_success_time is not None


# ---------------------------------------------------------------------------
# GET — error_indication returns None and records failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_error_indication(client):
    """GET with error_indication returns None and records failure."""
    mock_result = ("requestTimedOut", None, 0, [])

    with patch("src.snmp_client.getCmd", new_callable=AsyncMock, return_value=mock_result):
        result = await client.get("1.3.6.1.4.1.3808")

    assert result is None
    assert client._total_gets == 1
    assert client._failed_gets == 1
    assert client._consecutive_failures == 1
    assert client._last_error_msg is not None
    assert "requestTimedOut" in client._last_error_msg


# ---------------------------------------------------------------------------
# GET — error_status returns None and records failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_error_status(client):
    """GET with error_status returns None and records failure."""
    error_status = MagicMock()
    error_status.prettyPrint.return_value = "noSuchName"
    error_status.__bool__ = lambda self: True

    var_binds = [_make_var_bind("1.3.6.1.4.1.3808", 0)]
    mock_result = (None, error_status, 1, var_binds)

    with patch("src.snmp_client.getCmd", new_callable=AsyncMock, return_value=mock_result):
        result = await client.get("1.3.6.1.4.1.3808")

    assert result is None
    assert client._failed_gets == 1
    assert "noSuchName" in client._last_error_msg


# ---------------------------------------------------------------------------
# GET — exception returns None and records failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_exception(client):
    """GET that raises an exception returns None and records failure."""
    with patch("src.snmp_client.getCmd", new_callable=AsyncMock, side_effect=OSError("socket error")):
        result = await client.get("1.3.6.1.4.1.3808")

    assert result is None
    assert client._failed_gets == 1
    assert client._consecutive_failures == 1
    assert "socket error" in client._last_error_msg


# ---------------------------------------------------------------------------
# get_many — parallel batches with correct ordering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_many_batches_parallel(client):
    """get_many with batch_size=2 processes OIDs in correct batches and
    preserves OID-to-value mapping."""
    oids = ["1.1", "1.2", "1.3", "1.4", "1.5"]
    call_order = []

    async def fake_get(oid):
        call_order.append(oid)
        return int(oid.split(".")[-1]) * 10  # 10, 20, 30, 40, 50

    with patch.object(client, "get", side_effect=fake_get):
        results = await client.get_many(oids, batch_size=2)

    # All OIDs should have been queried
    assert set(call_order) == set(oids)

    # Results map each OID to its expected value
    assert results == {
        "1.1": 10,
        "1.2": 20,
        "1.3": 30,
        "1.4": 40,
        "1.5": 50,
    }

    # last_poll_duration should be recorded
    assert client._last_poll_duration is not None
    assert client._last_poll_duration >= 0


# ---------------------------------------------------------------------------
# get_many — mixed successes and failures
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_many_mixed_results(client):
    """get_many drops failed OIDs (None returns) from the result dict."""
    oids = ["1.1", "1.2", "1.3"]

    async def fake_get(oid):
        if oid == "1.2":
            return None  # simulate failure
        return f"val-{oid}"

    with patch.object(client, "get", side_effect=fake_get):
        results = await client.get_many(oids, batch_size=10)

    assert "1.1" in results
    assert "1.2" not in results  # failed — excluded
    assert "1.3" in results
    assert results["1.1"] == "val-1.1"


# ---------------------------------------------------------------------------
# get_many — exceptions from gather are handled
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_many_exception_in_gather(client):
    """get_many handles exceptions returned by asyncio.gather(return_exceptions=True)."""
    oids = ["1.1", "1.2"]

    async def fake_get(oid):
        if oid == "1.1":
            raise RuntimeError("boom")
        return 42

    with patch.object(client, "get", side_effect=fake_get):
        results = await client.get_many(oids, batch_size=10)

    # 1.1 raised — should be excluded; 1.2 should be present
    assert "1.1" not in results
    assert results["1.2"] == 42


# ---------------------------------------------------------------------------
# SET — success returns True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_success(client):
    """Successful SNMP SET returns True and records success."""
    var_binds = [_make_var_bind("1.3.6.1.4.1.3808", 1)]
    mock_result = (None, None, 0, var_binds)

    with patch("src.snmp_client.setCmd", new_callable=AsyncMock, return_value=mock_result):
        result = await client.set("1.3.6.1.4.1.3808", 1)

    assert result is True
    assert client._total_sets == 1
    assert client._failed_sets == 0
    assert client._consecutive_failures == 0
    assert client._last_success_time is not None


# ---------------------------------------------------------------------------
# SET — error_indication returns False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_error_indication(client):
    """SET with error_indication returns False and records failure."""
    mock_result = ("requestTimedOut", None, 0, [])

    with patch("src.snmp_client.setCmd", new_callable=AsyncMock, return_value=mock_result):
        result = await client.set("1.3.6.1.4.1.3808", 1)

    assert result is False
    assert client._total_sets == 1
    assert client._failed_sets == 1
    assert client._consecutive_failures == 1


# ---------------------------------------------------------------------------
# SET — error_status returns False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_error_status(client):
    """SET with error_status returns False and records failure."""
    error_status = MagicMock()
    error_status.prettyPrint.return_value = "readOnly"
    error_status.__bool__ = lambda self: True

    var_binds = [_make_var_bind("1.3.6.1.4.1.3808", 1)]
    mock_result = (None, error_status, 1, var_binds)

    with patch("src.snmp_client.setCmd", new_callable=AsyncMock, return_value=mock_result):
        result = await client.set("1.3.6.1.4.1.3808", 1)

    assert result is False
    assert client._failed_sets == 1
    assert "readOnly" in client._last_error_msg


# ---------------------------------------------------------------------------
# SET — exception returns False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_exception(client):
    """SET that raises an exception returns False and records failure."""
    with patch("src.snmp_client.setCmd", new_callable=AsyncMock, side_effect=OSError("connection refused")):
        result = await client.set("1.3.6.1.4.1.3808", 1)

    assert result is False
    assert client._failed_sets == 1
    assert client._consecutive_failures == 1
    assert "connection refused" in client._last_error_msg


# ---------------------------------------------------------------------------
# SET STRING — success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_string_success(client):
    """Successful SNMP SET with OctetString returns True."""
    var_binds = [_make_var_bind("1.3.6.1.2.1.1.6.0", "Server Room")]
    mock_result = (None, None, 0, var_binds)

    with patch("src.snmp_client.setCmd", new_callable=AsyncMock, return_value=mock_result):
        result = await client.set_string("1.3.6.1.2.1.1.6.0", "Server Room")

    assert result is True
    assert client._total_sets == 1
    assert client._failed_sets == 0


# ---------------------------------------------------------------------------
# SET STRING — error_indication returns False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_string_error_indication(client):
    """SET STRING with error_indication returns False."""
    mock_result = ("requestTimedOut", None, 0, [])

    with patch("src.snmp_client.setCmd", new_callable=AsyncMock, return_value=mock_result):
        result = await client.set_string("1.3.6.1.2.1.1.6.0", "Rack A")

    assert result is False
    assert client._failed_sets == 1


# ---------------------------------------------------------------------------
# SET STRING — exception returns False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_string_exception(client):
    """SET STRING that raises an exception returns False."""
    with patch("src.snmp_client.setCmd", new_callable=AsyncMock, side_effect=OSError("refused")):
        result = await client.set_string("1.3.6.1.2.1.1.6.0", "Rack B")

    assert result is False
    assert client._failed_sets == 1
    assert "refused" in client._last_error_msg


# ---------------------------------------------------------------------------
# Health metrics — tracking across multiple operations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_metrics_tracking(client):
    """Health metrics accumulate correctly across successes and failures."""
    success_result = (None, None, 0, [_make_var_bind("1.1", 1)])
    fail_result = ("timeout", None, 0, [])

    with patch("src.snmp_client.getCmd", new_callable=AsyncMock, return_value=success_result):
        await client.get("1.1")

    health = client.get_health()
    assert health["total_gets"] == 1
    assert health["failed_gets"] == 0
    assert health["consecutive_failures"] == 0
    assert health["last_success"] is not None
    assert health["last_error"] is None
    assert health["reachable"] is True

    # Now cause two failures
    with patch("src.snmp_client.getCmd", new_callable=AsyncMock, return_value=fail_result):
        await client.get("1.2")
        await client.get("1.3")

    health = client.get_health()
    assert health["total_gets"] == 3
    assert health["failed_gets"] == 2
    assert health["consecutive_failures"] == 2
    assert health["last_error"] is not None
    assert health["last_error_msg"] is not None
    assert health["reachable"] is True  # still < 10

    # A success resets consecutive failures
    with patch("src.snmp_client.getCmd", new_callable=AsyncMock, return_value=success_result):
        await client.get("1.4")

    health = client.get_health()
    assert health["consecutive_failures"] == 0
    assert health["total_gets"] == 4
    assert health["failed_gets"] == 2  # unchanged


# ---------------------------------------------------------------------------
# Health reports reachable=False after 10+ consecutive failures
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_reachable_false_after_10_failures(client):
    """reachable becomes False after 10 consecutive failures."""
    fail_result = ("timeout", None, 0, [])

    with patch("src.snmp_client.getCmd", new_callable=AsyncMock, return_value=fail_result):
        for _ in range(10):
            await client.get("1.1")

    health = client.get_health()
    assert health["consecutive_failures"] == 10
    assert health["reachable"] is False

    # One success brings it back
    success_result = (None, None, 0, [_make_var_bind("1.1", 1)])
    with patch("src.snmp_client.getCmd", new_callable=AsyncMock, return_value=success_result):
        await client.get("1.1")

    health = client.get_health()
    assert health["consecutive_failures"] == 0
    assert health["reachable"] is True


# ---------------------------------------------------------------------------
# Health — target string and poll duration
# ---------------------------------------------------------------------------

def test_health_target_string(client, config):
    """get_health() returns the correct target host:port."""
    health = client.get_health()
    assert health["target"] == f"{config.pdu_host}:{config.pdu_snmp_port}"


def test_health_initial_state(client):
    """Fresh client has zero counters and None timestamps."""
    health = client.get_health()
    assert health["total_gets"] == 0
    assert health["failed_gets"] == 0
    assert health["total_sets"] == 0
    assert health["failed_sets"] == 0
    assert health["consecutive_failures"] == 0
    assert health["last_success"] is None
    assert health["last_error"] is None
    assert health["last_error_msg"] is None
    assert health["last_poll_duration_ms"] is None
    assert health["reachable"] is True


@pytest.mark.asyncio
async def test_health_poll_duration_ms(client):
    """last_poll_duration_ms is populated after get_many()."""
    async def fake_get(oid):
        return 1

    with patch.object(client, "get", side_effect=fake_get):
        await client.get_many(["1.1", "1.2"])

    health = client.get_health()
    assert health["last_poll_duration_ms"] is not None
    assert isinstance(health["last_poll_duration_ms"], float)
    assert health["last_poll_duration_ms"] >= 0


# ---------------------------------------------------------------------------
# SET failures also increment _failed_sets correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_failure_increments_failed_sets(client):
    """Multiple SET failures correctly increment failed_sets counter."""
    fail_result = ("timeout", None, 0, [])

    with patch("src.snmp_client.setCmd", new_callable=AsyncMock, return_value=fail_result):
        await client.set("1.1", 1)
        await client.set("1.2", 2)
        await client.set("1.3", 3)

    health = client.get_health()
    assert health["total_sets"] == 3
    assert health["failed_sets"] == 3


# ---------------------------------------------------------------------------
# close() — handles exceptions gracefully
# ---------------------------------------------------------------------------

def test_close_success(client):
    """close() calls engine.close_dispatcher() without error."""
    client.engine = MagicMock()
    client.close()
    client.engine.close_dispatcher.assert_called_once()


def test_close_handles_exception(client):
    """close() swallows exceptions from close_dispatcher()."""
    client.engine = MagicMock()
    client.engine.close_dispatcher.side_effect = RuntimeError("already closed")

    # Should not raise
    client.close()
    client.engine.close_dispatcher.assert_called_once()


# ---------------------------------------------------------------------------
# _record_success and _record_failure internals
# ---------------------------------------------------------------------------

def test_record_success_resets_consecutive_failures(client):
    """_record_success resets consecutive_failures and records timestamp."""
    client._consecutive_failures = 5
    before = time.time()
    client._record_success()
    after = time.time()

    assert client._consecutive_failures == 0
    assert before <= client._last_success_time <= after


def test_record_failure_increments_and_records(client):
    """_record_failure increments counters and stores error details."""
    before = time.time()
    client._record_failure("test error")
    after = time.time()

    assert client._failed_gets == 1
    assert client._consecutive_failures == 1
    assert before <= client._last_error_time <= after
    assert client._last_error_msg == "test error"


def test_record_failure_accumulates(client):
    """Repeated _record_failure calls increment counters correctly."""
    for i in range(5):
        client._record_failure(f"error {i}")

    assert client._failed_gets == 5
    assert client._consecutive_failures == 5
    assert client._last_error_msg == "error 4"


# ---------------------------------------------------------------------------
# get_many with empty OID list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_many_empty_oids(client):
    """get_many with an empty list returns an empty dict."""
    results = await client.get_many([])
    assert results == {}
    assert client._last_poll_duration is not None


# ---------------------------------------------------------------------------
# Constructor wiring
# ---------------------------------------------------------------------------

def test_constructor_creates_engine_and_targets(config):
    """SNMPClient.__init__ creates engine, community data, and transport."""
    with patch("src.snmp_client.SnmpEngine") as mock_engine, \
         patch("src.snmp_client.CommunityData") as mock_community, \
         patch("src.snmp_client.UdpTransportTarget") as mock_target:

        client = SNMPClient(config)

        mock_engine.assert_called_once()
        assert mock_community.call_count == 2  # read + write
        mock_community.assert_any_call(config.pdu_community_read)
        mock_community.assert_any_call(config.pdu_community_write)
        mock_target.assert_called_once_with(
            (config.pdu_host, config.pdu_snmp_port),
            timeout=config.snmp_timeout,
            retries=config.snmp_retries,
        )
