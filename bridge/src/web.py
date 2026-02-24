# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Web UI and REST API server for PDU automation — multi-PDU support."""

import asyncio
import collections
import csv
import glob as globmod
import hashlib
import io
import json
import logging
import os
import platform
import secrets
import signal
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

from aiohttp import web

from .automation import AutomationEngine
from .pdu_model import ATS_SOURCE_MAP, PDUData

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

CommandCallback = Callable[[int, str], Awaitable[None]]
OutletNamesCallback = Callable[[dict[str, str]], None]
PduConfigCallback = Callable[[dict[str, Any]], Awaitable[None]]
DiscoveryCallback = Callable[[], Awaitable[list[dict[str, Any]]]]
SnmpSetCallback = Callable[[str, str, str], Awaitable[None]]
AddPduCallback = Callable[[dict[str, Any]], Awaitable[None]]
RemovePduCallback = Callable[[str], Awaitable[None]]
TestConnectionCallback = Callable[[str, str, int], Awaitable[dict[str, Any]]]
TestSerialCallback = Callable[[str, str, str], Awaitable[dict[str, Any]]]
ManagementCallback = Callable[..., Awaitable[Any]]
PollerStatusCallback = Callable[[], list[dict[str, Any]]]
SnmpConfigCallback = Callable[[float, int], Awaitable[None]]
ReportListCallback = Callable[[str | None], Awaitable[list[dict[str, Any]]]]
ReportGenerateCallback = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


# ---------------------------------------------------------------------------
# RingBufferHandler — in-memory log capture for web viewer
# ---------------------------------------------------------------------------

class RingBufferHandler(logging.Handler):
    """Logging handler that stores records in a bounded deque for web access."""

    def __init__(self, capacity: int = 1000):
        super().__init__()
        self._records: collections.deque = collections.deque(maxlen=capacity)

    def emit(self, record):
        try:
            self._records.append({
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            })
        except Exception:
            pass

    def get_records(self, level: str | None = None, limit: int = 200,
                    search: str | None = None) -> list[dict]:
        """Return filtered log records, newest first."""
        level_num = getattr(logging, level.upper(), 0) if level else 0
        results = []
        for rec in reversed(self._records):
            if level_num and getattr(logging, rec["level"], 0) < level_num:
                continue
            if search and search.lower() not in rec["message"].lower():
                continue
            results.append(rec)
            if len(results) >= limit:
                break
        return results

# Time range presets (query param -> seconds)
RANGE_MAP = {
    "1h": 3600,
    "6h": 6 * 3600,
    "24h": 24 * 3600,
    "7d": 7 * 86400,
    "30d": 30 * 86400,
}


@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


