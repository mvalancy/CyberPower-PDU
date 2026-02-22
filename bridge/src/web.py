# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""Web UI and REST API server for PDU automation — multi-PDU support."""

import csv
import io
import json
import logging
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
                 mqtt=None, history=None):
        self._default_device_id = device_id
        self._port = port
        self._mqtt = mqtt
        self._history = history

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

        self.outlet_names: dict[str, str] = {}
        self._app = web.Application(middlewares=[cors_middleware])
        self._runner: web.AppRunner | None = None
        self._setup_routes()

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

    def register_automation_engine(self, device_id: str, engine: AutomationEngine):
        """Register a per-device automation engine."""
        self._engines[device_id] = engine

    def register_pdu(self, device_id: str, pdu_config_dict: dict[str, Any]):
        """Register a PDU's config info (host, community, label, etc.)."""
        self._pdu_configs[device_id] = pdu_config_dict

    # --- Route setup ---

    def _setup_routes(self):
        # Multi-PDU management
        self._app.router.add_get("/api/pdus", self._handle_list_pdus)
        self._app.router.add_post("/api/pdus", self._handle_add_pdu)
        self._app.router.add_put("/api/pdus/{device_id}", self._handle_update_pdu)
        self._app.router.add_delete("/api/pdus/{device_id}", self._handle_delete_pdu)
        self._app.router.add_post("/api/pdus/discover", self._handle_discover_pdus)

        # Bridge config
        self._app.router.add_get("/api/config", self._handle_get_config)
        self._app.router.add_put("/api/config", self._handle_update_config)

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

        # Reports
        self._app.router.add_get("/api/reports", self._handle_list_reports)
        self._app.router.add_get("/api/reports/latest", self._handle_latest_report)
        self._app.router.add_get("/api/reports/{id}", self._handle_get_report)

        # Outlet naming
        self._app.router.add_put("/api/outlets/{n}/name", self._handle_rename_outlet)
        self._app.router.add_get("/api/outlet-names", self._handle_get_outlet_names)

        # Static
        self._app.router.add_get("/", self._handle_index)

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
        pdus = []
        for did, config in self._pdu_configs.items():
            data = self._pdu_data.get(did)
            data_time = self._pdu_data_times.get(did)
            data_age = round(now - data_time, 1) if data_time else None

            # Determine health status
            status = "unknown"
            if data_time is None:
                status = "no_data"
            elif data_age is not None and data_age > 30:
                status = "degraded"
            else:
                status = "healthy"

            identity = None
            if data and data.identity:
                identity = data.identity.to_dict()

            pdus.append({
                "device_id": did,
                "config": config,
                "identity": identity,
                "status": status,
                "data_age_seconds": data_age,
                "has_data": data is not None,
            })

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
        """POST /api/pdus/discover — trigger network scan."""
        if not self._discovery_callback:
            return self._json({"error": "discovery not available"}, 503)

        try:
            results = await self._discovery_callback()
            return self._json({"discovered": results})
        except Exception as e:
            logger.exception("PDU discovery failed")
            return self._json({"error": f"Discovery failed: {e}"}, 500)

    # --- Bridge config endpoints ---

    async def _handle_get_config(self, request):
        """GET /api/config — get bridge configuration."""
        config = {
            "poll_interval": getattr(self, "_poll_interval", 5),
            "port": self._port,
            "pdu_count": len(self._pdu_configs),
            "default_device_id": self._default_device_id,
        }
        return self._json(config)

    async def _handle_update_config(self, request):
        """PUT /api/config — update poll_interval at runtime."""
        try:
            body = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)

        updated = {}
        if "poll_interval" in body:
            interval = body["poll_interval"]
            if not isinstance(interval, (int, float)) or interval < 1:
                return self._json({"error": "poll_interval must be >= 1"}, 400)
            self._poll_interval = interval
            updated["poll_interval"] = interval

        if not updated:
            return self._json({"error": "no valid config fields provided"}, 400)

        return self._json({"updated": updated, "ok": True})

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

        result = {
            "status": "healthy" if healthy else "degraded",
            "issues": all_issues,
            "pdu_count": len(self._pdu_configs) or (1 if self._last_data else 0),
            "subsystems": subsystems,
            "uptime_seconds": round(now - earliest_time, 1) if earliest_time else 0,
        }

        status_code = 200 if healthy else 503
        return self._json(result, status_code)

    # --- Status ---

    async def _handle_status(self, request):
        device_id = self._resolve_device_id(request)
        if device_id is None:
            # If no device_id resolved and we have multiple, return multi-status summary
            if len(self._pdu_data) > 1:
                return self._json({
                    "error": "device_id required (multiple PDUs registered)",
                    "available_devices": list(self._pdu_data.keys()),
                }, 400)
            # Fallback to legacy
            data = self._last_data
            data_time = self._last_data_time
            did = self._default_device_id
        else:
            data = self._pdu_data.get(device_id)
            data_time = self._pdu_data_times.get(device_id)
            did = device_id

        if data is None:
            return self._json({"error": "no data yet"}, 503)

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
                "id": did,
                "outlet_count": data.outlet_count,
                "phase_count": data.phase_count,
            },
            "ats": {
                "preferred_source": preferred,
                "preferred_label": ATS_SOURCE_MAP.get(preferred, "?"),
                "current_source": current,
                "current_label": ATS_SOURCE_MAP.get(current, "?"),
                "auto_transfer": data.ats_auto_transfer,
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
                "input_voltage": data.input_voltage,
                "input_frequency": data.input_frequency,
                "active_outlets": active,
                "total_outlets": data.outlet_count,
            },
            "ts": time.time(),
        }

        # Identity block from PDUData.identity
        if data.identity:
            result["identity"] = data.identity.to_dict()

        # MQTT connection status
        if self._mqtt:
            result["mqtt"] = self._mqtt.get_status()

        # Data age
        if data_time:
            result["data_age_seconds"] = round(time.time() - data_time, 1)

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
        engine = self._get_engine(device_id)
        if engine is None:
            return self._json({"error": "automation engine not available", "device_id": device_id}, 503)
        return self._json(engine.get_events())

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

        if action not in ("on", "off", "reboot"):
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

    # --- Reports (multi-PDU aware) ---

    async def _handle_list_reports(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        device_id = self._resolve_device_id(request)
        return self._json(self._history.list_reports(device_id=device_id))

    async def _handle_latest_report(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        device_id = self._resolve_device_id(request)
        report = self._history.get_latest_report(device_id=device_id)
        if not report:
            return self._json({"error": "no reports yet"}, 404)
        return self._json(report)

    async def _handle_get_report(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        try:
            report_id = int(request.match_info["id"])
        except ValueError:
            return self._json({"error": "invalid report id"}, 400)
        report = self._history.get_report(report_id)
        if not report:
            return self._json({"error": "report not found"}, 404)
        return self._json(report)

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

    # --- Static ---

    async def _handle_index(self, request):
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            return web.Response(text="index.html not found", status=404)
        return web.FileResponse(index_file)

    async def start(self):
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        logger.info("Web UI started on http://0.0.0.0:%d", self._port)

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()
