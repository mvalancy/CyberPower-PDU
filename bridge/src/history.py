# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""SQLite history storage with 1Hz sample recording and weekly reports."""

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
                device_id TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS outlet_samples (
                ts INTEGER NOT NULL,
                outlet INTEGER NOT NULL,
                state TEXT,
                current REAL,
                power REAL,
                energy REAL,
                device_id TEXT NOT NULL DEFAULT ''
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

        try:
            for idx, bank in data.banks.items():
                self._conn.execute(
                    "INSERT INTO bank_samples "
                    "(ts, bank, voltage, current, power, apparent, pf, device_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (now, idx, bank.voltage, bank.current,
                     bank.power, bank.apparent_power, bank.power_factor,
                     device_id),
                )

            for n, outlet in data.outlets.items():
                self._conn.execute(
                    "INSERT INTO outlet_samples "
                    "(ts, outlet, state, current, power, energy, device_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (now, n, outlet.state, outlet.current,
                     outlet.power, outlet.energy, device_id),
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

    # --- Reports ---

    def generate_weekly_report(self, device_id: str = "") -> dict | None:
        """Generate report for the most recent complete Mon-Sun week, if missing."""
        now = datetime.now()
        # Find last Monday
        days_since_monday = now.weekday()
        if days_since_monday == 0 and now.hour < 1:
            # It's Monday early AM, report for week before last
            last_monday = now - timedelta(days=7 + days_since_monday)
        else:
            last_monday = now - timedelta(days=days_since_monday)
        # Go back one more week for the *complete* week
        week_end = last_monday.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = week_end - timedelta(days=7)

        week_start_str = week_start.strftime("%Y-%m-%d")
        week_end_str = week_end.strftime("%Y-%m-%d")

        # Check if already generated (per device_id)
        existing = self._conn.execute(
            "SELECT id FROM energy_reports "
            "WHERE week_start = ? AND device_id = ?",
            (week_start_str, device_id),
        ).fetchone()
        if existing:
            return None

        start_ts = week_start.timestamp()
        end_ts = week_end.timestamp()

        # Query bank data for total power (filtered by device_id)
        bank_rows = self._conn.execute(
            "SELECT ts, bank, power, voltage, current FROM bank_samples "
            "WHERE ts >= ? AND ts < ? AND device_id = ? ORDER BY ts",
            (int(start_ts), int(end_ts), device_id),
        ).fetchall()

        # Query outlet data (filtered by device_id)
        outlet_rows = self._conn.execute(
            "SELECT ts, outlet, power, energy, state FROM outlet_samples "
            "WHERE ts >= ? AND ts < ? AND device_id = ? ORDER BY ts",
            (int(start_ts), int(end_ts), device_id),
        ).fetchall()

        if not bank_rows and not outlet_rows:
            return None  # No data for this week

        # Compute total kWh from 1Hz power samples
        # Each sample covers 1 second = 1/3600 hour
        total_power_samples = {}
        for r in bank_rows:
            ts = r["ts"]
            total_power_samples.setdefault(ts, 0)
            if r["power"] is not None:
                total_power_samples[ts] += r["power"]

        total_kwh = sum(total_power_samples.values()) / 3600.0 / 1000.0

        # Peak and average power
        power_vals = [v for v in total_power_samples.values() if v > 0]
        peak_power = max(power_vals) if power_vals else 0
        avg_power = sum(power_vals) / len(power_vals) if power_vals else 0

        # Per-outlet breakdown
        outlet_energy: dict[int, dict] = {}
        for r in outlet_rows:
            o = r["outlet"]
            if o not in outlet_energy:
                outlet_energy[o] = {"powers": [], "first_energy": None, "last_energy": None}
            if r["power"] is not None:
                outlet_energy[o]["powers"].append(r["power"])
            if r["energy"] is not None:
                if outlet_energy[o]["first_energy"] is None:
                    outlet_energy[o]["first_energy"] = r["energy"]
                outlet_energy[o]["last_energy"] = r["energy"]

        per_outlet = {}
        for o, info in outlet_energy.items():
            # Estimate kWh from 1Hz power samples
            kwh = sum(info["powers"]) / 3600.0 / 1000.0 if info["powers"] else 0
            per_outlet[str(o)] = {
                "kwh": round(kwh, 3),
                "avg_power": round(sum(info["powers"]) / len(info["powers"]), 1) if info["powers"] else 0,
                "peak_power": round(max(info["powers"]), 1) if info["powers"] else 0,
            }

        # Daily breakdown
        daily = {}
        for ts, power in total_power_samples.items():
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            daily.setdefault(day, []).append(power)
        daily_breakdown = {}
        for day, powers in sorted(daily.items()):
            daily_breakdown[day] = {
                "kwh": round(sum(powers) / 3600.0 / 1000.0, 3),
                "avg_power": round(sum(powers) / len(powers), 1),
                "peak_power": round(max(powers), 1),
            }

        # House comparison
        house_pct = None
        if self._house_monthly_kwh > 0:
            weekly_house = self._house_monthly_kwh * 7 / 30
            house_pct = round(total_kwh / weekly_house * 100, 1) if weekly_house > 0 else None

        report_data = {
            "week_start": week_start_str,
            "week_end": week_end_str,
            "device_id": device_id,
            "total_kwh": round(total_kwh, 3),
            "peak_power_w": round(peak_power, 1),
            "avg_power_w": round(avg_power, 1),
            "per_outlet": per_outlet,
            "daily": daily_breakdown,
            "house_pct": house_pct,
            "sample_count": len(total_power_samples),
        }

        self._conn.execute(
            "INSERT INTO energy_reports "
            "(week_start, week_end, created_at, data, device_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (week_start_str, week_end_str,
             datetime.now().isoformat(), json.dumps(report_data),
             device_id),
        )
        self._conn.commit()
        logger.info("Generated weekly report for %s to %s (device=%s): %.1f kWh",
                     week_start_str, week_end_str, device_id or "(default)", total_kwh)
        return report_data

    def list_reports(self, device_id: str | None = None) -> list[dict]:
        if device_id is not None:
            rows = self._conn.execute(
                "SELECT id, week_start, week_end, created_at, device_id "
                "FROM energy_reports WHERE device_id = ? "
                "ORDER BY week_start DESC",
                (device_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, week_start, week_end, created_at, device_id "
                "FROM energy_reports ORDER BY week_start DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    def get_report(self, report_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM energy_reports WHERE id = ?", (report_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["data"] = json.loads(result["data"])
        except (json.JSONDecodeError, TypeError):
            logger.error("Corrupt report data for id=%d", report_id)
            result["data"] = {}
        return result

    def get_latest_report(self) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM energy_reports ORDER BY week_start DESC LIMIT 1",
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["data"] = json.loads(result["data"])
        except (json.JSONDecodeError, TypeError):
            logger.error("Corrupt report data for latest report")
            result["data"] = {}
        return result

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
