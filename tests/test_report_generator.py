# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Tests for PDF energy report generator."""

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bridge.src.report_generator import (
    COLOR_CYAN,
    COLOR_GREEN,
    CyberPDFReport,
    _parse_filename,
    _pct_change,
    _per_day_kwh,
    _per_day_source_kwh,
    _per_outlet_kwh,
    _per_outlet_source_kwh,
    _sum_source_kwh,
    _sum_total_kwh,
    generate_monthly_report,
    generate_weekly_report,
    get_report_path,
    list_reports,
)


# ---------------------------------------------------------------------------
# Helper: mock HistoryStore
# ---------------------------------------------------------------------------

def _make_daily_rows(start_date: str, days: int, base_kwh: float = 1.0):
    """Create fake energy_daily rows with total + source + outlet breakdown."""
    rows = []
    dt = datetime.strptime(start_date, "%Y-%m-%d")
    for d in range(days):
        date = (dt + timedelta(days=d)).strftime("%Y-%m-%d")
        # Total row (source=None, outlet=None)
        rows.append({
            "date": date, "device_id": "pdu-1", "source": None, "outlet": None,
            "kwh": base_kwh, "peak_power_w": 200.0, "avg_power_w": 120.0, "samples": 86400,
        })
        # Source A
        rows.append({
            "date": date, "device_id": "pdu-1", "source": 1, "outlet": None,
            "kwh": base_kwh * 0.6, "peak_power_w": 150.0, "avg_power_w": 80.0, "samples": 50000,
        })
        # Source B
        rows.append({
            "date": date, "device_id": "pdu-1", "source": 2, "outlet": None,
            "kwh": base_kwh * 0.4, "peak_power_w": 100.0, "avg_power_w": 50.0, "samples": 36000,
        })
        # Outlet 1 total
        rows.append({
            "date": date, "device_id": "pdu-1", "source": None, "outlet": 1,
            "kwh": base_kwh * 0.3, "peak_power_w": 80.0, "avg_power_w": 40.0, "samples": 86400,
        })
        # Outlet 1 source A
        rows.append({
            "date": date, "device_id": "pdu-1", "source": 1, "outlet": 1,
            "kwh": base_kwh * 0.18, "peak_power_w": 50.0, "avg_power_w": 25.0, "samples": 50000,
        })
        # Outlet 1 source B
        rows.append({
            "date": date, "device_id": "pdu-1", "source": 2, "outlet": 1,
            "kwh": base_kwh * 0.12, "peak_power_w": 40.0, "avg_power_w": 20.0, "samples": 36000,
        })
        # Outlet 2 total
        rows.append({
            "date": date, "device_id": "pdu-1", "source": None, "outlet": 2,
            "kwh": base_kwh * 0.7, "peak_power_w": 150.0, "avg_power_w": 80.0, "samples": 86400,
        })
        # Outlet 2 source A
        rows.append({
            "date": date, "device_id": "pdu-1", "source": 1, "outlet": 2,
            "kwh": base_kwh * 0.42, "peak_power_w": 100.0, "avg_power_w": 55.0, "samples": 50000,
        })
        # Outlet 2 source B
        rows.append({
            "date": date, "device_id": "pdu-1", "source": 2, "outlet": 2,
            "kwh": base_kwh * 0.28, "peak_power_w": 60.0, "avg_power_w": 30.0, "samples": 36000,
        })
    return rows


def _mock_history(rows, prev_rows=None):
    """Create a mock HistoryStore that returns specified rows."""
    mock = MagicMock()
    call_count = [0]

    def query_all(start, end, device_id=""):
        call_count[0] += 1
        if call_count[0] == 1:
            return rows
        return prev_rows or []

    mock.query_energy_daily_all = MagicMock(side_effect=query_all)
    return mock


# ---------------------------------------------------------------------------
# Tests: filename parsing
# ---------------------------------------------------------------------------

