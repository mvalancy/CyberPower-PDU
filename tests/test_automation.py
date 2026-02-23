# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Unit tests for automation engine."""

import asyncio
import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.automation import AutomationEngine, AutomationRule, RuleState
from src.pdu_model import BankData, OutletData, PDUData, SourceData


def make_pdu_data(
    bank1_voltage=120.0, bank2_voltage=120.0,
    source_a_voltage=120.0, source_b_voltage=120.0,
    ats_current_source=1, ats_preferred_source=1,
):
    """Helper to create PDUData with specified voltages.

    Note: bank voltages are OUTPUT load banks (same on ATS PDUs).
    source voltages are the real per-input voltages from ePDU2.
    """
    return PDUData(
        device_name="Test PDU",
        outlet_count=10,
        phase_count=1,
        input_voltage=bank1_voltage,
        input_frequency=60.0,
        outlets={
            1: OutletData(number=1, name="Outlet 1", state="on"),
        },
        banks={
            1: BankData(number=1, voltage=bank1_voltage, current=5.0, load_state="normal"),
            2: BankData(number=2, voltage=bank2_voltage, current=3.0, load_state="normal"),
        },
        source_a=SourceData(
            voltage=source_a_voltage,
            frequency=60.0,
            voltage_status="normal" if source_a_voltage > 10 else "underVoltage",
        ),
        source_b=SourceData(
            voltage=source_b_voltage,
            frequency=60.0,
            voltage_status="normal" if source_b_voltage > 10 else "underVoltage",
        ),
        ats_current_source=ats_current_source,
        ats_preferred_source=ats_preferred_source,
        redundancy_ok=(source_a_voltage > 10 and source_b_voltage > 10),
    )


class TestAutomationRule:
    def test_to_dict(self):
        rule = AutomationRule(
            name="test", input=1, condition="voltage_below",
            threshold=10.0, outlet=1, action="off",
        )
        d = rule.to_dict()
        assert d["name"] == "test"
        assert d["input"] == 1
        assert d["threshold"] == 10.0
        assert d["restore"] is True
        assert d["delay"] == 5

    def test_from_dict(self):
        d = {
            "name": "r1", "input": 2, "condition": "voltage_above",
            "threshold": 130.0, "outlet": 5, "action": "on",
            "restore": False, "delay": 10,
        }
        rule = AutomationRule.from_dict(d)
        assert rule.name == "r1"
        assert rule.input == 2
        assert rule.condition == "voltage_above"
        assert rule.threshold == 130.0
        assert rule.outlet == 5
        assert rule.action == "on"
        assert rule.restore is False
        assert rule.delay == 10

    def test_from_dict_defaults(self):
        d = {
            "name": "r2", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off",
        }
        rule = AutomationRule.from_dict(d)
        assert rule.restore is True
        assert rule.delay == 5

    def test_roundtrip(self):
        rule = AutomationRule(
            name="rt", input=1, condition="voltage_below",
            threshold=15.0, outlet=3, action="off", restore=False, delay=0,
        )
        rule2 = AutomationRule.from_dict(rule.to_dict())
        assert rule == rule2


class TestRuleState:
    def test_defaults(self):
        state = RuleState()
        assert state.triggered is False
        assert state.condition_since is None
        assert state.fired_at is None

    def test_to_dict(self):
        state = RuleState(triggered=True, condition_since=1000.0, fired_at=1005.0)
        d = state.to_dict()
        assert d["triggered"] is True
        assert d["condition_since"] == 1000.0
        assert d["fired_at"] == 1005.0


