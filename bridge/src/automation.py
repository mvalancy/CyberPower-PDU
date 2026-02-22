# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""Automation engine — input-failure outlet control rules."""

import json
import logging
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .pdu_model import PDUData

logger = logging.getLogger(__name__)

VALID_CONDITIONS = frozenset({
    "voltage_below", "voltage_above",
    "ats_source_is", "ats_preferred_lost",
    "time_after", "time_before", "time_between",
})
VALID_ACTIONS = frozenset({"on", "off"})


@dataclass
class AutomationRule:
    name: str
    input: int            # 1 (bank A) or 2 (bank B), ignored for time conditions
    condition: str        # "voltage_below", "voltage_above", "time_after", etc.
    threshold: Any        # volts (float) or time string ("22:00", "22:00-06:00")
    outlet: int           # outlet number to act on
    action: str           # "on" or "off"
    restore: bool = True  # reverse action when condition clears
    delay: int = 5        # seconds condition must hold before acting

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "input": self.input,
            "condition": self.condition,
            "threshold": self.threshold,
            "outlet": self.outlet,
            "action": self.action,
            "restore": self.restore,
            "delay": self.delay,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AutomationRule":
        condition = d["condition"]
        if condition not in VALID_CONDITIONS:
            raise ValueError(f"Unknown condition: {condition!r}")

        action = d["action"]
        if action not in VALID_ACTIONS:
            raise ValueError(f"Invalid action: {action!r} (must be 'on' or 'off')")

        # Time conditions keep threshold as string; voltage conditions as float
        if condition in ("time_after", "time_before", "time_between"):
            threshold = str(d["threshold"])
            # Validate time format
            if condition == "time_between":
                parts = threshold.split("-")
                if len(parts) != 2:
                    raise ValueError(f"time_between threshold must be HH:MM-HH:MM, got {threshold!r}")
                _validate_time_str(parts[0])
                _validate_time_str(parts[1])
            else:
                _validate_time_str(threshold)
        elif condition in ("ats_source_is",):
            threshold = int(d["threshold"])
        else:
            threshold = float(d["threshold"])

        outlet = int(d["outlet"])
        if outlet < 1:
            raise ValueError(f"Outlet must be >= 1, got {outlet}")

        return cls(
            name=d["name"],
            input=int(d.get("input", 0)),
            condition=condition,
            threshold=threshold,
            outlet=outlet,
            action=action,
            restore=d.get("restore", True),
            delay=int(d.get("delay", 5)),
        )


def _validate_time_str(s: str):
    """Validate HH:MM format."""
    s = s.strip()
    parts = s.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format: {s!r} (expected HH:MM)")
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid time format: {s!r} (non-numeric)")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Invalid time: {s!r} (hour 0-23, minute 0-59)")


@dataclass
class RuleState:
    triggered: bool = False
    condition_since: float | None = None  # when condition first became true
    fired_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "condition_since": self.condition_since,
            "fired_at": self.fired_at,
        }


EventCallback = Any  # callable for publishing events


