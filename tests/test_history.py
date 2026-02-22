"""Unit tests for SQLite history storage."""

import json
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.history import HistoryStore
from src.pdu_model import BankData, OutletData, PDUData


def make_pdu_data(power=100.0, voltage=120.0, current=0.8, outlet_power=50.0):
    """Create a PDUData with specified values."""
    return PDUData(
        device_name="Test PDU",
        outlet_count=2,
        phase_count=1,
        input_voltage=voltage,
        input_frequency=60.0,
        outlets={
            1: OutletData(number=1, name="Outlet 1", state="on",
                          current=current, power=outlet_power, energy=1.5),
            2: OutletData(number=2, name="Outlet 2", state="on",
                          current=0.2, power=24.0, energy=0.3),
        },
        banks={
            1: BankData(number=1, voltage=voltage, current=current,
                        power=power, apparent_power=110.0, power_factor=0.91),
        },
    )


class TestHistoryStore:
    def _make_store(self, **kwargs):
        tmp = tempfile.mktemp(suffix=".db")
        store = HistoryStore(tmp, **kwargs)
        return store, tmp

    def test_create_tables(self):
        store, path = self._make_store()
        # Check tables exist
        tables = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t["name"] for t in tables]
        assert "bank_samples" in table_names
        assert "outlet_samples" in table_names
        assert "energy_reports" in table_names
        store.close()
        os.unlink(path)

    def test_record_accumulates(self):
        store, path = self._make_store()
        data = make_pdu_data()
        # Record should buffer, not write immediately
        store.record(data)
        rows = store._conn.execute("SELECT COUNT(*) as c FROM bank_samples").fetchone()
        assert rows["c"] == 0  # Still in buffer
        store.close()
        os.unlink(path)

    def test_flush_writes_data(self):
        store, path = self._make_store()
        data = make_pdu_data(power=100.0, voltage=120.0, current=0.8)

        # Manually set up accumulator and flush
        store._current_minute = int(time.time()) // 60
        store._bank_accum[1] = [
            {"voltage": 120.0, "current": 0.8, "power": 100.0, "apparent": 110.0, "pf": 0.91},
            {"voltage": 121.0, "current": 0.9, "power": 105.0, "apparent": 115.0, "pf": 0.92},
        ]
        store._outlet_accum[1] = [
            {"state": "on", "current": 0.8, "power": 50.0, "energy": 1.5},
            {"state": "on", "current": 0.9, "power": 55.0, "energy": 1.6},
        ]
        store._flush()

        # Check bank data was averaged
        bank_rows = store._conn.execute("SELECT * FROM bank_samples").fetchall()
        assert len(bank_rows) == 1
        assert bank_rows[0]["voltage"] == pytest.approx(120.5, abs=0.01)
        assert bank_rows[0]["current"] == pytest.approx(0.85, abs=0.01)
        assert bank_rows[0]["power"] == pytest.approx(102.5, abs=0.01)

        # Check outlet data
        outlet_rows = store._conn.execute("SELECT * FROM outlet_samples").fetchall()
        assert len(outlet_rows) == 1
        assert outlet_rows[0]["current"] == pytest.approx(0.85, abs=0.01)
        assert outlet_rows[0]["power"] == pytest.approx(52.5, abs=0.01)
        # Energy should be last reading
        assert outlet_rows[0]["energy"] == pytest.approx(1.6, abs=0.01)
        # State should be last known
        assert outlet_rows[0]["state"] == "on"

        store.close()
        os.unlink(path)

    def test_minute_rollover_triggers_flush(self):
        store, path = self._make_store()

        # Set current minute to an old value
        store._current_minute = 1000
        store._bank_accum[1] = [
            {"voltage": 120.0, "current": 0.8, "power": 100.0, "apparent": 110.0, "pf": 0.91},
        ]

        # Record with a new minute (simulating time passing)
        data = make_pdu_data()
        store._current_minute = 1000  # Ensure it's the old minute
        # Manually trigger rollover by recording with a different minute
        now_minute = int(time.time()) // 60
        if now_minute == 1000:
            now_minute = 1001
        store._current_minute = now_minute - 1  # Force a different minute
        store._bank_accum[1] = [
            {"voltage": 119.0, "current": 0.7, "power": 95.0, "apparent": 105.0, "pf": 0.9},
        ]
        store.record(data)  # This should flush old data

        bank_rows = store._conn.execute("SELECT * FROM bank_samples").fetchall()
        assert len(bank_rows) == 1  # One minute of data was flushed

        store.close()
        os.unlink(path)

    def test_query_banks(self):
        store, path = self._make_store()
        now = int(time.time())

        # Insert test data
        for i in range(5):
            store._conn.execute(
                "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now - (4 - i) * 60, 1, 120.0 + i, 0.8, 100.0 + i * 10, 110.0, 0.91),
            )
        store._conn.commit()

        rows = store.query_banks(now - 300, now)
        assert len(rows) >= 1
        # Values should be present
        assert rows[0]["voltage"] is not None

        store.close()
        os.unlink(path)

    def test_query_outlets(self):
        store, path = self._make_store()
        now = int(time.time())

        for i in range(3):
            store._conn.execute(
                "INSERT INTO outlet_samples (ts, outlet, state, current, power, energy) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now - (2 - i) * 60, 1, "on", 0.5, 50.0, 1.0 + i * 0.1),
            )
        store._conn.commit()

        rows = store.query_outlets(now - 300, now)
        assert len(rows) >= 1
        assert rows[0]["power"] is not None

        store.close()
        os.unlink(path)

    def test_auto_downsampling(self):
        store, path = self._make_store()
        now = int(time.time())

        # 1m interval for <6h
        assert store._pick_interval(now - 3600, now) == 60
        # 5m interval for <24h
        assert store._pick_interval(now - 12 * 3600, now) == 300
        # 15m interval for <7d
        assert store._pick_interval(now - 3 * 86400, now) == 900
        # 1h interval for 30d
        assert store._pick_interval(now - 30 * 86400, now) == 3600

        store.close()
        os.unlink(path)

    def test_retention_cleanup(self):
        store, path = self._make_store(retention_days=1)
        now = int(time.time())

        # Insert old data (2 days ago)
        old_ts = now - 2 * 86400
        store._conn.execute(
            "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (old_ts, 1, 120.0, 0.8, 100.0, 110.0, 0.91),
        )
        # Insert recent data
        store._conn.execute(
            "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now, 1, 120.0, 0.8, 100.0, 110.0, 0.91),
        )
        store._conn.commit()

        store.cleanup()

        rows = store._conn.execute("SELECT * FROM bank_samples").fetchall()
        assert len(rows) == 1
        assert rows[0]["ts"] == now

        store.close()
        os.unlink(path)

    def test_average_samples(self):
        samples = [
            {"voltage": 120.0, "current": 0.8, "power": None},
            {"voltage": 122.0, "current": 0.9, "power": 100.0},
        ]
        result = HistoryStore._average_samples(samples, ["voltage", "current", "power"])
        assert result["voltage"] == pytest.approx(121.0, abs=0.01)
        assert result["current"] == pytest.approx(0.85, abs=0.01)
        # Power has one None — should average the non-None value
        assert result["power"] == pytest.approx(100.0, abs=0.01)

    def test_average_samples_all_none(self):
        samples = [{"voltage": None}, {"voltage": None}]
        result = HistoryStore._average_samples(samples, ["voltage"])
        assert result["voltage"] is None

    def test_report_list_empty(self):
        store, path = self._make_store()
        assert store.list_reports() == []
        store.close()
        os.unlink(path)

    def test_report_get_nonexistent(self):
        store, path = self._make_store()
        assert store.get_report(999) is None
        store.close()
        os.unlink(path)

    def test_report_latest_empty(self):
        store, path = self._make_store()
        assert store.get_latest_report() is None
        store.close()
        os.unlink(path)

    def test_generate_report_no_data(self):
        store, path = self._make_store()
        # Should return None when no data exists
        result = store.generate_weekly_report()
        assert result is None
        store.close()
        os.unlink(path)

    def test_report_store_and_retrieve(self):
        store, path = self._make_store()

        # Manually insert a report
        report_data = {
            "week_start": "2026-01-05",
            "week_end": "2026-01-12",
            "total_kwh": 42.5,
            "peak_power_w": 500.0,
            "avg_power_w": 250.0,
            "per_outlet": {"1": {"kwh": 20.0}, "2": {"kwh": 22.5}},
            "daily": {},
            "house_pct": None,
            "sample_count": 1000,
        }
        store._conn.execute(
            "INSERT INTO energy_reports (week_start, week_end, created_at, data) "
            "VALUES (?, ?, ?, ?)",
            ("2026-01-05", "2026-01-12", "2026-01-12T00:00:00", json.dumps(report_data)),
        )
        store._conn.commit()

        # List
        reports = store.list_reports()
        assert len(reports) == 1
        assert reports[0]["week_start"] == "2026-01-05"

        # Get by ID
        report = store.get_report(reports[0]["id"])
        assert report is not None
        assert report["data"]["total_kwh"] == 42.5

        # Get latest
        latest = store.get_latest_report()
        assert latest is not None
        assert latest["data"]["total_kwh"] == 42.5

        store.close()
        os.unlink(path)

    def test_close_flushes(self):
        store, path = self._make_store()

        # Add data to accumulator
        store._current_minute = int(time.time()) // 60
        store._bank_accum[1] = [
            {"voltage": 120.0, "current": 0.8, "power": 100.0, "apparent": 110.0, "pf": 0.91},
        ]

        store.close()

        # Reopen and check data was written
        import sqlite3
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM bank_samples").fetchall()
        assert len(rows) == 1
        conn.close()
        os.unlink(path)
