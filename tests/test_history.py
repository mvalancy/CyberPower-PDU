# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

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

    def test_record_writes_immediately(self):
        store, path = self._make_store()
        data = make_pdu_data()
        store.record(data)
        # Force commit for test
        store._conn.commit()
        rows = store._conn.execute("SELECT COUNT(*) as c FROM bank_samples").fetchone()
        assert rows["c"] == 1  # Written immediately
        outlet_rows = store._conn.execute("SELECT COUNT(*) as c FROM outlet_samples").fetchone()
        assert outlet_rows["c"] == 2  # 2 outlets
        store.close()
        os.unlink(path)

    def test_record_values_correct(self):
        store, path = self._make_store()
        data = make_pdu_data(power=100.0, voltage=120.0, current=0.8)
        store.record(data)
        store._conn.commit()

        bank_rows = store._conn.execute("SELECT * FROM bank_samples").fetchall()
        assert len(bank_rows) == 1
        assert bank_rows[0]["voltage"] == pytest.approx(120.0)
        assert bank_rows[0]["current"] == pytest.approx(0.8)
        assert bank_rows[0]["power"] == pytest.approx(100.0)
        assert bank_rows[0]["apparent"] == pytest.approx(110.0)
        assert bank_rows[0]["pf"] == pytest.approx(0.91)

        outlet_rows = store._conn.execute(
            "SELECT * FROM outlet_samples WHERE outlet=1"
        ).fetchall()
        assert len(outlet_rows) == 1
        assert outlet_rows[0]["current"] == pytest.approx(0.8)
        assert outlet_rows[0]["power"] == pytest.approx(50.0)
        assert outlet_rows[0]["energy"] == pytest.approx(1.5)
        assert outlet_rows[0]["state"] == "on"

        store.close()
        os.unlink(path)

    def test_batch_commit(self):
        store, path = self._make_store()
        data = make_pdu_data()

        # Record 10 samples to trigger auto-commit
        for _ in range(10):
            store.record(data)

        assert store._write_count == 0  # Reset after commit

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

        # Raw 1s for <=1h
        assert store._pick_interval(now - 3600, now) == 1
        # 10s for <=6h
        assert store._pick_interval(now - 6 * 3600, now) == 10
        # 1m for <=24h
        assert store._pick_interval(now - 12 * 3600, now) == 60
        # 5m for <=7d
        assert store._pick_interval(now - 3 * 86400, now) == 300
        # 15m for <=30d
        assert store._pick_interval(now - 15 * 86400, now) == 900
        # 30m for 60d
        assert store._pick_interval(now - 60 * 86400, now) == 1800

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

    def test_close_commits(self):
        store, path = self._make_store()

        data = make_pdu_data()
        store.record(data)
        # Data might not be committed yet
        store.close()

        # Reopen and check data was written
        import sqlite3
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM bank_samples").fetchall()
        assert len(rows) == 1
        conn.close()
        os.unlink(path)

    def test_default_retention_60_days(self):
        store, path = self._make_store()
        assert store._retention_days == 60
        store.close()
        os.unlink(path)