class TestAutomationEngine:
    def _make_engine(self, rules=None, command_callback=None):
        """Create an engine with a temp rules file."""
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        if rules:
            json.dump(rules, tmp)
        tmp.close()
        engine = AutomationEngine(tmp.name, command_callback=command_callback)
        return engine, tmp.name

    def test_load_empty(self):
        tmp = tempfile.mktemp(suffix=".json")
        engine = AutomationEngine(tmp)
        assert engine.list_rules() == []

    def test_crud_create(self):
        engine, path = self._make_engine()
        rule = engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off",
        })
        assert rule.name == "r1"
        rules = engine.list_rules()
        assert len(rules) == 1
        assert rules[0]["name"] == "r1"
        os.unlink(path)

    def test_crud_create_duplicate(self):
        engine, path = self._make_engine()
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off",
        })
        with pytest.raises(ValueError, match="already exists"):
            engine.create_rule({
                "name": "r1", "input": 1, "condition": "voltage_below",
                "threshold": 10.0, "outlet": 1, "action": "off",
            })
        os.unlink(path)

    def test_crud_update(self):
        engine, path = self._make_engine()
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off",
        })
        engine.update_rule("r1", {
            "input": 2, "condition": "voltage_above",
            "threshold": 130.0, "outlet": 5, "action": "on",
        })
        rules = engine.list_rules()
        assert rules[0]["input"] == 2
        assert rules[0]["threshold"] == 130.0
        os.unlink(path)

    def test_crud_update_nonexistent(self):
        engine, path = self._make_engine()
        with pytest.raises(KeyError, match="not found"):
            engine.update_rule("nope", {
                "input": 1, "condition": "voltage_below",
                "threshold": 10.0, "outlet": 1, "action": "off",
            })
        os.unlink(path)

    def test_crud_delete(self):
        engine, path = self._make_engine()
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off",
        })
        engine.delete_rule("r1")
        assert engine.list_rules() == []
        os.unlink(path)

    def test_crud_delete_nonexistent(self):
        engine, path = self._make_engine()
        with pytest.raises(KeyError, match="not found"):
            engine.delete_rule("nope")
        os.unlink(path)

    def test_persistence(self):
        """Rules survive engine restart."""
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        tmp.close()
        engine1 = AutomationEngine(tmp.name)
        engine1.create_rule({
            "name": "persist_test", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off",
        })
        # Load a new engine from the same file
        engine2 = AutomationEngine(tmp.name)
        rules = engine2.list_rules()
        assert len(rules) == 1
        assert rules[0]["name"] == "persist_test"
        os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_evaluate_no_trigger(self):
        """Normal voltage should not trigger voltage_below rule."""
        engine, path = self._make_engine()
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 0,
        })
        data = make_pdu_data(source_a_voltage=120.0)
        events = await engine.evaluate(data)
        assert events == []
        rules = engine.list_rules()
        assert rules[0]["state"]["triggered"] is False
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_evaluate_trigger_immediate(self):
        """Zero-delay rule fires immediately when condition met."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 0,
        })
        # Source A at 0V (failed), but bank voltage still 120V (ATS transferred)
        data = make_pdu_data(bank1_voltage=120.0, source_a_voltage=0.0)
        events = await engine.evaluate(data)

        assert len(events) == 1
        assert events[0]["type"] == "triggered"
        assert commands == [(1, "off")]

        rules = engine.list_rules()
        assert rules[0]["state"]["triggered"] is True
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_evaluate_trigger_with_delay(self):
        """Rule with delay doesn't fire until delay elapsed."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 5,
        })
        data = make_pdu_data(source_a_voltage=0.0)

        # First eval — starts the timer but doesn't fire
        events = await engine.evaluate(data)
        assert events == []
        assert commands == []

        # Simulate time passage by backdating condition_since
        engine._states["r1"].condition_since -= 6

        # Second eval — delay exceeded, should fire
        events = await engine.evaluate(data)
        assert len(events) == 1
        assert events[0]["type"] == "triggered"
        assert commands == [(1, "off")]
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_evaluate_restore(self):
        """Rule restores when condition clears."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off",
            "restore": True, "delay": 0,
        })

        # Trigger — source A fails
        data_fail = make_pdu_data(source_a_voltage=0.0)
        await engine.evaluate(data_fail)
        assert commands == [(1, "off")]

        # Restore — source A recovers
        data_ok = make_pdu_data(source_a_voltage=120.0)
        events = await engine.evaluate(data_ok)
        assert len(events) == 1
        assert events[0]["type"] == "restored"
        assert commands == [(1, "off"), (1, "on")]
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_evaluate_no_restore_when_disabled(self):
        """Rule does not restore when restore=False."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off",
            "restore": False, "delay": 0,
        })

        # Trigger
        await engine.evaluate(make_pdu_data(source_a_voltage=0.0))
        assert commands == [(1, "off")]

        # Condition clears — no restore
        events = await engine.evaluate(make_pdu_data(source_a_voltage=120.0))
        assert events == []
        assert commands == [(1, "off")]  # No additional command
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_evaluate_voltage_above(self):
        """voltage_above condition triggers when source voltage exceeds threshold."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "overvolt", "input": 2, "condition": "voltage_above",
            "threshold": 130.0, "outlet": 3, "action": "off", "delay": 0,
        })

        # Below threshold — no trigger
        events = await engine.evaluate(make_pdu_data(source_b_voltage=125.0))
        assert events == []

        # Above threshold — triggers
        events = await engine.evaluate(make_pdu_data(source_b_voltage=135.0))
        assert len(events) == 1
        assert commands == [(3, "off")]
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_evaluate_condition_clears_before_delay(self):
        """If condition clears before delay, rule does not fire."""
        engine, path = self._make_engine()
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 5,
        })

        # Condition met — starts timer
        await engine.evaluate(make_pdu_data(source_a_voltage=0.0))
        assert engine._states["r1"].condition_since is not None

        # Condition clears — resets timer
        await engine.evaluate(make_pdu_data(source_a_voltage=120.0))
        assert engine._states["r1"].condition_since is None
        assert engine._states["r1"].triggered is False
        os.unlink(path)

    def test_events_list(self):
        engine, path = self._make_engine()
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off",
        })
        events = engine.get_events()
        assert len(events) == 1
        assert events[0]["type"] == "created"
        os.unlink(path)

    def test_events_max_limit(self):
        engine, path = self._make_engine()
        for i in range(150):
            engine._add_event("test", "info", f"event {i}")
        assert len(engine._events) == 100
        os.unlink(path)

    # --- ATS condition tests ---

    @pytest.mark.asyncio
    async def test_ats_source_is_trigger(self):
        """ats_source_is triggers when active source matches threshold."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "on_source_b", "input": 1, "condition": "ats_source_is",
            "threshold": 2, "outlet": 1, "action": "off", "delay": 0,
        })

        # Source A active — no trigger
        events = await engine.evaluate(make_pdu_data(ats_current_source=1))
        assert events == []

        # Source B active — triggers
        events = await engine.evaluate(make_pdu_data(ats_current_source=2))
        assert len(events) == 1
        assert events[0]["type"] == "triggered"
        assert commands == [(1, "off")]
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_ats_preferred_lost_trigger(self):
        """ats_preferred_lost triggers when current != preferred source."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "pref_lost", "input": 1, "condition": "ats_preferred_lost",
            "threshold": 0, "outlet": 1, "action": "off", "delay": 0,
        })

        # Preferred=A, Current=A — no trigger
        events = await engine.evaluate(
            make_pdu_data(ats_preferred_source=1, ats_current_source=1)
        )
        assert events == []

        # Preferred=A, Current=B (transferred) — triggers
        events = await engine.evaluate(
            make_pdu_data(ats_preferred_source=1, ats_current_source=2)
        )
        assert len(events) == 1
        assert events[0]["type"] == "triggered"
        assert commands == [(1, "off")]
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_ats_preferred_lost_restore(self):
        """ats_preferred_lost restores when ATS transfers back."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "pref_lost", "input": 1, "condition": "ats_preferred_lost",
            "threshold": 0, "outlet": 1, "action": "off",
            "restore": True, "delay": 0,
        })

        # Transfer away — triggers
        await engine.evaluate(
            make_pdu_data(ats_preferred_source=1, ats_current_source=2)
        )
        assert commands == [(1, "off")]

        # Transfer back — restores
        events = await engine.evaluate(
            make_pdu_data(ats_preferred_source=1, ats_current_source=1)
        )
        assert len(events) == 1
        assert events[0]["type"] == "restored"
        assert commands == [(1, "off"), (1, "on")]
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_source_voltage_independent_of_bank(self):
        """Voltage rules use source voltage, not bank voltage.

        On an ATS PDU, both load banks show ~120V even when one input fails.
        Only source_a/source_b voltages reflect individual input health.
        """
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "input_a_fail", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 0,
        })

        # Source A is dead, but bank still shows 120V (ATS on source B)
        data = make_pdu_data(
            bank1_voltage=120.0, bank2_voltage=120.0,
            source_a_voltage=0.0, source_b_voltage=120.0,
            ats_current_source=2,
        )
        events = await engine.evaluate(data)
        assert len(events) == 1
        assert events[0]["type"] == "triggered"
        assert commands == [(1, "off")]
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_input_b_voltage_below(self):
        """Input B voltage_below uses source_b voltage."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "input_b_fail", "input": 2, "condition": "voltage_below",
            "threshold": 80.0, "outlet": 1, "action": "off", "delay": 0,
        })

        # Source B at 120V — no trigger
        events = await engine.evaluate(make_pdu_data(source_b_voltage=120.0))
        assert events == []

        # Source B at 0V — triggers
        events = await engine.evaluate(make_pdu_data(source_b_voltage=0.0))
        assert len(events) == 1
        assert commands == [(1, "off")]
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_missing_source_data_no_crash(self):
        """Rule doesn't crash when source data is None."""
        engine, path = self._make_engine()
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 0,
        })
        data = PDUData(
            device_name="Test PDU",
            outlet_count=10,
            phase_count=1,
            outlets={1: OutletData(number=1, name="Outlet 1", state="on")},
            banks={1: BankData(number=1, voltage=120.0)},
            source_a=None,
            source_b=None,
        )
        events = await engine.evaluate(data)
        assert events == []
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_multiple_rules_independent(self):
        """Multiple rules fire independently."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 0,
        })
        engine.create_rule({
            "name": "r2", "input": 2, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 2, "action": "off", "delay": 0,
        })

        # Only source A fails
        data = make_pdu_data(source_a_voltage=0.0, source_b_voltage=120.0)
        events = await engine.evaluate(data)
        assert len(events) == 1
        assert commands == [(1, "off")]

        # Both fail
        commands.clear()
        # Reset r1 state to test both triggering
        engine._states["r1"].triggered = False
        engine._states["r1"].condition_since = None
        data = make_pdu_data(source_a_voltage=0.0, source_b_voltage=0.0)
        events = await engine.evaluate(data)
        assert len(events) == 2
        assert (1, "off") in commands
        assert (2, "off") in commands
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_triggered_rule_stays_triggered(self):
        """Once triggered, rule stays triggered while condition persists."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "r1", "input": 1, "condition": "voltage_below",
            "threshold": 10.0, "outlet": 1, "action": "off", "delay": 0,
        })

        # First trigger
        await engine.evaluate(make_pdu_data(source_a_voltage=0.0))
        assert commands == [(1, "off")]

        # Second eval with condition still met — should NOT fire again
        events = await engine.evaluate(make_pdu_data(source_a_voltage=0.0))
        assert events == []
        assert commands == [(1, "off")]  # No duplicate command
        os.unlink(path)


