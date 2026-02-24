# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""PDF energy report generator — weekly and monthly reports with cyberpunk theme."""

import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fpdf import FPDF

logger = logging.getLogger(__name__)

# Default report output directory
REPORTS_DIR = os.environ.get("BRIDGE_REPORTS_DIR", "/data/reports")

# Theme colors (matching web UI --cyan, --green, --red, --amber)
COLOR_BG = (10, 10, 15)           # #0a0a0f
COLOR_SURFACE = (18, 18, 30)      # #12121e
COLOR_CARD = (26, 26, 42)         # #1a1a2a
COLOR_CYAN = (0, 240, 255)        # #00f0ff
COLOR_GREEN = (5, 255, 161)       # #05ffa1
COLOR_AMBER = (255, 191, 0)       # #ffbf00
COLOR_RED = (255, 42, 109)        # #ff2a6d
COLOR_TEXT = (200, 208, 220)       # #c8d0dc
COLOR_DIM = (100, 110, 130)       # #646e82
COLOR_WHITE = (240, 240, 245)     # #f0f0f5

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

# Filename patterns
_WEEKLY_RE = re.compile(r"^(.+)_weekly_(\d{4}-\d{2}-\d{2})\.pdf$")
_MONTHLY_RE = re.compile(r"^(.+)_monthly_(\d{4}-\d{2})\.pdf$")


