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