class TestTimeConditions:
    """Tests for time_after, time_before, time_between conditions."""

    def _make_engine(self, command_callback=None):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        tmp.close()
        engine = AutomationEngine(tmp.name, command_callback=command_callback)
        return engine, tmp.name

    def test_from_dict_time_after(self):
        """time_after keeps threshold as string."""
        d = {
            "name": "bedtime", "input": 0, "condition": "time_after",
            "threshold": "22:00", "outlet": 1, "action": "off",
        }
        rule = AutomationRule.from_dict(d)
        assert rule.threshold == "22:00"
        assert rule.input == 0

    def test_from_dict_time_between(self):
        """time_between keeps threshold as string."""
        d = {
            "name": "night", "input": 0, "condition": "time_between",
            "threshold": "22:00-06:00", "outlet": 1, "action": "off",
        }
        rule = AutomationRule.from_dict(d)
        assert rule.threshold == "22:00-06:00"

    def test_roundtrip_time_rule(self):
        """Time rule survives serialization/deserialization."""
        rule = AutomationRule(
            name="sched", input=0, condition="time_between",
            threshold="23:00-05:00", outlet=3, action="off",
            restore=True, delay=0,
        )
        d = rule.to_dict()
        rule2 = AutomationRule.from_dict(d)
        assert rule2.condition == "time_between"
        assert rule2.threshold == "23:00-05:00"

    @pytest.mark.asyncio
    async def test_time_after_true(self):
        """time_after triggers when current time is after threshold."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "r1", "input": 0, "condition": "time_after",
            "threshold": "10:00", "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data()

        # Mock time to 14:00
        with patch.object(AutomationEngine, '_time_now', return_value=(14, 0)):
            events = await engine.evaluate(data)

        assert len(events) == 1
        assert events[0]["type"] == "triggered"
        assert commands == [(1, "off")]
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_time_after_false(self):
        """time_after does not trigger before threshold time."""
        engine, path = self._make_engine()
        engine.create_rule({
            "name": "r1", "input": 0, "condition": "time_after",
            "threshold": "22:00", "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data()
        with patch.object(AutomationEngine, '_time_now', return_value=(10, 0)):
            events = await engine.evaluate(data)

        assert events == []
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_time_before_true(self):
        """time_before triggers when current time is before threshold."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "r1", "input": 0, "condition": "time_before",
            "threshold": "06:00", "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data()
        with patch.object(AutomationEngine, '_time_now', return_value=(3, 30)):
            events = await engine.evaluate(data)

        assert len(events) == 1
        assert commands == [(1, "off")]
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_time_before_false(self):
        """time_before does not trigger after threshold time."""
        engine, path = self._make_engine()
        engine.create_rule({
            "name": "r1", "input": 0, "condition": "time_before",
            "threshold": "06:00", "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data()
        with patch.object(AutomationEngine, '_time_now', return_value=(10, 0)):
            events = await engine.evaluate(data)

        assert events == []
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_time_between_same_day(self):
        """time_between works for same-day ranges (e.g., 09:00-17:00)."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "r1", "input": 0, "condition": "time_between",
            "threshold": "09:00-17:00", "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data()

        # Inside range
        with patch.object(AutomationEngine, '_time_now', return_value=(12, 0)):
            events = await engine.evaluate(data)
        assert len(events) == 1
        assert commands == [(1, "off")]

        os.unlink(path)

    @pytest.mark.asyncio
    async def test_time_between_same_day_outside(self):
        """time_between does not trigger outside same-day range."""
        engine, path = self._make_engine()
        engine.create_rule({
            "name": "r1", "input": 0, "condition": "time_between",
            "threshold": "09:00-17:00", "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data()
        with patch.object(AutomationEngine, '_time_now', return_value=(20, 0)):
            events = await engine.evaluate(data)
        assert events == []
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_time_between_midnight_wrap_night(self):
        """time_between handles midnight wrap (e.g., 22:00-06:00) — night time."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "night", "input": 0, "condition": "time_between",
            "threshold": "22:00-06:00", "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data()

        # 23:00 — inside range (after start)
        with patch.object(AutomationEngine, '_time_now', return_value=(23, 0)):
            events = await engine.evaluate(data)
        assert len(events) == 1
        assert commands == [(1, "off")]

        os.unlink(path)

    @pytest.mark.asyncio
    async def test_time_between_midnight_wrap_early_morning(self):
        """time_between midnight wrap — early morning is inside range."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "night", "input": 0, "condition": "time_between",
            "threshold": "22:00-06:00", "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data()

        # 03:00 — inside range (before end, after midnight)
        with patch.object(AutomationEngine, '_time_now', return_value=(3, 0)):
            events = await engine.evaluate(data)
        assert len(events) == 1
        assert commands == [(1, "off")]

        os.unlink(path)

    @pytest.mark.asyncio
    async def test_time_between_midnight_wrap_outside(self):
        """time_between midnight wrap — daytime is outside range."""
        engine, path = self._make_engine()
        engine.create_rule({
            "name": "night", "input": 0, "condition": "time_between",
            "threshold": "22:00-06:00", "outlet": 1, "action": "off", "delay": 0,
        })

        data = make_pdu_data()

        # 12:00 — outside range
        with patch.object(AutomationEngine, '_time_now', return_value=(12, 0)):
            events = await engine.evaluate(data)
        assert events == []
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_time_between_restore(self):
        """Time rule restores when time moves outside the range."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        engine.create_rule({
            "name": "night", "input": 0, "condition": "time_between",
            "threshold": "22:00-06:00", "outlet": 1, "action": "off",
            "restore": True, "delay": 0,
        })

        data = make_pdu_data()

        # Trigger at 23:00
        with patch.object(AutomationEngine, '_time_now', return_value=(23, 0)):
            await engine.evaluate(data)
        assert commands == [(1, "off")]

        # Restore at 08:00 (outside range)
        with patch.object(AutomationEngine, '_time_now', return_value=(8, 0)):
            events = await engine.evaluate(data)
        assert len(events) == 1
        assert events[0]["type"] == "restored"
        assert commands == [(1, "off"), (1, "on")]
        os.unlink(path)

    def test_parse_time(self):
        assert AutomationEngine._parse_time("22:00") == (22, 0)
        assert AutomationEngine._parse_time("06:30") == (6, 30)
        assert AutomationEngine._parse_time("0:00") == (0, 0)
        assert AutomationEngine._parse_time("23:59") == (23, 59)