class CyberPDFReport(FPDF):
    """Dark-themed PDF report matching the web UI cyberpunk style."""

    def __init__(self, title: str = "", device_name: str = "",
                 model: str = "", period: str = "", device_id: str = ""):
        super().__init__(orientation="P", unit="mm", format="A4")
        self._title = title
        self._device_name = device_name
        self._model = model
        self._period = period
        self._device_id = device_id
        self._fonts_loaded = False
        self._load_fonts()
        self.set_auto_page_break(auto=True, margin=20)

    def _load_fonts(self):
        """Register TTF fonts, falling back to built-in Helvetica/Courier."""
        try:
            regular = FONT_DIR / "Inter-Regular.ttf"
            bold = FONT_DIR / "Inter-Bold.ttf"
            mono = FONT_DIR / "JetBrainsMono-Regular.ttf"
            if regular.exists() and bold.exists() and mono.exists():
                self.add_font("Inter", "", str(regular))
                self.add_font("Inter", "B", str(bold))
                self.add_font("JetBrainsMono", "", str(mono))
                self._fonts_loaded = True
                return
        except Exception:
            logger.debug("TTF font loading failed, using built-in fonts", exc_info=True)
        self._fonts_loaded = False

    def _font(self, family: str = "Inter", style: str = "", size: int = 10):
        """Set font with fallback."""
        if self._fonts_loaded:
            self.set_font(family, style, size)
        else:
            fallback = "Helvetica" if family == "Inter" else "Courier"
            self.set_font(fallback, style, size)

    def header(self):
        # Dark header band
        self.set_fill_color(*COLOR_BG)
        self.rect(0, 0, 210, 40, "F")

        # Cyan accent line
        self.set_fill_color(*COLOR_CYAN)
        self.rect(0, 40, 210, 0.8, "F")

        # Title
        self._font("Inter", "B", 16)
        self.set_text_color(*COLOR_CYAN)
        self.set_y(8)
        self.cell(0, 8, "CYBERPOWER PDU ENERGY REPORT", align="C", new_x="LMARGIN", new_y="NEXT")

        # Subtitle: device info + period
        self._font("Inter", "", 9)
        self.set_text_color(*COLOR_DIM)
        parts = []
        if self._device_name:
            parts.append(self._device_name)
        if self._model:
            parts.append(self._model)
        if self._device_id:
            parts.append(f"ID: {self._device_id}")
        subtitle = " | ".join(parts)
        self.cell(0, 5, subtitle, align="C", new_x="LMARGIN", new_y="NEXT")

        # Period + generation date
        self.set_text_color(*COLOR_TEXT)
        self._font("Inter", "", 9)
        period_line = f"Period: {self._period}  |  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        self.cell(0, 5, period_line, align="C", new_x="LMARGIN", new_y="NEXT")

        self.set_y(44)

    def footer(self):
        self.set_y(-15)
        self.set_fill_color(*COLOR_BG)
        self.rect(0, self.h - 15, 210, 15, "F")
        self._font("Inter", "", 7)
        self.set_text_color(*COLOR_DIM)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")
        self.set_x(10)
        self.cell(0, 10, "Valpatel Software LLC", align="L")

    def _dark_page(self):
        """Fill the current page with the dark background."""
        self.set_fill_color(*COLOR_BG)
        self.rect(0, 0, 210, 297, "F")

    def section_title(self, title: str):
        """Render a cyan uppercase section heading with underline."""
        self.ln(4)
        self._font("Inter", "B", 12)
        self.set_text_color(*COLOR_CYAN)
        self.cell(0, 8, title.upper(), new_x="LMARGIN", new_y="NEXT")
        # Underline
        self.set_fill_color(*COLOR_CYAN)
        self.rect(self.l_margin, self.get_y(), 190, 0.3, "F")
        self.ln(3)

    def summary_card(self, label: str, value: str, unit: str = "",
                     color: tuple = COLOR_CYAN, comparison: str = ""):
        """Render an inline metric card."""
        card_w = 44
        card_h = 22
        x = self.get_x()
        y = self.get_y()

        # Card background
        self.set_fill_color(*COLOR_CARD)
        self.rect(x, y, card_w, card_h, "F")

        # Label
        self._font("Inter", "", 7)
        self.set_text_color(*COLOR_DIM)
        self.set_xy(x + 2, y + 2)
        self.cell(card_w - 4, 4, label, new_x="LEFT", new_y="NEXT")

        # Value
        self._font("Inter", "B", 14)
        self.set_text_color(*color)
        self.set_xy(x + 2, y + 7)
        display = f"{value} {unit}".strip()
        self.cell(card_w - 4, 7, display, new_x="LEFT", new_y="NEXT")

        # Comparison (e.g., "+12% vs last week")
        if comparison:
            self._font("Inter", "", 6)
            # Color based on sign
            if comparison.startswith("+"):
                self.set_text_color(*COLOR_RED)
            elif comparison.startswith("-"):
                self.set_text_color(*COLOR_GREEN)
            else:
                self.set_text_color(*COLOR_DIM)
            self.set_xy(x + 2, y + 16)
            self.cell(card_w - 4, 4, comparison, new_x="LEFT", new_y="NEXT")

        # Move cursor to next card position
        self.set_xy(x + card_w + 2, y)

    def data_table(self, headers: list[str], rows: list[list[str]],
                   col_widths: list[float] | None = None):
        """Render a data table with alternating row colors."""
        if col_widths is None:
            col_widths = [190 / len(headers)] * len(headers)

        # Header row
        self.set_fill_color(*COLOR_SURFACE)
        self._font("Inter", "B", 8)
        self.set_text_color(*COLOR_CYAN)
        row_h = 6
        for i, h in enumerate(headers):
            self.cell(col_widths[i], row_h, h, border=0, fill=True, align="C")
        self.ln(row_h)

        # Data rows
        self._font("Inter", "", 8)
        for idx, row in enumerate(rows):
            if idx % 2 == 0:
                self.set_fill_color(*COLOR_CARD)
            else:
                self.set_fill_color(*COLOR_SURFACE)
            self.set_text_color(*COLOR_TEXT)
            for i, val in enumerate(row):
                align = "L" if i == 0 else "R"
                self.cell(col_widths[i], row_h, str(val), border=0, fill=True, align=align)
            self.ln(row_h)

    def bar_chart(self, data: list[tuple[str, float]],
                  bar_color: tuple = COLOR_CYAN, max_width: float = 120):
        """Draw horizontal bar chart with labels."""
        if not data:
            return
        max_val = max(v for _, v in data) or 1
        bar_h = 5
        label_w = 35
        value_w = 25

        for label, value in data:
            y = self.get_y()
            # Label
            self._font("Inter", "", 7)
            self.set_text_color(*COLOR_TEXT)
            self.set_xy(self.l_margin, y)
            self.cell(label_w, bar_h, label[:18], align="R")

            # Bar
            bar_width = (value / max_val) * max_width if max_val > 0 else 0
            self.set_fill_color(*bar_color)
            bar_x = self.l_margin + label_w + 2
            self.rect(bar_x, y + 0.5, max(bar_width, 0.5), bar_h - 1, "F")

            # Value label
            self._font("JetBrainsMono", "", 7)
            self.set_text_color(*COLOR_DIM)
            self.set_xy(bar_x + max_width + 2, y)
            self.cell(value_w, bar_h, f"{value:.3f}", align="L")

            self.set_y(y + bar_h + 0.5)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _sum_total_kwh(rows: list[dict]) -> float:
    """Sum kWh for total rows (source=NULL, outlet=NULL)."""
    return sum(r["kwh"] for r in rows
               if r.get("source") is None and r.get("outlet") is None)