class TestFilenameParser:
    def test_weekly_filename(self):
        result = _parse_filename("pdu-1_weekly_2026-02-16.pdf")
        assert result is not None
        assert result["device_id"] == "pdu-1"
        assert result["report_type"] == "weekly"
        assert result["period"] == "2026-02-16"
        assert result["filename"] == "pdu-1_weekly_2026-02-16.pdf"

    def test_monthly_filename(self):
        result = _parse_filename("pdu-1_monthly_2026-01.pdf")
        assert result is not None
        assert result["device_id"] == "pdu-1"
        assert result["report_type"] == "monthly"
        assert result["period"] == "2026-01"

    def test_device_id_with_hyphens(self):
        result = _parse_filename("my-pdu-rack-1_weekly_2026-01-06.pdf")
        assert result is not None
        assert result["device_id"] == "my-pdu-rack-1"

    def test_invalid_filename(self):
        assert _parse_filename("random.pdf") is None
        assert _parse_filename("not_a_report.txt") is None
        assert _parse_filename("") is None

    def test_default_device_id(self):
        result = _parse_filename("default_weekly_2026-01-06.pdf")
        assert result is not None
        assert result["device_id"] == "default"


# ---------------------------------------------------------------------------
# Tests: sum helpers
# ---------------------------------------------------------------------------

class TestSumHelpers:
    def test_sum_total_kwh(self):
        rows = _make_daily_rows("2026-02-16", 7)
        assert _sum_total_kwh(rows) == pytest.approx(7.0, abs=0.001)

    def test_sum_total_kwh_empty(self):
        assert _sum_total_kwh([]) == 0

    def test_sum_source_kwh_a(self):
        rows = _make_daily_rows("2026-02-16", 7)
        assert _sum_source_kwh(rows, 1) == pytest.approx(4.2, abs=0.001)

    def test_sum_source_kwh_b(self):
        rows = _make_daily_rows("2026-02-16", 7)
        assert _sum_source_kwh(rows, 2) == pytest.approx(2.8, abs=0.001)

    def test_per_outlet_kwh(self):
        rows = _make_daily_rows("2026-02-16", 7)
        outlets = _per_outlet_kwh(rows)
        assert 1 in outlets
        assert 2 in outlets
        assert outlets[1] == pytest.approx(2.1, abs=0.001)
        assert outlets[2] == pytest.approx(4.9, abs=0.001)

    def test_per_outlet_source_kwh(self):
        rows = _make_daily_rows("2026-02-16", 7)
        by_source = _per_outlet_source_kwh(rows)
        assert 1 in by_source
        assert 2 in by_source
        # Outlet 1: 7 * 0.18 = 1.26 from source A, 7 * 0.12 = 0.84 from source B
        assert by_source[1][1] == pytest.approx(1.26, abs=0.001)
        assert by_source[1][2] == pytest.approx(0.84, abs=0.001)
        # Outlet 2: 7 * 0.42 = 2.94 from source A, 7 * 0.28 = 1.96 from source B
        assert by_source[2][1] == pytest.approx(2.94, abs=0.001)
        assert by_source[2][2] == pytest.approx(1.96, abs=0.001)

    def test_per_outlet_source_kwh_empty(self):
        assert _per_outlet_source_kwh([]) == {}

    def test_per_day_kwh(self):
        rows = _make_daily_rows("2026-02-16", 3)
        days = _per_day_kwh(rows)
        assert len(days) == 3
        assert all(v == pytest.approx(1.0, abs=0.001) for v in days.values())

    def test_per_day_source_kwh(self):
        rows = _make_daily_rows("2026-02-16", 3)
        days = _per_day_source_kwh(rows, 1)
        assert len(days) == 3
        assert all(v == pytest.approx(0.6, abs=0.001) for v in days.values())


class TestPctChange:
    def test_increase(self):
        result = _pct_change(12.0, 10.0)
        assert result == "+20% vs prior"

    def test_decrease(self):
        result = _pct_change(8.0, 10.0)
        assert result == "-20% vs prior"

    def test_zero_previous(self):
        assert _pct_change(5.0, 0) == ""

    def test_no_change(self):
        assert _pct_change(10.0, 10.0) == "+0% vs prior"


# ---------------------------------------------------------------------------
# Tests: path security
# ---------------------------------------------------------------------------