# ===========================================================================
# New feature tests: days_of_week, schedule_type, enabled, multi-outlet,
# toggle_rule, execution_count/last_execution, backward compat
# ===========================================================================

class TestAutomationRuleNewFields:
    """Tests for new AutomationRule fields: days_of_week, schedule_type, enabled, multi-outlet."""

    def test_outlet_list_single_int(self):
        """_outlet_list() returns [n] for a single int outlet."""
        rule = AutomationRule(
            name="test", input=1, condition="voltage_below",
            threshold=100.0, outlet=3, action="off",
        )
        assert rule._outlet_list() == [3]

    def test_outlet_list_multi(self):
        """_outlet_list() returns the list when outlet is a list."""
        rule = AutomationRule(
            name="test", input=1, condition="voltage_below",
            threshold=100.0, outlet=[1, 2, 5], action="off",
        )
        assert rule._outlet_list() == [1, 2, 5]

    def test_default_days_of_week_empty(self):
        """days_of_week defaults to empty list (run every day)."""
        rule = AutomationRule(
            name="test", input=1, condition="voltage_below",
            threshold=100.0, outlet=1, action="off",
        )
        assert rule.days_of_week == []

    def test_default_schedule_type_continuous(self):
        """schedule_type defaults to 'continuous'."""
        rule = AutomationRule(
            name="test", input=1, condition="voltage_below",
            threshold=100.0, outlet=1, action="off",
        )
        assert rule.schedule_type == "continuous"

    def test_default_enabled_true(self):
        """enabled defaults to True."""
        rule = AutomationRule(
            name="test", input=1, condition="voltage_below",
            threshold=100.0, outlet=1, action="off",
        )
        assert rule.enabled is True

    def test_to_dict_includes_new_fields(self):
        """to_dict() serializes days_of_week, schedule_type, enabled."""
        rule = AutomationRule(
            name="test", input=1, condition="voltage_below",
            threshold=100.0, outlet=[2, 3], action="off",
            days_of_week=[0, 4], schedule_type="oneshot", enabled=False,
        )
        d = rule.to_dict()
        assert d["days_of_week"] == [0, 4]
        assert d["schedule_type"] == "oneshot"
        assert d["enabled"] is False
        assert d["outlet"] == [2, 3]

    def test_from_dict_with_new_fields(self):
        """from_dict() parses days_of_week, schedule_type, enabled."""
        d = {
            "name": "weekday_only",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 90.0,
            "outlet": [1, 4],
            "action": "off",
            "days_of_week": [0, 1, 2, 3, 4],
            "schedule_type": "oneshot",
            "enabled": False,
        }
        rule = AutomationRule.from_dict(d)
        assert rule.days_of_week == [0, 1, 2, 3, 4]
        assert rule.schedule_type == "oneshot"
        assert rule.enabled is False
        assert rule.outlet == [1, 4]
        assert rule._outlet_list() == [1, 4]

    def test_from_dict_roundtrip_new_fields(self):
        """to_dict -> from_dict preserves all new fields."""
        original = AutomationRule(
            name="roundtrip", input=2, condition="voltage_above",
            threshold=130.0, outlet=[1, 2, 3], action="on",
            restore=False, delay=10,
            days_of_week=[5, 6], schedule_type="oneshot", enabled=False,
        )
        d = original.to_dict()
        restored = AutomationRule.from_dict(d)
        assert restored.name == original.name
        assert restored.outlet == [1, 2, 3]
        assert restored.days_of_week == [5, 6]
        assert restored.schedule_type == "oneshot"
        assert restored.enabled is False
        assert restored.restore is False
        assert restored.delay == 10

    def test_from_dict_invalid_schedule_type(self):
        """from_dict() rejects unknown schedule_type."""
        d = {
            "name": "bad",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 90.0,
            "outlet": 1,
            "action": "off",
            "schedule_type": "weekly",
        }
        with pytest.raises(ValueError, match="Invalid schedule_type"):
            AutomationRule.from_dict(d)

    def test_from_dict_invalid_days_of_week(self):
        """from_dict() rejects day values outside 0-6."""
        d = {
            "name": "bad",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 90.0,
            "outlet": 1,
            "action": "off",
            "days_of_week": [0, 7],
        }
        with pytest.raises(ValueError, match="days_of_week"):
            AutomationRule.from_dict(d)

    def test_from_dict_outlet_list_validation(self):
        """from_dict() rejects outlet list with values < 1."""
        d = {
            "name": "bad",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 90.0,
            "outlet": [0, 1],
            "action": "off",
        }
        with pytest.raises(ValueError, match="outlets must be >= 1"):
            AutomationRule.from_dict(d)