def _sum_source_kwh(rows: list[dict], source: int) -> float:
    """Sum kWh for a specific source (outlet=NULL)."""
    return sum(r["kwh"] for r in rows
               if r.get("source") == source and r.get("outlet") is None)


def _per_outlet_kwh(rows: list[dict]) -> dict[int, float]:
    """Get per-outlet kWh totals (source=NULL, outlet!=NULL)."""
    outlets: dict[int, float] = {}
    for r in rows:
        if r.get("source") is None and r.get("outlet") is not None:
            outlets[r["outlet"]] = outlets.get(r["outlet"], 0) + r["kwh"]
    return outlets


def _per_outlet_source_kwh(rows: list[dict]) -> dict[int, dict[int, float]]:
    """Get per-outlet kWh broken down by source.

    Returns {outlet: {source: kwh}}, e.g. {1: {1: 0.6, 2: 0.4}}.
    Only includes rows where both source and outlet are non-None.
    """
    result: dict[int, dict[int, float]] = {}
    for r in rows:
        src = r.get("source")
        outlet = r.get("outlet")
        if src is not None and outlet is not None:
            result.setdefault(outlet, {})
            result[outlet][src] = result[outlet].get(src, 0) + r["kwh"]
    return result


def _per_day_kwh(rows: list[dict]) -> dict[str, float]:
    """Get per-day total kWh (source=NULL, outlet=NULL)."""
    days: dict[str, float] = {}
    for r in rows:
        if r.get("source") is None and r.get("outlet") is None:
            days[r["date"]] = days.get(r["date"], 0) + r["kwh"]
    return days


def _per_day_source_kwh(rows: list[dict], source: int) -> dict[str, float]:
    """Get per-day kWh for a specific source (outlet=NULL)."""
    days: dict[str, float] = {}
    for r in rows:
        if r.get("source") == source and r.get("outlet") is None:
            days[r["date"]] = days.get(r["date"], 0) + r["kwh"]
    return days


def _pct_change(current: float, previous: float) -> str:
    """Format percentage change string."""
    if previous == 0:
        return ""
    pct = ((current - previous) / previous) * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.0f}% vs prior"


