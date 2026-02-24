# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Unit tests for SQLite history storage."""

import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta

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

    def test_energy_reports_table_still_exists(self):
        """The legacy energy_reports table should still exist (not dropped)."""
        store, path = self._make_store()
        tables = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t["name"] for t in tables]
        assert "energy_reports" in table_names
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

    # --- active_source migration tests ---

    def test_migrate_active_source_idempotent(self):
        """Calling _migrate_active_source multiple times should not raise."""
        store, path = self._make_store()
        store._migrate_active_source()
        store._migrate_active_source()  # Should not raise
        # Verify column exists
        row = store._conn.execute(
            "PRAGMA table_info(bank_samples)"
        ).fetchall()
        col_names = [r["name"] for r in row]
        assert "active_source" in col_names
        store.close()
        os.unlink(path)

    def test_record_stores_active_source(self):
        """record() should store ats_current_source in both tables."""
        store, path = self._make_store()
        data = make_pdu_data()
        data.ats_current_source = 1
        store.record(data)
        store._conn.commit()

        bank_rows = store._conn.execute("SELECT active_source FROM bank_samples").fetchall()
        assert len(bank_rows) == 1
        assert bank_rows[0]["active_source"] == 1

        outlet_rows = store._conn.execute("SELECT active_source FROM outlet_samples").fetchall()
        assert len(outlet_rows) == 2
        assert outlet_rows[0]["active_source"] == 1
        assert outlet_rows[1]["active_source"] == 1

        store.close()
        os.unlink(path)

    def test_record_stores_null_active_source(self):
        """When ats_current_source is None, active_source should be NULL."""
        store, path = self._make_store()
        data = make_pdu_data()
        data.ats_current_source = None
        store.record(data)
        store._conn.commit()

        row = store._conn.execute("SELECT active_source FROM bank_samples").fetchone()
        assert row["active_source"] is None

        store.close()
        os.unlink(path)

    # --- Energy rollup tables exist ---

    def test_rollup_tables_created(self):
        store, path = self._make_store()
        tables = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t["name"] for t in tables]
        assert "energy_daily" in table_names
        assert "energy_monthly" in table_names
        store.close()
        os.unlink(path)

    # --- Daily rollup computation ---

    def test_compute_daily_rollups_basic(self):
        """Test daily rollup from 1Hz power samples."""
        store, path = self._make_store()

        # Insert 100 bank samples for yesterday, all source A
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        day_start = int(datetime.strptime(yesterday, "%Y-%m-%d").timestamp())

        for i in range(100):
            store._conn.execute(
                "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf, device_id, active_source) "
                "VALUES (?, 1, 120.0, 1.0, 120.0, 130.0, 0.92, '', 1)",
                (day_start + i,),
            )
        store._conn.commit()

        store.compute_daily_rollups(device_id="")

        # Check total row exists
        rows = store._conn.execute(
            "SELECT * FROM energy_daily WHERE date = ? AND source IS NULL AND outlet IS NULL",
            (yesterday,),
        ).fetchall()
        assert len(rows) == 1
        # 100 samples * 120W each / 3600 / 1000 = 0.003333 kWh
        expected_kwh = 100 * 120.0 / 3600.0 / 1000.0
        assert rows[0]["kwh"] == pytest.approx(expected_kwh, abs=0.001)
        assert rows[0]["samples"] == 100
        assert rows[0]["peak_power_w"] == pytest.approx(120.0, abs=0.1)

        # Check source A row exists
        src_rows = store._conn.execute(
            "SELECT * FROM energy_daily WHERE date = ? AND source = 1 AND outlet IS NULL",
            (yesterday,),
        ).fetchall()
        assert len(src_rows) == 1
        assert src_rows[0]["kwh"] == pytest.approx(expected_kwh, abs=0.001)

        store.close()
        os.unlink(path)

    def test_compute_daily_rollups_per_source_split(self):
        """Test that source A and B get separate rollup rows."""
        store, path = self._make_store()

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        day_start = int(datetime.strptime(yesterday, "%Y-%m-%d").timestamp())

        # 50 samples on source A, 50 on source B
        for i in range(50):
            store._conn.execute(
                "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf, device_id, active_source) "
                "VALUES (?, 1, 120.0, 1.0, 100.0, 110.0, 0.91, '', 1)",
                (day_start + i,),
            )
        for i in range(50, 100):
            store._conn.execute(
                "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf, device_id, active_source) "
                "VALUES (?, 1, 120.0, 1.0, 200.0, 220.0, 0.91, '', 2)",
                (day_start + i,),
            )
        store._conn.commit()

        store.compute_daily_rollups(device_id="")

        # Source A: 50 * 100 / 3600 / 1000
        src_a = store._conn.execute(
            "SELECT * FROM energy_daily WHERE date = ? AND source = 1 AND outlet IS NULL",
            (yesterday,),
        ).fetchone()
        assert src_a is not None
        assert src_a["kwh"] == pytest.approx(50 * 100.0 / 3600.0 / 1000.0, abs=0.001)

        # Source B: 50 * 200 / 3600 / 1000
        src_b = store._conn.execute(
            "SELECT * FROM energy_daily WHERE date = ? AND source = 2 AND outlet IS NULL",
            (yesterday,),
        ).fetchone()
        assert src_b is not None
        assert src_b["kwh"] == pytest.approx(50 * 200.0 / 3600.0 / 1000.0, abs=0.001)

        # Total row: sum of both
        total = store._conn.execute(
            "SELECT * FROM energy_daily WHERE date = ? AND source IS NULL AND outlet IS NULL",
            (yesterday,),
        ).fetchone()
        expected_total = (50 * 100.0 + 50 * 200.0) / 3600.0 / 1000.0
        assert total["kwh"] == pytest.approx(expected_total, abs=0.001)

        store.close()
        os.unlink(path)

    def test_compute_daily_rollups_per_outlet(self):
        """Test per-outlet rollup rows."""
        store, path = self._make_store()

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        day_start = int(datetime.strptime(yesterday, "%Y-%m-%d").timestamp())

        # Need at least one bank sample for the rollup to proceed
        store._conn.execute(
            "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf, device_id, active_source) "
            "VALUES (?, 1, 120.0, 1.0, 100.0, 110.0, 0.91, '', 1)",
            (day_start,),
        )

        # Insert per-outlet samples
        for i in range(60):
            store._conn.execute(
                "INSERT INTO outlet_samples (ts, outlet, state, current, power, energy, device_id, active_source) "
                "VALUES (?, 1, 'on', 0.5, 60.0, 1.0, '', 1)",
                (day_start + i,),
            )
            store._conn.execute(
                "INSERT INTO outlet_samples (ts, outlet, state, current, power, energy, device_id, active_source) "
                "VALUES (?, 2, 'on', 0.3, 36.0, 0.5, '', 1)",
                (day_start + i,),
            )
        store._conn.commit()

        store.compute_daily_rollups(device_id="")

        # Per-outlet total rows
        o1 = store._conn.execute(
            "SELECT * FROM energy_daily WHERE date = ? AND outlet = 1 AND source IS NULL",
            (yesterday,),
        ).fetchone()
        assert o1 is not None
        assert o1["kwh"] == pytest.approx(60 * 60.0 / 3600.0 / 1000.0, abs=0.001)

        o2 = store._conn.execute(
            "SELECT * FROM energy_daily WHERE date = ? AND outlet = 2 AND source IS NULL",
            (yesterday,),
        ).fetchone()
        assert o2 is not None
        assert o2["kwh"] == pytest.approx(60 * 36.0 / 3600.0 / 1000.0, abs=0.001)

        store.close()
        os.unlink(path)

    def test_compute_daily_rollups_idempotent(self):
        """Running compute_daily_rollups twice should not create duplicate rows."""
        store, path = self._make_store()

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        day_start = int(datetime.strptime(yesterday, "%Y-%m-%d").timestamp())

        for i in range(10):
            store._conn.execute(
                "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf, device_id, active_source) "
                "VALUES (?, 1, 120.0, 1.0, 100.0, 110.0, 0.91, '', 1)",
                (day_start + i,),
            )
        store._conn.commit()

        store.compute_daily_rollups(device_id="")
        count1 = store._conn.execute("SELECT COUNT(*) as c FROM energy_daily").fetchone()["c"]

        store.compute_daily_rollups(device_id="")
        count2 = store._conn.execute("SELECT COUNT(*) as c FROM energy_daily").fetchone()["c"]

        assert count1 == count2  # No duplicates

        store.close()
        os.unlink(path)

    def test_compute_daily_rollups_no_data(self):
        """compute_daily_rollups with no samples should be a no-op."""
        store, path = self._make_store()
        store.compute_daily_rollups(device_id="")
        count = store._conn.execute("SELECT COUNT(*) as c FROM energy_daily").fetchone()["c"]
        assert count == 0
        store.close()
        os.unlink(path)

    # --- Monthly rollup computation ---

    def test_compute_monthly_rollups(self):
        """Monthly rollup should aggregate daily rows."""
        store, path = self._make_store()

        now = datetime.now()
        current_month = now.strftime("%Y-%m")
        # Insert some daily rows for the current month
        for day in range(1, 4):
            date = f"{current_month}-{day:02d}"
            store._conn.execute(
                "INSERT INTO energy_daily (date, device_id, source, outlet, kwh, peak_power_w, avg_power_w, samples) "
                "VALUES (?, '', NULL, NULL, ?, 500.0, 250.0, 3600)",
                (date, 1.5 * day),
            )
        store._conn.commit()

        store.compute_monthly_rollups(device_id="")

        rows = store._conn.execute(
            "SELECT * FROM energy_monthly WHERE month = ? AND device_id = '' "
            "AND source IS NULL AND outlet IS NULL",
            (current_month,),
        ).fetchall()
        assert len(rows) == 1
        # Sum of 1.5 + 3.0 + 4.5 = 9.0 kWh
        assert rows[0]["kwh"] == pytest.approx(9.0, abs=0.01)
        assert rows[0]["days"] == 3

        store.close()
        os.unlink(path)

    def test_compute_monthly_rollups_recompute(self):
        """Monthly rollup should recompute (not accumulate) on repeated calls."""
        store, path = self._make_store()

        now = datetime.now()
        current_month = now.strftime("%Y-%m")
        store._conn.execute(
            "INSERT INTO energy_daily (date, device_id, source, outlet, kwh, peak_power_w, avg_power_w, samples) "
            "VALUES (?, '', NULL, NULL, 5.0, 500.0, 250.0, 3600)",
            (f"{current_month}-01",),
        )
        store._conn.commit()

        store.compute_monthly_rollups(device_id="")
        store.compute_monthly_rollups(device_id="")

        rows = store._conn.execute(
            "SELECT * FROM energy_monthly WHERE month = ? AND source IS NULL AND outlet IS NULL",
            (current_month,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["kwh"] == pytest.approx(5.0, abs=0.01)

        store.close()
        os.unlink(path)

    # --- Rollup tables NOT purged by cleanup ---

    def test_cleanup_does_not_purge_rollup_tables(self):
        """cleanup() should only affect sample tables, not energy_daily/monthly."""
        store, path = self._make_store(retention_days=1)

        # Insert old sample data
        old_ts = int(time.time()) - 2 * 86400
        store._conn.execute(
            "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf, device_id) "
            "VALUES (?, 1, 120.0, 0.8, 100.0, 110.0, 0.91, '')",
            (old_ts,),
        )

        # Insert rollup data with old dates
        store._conn.execute(
            "INSERT INTO energy_daily (date, device_id, source, outlet, kwh, peak_power_w, avg_power_w, samples) "
            "VALUES ('2020-01-01', '', NULL, NULL, 10.0, 500.0, 250.0, 86400)",
        )
        store._conn.execute(
            "INSERT INTO energy_monthly (month, device_id, source, outlet, kwh, peak_power_w, avg_power_w, days) "
            "VALUES ('2020-01', '', NULL, NULL, 300.0, 500.0, 250.0, 30)",
        )
        store._conn.commit()

        store.cleanup()

        # Old bank samples should be deleted
        bank_count = store._conn.execute("SELECT COUNT(*) as c FROM bank_samples").fetchone()["c"]
        assert bank_count == 0

        # Rollup tables should be untouched
        daily_count = store._conn.execute("SELECT COUNT(*) as c FROM energy_daily").fetchone()["c"]
        assert daily_count == 1
        monthly_count = store._conn.execute("SELECT COUNT(*) as c FROM energy_monthly").fetchone()["c"]
        assert monthly_count == 1

        store.close()
        os.unlink(path)

    # --- Query methods ---

    def test_query_energy_daily(self):
        store, path = self._make_store()

        store._conn.execute(
            "INSERT INTO energy_daily (date, device_id, source, outlet, kwh, peak_power_w, avg_power_w, samples) "
            "VALUES ('2026-02-20', '', NULL, NULL, 5.0, 500.0, 250.0, 86400)",
        )
        store._conn.execute(
            "INSERT INTO energy_daily (date, device_id, source, outlet, kwh, peak_power_w, avg_power_w, samples) "
            "VALUES ('2026-02-20', '', 1, NULL, 3.0, 500.0, 250.0, 50000)",
        )
        store._conn.execute(
            "INSERT INTO energy_daily (date, device_id, source, outlet, kwh, peak_power_w, avg_power_w, samples) "
            "VALUES ('2026-02-20', '', 2, NULL, 2.0, 400.0, 200.0, 36400)",
        )
        store._conn.commit()

        # Query total only
        rows = store.query_energy_daily("2026-02-20", "2026-02-20", device_id="")
        assert len(rows) == 1
        assert rows[0]["kwh"] == pytest.approx(5.0)

        # Query source A only
        rows = store.query_energy_daily("2026-02-20", "2026-02-20", device_id="", source=1)
        assert len(rows) == 1
        assert rows[0]["kwh"] == pytest.approx(3.0)

        # Query all rows
        rows = store.query_energy_daily_all("2026-02-20", "2026-02-20", device_id="")
        assert len(rows) == 3

        store.close()
        os.unlink(path)

    def test_query_energy_monthly(self):
        store, path = self._make_store()

        store._conn.execute(
            "INSERT INTO energy_monthly (month, device_id, source, outlet, kwh, peak_power_w, avg_power_w, days) "
            "VALUES ('2026-02', '', NULL, NULL, 150.0, 500.0, 250.0, 28)",
        )
        store._conn.commit()

        rows = store.query_energy_monthly("2026-02", "2026-02", device_id="")
        assert len(rows) == 1
        assert rows[0]["kwh"] == pytest.approx(150.0)
        assert rows[0]["days"] == 28

        store.close()
        os.unlink(path)

    def test_get_energy_summary_empty(self):
        """Energy summary with no data should return all zeros."""
        store, path = self._make_store()
        summary = store.get_energy_summary(device_id="")
        assert summary["today"]["total_kwh"] == 0
        assert summary["this_week"]["total_kwh"] == 0
        assert summary["this_month"]["total_kwh"] == 0
        assert summary["all_time"]["total_kwh"] == 0
        store.close()
        os.unlink(path)

    def test_get_energy_summary_with_data(self):
        """Energy summary should aggregate today's live data + historical rollups."""
        store, path = self._make_store()

        # Insert live data for today
        now = int(time.time())
        for i in range(100):
            store._conn.execute(
                "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf, device_id, active_source) "
                "VALUES (?, 1, 120.0, 1.0, 120.0, 130.0, 0.92, '', 1)",
                (now - 100 + i,),
            )
        store._conn.commit()

        summary = store.get_energy_summary(device_id="")
        # Should have nonzero today data
        assert summary["today"]["total_kwh"] > 0
        assert summary["today"]["source_a_kwh"] > 0
        assert summary["today"]["source_b_kwh"] == 0

        store.close()
        os.unlink(path)