class TestRuleStateNewFields:
    """Tests for RuleState execution_count and last_execution fields."""

    def test_defaults(self):
        """RuleState defaults: execution_count=0, last_execution=None."""
        state = RuleState()
        assert state.execution_count == 0
        assert state.last_execution is None

    def test_to_dict_includes_new_fields(self):
        """RuleState.to_dict() serializes execution_count and last_execution."""
        state = RuleState(
            triggered=True,
            condition_since=1000.0,
            fired_at=1005.0,
            execution_count=3,
            last_execution=1005.0,
        )
        d = state.to_dict()
        assert d["execution_count"] == 3
        assert d["last_execution"] == 1005.0
        assert d["triggered"] is True
        assert d["fired_at"] == 1005.0


class TestBackwardCompatibility:
    """Test that old rule dicts without new fields get correct defaults."""

    def test_old_rule_without_new_fields(self):
        """from_dict() with a minimal dict (no new fields) uses defaults."""
        d = {
            "name": "legacy",
            "input": 1,
            "condition": "voltage_below",
            "threshold": 100.0,
            "outlet": 2,
            "action": "off",
        }
        rule = AutomationRule.from_dict(d)
        assert rule.days_of_week == []
        assert rule.schedule_type == "continuous"
        assert rule.enabled is True
        assert rule.restore is True
        assert rule.delay == 5

    def test_old_rule_without_input_field(self):
        """from_dict() defaults input to 0 when not provided."""
        d = {
            "name": "no_input",
            "condition": "time_after",
            "threshold": "22:00",
            "outlet": 1,
            "action": "off",
        }
        rule = AutomationRule.from_dict(d)
        assert rule.input == 0

    def test_engine_loads_old_format_rules(self):
        """Engine loads rules from a file that has no new fields."""
        rules_data = [
            {
                "name": "old_rule",
                "input": 1,
                "condition": "voltage_below",
                "threshold": 90.0,
                "outlet": 1,
                "action": "off",
            }
        ]
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump(rules_data, tmp)
        tmp.close()
        try:
            engine = AutomationEngine(tmp.name)
            rules = engine.list_rules()
            assert len(rules) == 1
            assert rules[0]["enabled"] is True
            assert rules[0]["schedule_type"] == "continuous"
            assert rules[0]["days_of_week"] == []
        finally:
            os.unlink(tmp.name)