def _ensure_reports_dir(reports_dir: str | None = None) -> Path:
    """Create reports directory if it doesn't exist."""
    d = Path(reports_dir or REPORTS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Report generators
# ---------------------------------------------------------------------------

def generate_weekly_report(
    history,
    device_id: str = "",
    device_name: str = "PDU",
    model: str = "",
    week_start: str | None = None,
    reports_dir: str | None = None,
) -> str | None:
    """Generate a weekly energy PDF report.

    Args:
        history: HistoryStore instance
        device_id: PDU device identifier
        device_name: Human-readable PDU name
        model: PDU model string
        week_start: Monday date as YYYY-MM-DD (default: previous week)
        reports_dir: Output directory (default: REPORTS_DIR)

    Returns:
        Path to generated PDF, or None if no data.
    """
    # Determine week range
    if week_start:
        start = datetime.strptime(week_start, "%Y-%m-%d")
    else:
        today = datetime.now()
        # Previous Monday
        start = today - timedelta(days=today.weekday() + 7)
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)

    end = start + timedelta(days=6)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    # Query data
    rows = history.query_energy_daily_all(start_str, end_str, device_id)
    if not rows:
        logger.info("No energy data for weekly report %s to %s (device=%s)",
                     start_str, end_str, device_id)
        return None

    # Query previous week for comparison
    prev_start = start - timedelta(days=7)
    prev_end = prev_start + timedelta(days=6)
    prev_rows = history.query_energy_daily_all(
        prev_start.strftime("%Y-%m-%d"), prev_end.strftime("%Y-%m-%d"), device_id
    )

    # Compute metrics
    total_kwh = _sum_total_kwh(rows)
    source_a = _sum_source_kwh(rows, 1)
    source_b = _sum_source_kwh(rows, 2)
    daily_totals = _per_day_kwh(rows)
    outlet_totals = _per_outlet_kwh(rows)
    outlet_by_source = _per_outlet_source_kwh(rows)
    daily_a = _per_day_source_kwh(rows, 1)
    daily_b = _per_day_source_kwh(rows, 2)

    prev_total = _sum_total_kwh(prev_rows)
    prev_a = _sum_source_kwh(prev_rows, 1)
    prev_b = _sum_source_kwh(prev_rows, 2)

    # Peak power from daily rows
    peak_power = max((r.get("peak_power_w") or 0) for r in rows
                     if r.get("source") is None and r.get("outlet") is None)

    # Build PDF
    period = f"{start_str} to {end_str}"
    pdf = CyberPDFReport(
        title="Weekly Energy Report",
        device_name=device_name,
        model=model,
        period=period,
        device_id=device_id,
    )
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf._dark_page()

    # --- Summary Cards ---
    pdf.section_title("Energy Summary")
    y_cards = pdf.get_y()
    pdf.set_xy(pdf.l_margin, y_cards)
    pdf.summary_card("Total Energy", f"{total_kwh:.3f}", "kWh",
                     COLOR_CYAN, _pct_change(total_kwh, prev_total))
    pdf.summary_card("Source A", f"{source_a:.3f}", "kWh",
                     COLOR_GREEN, _pct_change(source_a, prev_a))
    pdf.summary_card("Source B", f"{source_b:.3f}", "kWh",
                     COLOR_AMBER, _pct_change(source_b, prev_b))
    pdf.summary_card("Peak Power", f"{peak_power:.0f}", "W", COLOR_RED)
    pdf.set_y(y_cards + 26)

    # --- Daily Breakdown ---
    pdf.section_title("Daily Breakdown")
    day_headers = ["Date", "Total kWh", "Source A", "Source B", "Peak W", "Avg W"]
    day_rows = []
    for r in rows:
        if r.get("source") is None and r.get("outlet") is None:
            day_a = sum(rr["kwh"] for rr in rows
                        if rr.get("date") == r["date"] and rr.get("source") == 1
                        and rr.get("outlet") is None)
            day_b = sum(rr["kwh"] for rr in rows
                        if rr.get("date") == r["date"] and rr.get("source") == 2
                        and rr.get("outlet") is None)
            day_rows.append([
                r["date"],
                f"{r['kwh']:.3f}",
                f"{day_a:.3f}",
                f"{day_b:.3f}",
                f"{r.get('peak_power_w', 0) or 0:.0f}",
                f"{r.get('avg_power_w', 0) or 0:.0f}",
            ])
    pdf.data_table(day_headers, day_rows, [30, 28, 28, 28, 25, 25])

    # Daily chart
    pdf.ln(3)
    chart_data = [(d, kwh) for d, kwh in sorted(daily_totals.items())]
    pdf.bar_chart(chart_data, COLOR_CYAN)

    # --- Per-Outlet Breakdown (by source) ---
    if outlet_totals:
        pdf.section_title("Per-Outlet Breakdown")
        sorted_outlets = sorted(outlet_totals.items(), key=lambda x: x[1], reverse=True)
        outlet_headers = ["Outlet", "Total kWh", "Source A", "Source B", "% of Total"]
        outlet_rows = []
        for num, kwh in sorted_outlets:
            pct = (kwh / total_kwh * 100) if total_kwh > 0 else 0
            src = outlet_by_source.get(num, {})
            outlet_rows.append([
                f"Outlet {num}",
                f"{kwh:.3f}",
                f"{src.get(1, 0):.3f}",
                f"{src.get(2, 0):.3f}",
                f"{pct:.1f}%",
            ])
        pdf.data_table(outlet_headers, outlet_rows, [40, 32, 32, 32, 28])

        # Outlet chart
        pdf.ln(3)
        outlet_chart = [(f"Outlet {n}", kwh) for n, kwh in sorted_outlets[:16]]
        pdf.bar_chart(outlet_chart, COLOR_GREEN)

    # --- Source A vs B ---
    if source_a > 0 or source_b > 0:
        pdf.section_title("Source A vs B Daily Comparison")
        src_headers = ["Date", "Source A kWh", "Source B kWh"]
        src_rows = []
        all_dates = sorted(set(list(daily_a.keys()) + list(daily_b.keys())))
        for d in all_dates:
            src_rows.append([
                d,
                f"{daily_a.get(d, 0):.3f}",
                f"{daily_b.get(d, 0):.3f}",
            ])
        pdf.data_table(src_headers, src_rows, [50, 60, 60])

    # Save
    out_dir = _ensure_reports_dir(reports_dir)
    safe_id = device_id or "default"
    filename = f"{safe_id}_weekly_{start_str}.pdf"
    filepath = out_dir / filename
    pdf.output(str(filepath))
    logger.info("Generated weekly report: %s (%.1f kWh)", filepath, total_kwh)
    return str(filepath)