class WebServer:
    def __init__(self, device_id: str, port: int = 8080,
                 mqtt=None, history=None, config=None,
                 auth_username: str = "admin", auth_password: str = "",
                 session_secret: str = "", session_timeout: int = 86400):
        self._default_device_id = device_id
        self._port = port
        self._mqtt = mqtt
        self._history = history
        self._config = config  # Config object for settings persistence

        # Auth configuration (empty password = auth disabled)
        self._auth_username = auth_username
        self._auth_password = auth_password
        self._auth_enabled = bool(auth_password)
        self._session_secret = session_secret or secrets.token_hex(32)
        self._session_timeout = session_timeout
        self._sessions: dict[str, dict] = {}  # token -> {username, created, expires}

        # Multi-PDU storage — keyed by device_id
        self._pdu_data: dict[str, PDUData] = {}
        self._pdu_data_times: dict[str, float] = {}
        self._pdu_configs: dict[str, Any] = {}

        # Per-device automation engines
        self._engines: dict[str, AutomationEngine] = {}

        # Per-device command callbacks
        self._device_command_callbacks: dict[str, CommandCallback] = {}

        # Legacy single-PDU aliases (for backward compat)
        self._last_data: PDUData | None = None
        self._last_data_time: float | None = None

        # Legacy callbacks (used when no device_id-specific callback exists)
        self._command_callback: CommandCallback | None = None
        self._outlet_names_callback: OutletNamesCallback | None = None

        # Multi-PDU callbacks
        self._pdu_config_callback: PduConfigCallback | None = None
        self._discovery_callback: DiscoveryCallback | None = None
        self._snmp_set_callback: SnmpSetCallback | None = None
        self._add_pdu_callback: AddPduCallback | None = None
        self._remove_pdu_callback: RemovePduCallback | None = None
        self._test_connection_callback: TestConnectionCallback | None = None
        self._test_serial_callback: TestSerialCallback | None = None

        # Management callbacks (serial-specific)
        self._management_callbacks: dict[str, ManagementCallback] = {}

        # Poller status callback (returns detailed per-device health)
        self._poller_status_callback: PollerStatusCallback | None = None

        # System event log (keyed by device_id, max 200 per device)
        self._system_events: dict[str, list[dict]] = {}
        self._max_system_events = 200

        # SSE (Server-Sent Events) clients
        self._sse_clients: list[web.StreamResponse] = []

        # Restart tracking
        self._restart_required: list[str] = []

        # Log buffer (set via set_log_buffer)
        self._log_buffer: RingBufferHandler | None = None

        # System info (set via setters from main.py)
        self._bridge_version: str = "0.0.0"
        self._start_time: float = time.time()

        # SNMP config callback
        self._snmp_config_callback: SnmpConfigCallback | None = None

        # Report callbacks
        self._report_list_callback: ReportListCallback | None = None
        self._report_generate_callback: ReportGenerateCallback | None = None

        self.outlet_names: dict[str, str] = {}

        # Build middleware stack
        middlewares = []
        if self._auth_enabled:
            middlewares.append(self._auth_middleware)
            logger.info("Web auth enabled (username=%s)", self._auth_username)
        middlewares.append(cors_middleware)

        self._app = web.Application(middlewares=middlewares)
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    # --- System event log ---

    def add_system_event(self, device_id: str, event_type: str,
                         source: str, details: str):
        """Add a system-level event (ATS transfer, power loss, outlet change, etc.)."""
        did = device_id or self._default_device_id
        if did not in self._system_events:
            self._system_events[did] = []
        event = {
            "rule": source,
            "type": event_type,
            "details": details,
            "ts": time.time(),
            "system": True,
        }
        self._system_events[did].append(event)
        if len(self._system_events[did]) > self._max_system_events:
            self._system_events[did] = self._system_events[did][-self._max_system_events:]
        logger.debug("[%s] System event: %s — %s: %s", did, event_type, source, details)

    def get_system_events(self, device_id: str) -> list[dict]:
        """Return system events for a device, newest first."""
        did = device_id or self._default_device_id
        return list(reversed(self._system_events.get(did, [])))

    # --- Auth middleware and session management ---

    @web.middleware
    async def _auth_middleware(self, request, handler):
        """Authentication middleware — validates session token.

        Skips auth for: /, /api/auth/*, /api/health, OPTIONS, static files.
        """
        path = request.path
        # Skip auth for these paths
        if (request.method == "OPTIONS"
                or path == "/"
                or path == "/favicon.svg"
                or path.startswith("/api/auth/")
                or path == "/api/health"
                or path == "/api/stream"):
            return await handler(request)

        # Check for session token
        token = self._extract_token(request)
        if token and self._validate_session(token):
            return await handler(request)

        return self._json({"error": "Authentication required"}, 401)

    def _extract_token(self, request) -> str | None:
        """Extract session token from cookie or Authorization header."""
        # Check cookie first
        token = request.cookies.get("session_token")
        if token:
            return token
        # Check Authorization header
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return None

    def _validate_session(self, token: str) -> bool:
        """Check if session token is valid and not expired."""
        session = self._sessions.get(token)
        if not session:
            return False
        if time.time() > session["expires"]:
            del self._sessions[token]
            return False
        return True

    def _create_session(self, username: str) -> str:
        """Create a new session and return the token."""
        # Clean expired sessions
        now = time.time()
        expired = [t for t, s in self._sessions.items() if now > s["expires"]]
        for t in expired:
            del self._sessions[t]

        token = secrets.token_urlsafe(32)
        self._sessions[token] = {
            "username": username,
            "created": now,
            "expires": now + self._session_timeout,
        }
        return token

    # --- Callback registration ---

    def set_command_callback(self, callback: CommandCallback):
        """Set the legacy/default command callback (backward compat)."""
        self._command_callback = callback

    def set_device_command_callback(self, device_id: str, callback: CommandCallback):
        """Set a per-device command callback."""
        self._device_command_callbacks[device_id] = callback

    def set_outlet_names_callback(self, callback: OutletNamesCallback):
        self._outlet_names_callback = callback

    def set_pdu_config_callback(self, callback: PduConfigCallback):
        """Set callback for writing pdus.json when PDU config changes."""
        self._pdu_config_callback = callback

    def set_discovery_callback(self, callback: DiscoveryCallback):
        """Set callback for triggering network scan / PDU discovery."""
        self._discovery_callback = callback

    def set_snmp_set_callback(self, callback: SnmpSetCallback):
        """Set callback for SNMP SET operations (device_id, oid, value)."""
        self._snmp_set_callback = callback

    def set_add_pdu_callback(self, callback: AddPduCallback):
        """Set callback for runtime PDU addition (starts poller)."""
        self._add_pdu_callback = callback

    def set_remove_pdu_callback(self, callback: RemovePduCallback):
        """Set callback for runtime PDU removal (stops poller)."""
        self._remove_pdu_callback = callback

    def set_test_connection_callback(self, callback: TestConnectionCallback):
        """Set callback for testing SNMP connection to a host."""
        self._test_connection_callback = callback

    def set_test_serial_callback(self, callback: TestSerialCallback):
        """Set callback for testing serial port connectivity."""
        self._test_serial_callback = callback

    def set_management_callback(self, name: str, callback: ManagementCallback):
        """Set a named management callback (for serial-specific operations)."""
        self._management_callbacks[name] = callback

    def set_poller_status_callback(self, callback: PollerStatusCallback):
        """Set callback to get per-poller status details."""
        self._poller_status_callback = callback

    def set_snmp_config_callback(self, callback: SnmpConfigCallback):
        """Set callback for updating SNMP timeout/retries on all pollers."""
        self._snmp_config_callback = callback

    def set_report_list_callback(self, callback: ReportListCallback):
        """Set callback for listing available PDF reports."""
        self._report_list_callback = callback

    def set_report_generate_callback(self, callback: ReportGenerateCallback):
        """Set callback for on-demand PDF report generation."""
        self._report_generate_callback = callback

    def set_log_buffer(self, handler: RingBufferHandler):
        """Set the ring buffer handler for log viewing."""
        self._log_buffer = handler

    def set_bridge_version(self, version: str):
        self._bridge_version = version

    def set_start_time(self, start_time: float):
        self._start_time = start_time

    def register_automation_engine(self, device_id: str, engine: AutomationEngine):
        """Register a per-device automation engine."""
        self._engines[device_id] = engine

    def register_pdu(self, device_id: str, pdu_config_dict: dict[str, Any]):
        """Register a PDU's config info (host, community, label, etc.)."""
        self._pdu_configs[device_id] = pdu_config_dict

    # --- Route setup ---

    def _setup_routes(self):
        # Authentication
        self._app.router.add_post("/api/auth/login", self._handle_auth_login)
        self._app.router.add_post("/api/auth/logout", self._handle_auth_logout)
        self._app.router.add_get("/api/auth/status", self._handle_auth_status)

        # Multi-PDU management
        self._app.router.add_get("/api/pdus", self._handle_list_pdus)
        self._app.router.add_post("/api/pdus", self._handle_add_pdu)
        self._app.router.add_put("/api/pdus/{device_id}", self._handle_update_pdu)
        self._app.router.add_delete("/api/pdus/{device_id}", self._handle_delete_pdu)
        self._app.router.add_post("/api/pdus/discover", self._handle_discover_pdus)
        self._app.router.add_post("/api/pdus/test-connection", self._handle_test_connection)
        self._app.router.add_post("/api/pdus/test-serial", self._handle_test_serial)

        # Bridge config
        self._app.router.add_get("/api/config", self._handle_get_config)
        self._app.router.add_put("/api/config", self._handle_update_config)
        self._app.router.add_post("/api/config/test-mqtt", self._handle_test_mqtt)

        # Device SNMP SET endpoints
        self._app.router.add_put("/api/device/name", self._handle_set_device_name)
        self._app.router.add_put("/api/device/location", self._handle_set_device_location)

        # Existing endpoints (now multi-PDU aware)
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/health", self._handle_health)
        self._app.router.add_get("/api/rules", self._handle_list_rules)
        self._app.router.add_post("/api/rules", self._handle_create_rule)
        self._app.router.add_put("/api/rules/{name}", self._handle_update_rule)
        self._app.router.add_delete("/api/rules/{name}", self._handle_delete_rule)
        self._app.router.add_get("/api/events", self._handle_events)
        self._app.router.add_post("/api/outlets/{n}/command", self._handle_outlet_command)

        # History
        self._app.router.add_get("/api/history/banks", self._handle_history_banks)
        self._app.router.add_get("/api/history/outlets", self._handle_history_outlets)
        self._app.router.add_get("/api/history/banks.csv", self._handle_history_banks_csv)
        self._app.router.add_get("/api/history/outlets.csv", self._handle_history_outlets_csv)

        # Energy rollups
        self._app.router.add_get("/api/energy/daily", self._handle_energy_daily)
        self._app.router.add_get("/api/energy/monthly", self._handle_energy_monthly)
        self._app.router.add_get("/api/energy/daily.csv", self._handle_energy_daily_csv)
        self._app.router.add_get("/api/energy/monthly.csv", self._handle_energy_monthly_csv)
        self._app.router.add_get("/api/energy/summary", self._handle_energy_summary)

        # PDF Reports
        self._app.router.add_get("/api/reports", self._handle_list_reports)
        self._app.router.add_post("/api/reports/generate", self._handle_generate_report)
        self._app.router.add_get("/api/reports/download/{filename}", self._handle_download_report)

        # Outlet naming
        self._app.router.add_put("/api/outlets/{n}/name", self._handle_rename_outlet)
        self._app.router.add_get("/api/outlet-names", self._handle_get_outlet_names)

        # PDU Management (serial-specific)
        self._app.router.add_get("/api/pdu/network", self._handle_get_network)
        self._app.router.add_get("/api/pdu/thresholds", self._handle_get_thresholds)
        self._app.router.add_put("/api/pdu/thresholds/device", self._handle_set_device_thresholds)
        self._app.router.add_put("/api/pdu/thresholds/bank/{n}", self._handle_set_bank_thresholds)
        self._app.router.add_get("/api/pdu/outlets/config", self._handle_get_outlet_config)
        self._app.router.add_put("/api/pdu/outlets/{n}/config", self._handle_set_outlet_config)
        self._app.router.add_get("/api/pdu/eventlog", self._handle_get_eventlog)
        self._app.router.add_post("/api/pdu/security/check", self._handle_security_check)
        self._app.router.add_post("/api/pdu/security/password", self._handle_change_password)

        # ATS configuration
        self._app.router.add_get("/api/pdu/ats/config", self._handle_get_ats_config)
        self._app.router.add_put("/api/pdu/ats/preferred-source", self._handle_set_ats_preferred)
        self._app.router.add_put("/api/pdu/ats/auto-transfer", self._handle_set_ats_auto_transfer)
        self._app.router.add_put("/api/pdu/ats/sensitivity", self._handle_set_ats_sensitivity)
        self._app.router.add_put("/api/pdu/ats/voltage-limits", self._handle_set_ats_voltage_limits)
        self._app.router.add_put("/api/pdu/ats/coldstart", self._handle_set_ats_coldstart)

        # Network config write
        self._app.router.add_put("/api/pdu/network", self._handle_set_network)

        # User management
        self._app.router.add_get("/api/pdu/users", self._handle_get_users)

        # sysContact write
        self._app.router.add_put("/api/device/contact", self._handle_set_device_contact)

        # Notification configuration
        self._app.router.add_get("/api/pdu/notifications", self._handle_get_notifications)
        self._app.router.add_put("/api/pdu/notifications/traps/{index}", self._handle_set_trap)
        self._app.router.add_get("/api/pdu/notifications/smtp", self._handle_get_smtp)
        self._app.router.add_put("/api/pdu/notifications/smtp", self._handle_set_smtp)
        self._app.router.add_put("/api/pdu/notifications/email/{index}", self._handle_set_email)
        self._app.router.add_put("/api/pdu/notifications/syslog/{index}", self._handle_set_syslog)

        # EnergyWise
        self._app.router.add_get("/api/pdu/energywise", self._handle_get_energywise)
        self._app.router.add_put("/api/pdu/energywise", self._handle_set_energywise)

        # Automation toggle
        self._app.router.add_put("/api/rules/{name}/toggle", self._handle_toggle_rule)

        # SSE stream
        self._app.router.add_get("/api/stream", self._handle_sse)

        # System management
        self._app.router.add_post("/api/system/restart", self._handle_restart)
        self._app.router.add_get("/api/system/info", self._handle_system_info)
        self._app.router.add_get("/api/system/logs", self._handle_system_logs)
        self._app.router.add_get("/api/system/backup", self._handle_backup)
        self._app.router.add_post("/api/system/restore", self._handle_restore)

        # Static
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/favicon.svg", self._handle_favicon)

    # --- Data update ---

    def update_data(self, data: PDUData, device_id: str | None = None):
        """Store data for a specific PDU. If device_id is None, uses default."""
        did = device_id or self._default_device_id
        self._pdu_data[did] = data
        self._pdu_data_times[did] = time.time()

        # Maintain backward-compat aliases (point to first/default PDU)
        if did == self._default_device_id or len(self._pdu_data) == 1:
            self._last_data = data
            self._last_data_time = time.time()

        # Push SSE update to connected browsers
        if self._sse_clients:
            status_dict = self._build_status_dict(did)
            if status_dict:
                asyncio.ensure_future(self.broadcast_sse("status", status_dict))

    # --- Device resolution helper ---

    def _resolve_device_id(self, request) -> str | None:
        """Resolve device_id from query param.

        - If ?device_id=X is given, return X
        - If only one PDU is registered, auto-select it
        - Otherwise return None (ambiguous)
        """
        device_id = request.query.get("device_id")
        if device_id:
            return device_id
        # Auto-select if only one PDU
        if len(self._pdu_data) == 1:
            return next(iter(self._pdu_data))
        # Fall back to default if it has data
        if self._default_device_id in self._pdu_data:
            return self._default_device_id
        return None

    def _get_engine(self, device_id: str | None) -> AutomationEngine | None:
        """Get the automation engine for a device_id."""
        if device_id and device_id in self._engines:
            return self._engines[device_id]
        # Fallback: if only one engine, use it
        if len(self._engines) == 1:
            return next(iter(self._engines.values()))
        return None

    def _get_command_callback(self, device_id: str | None) -> CommandCallback | None:
        """Get the command callback for a device_id."""
        if device_id and device_id in self._device_command_callbacks:
            return self._device_command_callbacks[device_id]
        # Fallback to legacy callback
        return self._command_callback

    # --- Utility ---

    def _json(self, data, status=200):
        return web.Response(
            text=json.dumps(data),
            content_type="application/json",
            status=status,
        )

    def _parse_time_range(self, request) -> tuple[float, float]:
        """Parse start/end or range query params into timestamps."""
        now = time.time()
        range_str = request.query.get("range", "1h")
        if "start" in request.query and "end" in request.query:
            start = float(request.query["start"])
            end = float(request.query["end"])
            # Clamp to reasonable range
            if end - start > 90 * 86400:
                end = start + 90 * 86400
            return start, end
        seconds = RANGE_MAP.get(range_str, 3600)
        return now - seconds, now

    # --- Multi-PDU management endpoints ---

    async def _handle_list_pdus(self, request):
        """GET /api/pdus — list all registered PDUs with status summary."""
        now = time.time()

        # Get per-poller status if available
        poller_statuses = {}
        if self._poller_status_callback:
            try:
                for ps in self._poller_status_callback():
                    poller_statuses[ps["device_id"]] = ps
            except Exception:
                pass

        pdus = []
        for did, config in self._pdu_configs.items():
            data = self._pdu_data.get(did)
            data_time = self._pdu_data_times.get(did)
            data_age = round(now - data_time, 1) if data_time else None

            # Get poller detail if available
            poller_detail = poller_statuses.get(did, {})
            poller_state = poller_detail.get("state", "")

            # Determine health status with more detail
            status = "unknown"
            status_detail = ""
            if poller_state == "lost":
                status = "lost"
                scans = poller_detail.get("recovery_scans", 0)
                status_detail = f"PDU not found after {scans} recovery scans"
            elif poller_state == "recovering":
                status = "recovering"
                status_detail = "Scanning network for PDU at new IP address"
            elif poller_detail.get("serial_mismatch"):
                status = "error"
                status_detail = "Serial number mismatch — wrong PDU at this address"
            elif data_time is None:
                status = "no_data"
                status_detail = "Waiting for first successful poll"
            elif data_age is not None and data_age > 30:
                status = "degraded"
                failures = poller_detail.get("consecutive_failures", 0)
                transport = poller_detail.get("transport", "")
                if failures > 0:
                    status_detail = f"{transport.upper()} connection failed ({failures} consecutive errors)"
                else:
                    status_detail = f"Data is {data_age:.0f}s stale"
            else:
                status = "healthy"
                transport = poller_detail.get("transport", "")
                if transport:
                    status_detail = f"Polling via {transport.upper()}"

            identity = None
            if data and data.identity:
                identity = data.identity.to_dict()

            pdu_info = {
                "device_id": did,
                "config": config,
                "identity": identity,
                "status": status,
                "status_detail": status_detail,
                "data_age_seconds": data_age,
                "has_data": data is not None,
            }
            # Include transport and failure info
            if poller_detail:
                pdu_info["transport"] = poller_detail.get("transport", "")
                pdu_info["consecutive_failures"] = poller_detail.get("consecutive_failures", 0)
                pdu_info["poll_count"] = poller_detail.get("poll_count", 0)

            pdus.append(pdu_info)

        return self._json({"pdus": pdus, "count": len(pdus)})

    async def _handle_add_pdu(self, request):
        """POST /api/pdus — add a new PDU (writes pdus.json via callback)."""
        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        device_id = body.get("device_id") or body.get("host", "")
        if not device_id:
            return self._json({"error": "device_id or host is required"}, 400)

        if device_id in self._pdu_configs:
            return self._json({"error": f"PDU '{device_id}' already registered"}, 409)

        self._pdu_configs[device_id] = body

        if self._pdu_config_callback:
            try:
                await self._pdu_config_callback(self._pdu_configs)
            except Exception as e:
                logger.exception("Failed to persist PDU config")
                return self._json({"error": f"Config save failed: {e}"}, 500)

        if self._add_pdu_callback:
            try:
                await self._add_pdu_callback(body)
            except Exception as e:
                logger.exception("Failed to start runtime poller for %s", device_id)
                return self._json({"device_id": device_id, "ok": True,
                                   "warning": f"Saved but poller start failed: {e}"}, 201)

        return self._json({"device_id": device_id, "ok": True}, 201)

    async def _handle_update_pdu(self, request):
        """PUT /api/pdus/{device_id} — update PDU config."""
        device_id = request.match_info["device_id"]
        if device_id not in self._pdu_configs:
            return self._json({"error": f"PDU '{device_id}' not found"}, 404)

        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        self._pdu_configs[device_id] = body

        if self._pdu_config_callback:
            try:
                await self._pdu_config_callback(self._pdu_configs)
            except Exception as e:
                logger.exception("Failed to persist PDU config")
                return self._json({"error": f"Config save failed: {e}"}, 500)

        return self._json({"device_id": device_id, "ok": True})

    async def _handle_delete_pdu(self, request):
        """DELETE /api/pdus/{device_id} — remove a PDU."""
        device_id = request.match_info["device_id"]
        if device_id not in self._pdu_configs:
            return self._json({"error": f"PDU '{device_id}' not found"}, 404)

        if self._remove_pdu_callback:
            try:
                await self._remove_pdu_callback(device_id)
            except Exception as e:
                logger.exception("Failed to stop runtime poller for %s", device_id)

        self._pdu_configs.pop(device_id, None)
        self._pdu_data.pop(device_id, None)
        self._pdu_data_times.pop(device_id, None)
        self._engines.pop(device_id, None)
        self._device_command_callbacks.pop(device_id, None)

        if self._pdu_config_callback:
            try:
                await self._pdu_config_callback(self._pdu_configs)
            except Exception as e:
                logger.exception("Failed to persist PDU config")
                return self._json({"error": f"Config save failed: {e}"}, 500)

        return self._json({"device_id": device_id, "deleted": True})

    async def _handle_discover_pdus(self, request):
        """POST /api/pdus/discover — trigger network scan across all interfaces."""
        if not self._discovery_callback:
            return self._json({"error": "discovery not available"}, 503)

        try:
            results = await self._discovery_callback()
            # Support both old (list) and new (dict) callback return formats
            if isinstance(results, dict):
                return self._json(results)
            return self._json({"discovered": results})
        except Exception as e:
            logger.exception("PDU discovery failed")
            return self._json({"error": f"Discovery failed: {e}"}, 500)

    async def _handle_test_connection(self, request):
        """POST /api/pdus/test-connection — test SNMP connectivity to a host."""
        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        host = body.get("host", "").strip()
        if not host:
            return self._json({"error": "host is required"}, 400)

        community = body.get("community_read", "public")
        port = int(body.get("snmp_port", 161))

        if not self._test_connection_callback:
            return self._json({"error": "test connection not available"}, 503)

        try:
            result = await self._test_connection_callback(host, community, port)
            return self._json(result)
        except Exception as e:
            logger.exception("Test connection failed for %s", host)
            return self._json({"success": False, "error": str(e)}, 500)

    async def _handle_test_serial(self, request):
        """POST /api/pdus/test-serial — test serial port connectivity."""
        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        port = body.get("port", "").strip()
        if not port:
            return self._json({"error": "port is required"}, 400)

        username = body.get("username", "cyber")
        password = body.get("password", "cyber")

        if not self._test_serial_callback:
            return self._json({"error": "serial test not available"}, 503)

        try:
            result = await self._test_serial_callback(port, username, password)
            return self._json(result)
        except Exception as e:
            logger.exception("Test serial failed for %s", port)
            return self._json({"success": False, "error": str(e)}, 500)

    # --- Bridge config endpoints ---

    async def _handle_get_config(self, request):
        """GET /api/config — get bridge configuration (all settings)."""
        cfg = self._config
        config = {
            "poll_interval": cfg.poll_interval if cfg else getattr(self, "_poll_interval", 5),
            "port": self._port,
            "pdu_count": len(self._pdu_configs),
            "default_device_id": self._default_device_id,
            "mqtt_broker": cfg.mqtt_broker if cfg else "",
            "mqtt_port": cfg.mqtt_port if cfg else 1883,
            "mqtt_username": cfg.mqtt_username if cfg else "",
            "mqtt_has_password": bool(cfg.mqtt_password) if cfg else False,
            "log_level": cfg.log_level if cfg else "INFO",
            "history_retention_days": cfg.history_retention_days if cfg else 60,
            "auth_enabled": self._auth_enabled,
            "auth_username": self._auth_username,
            "snmp_timeout": cfg.snmp_timeout if cfg else 2.0,
            "snmp_retries": cfg.snmp_retries if cfg else 1,
            "recovery_enabled": cfg.recovery_enabled if cfg else True,
            "session_timeout": cfg.session_timeout if cfg else 86400,
            "reports_enabled": cfg.reports_enabled if cfg else True,
        }
        return self._json(config)

    async def _handle_update_config(self, request):
        """PUT /api/config — update bridge settings.

        Runtime-applied: poll_interval, log_level, history_retention_days.
        Persisted but requires restart: mqtt_*, web auth.
        """
        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        cfg = self._config
        if not cfg:
            return self._json({"error": "config not available"}, 503)

        updated = {}
        requires_restart = []

        # --- Runtime-applied settings ---
        if "poll_interval" in body:
            interval = body["poll_interval"]
            if not isinstance(interval, (int, float)) or interval < 0.1 or interval > 300:
                return self._json({"error": "poll_interval must be 0.1-300"}, 400)
            cfg.poll_interval = float(interval)
            self._poll_interval = float(interval)
            updated["poll_interval"] = cfg.poll_interval

        if "log_level" in body:
            level = str(body["log_level"]).upper()
            if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
                return self._json({"error": "log_level must be DEBUG/INFO/WARNING/ERROR"}, 400)
            cfg.log_level = level
            logging.getLogger().setLevel(getattr(logging, level, logging.INFO))
            updated["log_level"] = level

        if "history_retention_days" in body:
            days = body["history_retention_days"]
            if not isinstance(days, int) or days < 1 or days > 365:
                return self._json({"error": "history_retention_days must be 1-365"}, 400)
            cfg.history_retention_days = days
            if self._history:
                self._history.retention_days = days
            updated["history_retention_days"] = days

        # --- Restart-required settings ---
        if "mqtt_broker" in body:
            cfg.mqtt_broker = str(body["mqtt_broker"]).strip()
            updated["mqtt_broker"] = cfg.mqtt_broker
            requires_restart.append("mqtt_broker")

        if "mqtt_port" in body:
            port = body["mqtt_port"]
            if not isinstance(port, int) or port < 1 or port > 65535:
                return self._json({"error": "mqtt_port must be 1-65535"}, 400)
            cfg.mqtt_port = port
            updated["mqtt_port"] = port
            requires_restart.append("mqtt_port")

        if "mqtt_username" in body:
            cfg.mqtt_username = str(body["mqtt_username"]).strip()
            updated["mqtt_username"] = cfg.mqtt_username
            requires_restart.append("mqtt_username")

        if "mqtt_password" in body:
            cfg.mqtt_password = str(body["mqtt_password"])
            updated["mqtt_password"] = "(set)"
            requires_restart.append("mqtt_password")

        if "auth_username" in body:
            val = str(body["auth_username"]).strip()
            if val:
                cfg.web_username = val
                self._auth_username = val
                updated["auth_username"] = val

        if "auth_password" in body:
            val = str(body["auth_password"])
            cfg.web_password = val
            self._auth_password = val
            self._auth_enabled = bool(val)
            updated["auth_enabled"] = self._auth_enabled
            requires_restart.append("auth")

        # --- Advanced settings (runtime-applied) ---
        if "snmp_timeout" in body:
            val = body["snmp_timeout"]
            try:
                val = float(val)
            except (ValueError, TypeError):
                return self._json({"error": "snmp_timeout must be a number"}, 400)
            if val < 0.5 or val > 30:
                return self._json({"error": "snmp_timeout must be 0.5-30"}, 400)
            cfg.snmp_timeout = val
            updated["snmp_timeout"] = val

        if "snmp_retries" in body:
            val = body["snmp_retries"]
            try:
                val = int(val)
            except (ValueError, TypeError):
                return self._json({"error": "snmp_retries must be an integer"}, 400)
            if val < 0 or val > 5:
                return self._json({"error": "snmp_retries must be 0-5"}, 400)
            cfg.snmp_retries = val
            updated["snmp_retries"] = val

        if "recovery_enabled" in body:
            val = body["recovery_enabled"]
            if isinstance(val, bool):
                cfg.recovery_enabled = val
            else:
                cfg.recovery_enabled = str(val).lower() in ("true", "1", "yes")
            updated["recovery_enabled"] = cfg.recovery_enabled

        if "reports_enabled" in body:
            val = body["reports_enabled"]
            if isinstance(val, bool):
                cfg.reports_enabled = val
            else:
                cfg.reports_enabled = str(val).lower() in ("true", "1", "yes")
            updated["reports_enabled"] = cfg.reports_enabled

        if "session_timeout" in body:
            val = body["session_timeout"]
            try:
                val = int(val)
            except (ValueError, TypeError):
                return self._json({"error": "session_timeout must be an integer"}, 400)
            if val < 60 or val > 604800:
                return self._json({"error": "session_timeout must be 60-604800"}, 400)
            cfg.session_timeout = val
            self._session_timeout = val
            updated["session_timeout"] = val

        if not updated:
            return self._json({"error": "no valid config fields provided"}, 400)

        # Persist all settings to disk
        cfg.save_settings(cfg.settings_file)

        # Apply SNMP config changes to running pollers
        if ("snmp_timeout" in updated or "snmp_retries" in updated) and self._snmp_config_callback:
            try:
                asyncio.ensure_future(
                    self._snmp_config_callback(cfg.snmp_timeout, cfg.snmp_retries)
                )
            except Exception:
                logger.exception("Failed to apply SNMP config to pollers")

        # Track restart-required settings
        if requires_restart:
            self._restart_required = requires_restart

        result = {"updated": updated, "ok": True}
        if requires_restart:
            result["requires_restart"] = requires_restart
        return self._json(result)

    async def _handle_test_mqtt(self, request):
        """POST /api/config/test-mqtt — test MQTT broker connectivity.

        Tests connection with current saved settings, or with overrides
        provided in the request body (host, port, username, password).
        """
        import asyncio
        try:
            body = await request.json()
        except Exception:
            body = {}

        cfg = self._config
        host = body.get("host", cfg.mqtt_broker if cfg else "mosquitto")
        port = int(body.get("port", cfg.mqtt_port if cfg else 1883))
        username = body.get("username", cfg.mqtt_username if cfg else "")
        password = body.get("password", cfg.mqtt_password if cfg else "")

        if not host:
            return self._json({"success": False, "error": "No MQTT broker host configured"}, 400)

        # Test connection in a thread to avoid blocking the event loop
        def _test_mqtt():
            import paho.mqtt.client as mqtt
            result = {"success": False, "host": host, "port": port}
            connected_event = __import__("threading").Event()
            connect_rc = [None]

            def on_connect(client, userdata, flags, rc, properties=None):
                connect_rc[0] = rc
                connected_event.set()

            try:
                client = mqtt.Client(
                    client_id="pdu-bridge-test",
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                )
                client.on_connect = on_connect
                if username:
                    client.username_pw_set(username, password)
                client.connect(host, port, keepalive=5)
                client.loop_start()

                if connected_event.wait(timeout=5):
                    rc = connect_rc[0]
                    if hasattr(rc, 'value'):
                        rc = rc.value
                    if rc == 0:
                        result["success"] = True
                        result["message"] = "Connected successfully"
                    else:
                        rc_messages = {
                            1: "Incorrect protocol version",
                            2: "Invalid client identifier",
                            3: "Server unavailable",
                            4: "Bad username or password",
                            5: "Not authorized",
                        }
                        result["error"] = rc_messages.get(rc, f"Connection refused (code {rc})")
                else:
                    result["error"] = f"Connection timed out — broker at {host}:{port} not reachable"

                client.loop_stop()
                client.disconnect()
            except ConnectionRefusedError:
                result["error"] = f"Connection refused by {host}:{port}"
            except OSError as e:
                result["error"] = f"Network error: {e}"
            except Exception as e:
                result["error"] = str(e)
            return result

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _test_mqtt)
            return self._json(result)
        except Exception as e:
            return self._json({"success": False, "error": str(e)}, 500)

    # --- Device SNMP SET endpoints ---

    async def _handle_set_device_name(self, request):
        """PUT /api/device/name — set device name via SNMP SET callback."""
        device_id = self._resolve_device_id(request)
        if device_id is None:
            return self._json({"error": "device_id required (multiple PDUs registered)"}, 400)

        try:
            body = await request.json()
            name = body.get("name", "").strip()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        if not name:
            return self._json({"error": "name is required"}, 400)

        if not self._snmp_set_callback:
            return self._json({"error": "SNMP SET not available"}, 503)

        try:
            await self._snmp_set_callback(device_id, "device_name", name)
            return self._json({"device_id": device_id, "name": name, "ok": True})
        except Exception as e:
            logger.exception("Failed to set device name for %s", device_id)
            return self._json({"error": str(e)}, 500)

    async def _handle_set_device_location(self, request):
        """PUT /api/device/location — set sysLocation via SNMP SET callback."""
        device_id = self._resolve_device_id(request)
        if device_id is None:
            return self._json({"error": "device_id required (multiple PDUs registered)"}, 400)

        try:
            body = await request.json()
            location = body.get("location", "").strip()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        if not location:
            return self._json({"error": "location is required"}, 400)

        if not self._snmp_set_callback:
            return self._json({"error": "SNMP SET not available"}, 503)

        try:
            await self._snmp_set_callback(device_id, "sys_location", location)
            return self._json({"device_id": device_id, "location": location, "ok": True})
        except Exception as e:
            logger.exception("Failed to set device location for %s", device_id)
            return self._json({"error": str(e)}, 500)

    # --- Auth endpoints ---

    async def _handle_auth_login(self, request):
        """POST /api/auth/login — authenticate and create session."""
        if not self._auth_enabled:
            return self._json({"error": "Auth not enabled"}, 400)

        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        username = body.get("username", "")
        password = body.get("password", "")

        if username == self._auth_username and password == self._auth_password:
            token = self._create_session(username)
            resp = self._json({"ok": True, "username": username})
            resp.set_cookie(
                "session_token", token,
                max_age=self._session_timeout,
                httponly=True,
                samesite="Lax",
            )
            return resp

        return self._json({"error": "Invalid credentials"}, 401)

    async def _handle_auth_logout(self, request):
        """POST /api/auth/logout — invalidate session."""
        token = self._extract_token(request)
        if token:
            self._sessions.pop(token, None)
        resp = self._json({"ok": True})
        resp.del_cookie("session_token")
        return resp

    async def _handle_auth_status(self, request):
        """GET /api/auth/status — check auth state."""
        if not self._auth_enabled:
            return self._json({"auth_enabled": False, "authenticated": True})

        token = self._extract_token(request)
        if token and self._validate_session(token):
            session = self._sessions[token]
            return self._json({
                "auth_enabled": True,
                "authenticated": True,
                "username": session["username"],
            })

        return self._json({"auth_enabled": True, "authenticated": False})

    # --- Health endpoint ---

    async def _handle_health(self, request):
        """Health check endpoint for Docker HEALTHCHECK and monitoring."""
        now = time.time()

        # Aggregate health across all PDUs
        all_issues = []
        any_data = False

        for did in self._pdu_configs:
            data_time = self._pdu_data_times.get(did)
            if data_time is None:
                all_issues.append(f"[{did}] No data received yet")
            else:
                any_data = True
                data_age = now - data_time
                if data_age > 30:
                    all_issues.append(f"[{did}] Data is {data_age:.0f}s stale")

        # If no PDUs registered, check legacy data
        if not self._pdu_configs:
            data_age = now - self._last_data_time if self._last_data_time else None
            if data_age is None:
                all_issues.append("No data received yet")
            elif data_age > 30:
                all_issues.append(f"Data is {data_age:.0f}s stale")
            else:
                any_data = True

        subsystems = {
            "mqtt": self._mqtt.get_status() if self._mqtt else {"status": "unavailable"},
            "history": self._history.get_health() if self._history else {"status": "unavailable"},
        }

        if self._mqtt and not self._mqtt.get_status().get("connected"):
            all_issues.append("MQTT disconnected")

        if self._history:
            hist_health = self._history.get_health()
            if not hist_health.get("healthy"):
                all_issues.append("History write errors detected")

        healthy = len(all_issues) == 0 and any_data

        # Compute uptime from earliest data time
        earliest_time = None
        for dt in self._pdu_data_times.values():
            if earliest_time is None or dt < earliest_time:
                earliest_time = dt
        if earliest_time is None and self._last_data_time:
            earliest_time = self._last_data_time

        # Per-poller status details
        pollers = []
        if self._poller_status_callback:
            try:
                pollers = self._poller_status_callback()
            except Exception:
                logger.exception("Failed to get poller status")

        result = {
            "status": "healthy" if healthy else "degraded",
            "issues": all_issues,
            "pdu_count": len(self._pdu_configs) or (1 if self._last_data else 0),
            "subsystems": subsystems,
            "uptime_seconds": round(now - earliest_time, 1) if earliest_time else 0,
            "pollers": pollers,
        }

        if self._restart_required:
            result["restart_required"] = list(self._restart_required)

        status_code = 200 if healthy else 503
        return self._json(result, status_code)

    # --- SSE (Server-Sent Events) ---

    async def _handle_sse(self, request):
        """GET /api/stream — SSE endpoint for real-time push updates."""
        # Validate token if auth enabled
        if self._auth_enabled:
            token = request.query.get("token") or self._extract_token(request)
            if not token or not self._validate_session(token):
                return self._json({"error": "Authentication required"}, 401)

        response = web.StreamResponse()
        response.content_type = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["Access-Control-Allow-Origin"] = "*"
        await response.prepare(request)

        # Send initial connected event
        await response.write(b"event: connected\ndata: {}\n\n")
        self._sse_clients.append(response)

        try:
            while True:
                await asyncio.sleep(30)
                # Keepalive comment
                await response.write(b":\n\n")
        except (asyncio.CancelledError, ConnectionResetError, ConnectionError):
            pass
        finally:
            if response in self._sse_clients:
                self._sse_clients.remove(response)
        return response

    async def broadcast_sse(self, event_type: str, data: dict):
        """Send an SSE event to all connected clients."""
        if not self._sse_clients:
            return
        payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()
        dead = []
        for client in self._sse_clients:
            try:
                await client.write(payload)
            except Exception:
                dead.append(client)
        for client in dead:
            if client in self._sse_clients:
                self._sse_clients.remove(client)

    # --- Status dict builder ---

    def _build_status_dict(self, device_id: str) -> dict | None:
        """Build status dict for a device (shared by _handle_status and SSE)."""
        data = self._pdu_data.get(device_id)
        data_time = self._pdu_data_times.get(device_id)
        if data is None:
            return None

        inputs = {}
        for idx, bank in data.banks.items():
            inputs[str(idx)] = {
                "number": bank.number,
                "voltage": bank.voltage,
                "current": bank.current,
                "power": bank.power,
                "apparent_power": bank.apparent_power,
                "power_factor": bank.power_factor,
                "load_state": bank.load_state,
                "energy": bank.energy,
                "last_update": bank.last_update,
            }

        outlets = {}
        for n, outlet in data.outlets.items():
            outlets[str(n)] = {
                "number": outlet.number,
                "name": outlet.name,
                "state": outlet.state,
                "current": outlet.current,
                "power": outlet.power,
                "energy": outlet.energy,
                "bank_assignment": outlet.bank_assignment,
                "max_load": outlet.max_load,
            }

        total_power = sum(
            b.power for b in data.banks.values() if b.power is not None
        )
        active = sum(1 for o in data.outlets.values() if o.state == "on")

        preferred = data.ats_preferred_source
        current = data.ats_current_source

        result: dict[str, Any] = {
            "device": {
                "name": data.device_name,
                "id": device_id,
                "outlet_count": data.outlet_count,
                "phase_count": data.phase_count,
            },
            "ats": {
                "preferred_source": preferred,
                "preferred_label": ATS_SOURCE_MAP.get(preferred, "?"),
                "current_source": current,
                "current_label": ATS_SOURCE_MAP.get(current, "?"),
                "auto_transfer": data.ats_auto_transfer,
                "voltage_sensitivity": data.voltage_sensitivity,
                "transfer_voltage": data.transfer_voltage,
                "voltage_upper_limit": data.voltage_upper_limit,
                "voltage_lower_limit": data.voltage_lower_limit,
                "coldstart_delay": data.coldstart_delay,
                "coldstart_state": data.coldstart_state,
                "transferred": (
                    preferred is not None
                    and current is not None
                    and preferred != current
                ),
                "redundancy_ok": data.redundancy_ok,
                "source_a": {
                    "voltage": data.source_a.voltage if data.source_a else None,
                    "frequency": data.source_a.frequency if data.source_a else None,
                    "voltage_status": data.source_a.voltage_status if data.source_a else "unknown",
                },
                "source_b": {
                    "voltage": data.source_b.voltage if data.source_b else None,
                    "frequency": data.source_b.frequency if data.source_b else None,
                    "voltage_status": data.source_b.voltage_status if data.source_b else "unknown",
                },
            },
            "inputs": inputs,
            "outlets": outlets,
            "summary": {
                "total_power": round(total_power, 1),
                "total_load": data.total_load,
                "total_energy": data.total_energy,
                "input_voltage": data.input_voltage,
                "input_frequency": data.input_frequency,
                "active_outlets": active,
                "total_outlets": data.outlet_count,
            },
            "ts": time.time(),
        }

        # Environment block (conditional — only when sensor present)
        if data.environment and data.environment.sensor_present:
            result["environment"] = {
                "temperature": data.environment.temperature,
                "temperature_unit": data.environment.temperature_unit,
                "humidity": data.environment.humidity,
                "contacts": data.environment.contacts,
                "sensor_present": True,
            }

        # Identity block
        if data.identity:
            result["identity"] = data.identity.to_dict()

        # MQTT connection status
        if self._mqtt:
            result["mqtt"] = self._mqtt.get_status()

        # Data age
        if data_time:
            result["data_age_seconds"] = round(time.time() - data_time, 1)

        # Default credential warning from poller status
        if self._poller_status_callback:
            try:
                for ps in self._poller_status_callback():
                    if ps.get("device_id") == device_id:
                        if ps.get("default_credentials_active") is not None:
                            result["default_credentials_active"] = ps["default_credentials_active"]
                        break
            except Exception:
                pass

        return result

    # --- Status ---

    async def _handle_status(self, request):
        device_id = self._resolve_device_id(request)
        if device_id is None:
            if len(self._pdu_data) > 1:
                return self._json({
                    "error": "device_id required (multiple PDUs registered)",
                    "available_devices": list(self._pdu_data.keys()),
                }, 400)
            data = self._last_data
            did = self._default_device_id
        else:
            data = self._pdu_data.get(device_id)
            did = device_id

        if data is None:
            return self._json({"error": "no data yet"}, 503)

        result = self._build_status_dict(did)
        if result is None:
            return self._json({"error": "no data yet"}, 503)

        return self._json(result)

    # --- Rules (per-device automation) ---

    async def _handle_list_rules(self, request):
        device_id = self._resolve_device_id(request)
        engine = self._get_engine(device_id)
        if engine is None:
            return self._json({"error": "automation engine not available", "device_id": device_id}, 503)
        return self._json(engine.list_rules())

    async def _handle_create_rule(self, request):
        device_id = self._resolve_device_id(request)
        engine = self._get_engine(device_id)
        if engine is None:
            return self._json({"error": "automation engine not available", "device_id": device_id}, 503)
        try:
            body = await request.json()
            rule = engine.create_rule(body)
            return self._json(rule.to_dict(), 201)
        except ValueError as e:
            return self._json({"error": str(e)}, 409)
        except (KeyError, TypeError) as e:
            return self._json({"error": f"Invalid rule data: {e}"}, 400)

    async def _handle_update_rule(self, request):
        device_id = self._resolve_device_id(request)
        engine = self._get_engine(device_id)
        if engine is None:
            return self._json({"error": "automation engine not available", "device_id": device_id}, 503)
        name = request.match_info["name"]
        try:
            body = await request.json()
            rule = engine.update_rule(name, body)
            return self._json(rule.to_dict())
        except KeyError as e:
            return self._json({"error": str(e)}, 404)
        except (ValueError, TypeError) as e:
            return self._json({"error": f"Invalid rule data: {e}"}, 400)

    async def _handle_delete_rule(self, request):
        device_id = self._resolve_device_id(request)
        engine = self._get_engine(device_id)
        if engine is None:
            return self._json({"error": "automation engine not available", "device_id": device_id}, 503)
        name = request.match_info["name"]
        try:
            engine.delete_rule(name)
            return self._json({"deleted": name})
        except KeyError as e:
            return self._json({"error": str(e)}, 404)

    async def _handle_events(self, request):
        device_id = self._resolve_device_id(request)
        # Merge automation events + system events, sorted by timestamp desc
        events = []
        engine = self._get_engine(device_id)
        if engine is not None:
            events.extend(engine.get_events())
        events.extend(self.get_system_events(device_id or self._default_device_id))
        events.sort(key=lambda e: e.get("ts", 0), reverse=True)
        return self._json(events[:100])

    # --- Outlet command (per-device) ---

    async def _handle_outlet_command(self, request):
        device_id = self._resolve_device_id(request)
        if device_id is None and len(self._pdu_data) > 1:
            return self._json({
                "error": "device_id required (multiple PDUs registered)",
                "available_devices": list(self._pdu_data.keys()),
            }, 400)

        try:
            n = int(request.match_info["n"])
        except ValueError:
            return self._json({"error": "invalid outlet number"}, 400)
        try:
            body = await request.json()
            action = body.get("action", "").lower()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        valid_actions = ("on", "off", "reboot", "delayon", "delayoff", "cancel")
        if action not in valid_actions:
            return self._json({"error": f"invalid action: {action}"}, 400)

        callback = self._get_command_callback(device_id)
        if not callback:
            return self._json({"error": "command handler not available"}, 503)

        try:
            await callback(n, action)
            return self._json({"outlet": n, "action": action, "device_id": device_id, "ok": True})
        except Exception as e:
            logger.exception("Outlet command failed: device %s outlet %d action %s",
                             device_id, n, action)
            return self._json({"outlet": n, "action": action, "ok": False,
                               "error": str(e)}, 500)

    # --- History (multi-PDU aware) ---

    async def _handle_history_banks(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        device_id = self._resolve_device_id(request)
        start, end = self._parse_time_range(request)
        rows = self._history.query_banks(start, end, device_id=device_id)
        return self._json(rows)

    async def _handle_history_outlets(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        device_id = self._resolve_device_id(request)
        start, end = self._parse_time_range(request)
        rows = self._history.query_outlets(start, end, device_id=device_id)
        return self._json(rows)

    async def _handle_history_banks_csv(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        device_id = self._resolve_device_id(request)
        start, end = self._parse_time_range(request)
        rows = self._history.query_banks(start, end, device_id=device_id)
        return self._csv_response(rows, "bank_history.csv",
                                  ["bucket", "bank", "voltage", "current", "power", "apparent", "pf"])

    async def _handle_history_outlets_csv(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        device_id = self._resolve_device_id(request)
        start, end = self._parse_time_range(request)
        rows = self._history.query_outlets(start, end, device_id=device_id)
        return self._csv_response(rows, "outlet_history.csv",
                                  ["bucket", "outlet", "current", "power", "energy"])

    def _csv_response(self, rows: list[dict], filename: str, fields: list[str]):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return web.Response(
            text=output.getvalue(),
            content_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # --- Energy Rollups (multi-PDU aware) ---

    def _parse_date_range(self, request) -> tuple[str, str]:
        """Parse start/end date query params (YYYY-MM-DD)."""
        from datetime import datetime as dt, timedelta
        now = dt.now()
        start = request.query.get("start", (now - timedelta(days=30)).strftime("%Y-%m-%d"))
        end = request.query.get("end", now.strftime("%Y-%m-%d"))
        return start, end

    def _parse_month_range(self, request) -> tuple[str, str]:
        """Parse start/end month query params (YYYY-MM)."""
        from datetime import datetime as dt, timedelta
        now = dt.now()
        start = request.query.get("start", (now - timedelta(days=365)).strftime("%Y-%m"))
        end = request.query.get("end", now.strftime("%Y-%m"))
        return start, end

    async def _handle_energy_daily(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        device_id = self._resolve_device_id(request) or ""
        start, end = self._parse_date_range(request)
        rows = self._history.query_energy_daily_all(start, end, device_id)
        return self._json(rows)

    async def _handle_energy_monthly(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        device_id = self._resolve_device_id(request) or ""
        start, end = self._parse_month_range(request)
        rows = self._history.query_energy_monthly_all(start, end, device_id)
        return self._json(rows)

    async def _handle_energy_daily_csv(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        device_id = self._resolve_device_id(request) or ""
        start, end = self._parse_date_range(request)
        rows = self._history.query_energy_daily_all(start, end, device_id)
        return self._csv_response(
            rows, "energy_daily.csv",
            ["date", "device_id", "source", "outlet", "kwh", "peak_power_w", "avg_power_w", "samples"],
        )

    async def _handle_energy_monthly_csv(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        device_id = self._resolve_device_id(request) or ""
        start, end = self._parse_month_range(request)
        rows = self._history.query_energy_monthly_all(start, end, device_id)
        return self._csv_response(
            rows, "energy_monthly.csv",
            ["month", "device_id", "source", "outlet", "kwh", "peak_power_w", "avg_power_w", "days"],
        )

    async def _handle_energy_summary(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        device_id = self._resolve_device_id(request) or ""
        summary = self._history.get_energy_summary(device_id)
        return self._json(summary)

    # --- PDF Reports ---

    async def _handle_list_reports(self, request):
        """GET /api/reports — list available PDF reports."""
        if not self._report_list_callback:
            return self._json({"error": "reports not available"}, 503)
        device_id = request.query.get("device_id")
        reports = await self._report_list_callback(device_id)
        return self._json({"reports": reports, "count": len(reports)})

    async def _handle_generate_report(self, request):
        """POST /api/reports/generate — on-demand report generation."""
        if not self._report_generate_callback:
            return self._json({"error": "reports not available"}, 503)
        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        result = await self._report_generate_callback(body)
        if result.get("error"):
            return self._json(result, 400)
        return self._json(result)

    async def _handle_download_report(self, request):
        """GET /api/reports/download/{filename} — download a PDF report."""
        from .report_generator import get_report_path
        filename = request.match_info["filename"]
        reports_dir = self._config.reports_dir if self._config else "/data/reports"
        path = get_report_path(filename, reports_dir)
        if not path:
            return self._json({"error": "Report not found"}, 404)
        return web.FileResponse(
            path,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "application/pdf",
            },
        )

    # --- Outlet naming ---

    async def _handle_rename_outlet(self, request):
        try:
            n = int(request.match_info["n"])
        except ValueError:
            return self._json({"error": "invalid outlet number"}, 400)
        try:
            body = await request.json()
            name = body.get("name", "").strip()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        if name:
            self.outlet_names[str(n)] = name
        else:
            self.outlet_names.pop(str(n), None)

        if self._outlet_names_callback:
            self._outlet_names_callback(self.outlet_names)

        return self._json({"outlet": n, "name": name, "ok": True})

    async def _handle_get_outlet_names(self, request):
        return self._json(self.outlet_names)

    # --- PDU Management endpoints (serial-specific) ---

    async def _call_management(self, name: str, *args, **kwargs):
        """Call a named management callback, returning result or error dict."""
        cb = self._management_callbacks.get(name)
        if not cb:
            return None
        return await cb(*args, **kwargs)

    async def _handle_get_network(self, request):
        """GET /api/pdu/network — PDU network config."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("get_network_config")
        if not cb:
            return self._json({"error": "Serial transport required", "available": False}, 503)
        try:
            result = await cb(device_id)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_get_thresholds(self, request):
        """GET /api/pdu/thresholds — device + bank thresholds."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("get_thresholds")
        if not cb:
            return self._json({"error": "Serial transport required", "available": False}, 503)
        try:
            result = await cb(device_id)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_device_thresholds(self, request):
        """PUT /api/pdu/thresholds/device — set device-level thresholds."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("set_device_threshold")
        if not cb:
            return self._json({"error": "Serial transport required", "available": False}, 503)
        try:
            body = await request.json()
            result = await cb(device_id, body)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_bank_thresholds(self, request):
        """PUT /api/pdu/thresholds/bank/{n} — set bank thresholds."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("set_bank_threshold")
        if not cb:
            return self._json({"error": "Serial transport required", "available": False}, 503)
        try:
            bank = int(request.match_info["n"])
            body = await request.json()
            result = await cb(device_id, bank, body)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_get_outlet_config(self, request):
        """GET /api/pdu/outlets/config — outlet configuration."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("get_outlet_config")
        if not cb:
            return self._json({"error": "Serial transport required", "available": False}, 503)
        try:
            result = await cb(device_id)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_outlet_config(self, request):
        """PUT /api/pdu/outlets/{n}/config — set outlet configuration.

        Body fields: name, on_delay, off_delay, reboot_duration.
        All are optional — only provided fields are changed.
        """
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("set_outlet_config")
        if not cb:
            return self._json({"error": "Serial transport required", "available": False}, 503)
        try:
            outlet = int(request.match_info["n"])
            body = await request.json()
            result = await cb(device_id, outlet, body)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_get_eventlog(self, request):
        """GET /api/pdu/eventlog — PDU hardware event log."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("get_eventlog")
        if not cb:
            return self._json({"error": "Serial transport required", "available": False}, 503)
        try:
            result = await cb(device_id)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_security_check(self, request):
        """POST /api/pdu/security/check — check default credentials."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("check_credentials")
        if not cb:
            return self._json({"error": "Serial transport required", "available": False}, 503)
        try:
            result = await cb(device_id)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_change_password(self, request):
        """POST /api/pdu/security/password — change PDU password."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("change_password")
        if not cb:
            return self._json({"error": "Serial transport required", "available": False}, 503)
        try:
            body = await request.json()
            account = body.get("account", "admin")
            password = body.get("password", "")
            if not password:
                return self._json({"error": "password is required"}, 400)
            result = await cb(device_id, account, password)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # --- ATS Configuration endpoints ---

    async def _handle_get_ats_config(self, request):
        """GET /api/pdu/ats/config — ATS source config + coldstart."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("get_ats_config")
        if not cb:
            return self._json({"error": "Serial transport required", "available": False}, 503)
        try:
            result = await cb(device_id)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_ats_preferred(self, request):
        """PUT /api/pdu/ats/preferred-source — set ATS preferred source."""
        device_id = self._resolve_device_id(request)
        try:
            body = await request.json()
            source = body.get("source", "").upper()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)
        if source not in ("A", "B"):
            return self._json({"error": "source must be 'A' or 'B'"}, 400)

        cb = self._management_callbacks.get("set_preferred_source")
        if not cb:
            return self._json({"error": "Not available"}, 503)
        try:
            result = await cb(device_id, source)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_ats_auto_transfer(self, request):
        """PUT /api/pdu/ats/auto-transfer — set ATS auto-transfer."""
        device_id = self._resolve_device_id(request)
        try:
            body = await request.json()
            enabled = body.get("enabled", True)
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        cb = self._management_callbacks.get("set_auto_transfer")
        if not cb:
            return self._json({"error": "Not available"}, 503)
        try:
            result = await cb(device_id, bool(enabled))
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_ats_sensitivity(self, request):
        """PUT /api/pdu/ats/sensitivity — set voltage sensitivity."""
        device_id = self._resolve_device_id(request)
        try:
            body = await request.json()
            sensitivity = body.get("sensitivity", "").lower()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)
        if sensitivity not in ("normal", "high", "low"):
            return self._json({"error": "sensitivity must be 'normal', 'high', or 'low'"}, 400)

        cb = self._management_callbacks.get("set_voltage_sensitivity")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id, sensitivity)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_ats_voltage_limits(self, request):
        """PUT /api/pdu/ats/voltage-limits — set transfer voltage limits."""
        device_id = self._resolve_device_id(request)
        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        upper = body.get("upper")
        lower = body.get("lower")
        if upper is None and lower is None:
            return self._json({"error": "upper and/or lower required"}, 400)

        cb = self._management_callbacks.get("set_transfer_voltage")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id, upper, lower)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_ats_coldstart(self, request):
        """PUT /api/pdu/ats/coldstart — set coldstart delay and state."""
        device_id = self._resolve_device_id(request)
        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        cb = self._management_callbacks.get("set_coldstart")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id, body)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # --- Network config write ---

    async def _handle_set_network(self, request):
        """PUT /api/pdu/network — write network configuration (requires confirm)."""
        device_id = self._resolve_device_id(request)
        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        if not body.get("confirm"):
            return self._json({"error": "confirm: true required (network changes may cause connectivity loss)"}, 400)

        cb = self._management_callbacks.get("set_network_config")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id, body)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # --- User management ---

    async def _handle_get_users(self, request):
        """GET /api/pdu/users — user account listing."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("get_users")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # --- sysContact write ---

    async def _handle_set_device_contact(self, request):
        """PUT /api/device/contact — set sysContact via SNMP SET."""
        device_id = self._resolve_device_id(request)
        if device_id is None:
            return self._json({"error": "device_id required"}, 400)
        try:
            body = await request.json()
            contact = body.get("contact", "").strip()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)
        if not contact:
            return self._json({"error": "contact is required"}, 400)
        if not self._snmp_set_callback:
            return self._json({"error": "SNMP SET not available"}, 503)
        try:
            await self._snmp_set_callback(device_id, "sys_contact", contact)
            return self._json({"device_id": device_id, "contact": contact, "ok": True})
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # --- Notification configuration ---

    async def _handle_get_notifications(self, request):
        """GET /api/pdu/notifications — aggregated notification config."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("get_notifications")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_trap(self, request):
        """PUT /api/pdu/notifications/traps/{index} — configure trap receiver."""
        device_id = self._resolve_device_id(request)
        try:
            index = int(request.match_info["index"])
            body = await request.json()
        except (ValueError, Exception):
            return self._json({"error": "invalid request"}, 400)
        cb = self._management_callbacks.get("set_trap_receiver")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id, index, body)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_get_smtp(self, request):
        """GET /api/pdu/notifications/smtp — SMTP config."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("get_smtp_config")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_smtp(self, request):
        """PUT /api/pdu/notifications/smtp — configure SMTP."""
        device_id = self._resolve_device_id(request)
        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)
        cb = self._management_callbacks.get("set_smtp_config")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id, body)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_email(self, request):
        """PUT /api/pdu/notifications/email/{index} — configure email recipient."""
        device_id = self._resolve_device_id(request)
        try:
            index = int(request.match_info["index"])
            body = await request.json()
        except (ValueError, Exception):
            return self._json({"error": "invalid request"}, 400)
        cb = self._management_callbacks.get("set_email_recipient")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id, index, body)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_syslog(self, request):
        """PUT /api/pdu/notifications/syslog/{index} — configure syslog server."""
        device_id = self._resolve_device_id(request)
        try:
            index = int(request.match_info["index"])
            body = await request.json()
        except (ValueError, Exception):
            return self._json({"error": "invalid request"}, 400)
        cb = self._management_callbacks.get("set_syslog_server")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id, index, body)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # --- EnergyWise ---

    async def _handle_get_energywise(self, request):
        """GET /api/pdu/energywise — EnergyWise configuration."""
        device_id = self._resolve_device_id(request)
        cb = self._management_callbacks.get("get_energywise")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    async def _handle_set_energywise(self, request):
        """PUT /api/pdu/energywise — configure EnergyWise."""
        device_id = self._resolve_device_id(request)
        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)
        cb = self._management_callbacks.get("set_energywise")
        if not cb:
            return self._json({"error": "Serial transport required"}, 503)
        try:
            result = await cb(device_id, body)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # --- Automation toggle ---

    async def _handle_toggle_rule(self, request):
        """PUT /api/rules/{name}/toggle — enable/disable a rule."""
        device_id = self._resolve_device_id(request)
        engine = self._get_engine(device_id)
        if engine is None:
            return self._json({"error": "automation engine not available"}, 503)
        name = request.match_info["name"]
        try:
            result = engine.toggle_rule(name)
            return self._json(result)
        except KeyError as e:
            return self._json({"error": str(e)}, 404)

    # --- System Management ---

    async def _handle_restart(self, request):
        """POST /api/system/restart — trigger graceful bridge restart."""
        logger.info("Bridge restart requested via web UI")

        async def _delayed_kill():
            await asyncio.sleep(0.5)
            os.kill(os.getpid(), signal.SIGTERM)

        asyncio.ensure_future(_delayed_kill())
        return self._json({"ok": True, "message": "Restarting bridge..."})

    async def _handle_system_info(self, request):
        """GET /api/system/info — system information."""
        now = time.time()
        uptime = now - self._start_time

        # DB size
        db_path = self._history._db_path if self._history and hasattr(self._history, '_db_path') else None
        db_size_bytes = 0
        db_size = "unknown"
        if db_path:
            try:
                db_size_bytes = os.path.getsize(db_path)
                if db_size_bytes < 1024 * 1024:
                    db_size = f"{db_size_bytes / 1024:.1f} KB"
                else:
                    db_size = f"{db_size_bytes / (1024 * 1024):.1f} MB"
            except OSError:
                pass

        # Total polls from pollers
        total_polls = 0
        if self._poller_status_callback:
            try:
                for ps in self._poller_status_callback():
                    total_polls += ps.get("poll_count", 0)
            except Exception:
                pass

        return self._json({
            "version": self._bridge_version,
            "python_version": platform.python_version(),
            "uptime_seconds": round(uptime, 1),
            "db_size": db_size,
            "db_size_bytes": db_size_bytes,
            "pdu_count": len(self._pdu_configs) or (1 if self._last_data else 0),
            "total_polls": total_polls,
            "in_docker": Path("/.dockerenv").exists(),
            "mqtt_connected": self._mqtt.get_status().get("connected") if self._mqtt else False,
            "sse_clients": len(self._sse_clients),
        })

    async def _handle_system_logs(self, request):
        """GET /api/system/logs — retrieve log records from ring buffer."""
        if not self._log_buffer:
            return self._json({"error": "log buffer not available"}, 503)

        level = request.query.get("level")
        limit = min(int(request.query.get("limit", "200")), 1000)
        search = request.query.get("search")

        records = self._log_buffer.get_records(level=level, limit=limit, search=search)
        return self._json({"logs": records, "count": len(records)})

    async def _handle_backup(self, request):
        """GET /api/system/backup — export all config files as JSON."""
        data_dir = Path("/data")
        files = {}

        # Whitelist of config file patterns
        patterns = ["pdus.json", "bridge_settings.json", "rules*.json", "outlet_names*.json"]
        for pattern in patterns:
            for path in data_dir.glob(pattern):
                if path.is_file():
                    try:
                        files[path.name] = json.loads(path.read_text())
                    except (json.JSONDecodeError, OSError):
                        # Store raw text for non-JSON files
                        try:
                            files[path.name] = path.read_text()
                        except OSError:
                            pass

        backup = {
            "version": 1,
            "timestamp": time.time(),
            "files": files,
        }

        resp = web.Response(
            text=json.dumps(backup, indent=2),
            content_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="cyberpdu_backup.json"'},
        )
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    async def _handle_restore(self, request):
        """POST /api/system/restore — import config from backup JSON."""
        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        if not isinstance(body, dict) or "files" not in body:
            return self._json({"error": "invalid backup format (missing 'files')"}, 400)

        # Whitelist allowed filenames to prevent path traversal
        allowed_prefixes = ("pdus", "bridge_settings", "rules", "outlet_names")
        data_dir = Path("/data")
        data_dir.mkdir(parents=True, exist_ok=True)

        restored = []
        for filename, content in body["files"].items():
            # Security: validate filename
            if not any(filename.startswith(p) for p in allowed_prefixes):
                continue
            if "/" in filename or "\\" in filename or ".." in filename:
                continue
            if not filename.endswith(".json"):
                continue

            path = data_dir / filename
            try:
                if isinstance(content, (dict, list)):
                    path.write_text(json.dumps(content, indent=2))
                else:
                    path.write_text(str(content))
                restored.append(filename)
            except OSError as e:
                logger.error("Failed to restore %s: %s", filename, e)

        if restored:
            self._restart_required = ["config_restored"]

        return self._json({"ok": True, "restored": restored})

    # --- Static ---

    async def _handle_index(self, request):
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            return web.Response(text="index.html not found", status=404)
        return web.FileResponse(index_file)

    async def _handle_favicon(self, request):
        favicon_file = STATIC_DIR / "favicon.svg"
        if not favicon_file.exists():
            return web.Response(status=404)
        return web.FileResponse(favicon_file)

    async def start(self):
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        logger.info("Web UI started on http://0.0.0.0:%d", self._port)

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()
