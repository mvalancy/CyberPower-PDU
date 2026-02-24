# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""SQLite history storage with 1Hz sample recording and energy rollups."""

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .pdu_model import PDUData

logger = logging.getLogger(__name__)


class HistoryStore:
    def __init__(self, db_path: str, retention_days: int = 60,
                 house_monthly_kwh: float = 0):
        self._db_path = db_path
        self._retention_days = retention_days
        self._house_monthly_kwh = house_monthly_kwh
        self._write_count = 0
        self._write_errors = 0
        self._consecutive_write_errors = 0
        self._total_writes = 0

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()
        self._migrate_device_id()
        self._migrate_active_source()
        self._create_indexes()

    @property
    def retention_days(self) -> int:
        return self._retention_days

    @retention_days.setter
    def retention_days(self, value: int):
        self._retention_days = max(1, min(365, value))

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS bank_samples (
                ts INTEGER NOT NULL,
                bank INTEGER NOT NULL,
                voltage REAL,
                current REAL,
                power REAL,
                apparent REAL,
                pf REAL,
                device_id TEXT NOT NULL DEFAULT '',
                active_source INTEGER
            );

            CREATE TABLE IF NOT EXISTS outlet_samples (
                ts INTEGER NOT NULL,
                outlet INTEGER NOT NULL,
                state TEXT,
                current REAL,
                power REAL,
                energy REAL,
                device_id TEXT NOT NULL DEFAULT '',
                active_source INTEGER
            );

            CREATE TABLE IF NOT EXISTS energy_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                created_at TEXT NOT NULL,
                data TEXT NOT NULL,
                device_id TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS environment_samples (
                ts INTEGER NOT NULL,
                temperature REAL,
                humidity REAL,
                contact_1 INTEGER,
                contact_2 INTEGER,
                contact_3 INTEGER,
                contact_4 INTEGER,
                device_id TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS energy_daily (
                date TEXT NOT NULL,
                device_id TEXT NOT NULL DEFAULT '',
                source INTEGER,
                outlet INTEGER,
                kwh REAL NOT NULL,
                peak_power_w REAL,
                avg_power_w REAL,
                samples INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS energy_monthly (
                month TEXT NOT NULL,
                device_id TEXT NOT NULL DEFAULT '',
                source INTEGER,
                outlet INTEGER,
                kwh REAL NOT NULL,
                peak_power_w REAL,
                avg_power_w REAL,
                days INTEGER NOT NULL
            );
        """)
        self._conn.commit()

    def _create_indexes(self):
        """Create all indexes — runs after migration so device_id always exists."""
        self._conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_bank_ts ON bank_samples(ts);
            CREATE INDEX IF NOT EXISTS idx_outlet_ts ON outlet_samples(ts);
            CREATE INDEX IF NOT EXISTS idx_env_ts ON environment_samples(ts);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_report_week
                ON energy_reports(week_start, device_id);
            CREATE INDEX IF NOT EXISTS idx_energy_daily_lookup
                ON energy_daily(date, device_id);
            CREATE INDEX IF NOT EXISTS idx_energy_monthly_lookup
                ON energy_monthly(month, device_id);
        """)
        self._conn.commit()

    def _migrate_device_id(self):
        """Idempotent migration: add device_id column to existing tables."""
        migrations = [
            "ALTER TABLE bank_samples ADD COLUMN device_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE outlet_samples ADD COLUMN device_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE energy_reports ADD COLUMN device_id TEXT NOT NULL DEFAULT ''",
        ]
        for sql in migrations:
            try:
                self._conn.execute(sql)
                self._conn.commit()
            except sqlite3.OperationalError:
                # Column already exists — expected on subsequent startups
                pass

        # Create device_id indexes (always idempotent via IF NOT EXISTS)
        self._conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_bank_device
                ON bank_samples(device_id, ts);
            CREATE INDEX IF NOT EXISTS idx_outlet_device
                ON outlet_samples(device_id, ts);
            CREATE INDEX IF NOT EXISTS idx_report_device
                ON energy_reports(device_id);
        """)
        self._conn.commit()

        # Rebuild unique index on energy_reports to include device_id.
        # The original idx_report_week was ON (week_start) only; the new
        # CREATE TABLE already defines it as (week_start, device_id).
        # For migrated databases we drop-and-recreate so uniqueness covers
        # both columns, allowing one report per week per device.
        try:
            self._conn.execute("DROP INDEX IF EXISTS idx_report_week")
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_report_week "
                "ON energy_reports(week_start, device_id)"
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

    def _migrate_active_source(self):
        """Idempotent migration: add active_source column to sample tables."""
        for table in ("bank_samples", "outlet_samples"):
            try:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN active_source INTEGER"
                )
                self._conn.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists

    def get_health(self) -> dict:
        """Return history storage health metrics."""
        return {
            "db_path": self._db_path,
            "total_writes": self._total_writes,
            "write_errors": self._write_errors,
            "retention_days": self._retention_days,
            "healthy": self._write_errors == 0 or (
                self._total_writes > 0 and
                self._write_errors / self._total_writes < 0.1
            ),
        }

    def record(self, data: PDUData, device_id: str = ""):
        """Write every poll sample directly to SQLite at 1Hz."""
        self._total_writes += 1
        now = int(time.time())
        source = data.ats_current_source  # 1=A, 2=B, or None

        try:
            for idx, bank in data.banks.items():
                self._conn.execute(
                    "INSERT INTO bank_samples "
                    "(ts, bank, voltage, current, power, apparent, pf, device_id, active_source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now, idx, bank.voltage, bank.current,
                     bank.power, bank.apparent_power, bank.power_factor,
                     device_id, source),
                )

            for n, outlet in data.outlets.items():
                self._conn.execute(
                    "INSERT INTO outlet_samples "
                    "(ts, outlet, state, current, power, energy, device_id, active_source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (now, n, outlet.state, outlet.current,
                     outlet.power, outlet.energy, device_id, source),
                )

            # Environment samples (only when sensor present)
            if data.environment and data.environment.sensor_present:
                env = data.environment
                contacts = env.contacts or {}
                self._conn.execute(
                    "INSERT INTO environment_samples "
                    "(ts, temperature, humidity, contact_1, contact_2, "
                    "contact_3, contact_4, device_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (now, env.temperature, env.humidity,
                     int(contacts.get(1, False)),
                     int(contacts.get(2, False)),
                     int(contacts.get(3, False)),
                     int(contacts.get(4, False)),
                     device_id),
                )

            # Commit every 10 writes (~10 seconds) to batch disk I/O
            self._write_count += 1
            if self._write_count >= 10:
                self._conn.commit()
                self._write_count = 0

            self._consecutive_write_errors = 0

        except sqlite3.Error:
            self._write_errors += 1
            self._consecutive_write_errors += 1
            if self._write_errors <= 3 or self._write_errors % 60 == 0:
                logger.exception("History write failed (error %d)", self._write_errors)
            try:
                self._conn.rollback()
            except Exception:
                pass

            # Reconnect SQLite after 10 consecutive failures (file lock recovery)
            if self._consecutive_write_errors >= 10:
                logger.warning(
                    "History: %d consecutive write errors, reopening database",
                    self._consecutive_write_errors,
                )
                self._reopen_connection()

    def _reopen_connection(self):
        """Close and reopen the SQLite connection to recover from lock errors."""
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._consecutive_write_errors = 0
            logger.info("History: database connection reopened successfully")
        except Exception:
            logger.exception("History: failed to reopen database connection")

    @staticmethod
    def _average_samples(samples: list[dict], fields: list[str]) -> dict:
        result = {}
        for f in fields:
            vals = [s[f] for s in samples if s.get(f) is not None]
            result[f] = round(sum(vals) / len(vals), 3) if vals else None
        return result

    def _pick_interval(self, start: float, end: float) -> int:
        """Auto-select downsampling interval in seconds."""
        span = end - start
        if span <= 3600:
            return 1         # raw 1s for <=1h
        elif span <= 6 * 3600:
            return 10        # 10s for <=6h
        elif span <= 24 * 3600:
            return 60        # 1m for <=24h
        elif span <= 7 * 86400:
            return 300       # 5m for <=7d
        elif span <= 30 * 86400:
            return 900       # 15m for <=30d
        else:
            return 1800      # 30m for 60d

    def query_banks(self, start: float, end: float,
                    interval: int | None = None,
                    device_id: str | None = None) -> list[dict]:
        if interval is None:
            interval = self._pick_interval(start, end)
        interval = max(interval, 1)  # prevent division by zero

        sql = (
            "SELECT (ts / ?) * ? AS bucket, bank, "
            "AVG(voltage) AS voltage, AVG(current) AS current, "
            "AVG(power) AS power, AVG(apparent) AS apparent, AVG(pf) AS pf "
            "FROM bank_samples WHERE ts >= ? AND ts <= ? "
        )
        params: list[Any] = [interval, interval, int(start), int(end)]

        if device_id is not None:
            sql += "AND device_id = ? "
            params.append(device_id)

        sql += "GROUP BY bucket, bank ORDER BY bucket"

        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_outlets(self, start: float, end: float,
                      interval: int | None = None,
                      device_id: str | None = None) -> list[dict]:
        if interval is None:
            interval = self._pick_interval(start, end)
        interval = max(interval, 1)  # prevent division by zero

        sql = (
            "SELECT (ts / ?) * ? AS bucket, outlet, "
            "AVG(current) AS current, AVG(power) AS power, "
            "MAX(energy) AS energy "
            "FROM outlet_samples WHERE ts >= ? AND ts <= ? "
        )
        params: list[Any] = [interval, interval, int(start), int(end)]

        if device_id is not None:
            sql += "AND device_id = ? "
            params.append(device_id)

        sql += "GROUP BY bucket, outlet ORDER BY bucket"

        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def cleanup(self):
        """Delete rows older than retention period."""
        cutoff = int(time.time()) - self._retention_days * 86400
        try:
            c1 = self._conn.execute("DELETE FROM bank_samples WHERE ts < ?", (cutoff,))
            c2 = self._conn.execute("DELETE FROM outlet_samples WHERE ts < ?", (cutoff,))
            c3 = self._conn.execute("DELETE FROM environment_samples WHERE ts < ?", (cutoff,))
            self._conn.commit()
            total = (c1.rowcount or 0) + (c2.rowcount or 0) + (c3.rowcount or 0)
            if total > 0:
                logger.info("History cleanup: removed %d rows older than %d days",
                            total, self._retention_days)
        except sqlite3.Error:
            logger.exception("History cleanup failed")

    # --- Energy Rollups ---

    def compute_daily_rollups(self, device_id: str = ""):
        """Compute daily energy rollups from raw 1Hz samples for any missing days."""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # Check if yesterday already computed
        existing = self._conn.execute(
            "SELECT 1 FROM energy_daily WHERE date = ? AND device_id = ? LIMIT 1",
            (yesterday, device_id),
        ).fetchone()
        if existing:
            return

        # Timestamp range for yesterday
        day_start = datetime.strptime(yesterday, "%Y-%m-%d")
        start_ts = int(day_start.timestamp())
        end_ts = start_ts + 86400

        # --- Bank-level rollup (total power across all banks, by source) ---
        bank_rows = self._conn.execute(
            "SELECT ts, active_source, SUM(power) as total_power "
            "FROM bank_samples "
            "WHERE ts >= ? AND ts < ? AND device_id = ? AND power IS NOT NULL "
            "GROUP BY ts, active_source",
            (start_ts, end_ts, device_id),
        ).fetchall()

        if not bank_rows:
            return  # No data for yesterday

        # Group by source
        source_powers: dict[int | None, list[float]] = {}
        all_powers: list[float] = []
        for row in bank_rows:
            p = row["total_power"]
            src = row["active_source"]
            source_powers.setdefault(src, []).append(p)
            all_powers.append(p)

        # Insert total row (source=NULL, outlet=NULL)
        if all_powers:
            self._conn.execute(
                "INSERT INTO energy_daily (date, device_id, source, outlet, kwh, peak_power_w, avg_power_w, samples) "
                "VALUES (?, ?, NULL, NULL, ?, ?, ?, ?)",
                (yesterday, device_id,
                 round(sum(all_powers) / 3600.0 / 1000.0, 6),
                 round(max(all_powers), 1),
                 round(sum(all_powers) / len(all_powers), 1),
                 len(all_powers)),
            )

        # Insert per-source rows (source=1 or 2, outlet=NULL)
        for src, powers in source_powers.items():
            if src is not None:
                self._conn.execute(
                    "INSERT INTO energy_daily (date, device_id, source, outlet, kwh, peak_power_w, avg_power_w, samples) "
                    "VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
                    (yesterday, device_id, src,
                     round(sum(powers) / 3600.0 / 1000.0, 6),
                     round(max(powers), 1),
                     round(sum(powers) / len(powers), 1),
                     len(powers)),
                )

        # --- Per-outlet rollup (by outlet and source) ---
        outlet_rows = self._conn.execute(
            "SELECT outlet, active_source, "
            "COUNT(*) as cnt, SUM(power) as sum_power, "
            "MAX(power) as max_power, AVG(power) as avg_power "
            "FROM outlet_samples "
            "WHERE ts >= ? AND ts < ? AND device_id = ? AND power IS NOT NULL "
            "GROUP BY outlet, active_source",
            (start_ts, end_ts, device_id),
        ).fetchall()

        # Collect per-outlet totals for the total row
        outlet_totals: dict[int, dict] = {}
        for row in outlet_rows:
            o = row["outlet"]
            if o not in outlet_totals:
                outlet_totals[o] = {"sum_power": 0, "max_power": 0, "cnt": 0, "powers_for_avg": []}
            outlet_totals[o]["sum_power"] += row["sum_power"]
            outlet_totals[o]["max_power"] = max(outlet_totals[o]["max_power"], row["max_power"] or 0)
            outlet_totals[o]["cnt"] += row["cnt"]
            outlet_totals[o]["powers_for_avg"].append((row["avg_power"] or 0, row["cnt"]))

            # Per-outlet per-source row
            src = row["active_source"]
            if src is not None:
                self._conn.execute(
                    "INSERT INTO energy_daily (date, device_id, source, outlet, kwh, peak_power_w, avg_power_w, samples) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (yesterday, device_id, src, o,
                     round(row["sum_power"] / 3600.0 / 1000.0, 6),
                     round(row["max_power"] or 0, 1),
                     round(row["avg_power"] or 0, 1),
                     row["cnt"]),
                )

        # Per-outlet total rows (source=NULL)
        for o, totals in outlet_totals.items():
            weighted_avg = sum(a * c for a, c in totals["powers_for_avg"]) / totals["cnt"] if totals["cnt"] else 0
            self._conn.execute(
                "INSERT INTO energy_daily (date, device_id, source, outlet, kwh, peak_power_w, avg_power_w, samples) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
                (yesterday, device_id, o,
                 round(totals["sum_power"] / 3600.0 / 1000.0, 6),
                 round(totals["max_power"], 1),
                 round(weighted_avg, 1),
                 totals["cnt"]),
            )

        self._conn.commit()
        total_kwh = sum(all_powers) / 3600.0 / 1000.0 if all_powers else 0
        logger.info(
            "Computed daily rollup for %s (device=%s): %.3f kWh, %d samples",
            yesterday, device_id or "(default)", total_kwh, len(all_powers),
        )

    def compute_monthly_rollups(self, device_id: str = ""):
        """Recompute current and previous month from energy_daily."""
        now = datetime.now()
        current_month = now.strftime("%Y-%m")
        prev_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

        for month in (current_month, prev_month):
            # Delete existing rows for this month to recompute
            self._conn.execute(
                "DELETE FROM energy_monthly WHERE month = ? AND device_id = ?",
                (month, device_id),
            )

            # Aggregate from energy_daily
            rows = self._conn.execute(
                "SELECT source, outlet, "
                "SUM(kwh) as total_kwh, MAX(peak_power_w) as peak_power, "
                "AVG(avg_power_w) as avg_power, COUNT(*) as day_count "
                "FROM energy_daily "
                "WHERE date LIKE ? AND device_id = ? "
                "GROUP BY source, outlet",
                (month + "%", device_id),
            ).fetchall()

            for row in rows:
                self._conn.execute(
                    "INSERT INTO energy_monthly (month, device_id, source, outlet, kwh, peak_power_w, avg_power_w, days) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (month, device_id, row["source"], row["outlet"],
                     round(row["total_kwh"], 6),
                     round(row["peak_power"] or 0, 1),
                     round(row["avg_power"] or 0, 1),
                     row["day_count"]),
                )

        self._conn.commit()

    # --- Energy Query Methods ---

    def query_energy_daily(self, start_date: str, end_date: str,
                           device_id: str = "",
                           source: int | None = None,
                           outlet: int | None = None) -> list[dict]:
        """Query daily energy rollups for a date range."""
        sql = "SELECT * FROM energy_daily WHERE date >= ? AND date <= ? AND device_id = ?"
        params: list[Any] = [start_date, end_date, device_id]

        if source is not None:
            sql += " AND source = ?"
            params.append(source)
        else:
            sql += " AND source IS NULL"

        if outlet is not None:
            sql += " AND outlet = ?"
            params.append(outlet)
        else:
            sql += " AND outlet IS NULL"

        sql += " ORDER BY date"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_energy_daily_all(self, start_date: str, end_date: str,
                               device_id: str = "") -> list[dict]:
        """Query all daily energy rollup rows (all sources/outlets) for a date range."""
        sql = (
            "SELECT * FROM energy_daily "
            "WHERE date >= ? AND date <= ? AND device_id = ? "
            "ORDER BY date, source, outlet"
        )
        rows = self._conn.execute(sql, (start_date, end_date, device_id)).fetchall()
        return [dict(r) for r in rows]

    def query_energy_monthly(self, start_month: str, end_month: str,
                             device_id: str = "",
                             source: int | None = None,
                             outlet: int | None = None) -> list[dict]:
        """Query monthly energy rollups for a month range."""
        sql = "SELECT * FROM energy_monthly WHERE month >= ? AND month <= ? AND device_id = ?"
        params: list[Any] = [start_month, end_month, device_id]

        if source is not None:
            sql += " AND source = ?"
            params.append(source)
        else:
            sql += " AND source IS NULL"

        if outlet is not None:
            sql += " AND outlet = ?"
            params.append(outlet)
        else:
            sql += " AND outlet IS NULL"

        sql += " ORDER BY month"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_energy_monthly_all(self, start_month: str, end_month: str,
                                 device_id: str = "") -> list[dict]:
        """Query all monthly energy rollup rows for a month range."""
        sql = (
            "SELECT * FROM energy_monthly "
            "WHERE month >= ? AND month <= ? AND device_id = ? "
            "ORDER BY month, source, outlet"
        )
        rows = self._conn.execute(sql, (start_month, end_month, device_id)).fetchall()
        return [dict(r) for r in rows]

    def get_energy_summary(self, device_id: str = "") -> dict:
        """Get energy summary: today, this week, this month, all time."""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        # Start of week (Monday)
        week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        current_month = now.strftime("%Y-%m")

        def _sum_kwh(rows, source_filter=None):
            total = 0.0
            for r in rows:
                if source_filter is not None and r["source"] != source_filter:
                    continue
                if source_filter is None and r["source"] is not None:
                    continue  # Only use total rows
                total += r["kwh"]
            return round(total, 3)

        def _sum_kwh_by_source(rows, source_val):
            total = 0.0
            for r in rows:
                if r["source"] == source_val and r["outlet"] is None:
                    total += r["kwh"]
            return round(total, 3)

        # Today: compute from live 1Hz data
        today_start_ts = int(datetime.strptime(today, "%Y-%m-%d").timestamp())
        today_end_ts = today_start_ts + 86400

        today_banks = self._conn.execute(
            "SELECT active_source, SUM(power) as total_power "
            "FROM bank_samples "
            "WHERE ts >= ? AND ts < ? AND device_id = ? AND power IS NOT NULL "
            "GROUP BY ts, active_source",
            (today_start_ts, today_end_ts, device_id),
        ).fetchall()

        today_total = sum(r["total_power"] for r in today_banks) / 3600.0 / 1000.0 if today_banks else 0
        today_a = sum(r["total_power"] for r in today_banks if r["active_source"] == 1) / 3600.0 / 1000.0 if today_banks else 0
        today_b = sum(r["total_power"] for r in today_banks if r["active_source"] == 2) / 3600.0 / 1000.0 if today_banks else 0

        # This week: from energy_daily
        week_daily = self.query_energy_daily_all(week_start, today, device_id)
        week_total = _sum_kwh(week_daily)
        week_a = _sum_kwh_by_source(week_daily, 1)
        week_b = _sum_kwh_by_source(week_daily, 2)
        # Add today's live data
        week_total = round(week_total + today_total, 3)
        week_a = round(week_a + today_a, 3)
        week_b = round(week_b + today_b, 3)

        # This month: from energy_daily (not monthly, since month is incomplete)
        month_start = now.replace(day=1).strftime("%Y-%m-%d")
        month_daily = self.query_energy_daily_all(month_start, today, device_id)
        month_total = _sum_kwh(month_daily)
        month_a = _sum_kwh_by_source(month_daily, 1)
        month_b = _sum_kwh_by_source(month_daily, 2)
        month_total = round(month_total + today_total, 3)
        month_a = round(month_a + today_a, 3)
        month_b = round(month_b + today_b, 3)

        # All time: from energy_monthly + current incomplete month
        all_monthly = self._conn.execute(
            "SELECT source, outlet, SUM(kwh) as total "
            "FROM energy_monthly WHERE device_id = ? AND month < ? "
            "GROUP BY source, outlet",
            (device_id, current_month),
        ).fetchall()
        all_total = sum(r["total"] for r in all_monthly if r["source"] is None and r["outlet"] is None)
        all_a = sum(r["total"] for r in all_monthly if r["source"] == 1 and r["outlet"] is None)
        all_b = sum(r["total"] for r in all_monthly if r["source"] == 2 and r["outlet"] is None)
        # Add current month
        all_total = round(all_total + month_total, 3)
        all_a = round(all_a + month_a, 3)
        all_b = round(all_b + month_b, 3)

        return {
            "today": {"total_kwh": round(today_total, 3), "source_a_kwh": round(today_a, 3), "source_b_kwh": round(today_b, 3)},
            "this_week": {"total_kwh": week_total, "source_a_kwh": week_a, "source_b_kwh": week_b},
            "this_month": {"total_kwh": month_total, "source_a_kwh": month_a, "source_b_kwh": month_b},
            "all_time": {"total_kwh": all_total, "source_a_kwh": all_a, "source_b_kwh": all_b},
        }

    def close(self):
        try:
            self._conn.commit()
        except Exception:
            logger.debug("Error committing on close", exc_info=True)
        finally:
            try:
                self._conn.close()
            except Exception:
                logger.debug("Error closing database", exc_info=True)
