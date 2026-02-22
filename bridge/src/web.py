"""Web UI and REST API server for PDU automation."""

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
    def __init__(self, engine: AutomationEngine, device_id: str, port: int = 8080,
                 mqtt=None, history=None):
        self._engine = engine
        self._device_id = device_id
        self._port = port
        self._last_data: PDUData | None = None
        self._command_callback: CommandCallback | None = None
        self._outlet_names_callback: OutletNamesCallback | None = None
        self._mqtt = mqtt
        self._history = history
        self.outlet_names: dict[str, str] = {}
        self._app = web.Application(middlewares=[cors_middleware])
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    def set_command_callback(self, callback: CommandCallback):
        self._command_callback = callback

    def set_outlet_names_callback(self, callback: OutletNamesCallback):
        self._outlet_names_callback = callback

    def _setup_routes(self):
        self._app.router.add_get("/api/status", self._handle_status)
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

    def update_data(self, data: PDUData):
        self._last_data = data

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
            return float(request.query["start"]), float(request.query["end"])
        seconds = RANGE_MAP.get(range_str, 3600)
        return now - seconds, now

    async def _handle_status(self, request):
        data = self._last_data
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
                "id": self._device_id,
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

        # MQTT connection status
        if self._mqtt:
            result["mqtt"] = self._mqtt.get_status()

        return self._json(result)

    async def _handle_list_rules(self, request):
        return self._json(self._engine.list_rules())

    async def _handle_create_rule(self, request):
        try:
            body = await request.json()
            rule = self._engine.create_rule(body)
            return self._json(rule.to_dict(), 201)
        except ValueError as e:
            return self._json({"error": str(e)}, 409)
        except (KeyError, TypeError) as e:
            return self._json({"error": f"Invalid rule data: {e}"}, 400)

    async def _handle_update_rule(self, request):
        name = request.match_info["name"]
        try:
            body = await request.json()
            rule = self._engine.update_rule(name, body)
            return self._json(rule.to_dict())
        except KeyError as e:
            return self._json({"error": str(e)}, 404)
        except (ValueError, TypeError) as e:
            return self._json({"error": f"Invalid rule data: {e}"}, 400)

    async def _handle_delete_rule(self, request):
        name = request.match_info["name"]
        try:
            self._engine.delete_rule(name)
            return self._json({"deleted": name})
        except KeyError as e:
            return self._json({"error": str(e)}, 404)

    async def _handle_events(self, request):
        return self._json(self._engine.get_events())

    async def _handle_outlet_command(self, request):
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
        if not self._command_callback:
            return self._json({"error": "command handler not available"}, 503)

        await self._command_callback(n, action)
        return self._json({"outlet": n, "action": action, "ok": True})

    # --- History ---

    async def _handle_history_banks(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        start, end = self._parse_time_range(request)
        rows = self._history.query_banks(start, end)
        return self._json(rows)

    async def _handle_history_outlets(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        start, end = self._parse_time_range(request)
        rows = self._history.query_outlets(start, end)
        return self._json(rows)

    async def _handle_history_banks_csv(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        start, end = self._parse_time_range(request)
        rows = self._history.query_banks(start, end)
        return self._csv_response(rows, "bank_history.csv",
                                  ["bucket", "bank", "voltage", "current", "power", "apparent", "pf"])

    async def _handle_history_outlets_csv(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        start, end = self._parse_time_range(request)
        rows = self._history.query_outlets(start, end)
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

    # --- Reports ---

    async def _handle_list_reports(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        return self._json(self._history.list_reports())

    async def _handle_latest_report(self, request):
        if not self._history:
            return self._json({"error": "history not available"}, 503)
        report = self._history.get_latest_report()
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