class TestGetReportPath:
    def test_valid_filename(self, tmp_path):
        pdf = tmp_path / "pdu-1_weekly_2026-02-16.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        result = get_report_path("pdu-1_weekly_2026-02-16.pdf", str(tmp_path))
        assert result is not None
        assert result.name == "pdu-1_weekly_2026-02-16.pdf"

    def test_traversal_dots(self, tmp_path):
        assert get_report_path("../etc/passwd", str(tmp_path)) is None

    def test_traversal_slash(self, tmp_path):
        assert get_report_path("foo/bar.pdf", str(tmp_path)) is None

    def test_traversal_backslash(self, tmp_path):
        assert get_report_path("foo\\bar.pdf", str(tmp_path)) is None

    def test_non_pdf(self, tmp_path):
        assert get_report_path("report.txt", str(tmp_path)) is None

    def test_empty(self, tmp_path):
        assert get_report_path("", str(tmp_path)) is None

    def test_nonexistent(self, tmp_path):
        assert get_report_path("nonexistent.pdf", str(tmp_path)) is None


# ---------------------------------------------------------------------------
# Tests: CyberPDFReport rendering
# ---------------------------------------------------------------------------

class TestCyberPDFReport:
    def test_creates_valid_pdf(self, tmp_path):
        pdf = CyberPDFReport(
            title="Test Report",
            device_name="Test PDU",
            model="PDU44001",
            period="2026-02-16 to 2026-02-22",
            device_id="pdu-1",
        )
        pdf.alias_nb_pages()
        pdf.add_page()

        # Section title
        pdf.section_title("Test Section")

        # Summary cards
        y = pdf.get_y()
        pdf.set_xy(pdf.l_margin, y)
        pdf.summary_card("Total", "1.234", "kWh", COLOR_CYAN, "+5% vs prior")
        pdf.summary_card("Peak", "200", "W", COLOR_GREEN)
        pdf.set_y(y + 26)

        # Table
        pdf.data_table(
            ["Date", "kWh", "Peak W"],
            [["2026-02-16", "1.234", "200"], ["2026-02-17", "1.456", "210"]],
            [60, 50, 50],
        )

        # Bar chart
        pdf.bar_chart([("Day 1", 1.234), ("Day 2", 1.456)], COLOR_CYAN)

        out = tmp_path / "test.pdf"
        pdf.output(str(out))
        assert out.exists()
        assert out.stat().st_size > 100
        # Valid PDF header
        content = out.read_bytes()
        assert content[:5] == b"%PDF-"

    def test_empty_bar_chart(self, tmp_path):
        pdf = CyberPDFReport()
        pdf.add_page()
        pdf.bar_chart([], COLOR_CYAN)
        out = tmp_path / "empty.pdf"
        pdf.output(str(out))
        assert out.exists()


# ---------------------------------------------------------------------------
# Tests: weekly report generation
# ---------------------------------------------------------------------------

class TestWeeklyReport:
    def test_no_data_returns_none(self, tmp_path):
        mock = MagicMock()
        mock.query_energy_daily_all = MagicMock(return_value=[])
        result = generate_weekly_report(
            mock, "pdu-1", "Test PDU", "PDU44001",
            week_start="2026-02-16", reports_dir=str(tmp_path),
        )
        assert result is None

    def test_generates_valid_pdf(self, tmp_path):
        rows = _make_daily_rows("2026-02-16", 7)
        prev_rows = _make_daily_rows("2026-02-09", 7, base_kwh=0.8)
        history = _mock_history(rows, prev_rows)

        result = generate_weekly_report(
            history, "pdu-1", "Test PDU", "PDU44001",
            week_start="2026-02-16", reports_dir=str(tmp_path),
        )
        assert result is not None
        path = Path(result)
        assert path.exists()
        assert path.name == "pdu-1_weekly_2026-02-16.pdf"
        assert path.stat().st_size > 1000
        # Valid PDF
        assert path.read_bytes()[:5] == b"%PDF-"

    def test_default_week_start(self, tmp_path):
        rows = _make_daily_rows("2026-02-09", 7)
        history = _mock_history(rows)
        # Just test it doesn't crash — the week_start defaults to previous week
        result = generate_weekly_report(
            history, "pdu-1", reports_dir=str(tmp_path),
        )
        # May or may not return a path depending on dates
        # The point is it doesn't raise

    def test_snaps_midweek_to_monday(self, tmp_path):
        """If week_start is a Thursday, snaps back to the Monday of that week."""
        # 2026-02-19 is a Thursday, should snap to 2026-02-16 (Monday)
        rows = _make_daily_rows("2026-02-16", 7)
        prev_rows = _make_daily_rows("2026-02-09", 7, base_kwh=0.8)
        history = _mock_history(rows, prev_rows)

        result = generate_weekly_report(
            history, "pdu-1", "Test PDU", "PDU44001",
            week_start="2026-02-19", reports_dir=str(tmp_path),
        )
        assert result is not None
        path = Path(result)
        # Filename should use the Monday, not the Thursday
        assert path.name == "pdu-1_weekly_2026-02-16.pdf"

    def test_snaps_sunday_to_monday(self, tmp_path):
        """If week_start is a Sunday, snaps back to the Monday of that week."""
        # 2026-02-22 is a Sunday, should snap to 2026-02-16 (Monday)
        rows = _make_daily_rows("2026-02-16", 7)
        prev_rows = _make_daily_rows("2026-02-09", 7, base_kwh=0.8)
        history = _mock_history(rows, prev_rows)

        result = generate_weekly_report(
            history, "pdu-1", "Test PDU", "PDU44001",
            week_start="2026-02-22", reports_dir=str(tmp_path),
        )
        assert result is not None
        assert Path(result).name == "pdu-1_weekly_2026-02-16.pdf"

    def test_monday_stays_monday(self, tmp_path):
        """If week_start is already a Monday, it stays unchanged."""
        rows = _make_daily_rows("2026-02-16", 7)
        prev_rows = _make_daily_rows("2026-02-09", 7, base_kwh=0.8)
        history = _mock_history(rows, prev_rows)

        result = generate_weekly_report(
            history, "pdu-1", "Test PDU", "PDU44001",
            week_start="2026-02-16", reports_dir=str(tmp_path),
        )
        assert result is not None
        assert Path(result).name == "pdu-1_weekly_2026-02-16.pdf"


