# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""Pytest configuration — branded HTML reports with git metadata."""

import platform
import subprocess
from datetime import datetime


def _git(cmd: str) -> str:
    """Run a git command and return stripped output, or '' on failure."""
    try:
        return subprocess.check_output(
            ["git"] + cmd.split(), stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return ""


def pytest_configure(config):
    """Add project metadata to HTML report (only if pytest-metadata installed)."""
    try:
        from pytest_metadata.plugin import metadata_key
    except ImportError:
        return

    config.stash[metadata_key]["Project"] = "CyberPower PDU Bridge"
    config.stash[metadata_key]["Author"] = "Matthew Valancy, Valpatel Software LLC"
    config.stash[metadata_key]["Git Commit"] = _git("rev-parse --short HEAD")
    config.stash[metadata_key]["Git Branch"] = _git("rev-parse --abbrev-ref HEAD")
    config.stash[metadata_key]["Python"] = platform.python_version()
    config.stash[metadata_key]["Timestamp"] = datetime.now().isoformat(timespec="seconds")


# Conditional hooks — only registered when pytest-html is available
try:
    import pytest_html  # noqa: F401

    def pytest_html_report_title(report):
        """Set the HTML report title."""
        report.title = "CyberPower PDU Bridge — Test Report"

    def pytest_html_results_summary(prefix, summary, postfix):
        """Add Valpatel branding to the results summary."""
        prefix.extend([
            "<div style='padding:16px 0 8px;font-family:Inter,system-ui,sans-serif'>",
            "<h2 style='margin:0;color:#00f0ff;font-size:18px;letter-spacing:0.05em'>"
            "CyberPower PDU Bridge</h2>",
            "<p style='margin:4px 0 0;color:#8b8fa3;font-size:13px'>"
            "Created by <a href='https://mattvalancy.com' style='color:#00f0ff'>"
            "Matthew Valancy</a>, "
            "<a href='https://valpatel.com' style='color:#00f0ff'>"
            "Valpatel Software LLC</a></p>",
            "</div>",
        ])
except ImportError:
    pass