class AutomationEngine:
    def __init__(self, rules_file: str, command_callback=None):
        self._rules_file = Path(rules_file)
        self._rules: dict[str, AutomationRule] = {}
        self._states: dict[str, RuleState] = {}
        self._events: list[dict[str, Any]] = []
        self._max_events = 100
        self._command_callback = command_callback
        self._command_failures = 0
        self._load()

    def _load(self):
        if self._rules_file.exists():
            try:
                data = json.loads(self._rules_file.read_text())
                for d in data:
                    try:
                        rule = AutomationRule.from_dict(d)
                        self._rules[rule.name] = rule
                        self._states[rule.name] = RuleState()
                    except (KeyError, ValueError, TypeError) as e:
                        logger.error("Skipping invalid rule %s: %s", d.get("name", "?"), e)
                logger.info("Loaded %d automation rules from %s",
                            len(self._rules), self._rules_file)
            except Exception:
                logger.exception("Failed to load rules from %s", self._rules_file)
        else:
            logger.info("No rules file at %s, starting empty", self._rules_file)

    def _save(self):
        """Save rules atomically using temp file + rename."""
        self._rules_file.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps([r.to_dict() for r in self._rules.values()], indent=2)
        # Write to temp file then rename for atomicity
        tmp_path = self._rules_file.with_suffix(".tmp")
        try:
            tmp_path.write_text(data)
            tmp_path.rename(self._rules_file)
        except Exception:
            logger.exception("Failed to save rules to %s", self._rules_file)
            # Clean up temp file on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    def _add_event(self, rule_name: str, event_type: str, details: str):
        event = {
            "rule": rule_name,
            "type": event_type,
            "details": details,
            "ts": time.time(),
        }
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
        return event

    @staticmethod
    def _parse_time(s: str) -> tuple[int, int]:
        """Parse "HH:MM" into (hour, minute)."""
        parts = s.strip().split(":")
        return int(parts[0]), int(parts[1])

    @staticmethod
    def _time_now() -> tuple[int, int]:
        """Current local time as (hour, minute)."""
        now = datetime.now()
        return now.hour, now.minute

    def _check_condition(self, rule: AutomationRule, data: PDUData) -> bool:
        if rule.condition == "ats_source_is":
            # Triggers when the ATS active source matches threshold (1=A, 2=B)
            if data.ats_current_source is None:
                return False
            return data.ats_current_source == int(rule.threshold)
        elif rule.condition == "ats_preferred_lost":
            # Triggers when ATS has transferred away from the preferred source
            if data.ats_current_source is None or data.ats_preferred_source is None:
                return False
            return data.ats_current_source != data.ats_preferred_source
        elif rule.condition in ("time_after", "time_before", "time_between"):
            return self._check_time_condition(rule)
        else:
            # Voltage-based conditions use per-input SOURCE voltage (ePDU2),
            # NOT load bank voltage (which always shows ~120V on ATS PDUs).
            source = data.source_a if rule.input == 1 else data.source_b
            if source is None or source.voltage is None:
                return False
            if rule.condition == "voltage_below":
                return source.voltage < rule.threshold
            elif rule.condition == "voltage_above":
                return source.voltage > rule.threshold
        return False

    def _check_time_condition(self, rule: AutomationRule) -> bool:
        """Evaluate time-of-day conditions."""
        now_h, now_m = self._time_now()
        now_mins = now_h * 60 + now_m

        if rule.condition == "time_after":
            th, tm = self._parse_time(str(rule.threshold))
            return now_mins >= th * 60 + tm

        elif rule.condition == "time_before":
            th, tm = self._parse_time(str(rule.threshold))
            return now_mins < th * 60 + tm

        elif rule.condition == "time_between":
            parts = str(rule.threshold).split("-")
            start_h, start_m = self._parse_time(parts[0])
            end_h, end_m = self._parse_time(parts[1])
            start_mins = start_h * 60 + start_m
            end_mins = end_h * 60 + end_m

            if start_mins <= end_mins:
                # Same-day range (e.g., 09:00-17:00)
                return start_mins <= now_mins < end_mins
            else:
                # Midnight wrap (e.g., 22:00-06:00)
                return now_mins >= start_mins or now_mins < end_mins

        return False

    async def evaluate(self, data: PDUData) -> list[dict[str, Any]]:
        """Evaluate all rules against current PDU data. Returns new events."""
        now = time.time()
        new_events = []

        for name, rule in self._rules.items():
            state = self._states[name]

            try:
                condition_met = self._check_condition(rule, data)
            except Exception:
                logger.exception("Error checking condition for rule '%s'", name)
                continue

            if condition_met and not state.triggered:
                # Condition just became true or is still pending
                if state.condition_since is None:
                    state.condition_since = now
                    logger.debug("Rule '%s': condition met, starting delay", name)

                elapsed = now - state.condition_since
                if elapsed >= rule.delay:
                    # Fire the rule
                    event = self._add_event(
                        name, "triggered",
                        f"Input {rule.input} {rule.condition} {rule.threshold} "
                        f"-> outlet {rule.outlet} {rule.action}"
                    )
                    new_events.append(event)
                    logger.warning("Rule '%s' TRIGGERED: outlet %d -> %s",
                                   name, rule.outlet, rule.action)
                    if self._command_callback:
                        try:
                            await self._command_callback(rule.outlet, rule.action)
                            state.triggered = True
                            state.fired_at = now
                        except Exception:
                            self._command_failures += 1
                            logger.exception(
                                "Command failed for rule '%s': outlet %d -> %s",
                                name, rule.outlet, rule.action,
                            )
                            # Reset so we retry next cycle
                            state.condition_since = None
                    else:
                        state.triggered = True
                        state.fired_at = now

            elif not condition_met and state.triggered and rule.restore:
                # Condition cleared, restore
                restore_action = "on" if rule.action == "off" else "off"
                event = self._add_event(
                    name, "restored",
                    f"Input {rule.input} recovered "
                    f"-> outlet {rule.outlet} {restore_action}"
                )
                new_events.append(event)
                logger.info("Rule '%s' RESTORED: outlet %d -> %s",
                            name, rule.outlet, restore_action)
                if self._command_callback:
                    try:
                        await self._command_callback(rule.outlet, restore_action)
                    except Exception:
                        self._command_failures += 1
                        logger.exception(
                            "Restore command failed for rule '%s': outlet %d -> %s",
                            name, rule.outlet, restore_action,
                        )
                state.triggered = False
                state.condition_since = None
                state.fired_at = None

            elif not condition_met:
                # Condition not met, reset pending state
                state.condition_since = None

        return new_events

    # --- CRUD ---

    def list_rules(self) -> list[dict[str, Any]]:
        result = []
        for name, rule in self._rules.items():
            state = self._states[name]
            entry = rule.to_dict()
            entry["state"] = state.to_dict()
            result.append(entry)
        return result

    def create_rule(self, data: dict[str, Any]) -> AutomationRule:
        rule = AutomationRule.from_dict(data)
        if rule.name in self._rules:
            raise ValueError(f"Rule '{rule.name}' already exists")
        self._rules[rule.name] = rule
        self._states[rule.name] = RuleState()
        self._save()
        self._add_event(rule.name, "created", f"Rule '{rule.name}' created")
        logger.info("Created rule '%s'", rule.name)
        return rule

    def update_rule(self, name: str, data: dict[str, Any]) -> AutomationRule:
        if name not in self._rules:
            raise KeyError(f"Rule '{name}' not found")
        data["name"] = name
        rule = AutomationRule.from_dict(data)
        self._rules[name] = rule
        self._states[name] = RuleState()
        self._save()
        self._add_event(name, "updated", f"Rule '{name}' updated")
        logger.info("Updated rule '%s'", name)
        return rule

    def delete_rule(self, name: str):
        if name not in self._rules:
            raise KeyError(f"Rule '{name}' not found")
        del self._rules[name]
        del self._states[name]
        self._save()
        self._add_event(name, "deleted", f"Rule '{name}' deleted")
        logger.info("Deleted rule '%s'", name)

    def get_events(self) -> list[dict[str, Any]]:
        return list(reversed(self._events))