def generate_monthly_report(
    history,
    device_id: str = "",
    device_name: str = "PDU",
    model: str = "",
    month: str | None = None,
    reports_dir: str | None = None,
) -> str | None:
    """Generate a monthly energy PDF report.

    Args:
        history: HistoryStore instance
        device_id: PDU device identifier
        device_name: Human-readable PDU name
        model: PDU model string
        month: Month as YYYY-MM (default: previous month)
        reports_dir: Output directory (default: REPORTS_DIR)

    Returns:
        Path to generated PDF, or None if no data.
    """
    if month:
        month_dt = datetime.strptime(month + "-01", "%Y-%m-%d")
    else:
        today = datetime.now()
        first_of_this = today.replace(day=1)
        month_dt = (first_of_this - timedelta(days=1)).replace(day=1)

    month_str = month_dt.strftime("%Y-%m")
    # Last day of month
    if month_dt.month == 12:
        last_day = month_dt.replace(year=month_dt.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        last_day = month_dt.replace(month=month_dt.month + 1, day=1) - timedelta(days=1)

    start_str = month_dt.strftime("%Y-%m-%d")
    end_str = last_day.strftime("%Y-%m-%d")

    # Query daily data for the full month
    rows = history.query_energy_daily_all(start_str, end_str, device_id)
    if not rows:
        logger.info("No energy data for monthly report %s (device=%s)", month_str, device_id)
        return None

    # Previous month for comparison
    prev_month_dt = (month_dt - timedelta(days=1)).replace(day=1)
    if prev_month_dt.month == 12:
        prev_last = prev_month_dt.replace(year=prev_month_dt.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        prev_last = prev_month_dt.replace(month=prev_month_dt.month + 1, day=1) - timedelta(days=1)
    prev_rows = history.query_energy_daily_all(
        prev_month_dt.strftime("%Y-%m-%d"), prev_last.strftime("%Y-%m-%d"), device_id
    )

    # Metrics
    total_kwh = _sum_total_kwh(rows)
    source_a = _sum_source_kwh(rows, 1)
    source_b = _sum_source_kwh(rows, 2)
    daily_totals = _per_day_kwh(rows)
    outlet_totals = _per_outlet_kwh(rows)
    outlet_by_source = _per_outlet_source_kwh(rows)
    daily_a = _per_day_source_kwh(rows, 1)
    daily_b = _per_day_source_kwh(rows, 2)

    prev_total = _sum_total_kwh(prev_rows)
    prev_a = _sum_source_kwh(prev_rows, 1)
    prev_b = _sum_source_kwh(prev_rows, 2)

    peak_power = max((r.get("peak_power_w") or 0) for r in rows
                     if r.get("source") is None and r.get("outlet") is None)

    # Build PDF
    period = f"{month_dt.strftime('%B %Y')} ({start_str} to {end_str})"
    pdf = CyberPDFReport(
        title="Monthly Energy Report",
        device_name=device_name,
        model=model,
        period=period,
        device_id=device_id,
    )
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf._dark_page()

    # --- Summary Cards ---
    pdf.section_title("Energy Summary")
    y_cards = pdf.get_y()
    pdf.set_xy(pdf.l_margin, y_cards)
    pdf.summary_card("Total Energy", f"{total_kwh:.3f}", "kWh",
                     COLOR_CYAN, _pct_change(total_kwh, prev_total))
    pdf.summary_card("Source A", f"{source_a:.3f}", "kWh",
                     COLOR_GREEN, _pct_change(source_a, prev_a))
    pdf.summary_card("Source B", f"{source_b:.3f}", "kWh",
                     COLOR_AMBER, _pct_change(source_b, prev_b))
    pdf.summary_card("Peak Power", f"{peak_power:.0f}", "W", COLOR_RED)
    pdf.set_y(y_cards + 26)

    # --- Daily Breakdown ---
    pdf.section_title("Daily Breakdown")
    day_headers = ["Date", "Total kWh", "Source A", "Source B", "Peak W", "Avg W"]
    day_rows = []
    for r in rows:
        if r.get("source") is None and r.get("outlet") is None:
            day_a_val = sum(rr["kwh"] for rr in rows
                           if rr.get("date") == r["date"] and rr.get("source") == 1
                           and rr.get("outlet") is None)
            day_b_val = sum(rr["kwh"] for rr in rows
                           if rr.get("date") == r["date"] and rr.get("source") == 2
                           and rr.get("outlet") is None)
            day_rows.append([
                r["date"],
                f"{r['kwh']:.3f}",
                f"{day_a_val:.3f}",
                f"{day_b_val:.3f}",
                f"{r.get('peak_power_w', 0) or 0:.0f}",
                f"{r.get('avg_power_w', 0) or 0:.0f}",
            ])
    pdf.data_table(day_headers, day_rows, [30, 28, 28, 28, 25, 25])

    # Daily chart (only show if <= 31 days)
    if len(daily_totals) <= 31:
        pdf.ln(3)
        chart_data = [(d[-5:], kwh) for d, kwh in sorted(daily_totals.items())]
        pdf.bar_chart(chart_data, COLOR_CYAN)

    # --- Per-Outlet Breakdown (by source) ---
    if outlet_totals:
        pdf.section_title("Per-Outlet Breakdown")
        sorted_outlets = sorted(outlet_totals.items(), key=lambda x: x[1], reverse=True)
        outlet_headers = ["Outlet", "Total kWh", "Source A", "Source B", "% of Total"]
        outlet_rows = []
        for num, kwh in sorted_outlets:
            pct = (kwh / total_kwh * 100) if total_kwh > 0 else 0
            src = outlet_by_source.get(num, {})
            outlet_rows.append([
                f"Outlet {num}",
                f"{kwh:.3f}",
                f"{src.get(1, 0):.3f}",
                f"{src.get(2, 0):.3f}",
                f"{pct:.1f}%",
            ])
        pdf.data_table(outlet_headers, outlet_rows, [40, 32, 32, 32, 28])

        pdf.ln(3)
        outlet_chart = [(f"Outlet {n}", kwh) for n, kwh in sorted_outlets[:16]]
        pdf.bar_chart(outlet_chart, COLOR_GREEN)

    # --- Source A vs B ---
    if source_a > 0 or source_b > 0:
        pdf.section_title("Source A vs B Daily Comparison")
        src_headers = ["Date", "Source A kWh", "Source B kWh"]
        src_rows = []
        all_dates = sorted(set(list(daily_a.keys()) + list(daily_b.keys())))
        for d in all_dates:
            src_rows.append([
                d if len(daily_totals) <= 14 else d[-5:],
                f"{daily_a.get(d, 0):.3f}",
                f"{daily_b.get(d, 0):.3f}",
            ])
        pdf.data_table(src_headers, src_rows, [50, 60, 60])

    # Save
    out_dir = _ensure_reports_dir(reports_dir)
    safe_id = device_id or "default"
    filename = f"{safe_id}_monthly_{month_str}.pdf"
    filepath = out_dir / filename
    pdf.output(str(filepath))
    logger.info("Generated monthly report: %s (%.1f kWh)", filepath, total_kwh)
    return str(filepath)


# ---------------------------------------------------------------------------
# Listing and access
# ---------------------------------------------------------------------------

def list_reports(reports_dir: str | None = None,
                 device_id: str | None = None) -> list[dict[str, Any]]:
    """List available PDF reports, optionally filtered by device_id.

    Returns list of dicts: {filename, device_id, report_type, period, size_bytes, created}.
    """
    d = Path(reports_dir or REPORTS_DIR)
    if not d.exists():
        return []

    results = []
    for f in sorted(d.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True):
        info = _parse_filename(f.name)
        if info is None:
            continue
        if device_id and info["device_id"] != device_id:
            continue
        stat = f.stat()
        info["size_bytes"] = stat.st_size
        info["created"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        results.append(info)

    return results


def _parse_filename(filename: str) -> dict[str, str] | None:
    """Parse report filename into metadata dict."""
    m = _WEEKLY_RE.match(filename)
    if m:
        return {
            "filename": filename,
            "device_id": m.group(1),
            "report_type": "weekly",
            "period": m.group(2),
        }
    m = _MONTHLY_RE.match(filename)
    if m:
        return {
            "filename": filename,
            "device_id": m.group(1),
            "report_type": "monthly",
            "period": m.group(2),
        }
    return None


def get_report_path(filename: str,
                    reports_dir: str | None = None) -> Path | None:
    """Securely resolve a report filename to a full path.

    Rejects path traversal attempts and non-PDF files.
    Returns None if invalid or not found.
    """
    if not filename:
        return None
    # Block traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    if not filename.endswith(".pdf"):
        return None

    d = Path(reports_dir or REPORTS_DIR)
    path = d / filename
    # Ensure resolved path is still under reports_dir
    try:
        path.resolve().relative_to(d.resolve())
    except ValueError:
        return None

    if path.exists():
        return path
    return None
