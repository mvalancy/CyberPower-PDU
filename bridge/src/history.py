"""SQLite history storage with 1-minute aggregation and weekly reports."""

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
    def __init__(self, db_path: str, retention_days: int = 90,
                 house_monthly_kwh: float = 0):
        self._db_path = db_path
        self._retention_days = retention_days
        self._house_monthly_kwh = house_monthly_kwh

        # In-memory accumulators keyed by minute timestamp
        self._current_minute: int = 0
        self._bank_accum: dict[int, list[dict]] = {}  # bank_num -> [samples]
        self._outlet_accum: dict[int, list[dict]] = {}  # outlet_num -> [samples]

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS bank_samples (
                ts INTEGER NOT NULL,
                bank INTEGER NOT NULL,
                voltage REAL,
                current REAL,
                power REAL,
                apparent REAL,
                pf REAL
            );
            CREATE INDEX IF NOT EXISTS idx_bank_ts ON bank_samples(ts);

            CREATE TABLE IF NOT EXISTS outlet_samples (
                ts INTEGER NOT NULL,
                outlet INTEGER NOT NULL,
                state TEXT,
                current REAL,
                power REAL,
                energy REAL
            );
            CREATE INDEX IF NOT EXISTS idx_outlet_ts ON outlet_samples(ts);

            CREATE TABLE IF NOT EXISTS energy_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                created_at TEXT NOT NULL,
                data TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_report_week
                ON energy_reports(week_start);
        """)
        self._conn.commit()

    def record(self, data: PDUData):
        """Buffer a poll sample. Flushes to SQLite on minute rollover."""
        now = int(time.time())
        minute = now // 60

        if self._current_minute == 0:
            self._current_minute = minute

        if minute != self._current_minute:
            self._flush()
            self._current_minute = minute
            self._bank_accum.clear()
            self._outlet_accum.clear()

        # Accumulate bank data
        for idx, bank in data.banks.items():
            self._bank_accum.setdefault(idx, []).append({
                "voltage": bank.voltage,
                "current": bank.current,
                "power": bank.power,
                "apparent": bank.apparent_power,
                "pf": bank.power_factor,
            })

        # Accumulate outlet data
        for n, outlet in data.outlets.items():
            self._outlet_accum.setdefault(n, []).append({
                "state": outlet.state,
                "current": outlet.current,
                "power": outlet.power,
                "energy": outlet.energy,
            })

    def _flush(self):
        """Write averaged minute data to SQLite."""
        ts = self._current_minute * 60

        for bank_num, samples in self._bank_accum.items():
            if not samples:
                continue
            row = self._average_samples(samples,
                                        ["voltage", "current", "power", "apparent", "pf"])
            self._conn.execute(
                "INSERT INTO bank_samples (ts, bank, voltage, current, power, apparent, pf) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, bank_num, row["voltage"], row["current"],
                 row["power"], row["apparent"], row["pf"]),
            )

        for outlet_num, samples in self._outlet_accum.items():
            if not samples:
                continue
            row = self._average_samples(samples, ["current", "power"])
            # state = last known, energy = last reading (counter)
            last = samples[-1]
            self._conn.execute(
                "INSERT INTO outlet_samples (ts, outlet, state, current, power, energy) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, outlet_num, last["state"], row["current"],
                 row["power"], last["energy"]),
            )

        self._conn.commit()

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
        if span <= 6 * 3600:
            return 60        # 1m for <6h
        elif span <= 24 * 3600:
            return 300       # 5m for <24h
        elif span <= 7 * 86400:
            return 900       # 15m for <7d
        else:
            return 3600      # 1h for 30d+

    def query_banks(self, start: float, end: float,
                    interval: int | None = None) -> list[dict]:
        if interval is None:
            interval = self._pick_interval(start, end)

        rows = self._conn.execute(
            "SELECT (ts / ?) * ? AS bucket, bank, "
            "AVG(voltage) AS voltage, AVG(current) AS current, "
            "AVG(power) AS power, AVG(apparent) AS apparent, AVG(pf) AS pf "
            "FROM bank_samples WHERE ts >= ? AND ts <= ? "
            "GROUP BY bucket, bank ORDER BY bucket",
            (interval, interval, int(start), int(end)),
        ).fetchall()

        return [dict(r) for r in rows]

    def query_outlets(self, start: float, end: float,
                      interval: int | None = None) -> list[dict]:
        if interval is None:
            interval = self._pick_interval(start, end)

        rows = self._conn.execute(
            "SELECT (ts / ?) * ? AS bucket, outlet, "
            "AVG(current) AS current, AVG(power) AS power, "
            "MAX(energy) AS energy "
            "FROM outlet_samples WHERE ts >= ? AND ts <= ? "
            "GROUP BY bucket, outlet ORDER BY bucket",
            (interval, interval, int(start), int(end)),
        ).fetchall()

        return [dict(r) for r in rows]

    def cleanup(self):
        """Delete rows older than retention period."""
        cutoff = int(time.time()) - self._retention_days * 86400
        self._conn.execute("DELETE FROM bank_samples WHERE ts < ?", (cutoff,))
        self._conn.execute("DELETE FROM outlet_samples WHERE ts < ?", (cutoff,))
        self._conn.commit()
        logger.info("History cleanup: removed data older than %d days", self._retention_days)

    # --- Reports ---

    def generate_weekly_report(self) -> dict | None:
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

        # Check if already generated
        existing = self._conn.execute(
            "SELECT id FROM energy_reports WHERE week_start = ?",
            (week_start_str,),
        ).fetchone()
        if existing:
            return None

        start_ts = week_start.timestamp()
        end_ts = week_end.timestamp()

        # Query bank data for total power
        bank_rows = self._conn.execute(
            "SELECT ts, bank, power, voltage, current FROM bank_samples "
            "WHERE ts >= ? AND ts < ? ORDER BY ts",
            (int(start_ts), int(end_ts)),
        ).fetchall()

        # Query outlet data
        outlet_rows = self._conn.execute(
            "SELECT ts, outlet, power, energy, state FROM outlet_samples "
            "WHERE ts >= ? AND ts < ? ORDER BY ts",
            (int(start_ts), int(end_ts)),
        ).fetchall()

        if not bank_rows and not outlet_rows:
            return None  # No data for this week

        # Compute total kWh from per-minute power readings
        # Each sample covers 1 minute = 1/60 hour
        total_power_samples = {}
        for r in bank_rows:
            ts = r["ts"]
            total_power_samples.setdefault(ts, 0)
            if r["power"] is not None:
                total_power_samples[ts] += r["power"]

        total_kwh = sum(total_power_samples.values()) / 60.0 / 1000.0

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
            # Estimate kWh from power samples
            kwh = sum(info["powers"]) / 60.0 / 1000.0 if info["powers"] else 0
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
                "kwh": round(sum(powers) / 60.0 / 1000.0, 3),
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
            "total_kwh": round(total_kwh, 3),
            "peak_power_w": round(peak_power, 1),
            "avg_power_w": round(avg_power, 1),
            "per_outlet": per_outlet,
            "daily": daily_breakdown,
            "house_pct": house_pct,
            "sample_count": len(total_power_samples),
        }

        self._conn.execute(
            "INSERT INTO energy_reports (week_start, week_end, created_at, data) "
            "VALUES (?, ?, ?, ?)",
            (week_start_str, week_end_str,
             datetime.now().isoformat(), json.dumps(report_data)),
        )
        self._conn.commit()
        logger.info("Generated weekly report for %s to %s: %.1f kWh",
                     week_start_str, week_end_str, total_kwh)
        return report_data

    def list_reports(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, week_start, week_end, created_at FROM energy_reports "
            "ORDER BY week_start DESC",
        ).fetchall()
        return [dict(r) for r in rows]

    def get_report(self, report_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM energy_reports WHERE id = ?", (report_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["data"] = json.loads(result["data"])
        return result

    def get_latest_report(self) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM energy_reports ORDER BY week_start DESC LIMIT 1",
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["data"] = json.loads(result["data"])
        return result

    def close(self):
        # Flush any remaining data
        if self._bank_accum or self._outlet_accum:
            self._flush()
        self._conn.close()
