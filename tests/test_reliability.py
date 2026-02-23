# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Long-duration reliability and stress tests for the PDU bridge.

Exercises all subsystems with fault injection over sustained periods:
- MockPDU with input failures and ATS transfers
- History storage under continuous write load
- Automation engine with rapid state transitions
- MQTT handler publish/disconnect cycles
- Health metric accuracy under stress

Usage:
    # Quick smoke test (~30s)
    pytest tests/test_reliability.py -v

    # Extended run (set RELIABILITY_DURATION_SECONDS env var)
    RELIABILITY_DURATION_SECONDS=3600 pytest tests/test_reliability.py -v -s

    # Overnight run
    RELIABILITY_DURATION_SECONDS=28800 pytest tests/test_reliability.py -v -s
"""

import asyncio
import gc
import json
import logging
import os
import random
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bridge"))

from src.automation import AutomationEngine, AutomationRule
from src.config import Config
from src.history import HistoryStore
from src.mock_pdu import MockPDU
from src.mqtt_handler import MQTTHandler
from src.pdu_model import BankData, OutletData, PDUData, SourceData

logger = logging.getLogger("reliability_test")

# Test duration from env var (default 30s for CI, override for long runs)
DEFAULT_DURATION = 30
DURATION = int(os.environ.get("RELIABILITY_DURATION_SECONDS", DEFAULT_DURATION))

# How often to log progress (seconds)
PROGRESS_INTERVAL = max(10, DURATION // 20)


def make_config(**overrides):
    """Create a Config with mock mode enabled."""
    env = {
        "BRIDGE_MOCK_MODE": "true",
        "PDU_DEVICE_ID": "stress-test",
        "MQTT_BROKER": "localhost",
        "MQTT_PORT": "1883",
        "BRIDGE_POLL_INTERVAL": "0.1",
        "BRIDGE_LOG_LEVEL": "WARNING",
        "BRIDGE_SNMP_TIMEOUT": "2.0",
        "BRIDGE_SNMP_RETRIES": "1",
    }
    env.update(overrides)
    with patch.dict(os.environ, env, clear=False):
        return Config()


def make_pdu_data(
    voltage_a=120.0, voltage_b=120.0,
    ats_current=1, ats_preferred=1,
    num_outlets=10, outlet_power=50.0,
):
    """Create realistic PDUData with configurable parameters."""
    outlets = {}
    for n in range(1, num_outlets + 1):
        outlets[n] = OutletData(
            number=n,
            name=f"Outlet {n}",
            state="on",
            current=round(outlet_power / 120.0, 2),
            power=outlet_power,
            energy=round(n * 1.5, 1),
        )

    banks = {
        1: BankData(
            number=1, voltage=voltage_a, current=5.0,
            power=300.0, apparent_power=330.0,
            power_factor=0.91, load_state="normal",
        ),
        2: BankData(
            number=2, voltage=voltage_b, current=3.0,
            power=180.0, apparent_power=200.0,
            power_factor=0.90, load_state="normal",
        ),
    }

    return PDUData(
        device_name="Stress Test PDU",
        outlet_count=num_outlets,
        phase_count=1,
        input_voltage=voltage_a if ats_current == 1 else voltage_b,
        input_frequency=60.0,
        outlets=outlets,
        banks=banks,
        ats_preferred_source=ats_preferred,
        ats_current_source=ats_current,
        ats_auto_transfer=True,
        source_a=SourceData(
            voltage=voltage_a, frequency=60.0,
            voltage_status="normal" if voltage_a > 10 else "underVoltage",
        ),
        source_b=SourceData(
            voltage=voltage_b, frequency=60.0,
            voltage_status="normal" if voltage_b > 10 else "underVoltage",
        ),
        redundancy_ok=(voltage_a > 10 and voltage_b > 10),
    )


class StressMetrics:
    """Tracks metrics across the entire stress test run."""

    def __init__(self):
        self.start_time = time.monotonic()
        self.polls = 0
        self.poll_errors = 0
        self.history_writes = 0
        self.history_errors = 0
        self.automation_evals = 0
        self.automation_triggers = 0
        self.automation_restores = 0
        self.automation_errors = 0
        self.mqtt_publishes = 0
        self.mqtt_errors = 0
        self.fault_injections = 0
        self.ats_transfers = 0
        self.memory_snapshots: list[int] = []
        self.peak_memory_kb = 0

    def snapshot_memory(self):
        """Record current memory usage."""
        gc.collect()
        current, peak = tracemalloc.get_traced_memory()
        self.memory_snapshots.append(current)
        self.peak_memory_kb = max(self.peak_memory_kb, peak // 1024)

    def elapsed(self) -> float:
        return time.monotonic() - self.start_time

    def report(self) -> str:
        elapsed = self.elapsed()
        lines = [
            f"\n{'='*60}",
            f"  RELIABILITY TEST REPORT",
            f"{'='*60}",
            f"  Duration:            {elapsed:.1f}s ({elapsed/60:.1f}m)",
            f"  Polls:               {self.polls} ({self.polls/max(elapsed,1):.1f}/s)",
            f"  Poll errors:         {self.poll_errors}",
            f"  History writes:      {self.history_writes}",
            f"  History errors:      {self.history_errors}",
            f"  Automation evals:    {self.automation_evals}",
            f"  Automation triggers: {self.automation_triggers}",
            f"  Automation restores: {self.automation_restores}",
            f"  Automation errors:   {self.automation_errors}",
            f"  MQTT publishes:      {self.mqtt_publishes}",
            f"  MQTT errors:         {self.mqtt_errors}",
            f"  Fault injections:    {self.fault_injections}",
            f"  ATS transfers:       {self.ats_transfers}",
            f"  Peak memory:         {self.peak_memory_kb} KB",
        ]
        if self.memory_snapshots:
            first = self.memory_snapshots[0]
            last = self.memory_snapshots[-1]
            growth = (last - first) / 1024
            lines.append(f"  Memory growth:       {growth:.1f} KB")
        lines.append(f"{'='*60}")
        return "\n".join(lines)


# ── Test fixtures ──────────────────────────────────────────────

@pytest.fixture
def metrics():
    tracemalloc.start()
    m = StressMetrics()
    yield m
    tracemalloc.stop()
    print(m.report())


@pytest.fixture
def tmp_db():
    """Temporary SQLite database for history."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def tmp_rules():
    """Temporary rules file."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    Path(path).write_text("[]")
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def mock_mqtt():
    """MQTTHandler with mocked paho client."""
    config = make_config()
    with patch("src.mqtt_handler.mqtt.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.publish.return_value = MagicMock(rc=0)
        MockClient.return_value = mock_client
        handler = MQTTHandler(config)
        handler._connected = True
        handler._last_connect_time = time.time()
        yield handler


# ── Core reliability tests ─────────────────────────────────────


class TestMockPDUReliability:
    """Test MockPDU under sustained polling with fault injection."""

    @pytest.mark.asyncio
    async def test_sustained_polling(self, metrics):
        """Poll MockPDU continuously for the test duration."""
        pdu = MockPDU()
        deadline = time.monotonic() + DURATION
        last_progress = time.monotonic()

        while time.monotonic() < deadline:
            try:
                data = await pdu.poll()
                metrics.polls += 1

                # Validate data structure on every poll
                assert data.outlet_count == 10
                assert len(data.outlets) == 10
                assert len(data.banks) == 2
                assert data.source_a is not None
                assert data.source_b is not None
                assert data.input_voltage is not None
                assert 100 < data.input_voltage < 140 or data.input_voltage == 0.0

                for n, outlet in data.outlets.items():
                    assert outlet.state in ("on", "off", "unknown")

                for idx, bank in data.banks.items():
                    assert bank.voltage is not None
                    assert bank.load_state in ("normal", "low", "nearOverload", "overload")

            except Exception:
                metrics.poll_errors += 1

            if time.monotonic() - last_progress > PROGRESS_INTERVAL:
                metrics.snapshot_memory()
                logger.info("MockPDU poll progress: %d polls, %d errors",
                           metrics.polls, metrics.poll_errors)
                last_progress = time.monotonic()

            await asyncio.sleep(0.01)

        assert metrics.poll_errors == 0, f"{metrics.poll_errors} poll errors"
        assert metrics.polls > 0

    @pytest.mark.asyncio
    async def test_ats_failover_cycling(self, metrics):
        """Rapidly cycle ATS input failures and verify transfers."""
        pdu = MockPDU()
        deadline = time.monotonic() + DURATION

        while time.monotonic() < deadline:
            # Fail input A
            pdu.simulate_input_failure(1)
            metrics.fault_injections += 1
            data = await pdu.poll()
            if data.ats_current_source == 2:
                metrics.ats_transfers += 1
            assert data.source_a.voltage < 1.0

            await asyncio.sleep(0.05)

            # Restore A, fail B
            pdu.simulate_input_restore(1)
            pdu.simulate_input_failure(2)
            metrics.fault_injections += 1
            data = await pdu.poll()
            assert data.source_b.voltage < 1.0

            await asyncio.sleep(0.05)

            # Restore B (both OK)
            pdu.simulate_input_restore(2)
            data = await pdu.poll()
            assert data.source_a.voltage > 100
            assert data.source_b.voltage > 100

            metrics.polls += 3
            await asyncio.sleep(0.05)

        assert metrics.fault_injections > 0
        assert metrics.ats_transfers > 0

    @pytest.mark.asyncio
    async def test_outlet_command_cycling(self, metrics):
        """Rapidly toggle outlet states."""
        pdu = MockPDU()
        deadline = time.monotonic() + DURATION

        while time.monotonic() < deadline:
            for outlet in range(1, 11):
                # Turn off
                assert await pdu.command_outlet(outlet, 2)  # OUTLET_CMD_OFF
                # Turn on
                assert await pdu.command_outlet(outlet, 1)  # OUTLET_CMD_ON
                metrics.polls += 2

            data = await pdu.poll()
            on_count = sum(1 for o in data.outlets.values() if o.state == "on")
            assert on_count == 10  # All should be on

            await asyncio.sleep(0.01)


class TestHistoryReliability:
    """Test SQLite history under sustained write load."""

    def test_sustained_writes(self, tmp_db, metrics):
        """Write continuously for the test duration."""
        store = HistoryStore(tmp_db, retention_days=7)
        deadline = time.monotonic() + DURATION
        last_progress = time.monotonic()

        try:
            while time.monotonic() < deadline:
                data = make_pdu_data(
                    voltage_a=120.0 + random.uniform(-5, 5),
                    voltage_b=120.0 + random.uniform(-5, 5),
                    outlet_power=random.uniform(10, 100),
                )
                store.record(data)
                metrics.history_writes += 1

                if time.monotonic() - last_progress > PROGRESS_INTERVAL:
                    metrics.snapshot_memory()
                    health = store.get_health()
                    logger.info(
                        "History progress: %d writes, %d errors, healthy=%s",
                        metrics.history_writes, health["write_errors"],
                        health["healthy"],
                    )
                    last_progress = time.monotonic()

                time.sleep(0.005)  # ~200 writes/sec

        finally:
            metrics.history_errors = store.get_health()["write_errors"]
            store.close()

        assert metrics.history_errors == 0
        assert metrics.history_writes > 0

        # Verify data can be queried
        store2 = HistoryStore(tmp_db, retention_days=7)
        try:
            now = time.time()
            bank_rows = store2.query_banks(now - 3600, now)
            outlet_rows = store2.query_outlets(now - 3600, now)
            assert len(bank_rows) > 0, "No bank samples found after writes"
            assert len(outlet_rows) > 0, "No outlet samples found after writes"
        finally:
            store2.close()

    def test_concurrent_read_write(self, tmp_db, metrics):
        """Write while simultaneously querying (WAL mode test)."""
        store = HistoryStore(tmp_db, retention_days=7)
        deadline = time.monotonic() + min(DURATION, 30)  # Cap at 30s for this test
        query_count = 0
        query_errors = 0

        try:
            while time.monotonic() < deadline:
                # Write
                data = make_pdu_data()
                store.record(data)
                metrics.history_writes += 1

                # Every 10 writes, do a read
                if metrics.history_writes % 10 == 0:
                    try:
                        now = time.time()
                        store.query_banks(now - 60, now)
                        store.query_outlets(now - 60, now)
                        query_count += 1
                    except Exception:
                        query_errors += 1

                time.sleep(0.005)
        finally:
            store.close()

        assert query_errors == 0, f"{query_errors} query errors during concurrent access"
        assert query_count > 0

    def test_db_size_growth(self, tmp_db, metrics):
        """Monitor database file size over sustained writes."""
        store = HistoryStore(tmp_db, retention_days=1)
        sizes = []

        try:
            for i in range(min(DURATION * 50, 5000)):
                data = make_pdu_data(outlet_power=random.uniform(10, 100))
                store.record(data)
                metrics.history_writes += 1

                if i % 500 == 0:
                    store._conn.commit()  # Force commit to see file growth
                    size_kb = os.path.getsize(tmp_db) / 1024
                    sizes.append(size_kb)

        finally:
            store.close()

        if len(sizes) >= 2:
            # Size should grow linearly, not exponentially
            growth_rate = (sizes[-1] - sizes[0]) / len(sizes)
            # Each batch of 500 writes = 500 * (2 banks + 10 outlets) = 6000 rows
            # At ~50 bytes/row, expect ~300KB per batch
            assert growth_rate < 1000, f"DB growing too fast: {growth_rate:.0f} KB/batch"

    def test_cleanup_under_load(self, tmp_db, metrics):
        """Run cleanup while writing to verify no lock conflicts."""
        store = HistoryStore(tmp_db, retention_days=1)

        try:
            # Write some data
            for _ in range(100):
                store.record(make_pdu_data())
                metrics.history_writes += 1

            # Insert old data manually for cleanup to target
            old_ts = int(time.time()) - 2 * 86400
            for i in range(100):
                store._conn.execute(
                    "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf) "
                    "VALUES (?, 1, 120, 5, 300, 330, 0.91)",
                    (old_ts - i,),
                )
            store._conn.commit()

            # Cleanup should remove old data without error
            store.cleanup()

            # Continue writing after cleanup
            for _ in range(100):
                store.record(make_pdu_data())
                metrics.history_writes += 1

        finally:
            store.close()

        assert store.get_health()["write_errors"] == 0


class TestAutomationReliability:
    """Test automation engine under sustained evaluation."""

    @pytest.mark.asyncio
    async def test_sustained_evaluation(self, tmp_rules, metrics):
        """Evaluate rules continuously with varying inputs."""
        commands_issued = []

        async def track_cmd(outlet, action):
            commands_issued.append((outlet, action))

        engine = AutomationEngine(tmp_rules, command_callback=track_cmd)

        # Create rules covering different condition types
        engine.create_rule({
            "name": "voltage_guard",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 100.0,
            "outlet": 1,
            "action": "off",
            "delay": 0,
        })
        engine.create_rule({
            "name": "ats_monitor",
            "input": 1,
            "condition": "ats_preferred_lost",
            "threshold": 0,
            "outlet": 2,
            "action": "off",
            "delay": 0,
        })

        deadline = time.monotonic() + DURATION
        voltage = 120.0
        ats_current = 1

        while time.monotonic() < deadline:
            # Randomly vary voltage to trigger/restore rules
            if random.random() < 0.1:
                voltage = random.choice([80.0, 90.0, 100.0, 110.0, 120.0, 130.0])
            if random.random() < 0.05:
                ats_current = random.choice([1, 2])

            data = make_pdu_data(
                voltage_a=voltage,
                ats_current=ats_current,
                ats_preferred=1,
            )

            events = await engine.evaluate(data)
            metrics.automation_evals += 1

            for e in events:
                if e["type"] == "triggered":
                    metrics.automation_triggers += 1
                elif e["type"] == "restored":
                    metrics.automation_restores += 1

            await asyncio.sleep(0.01)

        assert metrics.automation_evals > 0
        assert metrics.automation_triggers > 0 or DURATION < 5
        assert engine._command_failures == 0

    @pytest.mark.asyncio
    async def test_callback_failure_resilience(self, tmp_rules, metrics):
        """Test that the engine recovers from command callback failures."""
        call_count = 0
        fail_rate = 0.3  # 30% failure rate

        async def flaky_cmd(outlet, action):
            nonlocal call_count
            call_count += 1
            if random.random() < fail_rate:
                raise RuntimeError("Simulated SNMP timeout")

        engine = AutomationEngine(tmp_rules, command_callback=flaky_cmd)
        engine.create_rule({
            "name": "flaky_rule",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 100.0,
            "outlet": 1,
            "action": "off",
            "delay": 0,
        })

        deadline = time.monotonic() + min(DURATION, 30)
        trigger_attempts = 0

        while time.monotonic() < deadline:
            # Alternate between triggering and non-triggering voltage
            low_voltage = make_pdu_data(voltage_a=80.0)
            events = await engine.evaluate(low_voltage)
            metrics.automation_evals += 1
            if events:
                trigger_attempts += 1

            # Let it restore
            normal_voltage = make_pdu_data(voltage_a=120.0)
            await engine.evaluate(normal_voltage)
            metrics.automation_evals += 1

            await asyncio.sleep(0.01)

        metrics.automation_errors = engine._command_failures
        # Some commands should have failed
        if DURATION >= 5:
            assert engine._command_failures > 0, "Expected some callback failures"
        # But the engine should still be functional
        assert metrics.automation_evals > 0

    @pytest.mark.asyncio
    async def test_rapid_rule_crud(self, tmp_rules, metrics):
        """Create, update, and delete rules rapidly."""
        engine = AutomationEngine(tmp_rules)
        deadline = time.monotonic() + min(DURATION, 30)
        rule_idx = 0

        while time.monotonic() < deadline:
            name = f"rule_{rule_idx}"
            # Create
            engine.create_rule({
                "name": name,
                "input": 1,
                "condition": "voltage_below",
                "threshold": random.uniform(90, 130),
                "outlet": random.randint(1, 10),
                "action": random.choice(["on", "off"]),
                "delay": 0,
            })

            # Update
            engine.update_rule(name, {
                "name": name,
                "input": 2,
                "condition": "voltage_above",
                "threshold": 130.0,
                "outlet": 1,
                "action": "on",
                "delay": 0,
            })

            # Delete
            engine.delete_rule(name)

            rule_idx += 1
            await asyncio.sleep(0.01)

        # Rules file should be empty (all deleted)
        rules = engine.list_rules()
        assert len(rules) == 0
        # Events should be bounded
        events = engine.get_events()
        assert len(events) <= 100


class TestMQTTReliability:
    """Test MQTT handler under sustained publish load."""

    def test_sustained_publishes(self, mock_mqtt, metrics):
        """Publish PDU data continuously."""
        handler = mock_mqtt
        deadline = time.monotonic() + DURATION
        last_progress = time.monotonic()

        while time.monotonic() < deadline:
            data = make_pdu_data(
                voltage_a=120.0 + random.uniform(-5, 5),
                outlet_power=random.uniform(10, 100),
            )
            handler.publish_pdu_data(data)
            metrics.mqtt_publishes += 1

            if time.monotonic() - last_progress > PROGRESS_INTERVAL:
                status = handler.get_status()
                logger.info(
                    "MQTT progress: %d publishes, %d errors",
                    status["total_publishes"], status["publish_errors"],
                )
                last_progress = time.monotonic()

            time.sleep(0.005)

        status = handler.get_status()
        metrics.mqtt_errors = status["publish_errors"]
        assert status["publish_errors"] == 0
        assert status["total_publishes"] > 0

    def test_publish_with_intermittent_failures(self, mock_mqtt, metrics):
        """Test publish error tracking with simulated failures."""
        handler = mock_mqtt
        publish_call_count = [0]
        original_rc = 0

        def flaky_publish(*args, **kwargs):
            publish_call_count[0] += 1
            mock_result = MagicMock()
            # Fail every 50th publish
            if publish_call_count[0] % 50 == 0:
                mock_result.rc = 1  # MQTT error
            else:
                mock_result.rc = 0
            return mock_result

        handler.client.publish.side_effect = flaky_publish
        deadline = time.monotonic() + min(DURATION, 30)

        while time.monotonic() < deadline:
            data = make_pdu_data()
            handler.publish_pdu_data(data)
            metrics.mqtt_publishes += 1
            time.sleep(0.005)

        status = handler.get_status()
        metrics.mqtt_errors = status["publish_errors"]
        # Should have some errors but not all
        if metrics.mqtt_publishes > 100:
            assert status["publish_errors"] > 0
            assert status["publish_errors"] < status["total_publishes"]

    def test_disconnect_reconnect_cycles(self, mock_mqtt, metrics):
        """Simulate repeated MQTT disconnect/reconnect cycles."""
        handler = mock_mqtt
        deadline = time.monotonic() + min(DURATION, 30)

        while time.monotonic() < deadline:
            # Simulate disconnect
            handler._on_disconnect(
                handler.client, None, MagicMock(), 1, None
            )
            assert not handler._connected
            metrics.fault_injections += 1

            # Publish while disconnected (should track errors gracefully)
            data = make_pdu_data()
            handler.publish_pdu_data(data)

            # Simulate reconnect
            handler._on_connect(
                handler.client, None, MagicMock(), 0, None
            )
            assert handler._connected

            # Publish while connected
            handler.publish_pdu_data(data)
            metrics.mqtt_publishes += 1

            time.sleep(0.01)

        status = handler.get_status()
        assert status["reconnect_count"] > 0

    def test_ha_discovery_idempotent(self, mock_mqtt, metrics):
        """Calling HA discovery multiple times only publishes once."""
        handler = mock_mqtt

        for _ in range(100):
            handler.publish_ha_discovery(10, 2)

        status = handler.get_status()
        assert status["ha_discovery_sent"] is True
        # Should only have published discovery configs once
        # 10 outlet switches + 12 bank sensors + 2 input sensors + 1 bridge sensor = 25
        # Check that total publishes is exactly 25 (not 2500)
        assert status["total_publishes"] == 25


class TestIntegratedReliability:
    """Test multiple subsystems working together."""

    @pytest.mark.asyncio
    async def test_full_poll_loop_simulation(self, tmp_db, tmp_rules, metrics):
        """Simulate the full bridge poll loop: MockPDU -> History + Automation + MQTT."""
        pdu = MockPDU()
        history = HistoryStore(tmp_db, retention_days=7)
        config = make_config()

        commands_issued = []

        async def cmd_callback(outlet, action):
            commands_issued.append((outlet, action))

        engine = AutomationEngine(tmp_rules, command_callback=cmd_callback)
        engine.create_rule({
            "name": "fail_guard",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 100.0,
            "outlet": 3,
            "action": "off",
            "delay": 0,
        })

        # Mock MQTT
        with patch("src.mqtt_handler.mqtt.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.publish.return_value = MagicMock(rc=0)
            MockClient.return_value = mock_client
            mqtt_handler = MQTTHandler(config)
            mqtt_handler._connected = True
            mqtt_handler._last_connect_time = time.time()

            deadline = time.monotonic() + DURATION
            last_progress = time.monotonic()
            fault_active = False

            try:
                while time.monotonic() < deadline:
                    # Inject faults periodically
                    if random.random() < 0.02 and not fault_active:
                        bank = random.choice([1, 2])
                        pdu.simulate_input_failure(bank)
                        fault_active = True
                        metrics.fault_injections += 1
                    elif random.random() < 0.1 and fault_active:
                        pdu.simulate_input_restore(1)
                        pdu.simulate_input_restore(2)
                        fault_active = False

                    # Poll
                    data = await pdu.poll()
                    metrics.polls += 1

                    # Record to history
                    history.record(data)
                    metrics.history_writes += 1

                    # Evaluate automation
                    events = await engine.evaluate(data)
                    metrics.automation_evals += 1
                    for e in events:
                        if e["type"] == "triggered":
                            metrics.automation_triggers += 1
                        elif e["type"] == "restored":
                            metrics.automation_restores += 1

                    # Publish to MQTT
                    mqtt_handler.publish_pdu_data(data)
                    mqtt_handler.publish_automation_status(engine.list_rules())
                    for event in events:
                        mqtt_handler.publish_automation_event(event)
                    metrics.mqtt_publishes += 1

                    if time.monotonic() - last_progress > PROGRESS_INTERVAL:
                        metrics.snapshot_memory()
                        hist_health = history.get_health()
                        mqtt_status = mqtt_handler.get_status()
                        logger.info(
                            "Integrated loop: %d polls, %d writes (%d errs), "
                            "%d evals, %d triggers, %d MQTT publishes (%d errs), "
                            "%d faults",
                            metrics.polls, metrics.history_writes,
                            hist_health["write_errors"],
                            metrics.automation_evals, metrics.automation_triggers,
                            mqtt_status["total_publishes"],
                            mqtt_status["publish_errors"],
                            metrics.fault_injections,
                        )
                        last_progress = time.monotonic()

                    await asyncio.sleep(0.01)

            finally:
                history.close()

        # Assertions
        assert metrics.polls > 0
        assert metrics.history_writes > 0
        assert metrics.automation_evals > 0
        assert history.get_health()["write_errors"] == 0

    @pytest.mark.asyncio
    async def test_memory_stability(self, tmp_db, tmp_rules, metrics):
        """Verify memory usage stays bounded over time."""
        pdu = MockPDU()
        history = HistoryStore(tmp_db, retention_days=7)

        async def noop_cmd(outlet, action):
            pass

        engine = AutomationEngine(tmp_rules, command_callback=noop_cmd)
        engine.create_rule({
            "name": "mem_test_rule",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 110.0,
            "outlet": 1,
            "action": "off",
            "delay": 0,
        })

        deadline = time.monotonic() + min(DURATION, 60)
        sample_interval = max(1, (min(DURATION, 60)) // 20)

        try:
            while time.monotonic() < deadline:
                for _ in range(50):  # Batch of 50 polls between memory samples
                    data = await pdu.poll()
                    history.record(data)
                    await engine.evaluate(data)
                    metrics.polls += 1

                metrics.snapshot_memory()
                await asyncio.sleep(0.01)

        finally:
            history.close()

        # Check memory growth
        if len(metrics.memory_snapshots) >= 4:
            # Compare first quarter average to last quarter average
            quarter = len(metrics.memory_snapshots) // 4
            first_avg = sum(metrics.memory_snapshots[:quarter]) / quarter
            last_avg = sum(metrics.memory_snapshots[-quarter:]) / quarter

            # Allow 5MB growth (generous — should be much less)
            growth_kb = (last_avg - first_avg) / 1024
            assert growth_kb < 5120, f"Memory grew by {growth_kb:.0f} KB — possible leak"


class TestEdgeCases:
    """Test boundary conditions and edge cases under load."""

    def test_history_with_none_values(self, tmp_db, metrics):
        """Write data with None fields (simulates incomplete SNMP responses)."""
        store = HistoryStore(tmp_db, retention_days=7)

        try:
            for _ in range(min(DURATION * 100, 1000)):
                # Create data with random None values
                data = PDUData(
                    device_name="Edge Test",
                    outlet_count=2,
                    phase_count=1,
                    input_voltage=random.choice([120.0, None]),
                    input_frequency=random.choice([60.0, None]),
                    outlets={
                        1: OutletData(
                            number=1, name="O1", state="on",
                            current=random.choice([0.5, None]),
                            power=random.choice([50.0, None]),
                            energy=random.choice([1.0, None]),
                        ),
                    },
                    banks={
                        1: BankData(
                            number=1,
                            voltage=random.choice([120.0, None]),
                            current=random.choice([5.0, None]),
                            power=random.choice([300.0, None]),
                            apparent_power=random.choice([330.0, None]),
                            power_factor=random.choice([0.91, None]),
                            load_state="normal",
                        ),
                    },
                )
                store.record(data)
                metrics.history_writes += 1

        finally:
            store.close()

        assert store.get_health()["write_errors"] == 0

    @pytest.mark.asyncio
    async def test_automation_event_ring_buffer(self, tmp_rules, metrics):
        """Verify event list stays bounded at max_events."""
        async def noop_cmd(outlet, action):
            pass

        engine = AutomationEngine(tmp_rules, command_callback=noop_cmd)
        engine.create_rule({
            "name": "rapid_fire",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 130.0,  # Always triggers at 120V
            "outlet": 1,
            "action": "off",
            "delay": 0,
        })

        # Rapidly trigger and restore to generate many events
        for _ in range(200):
            # Trigger
            await engine.evaluate(make_pdu_data(voltage_a=120.0))
            # Restore
            await engine.evaluate(make_pdu_data(voltage_a=140.0))

        events = engine.get_events()
        assert len(events) <= 100, f"Event buffer exceeded max: {len(events)}"

    def test_history_report_generation(self, tmp_db, metrics):
        """Generate reports from sustained data collection."""
        store = HistoryStore(tmp_db, retention_days=30, house_monthly_kwh=500)

        try:
            # Insert a week of backdated data
            base_ts = int(time.time()) - 8 * 86400  # 8 days ago
            for offset in range(0, 7 * 86400, 60):  # Every minute for a week
                ts = base_ts + offset
                store._conn.execute(
                    "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf) "
                    "VALUES (?, 1, 120.0, 5.0, 300.0, 330.0, 0.91)",
                    (ts,),
                )
                store._conn.execute(
                    "INSERT INTO outlet_samples (ts, outlet, state, current, power, energy) "
                    "VALUES (?, 1, 'on', 0.5, 50.0, ?)",
                    (ts, offset / 3600.0 * 0.05),
                )
            store._conn.commit()
            metrics.history_writes += 7 * 24 * 60

            # Generate report
            report = store.generate_weekly_report()
            if report:
                assert "total_kwh" in report
                assert report["total_kwh"] > 0
                assert "per_outlet" in report
                assert "daily" in report
                if report.get("house_pct") is not None:
                    assert report["house_pct"] > 0

        finally:
            store.close()

    @pytest.mark.asyncio
    async def test_empty_pdu_data(self, tmp_db, tmp_rules, metrics):
        """Handle PDU data with no outlets or banks gracefully."""
        history = HistoryStore(tmp_db, retention_days=7)

        async def noop_cmd(outlet, action):
            pass

        engine = AutomationEngine(tmp_rules, command_callback=noop_cmd)

        empty_data = PDUData(
            device_name="Empty",
            outlet_count=0,
            phase_count=0,
            outlets={},
            banks={},
        )

        try:
            for _ in range(100):
                history.record(empty_data)
                await engine.evaluate(empty_data)
                metrics.polls += 1

        finally:
            history.close()

        assert history.get_health()["write_errors"] == 0

    def test_history_close_reopen(self, tmp_db, metrics):
        """Close and reopen database repeatedly (simulates restarts)."""
        for _ in range(min(DURATION, 20)):
            store = HistoryStore(tmp_db, retention_days=7)
            for _ in range(50):
                store.record(make_pdu_data())
                metrics.history_writes += 1
            store.close()
            # Double-close should be safe
            store.close()

        # Final verification — data should persist across reopens
        store = HistoryStore(tmp_db, retention_days=7)
        try:
            now = time.time()
            rows = store.query_banks(now - 3600, now)
            assert len(rows) > 0
        finally:
            store.close()


class TestHealthMetricAccuracy:
    """Verify that health metrics accurately reflect actual operations."""

    def test_history_health_counters(self, tmp_db, metrics):
        """Verify write counts match actual writes."""
        store = HistoryStore(tmp_db, retention_days=7)
        write_count = 500

        try:
            for _ in range(write_count):
                store.record(make_pdu_data())
        finally:
            store.close()

        health = store.get_health()
        assert health["total_writes"] == write_count
        assert health["write_errors"] == 0
        assert health["healthy"] is True

    def test_mqtt_health_counters(self, mock_mqtt, metrics):
        """Verify publish counts match actual publishes."""
        handler = mock_mqtt

        for _ in range(100):
            handler.publish_pdu_data(make_pdu_data())

        status = handler.get_status()
        # Each publish_pdu_data publishes: 1 status + 2 input + (10 outlets * 5 fields)
        # + (2 banks * 6 fields) = 1 + 2 + 50 + 12 = 65 publishes per call
        assert status["total_publishes"] == 100 * 65
        assert status["publish_errors"] == 0

    @pytest.mark.asyncio
    async def test_automation_command_failure_counter(self, tmp_rules, metrics):
        """Verify command failure counter matches actual failures."""
        failure_count = [0]

        async def counting_fail_cmd(outlet, action):
            failure_count[0] += 1
            raise RuntimeError("Always fails")

        engine = AutomationEngine(tmp_rules, command_callback=counting_fail_cmd)
        engine.create_rule({
            "name": "always_trigger",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 130.0,
            "outlet": 1,
            "action": "off",
            "delay": 0,
        })

        for _ in range(50):
            await engine.evaluate(make_pdu_data(voltage_a=120.0))

        # Each eval tries to trigger (and fails), then resets for next attempt
        assert engine._command_failures == failure_count[0]
        assert engine._command_failures == 50