# ---------------------------------------------------------------------------
# Tests: monthly report generation
# ---------------------------------------------------------------------------

class TestMonthlyReport:
    def test_no_data_returns_none(self, tmp_path):
        mock = MagicMock()
        mock.query_energy_daily_all = MagicMock(return_value=[])
        result = generate_monthly_report(
            mock, "pdu-1", "Test PDU", "PDU44001",
            month="2026-01", reports_dir=str(tmp_path),
        )
        assert result is None

    def test_generates_valid_pdf(self, tmp_path):
        rows = _make_daily_rows("2026-01-01", 31)
        prev_rows = _make_daily_rows("2025-12-01", 31, base_kwh=0.9)
        history = _mock_history(rows, prev_rows)

        result = generate_monthly_report(
            history, "pdu-1", "Test PDU", "PDU44001",
            month="2026-01", reports_dir=str(tmp_path),
        )
        assert result is not None
        path = Path(result)
        assert path.exists()
        assert path.name == "pdu-1_monthly_2026-01.pdf"
        assert path.stat().st_size > 1000
        assert path.read_bytes()[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# Tests: list reports
# ---------------------------------------------------------------------------

class TestListReports:
    def test_empty_dir(self, tmp_path):
        result = list_reports(str(tmp_path))
        assert result == []

    def test_nonexistent_dir(self, tmp_path):
        result = list_reports(str(tmp_path / "nonexistent"))
        assert result == []

    def test_lists_weekly_and_monthly(self, tmp_path):
        (tmp_path / "pdu-1_weekly_2026-02-16.pdf").write_bytes(b"%PDF test")
        (tmp_path / "pdu-1_monthly_2026-01.pdf").write_bytes(b"%PDF test")
        (tmp_path / "not_a_report.pdf").write_bytes(b"nope")
        result = list_reports(str(tmp_path))
        assert len(result) == 2
        types = {r["report_type"] for r in result}
        assert types == {"weekly", "monthly"}

    def test_device_filter(self, tmp_path):
        (tmp_path / "pdu-1_weekly_2026-02-16.pdf").write_bytes(b"%PDF test")
        (tmp_path / "pdu-2_weekly_2026-02-16.pdf").write_bytes(b"%PDF test")
        result = list_reports(str(tmp_path), device_id="pdu-1")
        assert len(result) == 1
        assert result[0]["device_id"] == "pdu-1"

    def test_includes_size_and_created(self, tmp_path):
        (tmp_path / "pdu-1_weekly_2026-02-16.pdf").write_bytes(b"%PDF test content")
        result = list_reports(str(tmp_path))
        assert len(result) == 1
        assert result[0]["size_bytes"] > 0
        assert result[0]["created"]  # non-empty date string