class TestDaysOfWeekEvaluation:
    """Test that _check_condition respects the days_of_week filter."""

    def _make_engine(self, command_callback=None):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump([], tmp)
        tmp.close()
        engine = AutomationEngine(tmp.name, command_callback=command_callback)
        return engine, tmp.name

    @pytest.mark.asyncio
    async def test_day_of_week_match_triggers(self):
        """Rule triggers when today's weekday is in days_of_week."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "weekday_off", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
                "delay": 0, "days_of_week": [2],  # Wednesday
            })
            data = make_pdu_data(source_a_voltage=90.0)

            # Wednesday (weekday=2) -- should trigger
            with patch("src.automation.datetime") as mock_dt:
                mock_dt.now.return_value.weekday.return_value = 2
                mock_dt.now.return_value.hour = 12
                mock_dt.now.return_value.minute = 0
                await engine.evaluate(data)
            assert len(commands) == 1
            assert commands[0] == (1, "off")
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_day_of_week_no_match_skips(self):
        """Rule does NOT trigger when today's weekday is not in days_of_week."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "weekday_off", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
                "delay": 0, "days_of_week": [0, 1],  # Mon, Tue only
            })
            data = make_pdu_data(source_a_voltage=90.0)

            # Friday (weekday=4) -- should NOT trigger
            with patch("src.automation.datetime") as mock_dt:
                mock_dt.now.return_value.weekday.return_value = 4
                mock_dt.now.return_value.hour = 12
                mock_dt.now.return_value.minute = 0
                await engine.evaluate(data)
            assert len(commands) == 0
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_empty_days_of_week_runs_every_day(self):
        """Rule with empty days_of_week runs on any day."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "always", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
                "delay": 0, "days_of_week": [],
            })
            data = make_pdu_data(source_a_voltage=90.0)

            # Sunday (weekday=6)
            with patch("src.automation.datetime") as mock_dt:
                mock_dt.now.return_value.weekday.return_value = 6
                mock_dt.now.return_value.hour = 12
                mock_dt.now.return_value.minute = 0
                await engine.evaluate(data)
            assert len(commands) == 1
        finally:
            os.unlink(path)


class TestEnabledFlag:
    """Test that _check_condition respects the enabled flag."""

    def _make_engine(self, command_callback=None):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump([], tmp)
        tmp.close()
        engine = AutomationEngine(tmp.name, command_callback=command_callback)
        return engine, tmp.name

    @pytest.mark.asyncio
    async def test_disabled_rule_does_not_trigger(self):
        """Rule with enabled=False does not fire even when condition is met."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "disabled_rule", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
                "delay": 0, "enabled": False,
            })
            data = make_pdu_data(source_a_voltage=90.0)
            events = await engine.evaluate(data)
            assert len(commands) == 0
            assert len(events) == 0
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_enabled_rule_triggers(self):
        """Rule with enabled=True fires normally."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "enabled_rule", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
                "delay": 0, "enabled": True,
            })
            data = make_pdu_data(source_a_voltage=90.0)
            events = await engine.evaluate(data)
            assert len(commands) == 1
            assert len(events) == 1
        finally:
            os.unlink(path)


class TestToggleRule:
    """Tests for toggle_rule() method."""

    def _make_engine(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump([], tmp)
        tmp.close()
        engine = AutomationEngine(tmp.name)
        return engine, tmp.name

    def test_toggle_disables_enabled_rule(self):
        """toggle_rule disables a rule that is currently enabled."""
        engine, path = self._make_engine()
        try:
            engine.create_rule({
                "name": "toggle_me", "input": 1, "condition": "voltage_below",
                "threshold": 100.0, "outlet": 1, "action": "off",
            })
            result = engine.toggle_rule("toggle_me")
            assert result["enabled"] is False
            assert result["name"] == "toggle_me"
            # Verify persistence
            rules = engine.list_rules()
            assert rules[0]["enabled"] is False
        finally:
            os.unlink(path)

    def test_toggle_enables_disabled_rule(self):
        """toggle_rule enables a rule that is currently disabled."""
        engine, path = self._make_engine()
        try:
            engine.create_rule({
                "name": "toggle_me", "input": 1, "condition": "voltage_below",
                "threshold": 100.0, "outlet": 1, "action": "off",
                "enabled": False,
            })
            result = engine.toggle_rule("toggle_me")
            assert result["enabled"] is True
        finally:
            os.unlink(path)

    def test_toggle_roundtrip(self):
        """toggle_rule twice returns to original state."""
        engine, path = self._make_engine()
        try:
            engine.create_rule({
                "name": "toggle_me", "input": 1, "condition": "voltage_below",
                "threshold": 100.0, "outlet": 1, "action": "off",
            })
            engine.toggle_rule("toggle_me")
            engine.toggle_rule("toggle_me")
            rules = engine.list_rules()
            assert rules[0]["enabled"] is True
        finally:
            os.unlink(path)

    def test_toggle_nonexistent_rule(self):
        """toggle_rule raises KeyError for unknown rule name."""
        engine, path = self._make_engine()
        try:
            with pytest.raises(KeyError, match="not found"):
                engine.toggle_rule("no_such_rule")
        finally:
            os.unlink(path)

    def test_toggle_persists_to_file(self):
        """toggle_rule saves the state to disk."""
        engine, path = self._make_engine()
        try:
            engine.create_rule({
                "name": "persist_toggle", "input": 1, "condition": "voltage_below",
                "threshold": 100.0, "outlet": 1, "action": "off",
            })
            engine.toggle_rule("persist_toggle")
            # Re-load from disk
            engine2 = AutomationEngine(path)
            rules = engine2.list_rules()
            assert rules[0]["enabled"] is False
        finally:
            os.unlink(path)


class TestMultiOutletEvaluation:
    """Tests for multi-outlet evaluation (fire + restore)."""

    def _make_engine(self, command_callback=None):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump([], tmp)
        tmp.close()
        engine = AutomationEngine(tmp.name, command_callback=command_callback)
        return engine, tmp.name

    @pytest.mark.asyncio
    async def test_multi_outlet_fire(self):
        """Triggering a multi-outlet rule sends commands to all outlets."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "multi_fire", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": [1, 3, 5], "action": "off",
                "delay": 0,
            })
            data = make_pdu_data(source_a_voltage=90.0)
            events = await engine.evaluate(data)
            assert len(events) == 1
            assert "1,3,5" in events[0]["details"]
            assert commands == [(1, "off"), (3, "off"), (5, "off")]
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_multi_outlet_restore(self):
        """Restoring a multi-outlet rule sends reverse commands to all outlets."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "multi_restore", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": [2, 4], "action": "off",
                "delay": 0, "restore": True,
            })
            # Trigger
            data_low = make_pdu_data(source_a_voltage=90.0)
            await engine.evaluate(data_low)
            assert commands == [(2, "off"), (4, "off")]
            commands.clear()

            # Restore
            data_ok = make_pdu_data(source_a_voltage=120.0)
            events = await engine.evaluate(data_ok)
            assert len(events) == 1
            assert events[0]["type"] == "restored"
            assert commands == [(2, "on"), (4, "on")]
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_multi_outlet_partial_failure(self):
        """If one outlet command fails, state is not marked triggered (retry next cycle)."""
        commands = []
        call_count = 0

        async def mock_cmd(outlet, action):
            nonlocal call_count
            call_count += 1
            if outlet == 3:
                raise RuntimeError("SNMP timeout")
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "partial_fail", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": [1, 3, 5], "action": "off",
                "delay": 0,
            })
            data = make_pdu_data(source_a_voltage=90.0)
            events = await engine.evaluate(data)
            # Event is created but state not triggered due to failure
            assert len(events) == 1
            state = engine._states["partial_fail"]
            assert state.triggered is False
            assert engine._command_failures == 1
        finally:
            os.unlink(path)


class TestOneshotSchedule:
    """Tests for oneshot schedule_type behavior."""

    def _make_engine(self, command_callback=None):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump([], tmp)
        tmp.close()
        engine = AutomationEngine(tmp.name, command_callback=command_callback)
        return engine, tmp.name

    @pytest.mark.asyncio
    async def test_oneshot_disables_after_fire(self):
        """A oneshot rule auto-disables after successful execution."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "oneshot_rule", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
                "delay": 0, "schedule_type": "oneshot",
            })
            data = make_pdu_data(source_a_voltage=90.0)
            events = await engine.evaluate(data)
            assert len(events) == 1
            assert len(commands) == 1
            # Rule should now be disabled
            rules = engine.list_rules()
            assert rules[0]["enabled"] is False
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_oneshot_does_not_fire_twice(self):
        """A oneshot rule does not fire on subsequent evaluations."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "oneshot_once", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
                "delay": 0, "schedule_type": "oneshot", "restore": False,
            })
            data = make_pdu_data(source_a_voltage=90.0)

            # First eval: fires and auto-disables
            await engine.evaluate(data)
            assert len(commands) == 1
            assert commands[0] == (1, "off")

            # Condition clears (voltage recovers) — no restore since restore=False
            data_ok = make_pdu_data(source_a_voltage=120.0)
            await engine.evaluate(data_ok)

            # Condition re-appears — rule is disabled so it must NOT fire again
            data_low_again = make_pdu_data(source_a_voltage=90.0)
            await engine.evaluate(data_low_again)
            assert len(commands) == 1  # still only the original fire
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_oneshot_persists_disabled_to_file(self):
        """After oneshot fires, the disabled state is saved to disk."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "oneshot_persist", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
                "delay": 0, "schedule_type": "oneshot",
            })
            data = make_pdu_data(source_a_voltage=90.0)
            await engine.evaluate(data)

            # Reload from disk
            engine2 = AutomationEngine(path)
            rules = engine2.list_rules()
            assert rules[0]["enabled"] is False
            assert rules[0]["schedule_type"] == "oneshot"
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_oneshot_without_callback_still_disables(self):
        """Oneshot rule auto-disables even when no command callback is set."""
        engine, path = self._make_engine(command_callback=None)
        try:
            engine.create_rule({
                "name": "oneshot_no_cb", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
                "delay": 0, "schedule_type": "oneshot",
            })
            data = make_pdu_data(source_a_voltage=90.0)
            await engine.evaluate(data)
            rules = engine.list_rules()
            assert rules[0]["enabled"] is False
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_continuous_stays_enabled_after_fire(self):
        """A continuous rule stays enabled after firing (contrast with oneshot)."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "continuous_rule", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
                "delay": 0, "schedule_type": "continuous",
            })
            data = make_pdu_data(source_a_voltage=90.0)
            await engine.evaluate(data)
            rules = engine.list_rules()
            assert rules[0]["enabled"] is True
        finally:
            os.unlink(path)


class TestExecutionCountTracking:
    """Tests for execution_count and last_execution tracking in RuleState."""

    def _make_engine(self, command_callback=None):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump([], tmp)
        tmp.close()
        engine = AutomationEngine(tmp.name, command_callback=command_callback)
        return engine, tmp.name

    @pytest.mark.asyncio
    async def test_execution_count_increments(self):
        """execution_count increments on each successful fire."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "counter", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
                "delay": 0, "restore": True,
            })

            data_low = make_pdu_data(source_a_voltage=90.0)
            data_ok = make_pdu_data(source_a_voltage=120.0)

            # Fire #1
            await engine.evaluate(data_low)
            state = engine._states["counter"]
            assert state.execution_count == 1
            assert state.last_execution is not None
            first_exec = state.last_execution

            # Restore
            await engine.evaluate(data_ok)
            assert state.execution_count == 1  # restore doesn't increment

            # Fire #2
            await engine.evaluate(data_low)
            assert state.execution_count == 2
            assert state.last_execution >= first_exec
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_execution_count_in_list_rules(self):
        """list_rules() includes execution_count and last_execution in state."""
        commands = []

        async def mock_cmd(outlet, action):
            commands.append((outlet, action))

        engine, path = self._make_engine(command_callback=mock_cmd)
        try:
            engine.create_rule({
                "name": "visible", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
                "delay": 0,
            })

            data_low = make_pdu_data(source_a_voltage=90.0)
            await engine.evaluate(data_low)

            rules = engine.list_rules()
            assert rules[0]["state"]["execution_count"] == 1
            assert rules[0]["state"]["last_execution"] is not None
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_execution_count_zero_before_fire(self):
        """execution_count is 0 before any rule has fired."""
        engine, path = self._make_engine()
        try:
            engine.create_rule({
                "name": "unfired", "input": 1, "condition": "voltage_below",
                "threshold": 110.0, "outlet": 1, "action": "off",
            })
            rules = engine.list_rules()
            assert rules[0]["state"]["execution_count"] == 0
            assert rules[0]["state"]["last_execution"] is None
        finally:
            os.unlink(path)
