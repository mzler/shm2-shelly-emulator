"""HTTP server and Ingress web dashboard for Shelly Pro 3EM emulator & Solarbank Controller."""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

try:
    from aiohttp import web
except ImportError:
    web = None  # type: ignore

from src.config import AppConfig
from src.shelly_service import ShellyService

if TYPE_CHECKING:
    from src.controller import AutomationController
    from src.ha_client import HomeAssistantClient

logger = logging.getLogger(__name__)


def parse_query_params(query: Any) -> Dict[str, Any]:
    """Parse query parameters into typed Python values (int, float, bool, str)."""
    params: Dict[str, Any] = {}
    for k, v in query.items():
        v_str = str(v).strip()
        if v_str.lower() == "true":
            params[k] = True
        elif v_str.lower() == "false":
            params[k] = False
        elif v_str.isdigit():
            params[k] = int(v_str)
        else:
            try:
                params[k] = float(v_str)
            except ValueError:
                params[k] = v_str
    return params


class WebServer:
    """HTTP web server handling Shelly JSON-RPC requests, Ingress dashboard, and automation APIs."""

    def __init__(
        self,
        config: AppConfig,
        shelly_service: ShellyService,
        automation_controller: Optional[Any] = None,
        ha_client: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.shelly_service = shelly_service
        self.automation_controller: Optional[AutomationController] = automation_controller
        self.ha_client: Optional[HomeAssistantClient] = ha_client

        self.app: Optional[web.Application] = None
        self.runner_http: Optional[web.AppRunner] = None
        self.runner_ingress: Optional[web.AppRunner] = None

    def setup_routes(self, app: web.Application) -> None:
        """Register HTTP routes for Shelly RPC and dashboard."""
        # Shelly JSON-RPC Endpoints
        app.router.add_post("/rpc", self.handle_post_rpc)
        app.router.add_get("/rpc/{method}", self.handle_get_rpc_method)
        app.router.add_post("/rpc/{method}", self.handle_post_rpc_method)
        app.router.add_get("/rpc", self.handle_get_rpc_root)

        # Legacy Gen1 / direct endpoints for compatibility
        app.router.add_get("/shelly", self.handle_legacy_shelly)
        app.router.add_get("/status", self.handle_legacy_status)

        # Dashboard & APIs
        app.router.add_get("/", self.handle_dashboard)
        app.router.add_get("/api/status", self.handle_api_status)
        app.router.add_get("/api/debug", self.handle_api_debug)
        app.router.add_get("/api/ha/entities", self.handle_api_ha_entities)
        app.router.add_get("/api/automation", self.handle_api_automation_get)
        app.router.add_post("/api/automation", self.handle_api_automation_post)

    # -------------------------------------------------------------------------
    # RPC Handlers
    # -------------------------------------------------------------------------

    async def handle_post_rpc(self, request: web.Request) -> web.Response:
        """Handle standard POST /rpc with a JSON-RPC 2.0 payload."""
        client_ip = request.remote or "unknown"
        body_text = await request.text()

        try:
            req_data = json.loads(body_text) if body_text else {}
        except Exception:
            err_resp = {
                "id": None,
                "src": self.config.device_id,
                "error": {"code": -32700, "message": "Parse error"}
            }
            self.shelly_service.record_request(
                protocol="HTTP",
                client_ip=client_ip,
                client_port=0,
                http_method="POST",
                path=request.path,
                rpc_method="<parse_error>",
                params=None,
                body=body_text,
                response_status=400,
                response_data=err_resp,
            )
            return web.json_response(err_resp, status=400)

        req_id = req_data.get("id")
        method = req_data.get("method", "")
        params = req_data.get("params") or {}

        success, result_or_err = self.shelly_service.execute_rpc(method, params)

        if success:
            resp_payload = {
                "id": req_id,
                "src": self.config.device_id,
                "result": result_or_err,
            }
            status_code = 200
        else:
            resp_payload = {
                "id": req_id,
                "src": self.config.device_id,
                "error": result_or_err,
            }
            status_code = 200

        self.shelly_service.record_request(
            protocol="HTTP",
            client_ip=client_ip,
            client_port=0,
            http_method="POST",
            path=request.path,
            rpc_method=method,
            params=params,
            body=body_text,
            response_status=status_code,
            response_data=resp_payload,
        )
        return web.json_response(resp_payload, status=status_code)

    async def handle_get_rpc_method(self, request: web.Request) -> web.Response:
        """Handle GET /rpc/<Method>?<params>."""
        client_ip = request.remote or "unknown"
        method = request.match_info.get("method", "")
        params = parse_query_params(request.query)

        success, result_or_err = self.shelly_service.execute_rpc(method, params)

        if success:
            resp_payload = result_or_err
            status_code = 200
        else:
            resp_payload = {"error": result_or_err}
            status_code = 404 if result_or_err.get("code") == -32601 else 500

        self.shelly_service.record_request(
            protocol="HTTP",
            client_ip=client_ip,
            client_port=0,
            http_method="GET",
            path=request.path_qs,
            rpc_method=method,
            params=params,
            body="",
            response_status=status_code,
            response_data=resp_payload,
        )
        return web.json_response(resp_payload, status=status_code)

    async def handle_post_rpc_method(self, request: web.Request) -> web.Response:
        """Handle POST /rpc/<Method>."""
        client_ip = request.remote or "unknown"
        method = request.match_info.get("method", "")
        body_text = await request.text()
        params = parse_query_params(request.query)

        if body_text:
            try:
                body_json = json.loads(body_text)
                if isinstance(body_json, dict):
                    params.update(body_json)
            except Exception:
                pass

        success, result_or_err = self.shelly_service.execute_rpc(method, params)

        if success:
            resp_payload = result_or_err
            status_code = 200
        else:
            resp_payload = {"error": result_or_err}
            status_code = 404 if result_or_err.get("code") == -32601 else 500

        self.shelly_service.record_request(
            protocol="HTTP",
            client_ip=client_ip,
            client_port=0,
            http_method="POST",
            path=request.path,
            rpc_method=method,
            params=params,
            body=body_text,
            response_status=status_code,
            response_data=resp_payload,
        )
        return web.json_response(resp_payload, status=status_code)

    async def handle_get_rpc_root(self, request: web.Request) -> web.Response:
        """Handle GET /rpc root query."""
        if "method" in request.query:
            method = request.query["method"]
            params = parse_query_params({k: v for k, v in request.query.items() if k != "method"})
            success, result_or_err = self.shelly_service.execute_rpc(method, params)
            resp_payload = result_or_err if success else {"error": result_or_err}
            status_code = 200 if success else 404
            return web.json_response(resp_payload, status=status_code)

        info = self.shelly_service.handle_shelly_get_device_info({})
        return web.json_response(info)

    async def handle_legacy_shelly(self, request: web.Request) -> web.Response:
        """Handle GET /shelly for legacy discovery."""
        return web.json_response({
            "type": "SPEM-003CEBEU",
            "mac": self.config.mac_clean,
            "auth": False,
            "fw": self.config.firmware_version,
            "gen": 2,
            "id": self.config.device_id,
        })

    async def handle_legacy_status(self, request: web.Request) -> web.Response:
        """Handle GET /status for legacy tools."""
        return web.json_response(self.shelly_service.handle_shelly_get_status({}))

    # -------------------------------------------------------------------------
    # Web Dashboard & APIs
    # -------------------------------------------------------------------------

    async def handle_api_status(self, request: web.Request) -> web.Response:
        """Return live JSON data for web UI auto-refresh."""
        meter_data, is_stale = self.shelly_service.get_effective_meter_data()
        age = time.time() - meter_data.timestamp if meter_data.timestamp > 0 else 999.0

        auto_info = self.automation_controller.get_status_dict() if self.automation_controller else None

        return web.json_response({
            "device": {
                "id": self.config.device_id,
                "name": self.config.device_name,
                "model": self.config.model,
                "mac": self.config.emulated_mac,
                "ver": self.config.firmware_version,
                "uptime": int(time.time() - self.shelly_service.start_time),
            },
            "source": {
                "type": self.config.data_source,
                "serial": meter_data.serial or "N/A",
                "is_stale": is_stale,
                "data_age_sec": round(age, 1),
                "invert_sign": self.config.invert_sign,
            },
            "measurements": {
                "total_act_power": round(meter_data.total_act_power, 1),
                "total_current": round(meter_data.total_current, 3),
                "total_aprt_power": round(meter_data.total_aprt_power, 1),
                "total_act_energy_kwh": round(meter_data.total_act_energy / 1000.0, 3),
                "total_act_ret_energy_kwh": round(meter_data.total_act_ret_energy / 1000.0, 3),
                "phases": {
                    "a": {
                        "power": round(meter_data.a.act_power, 1),
                        "voltage": round(meter_data.a.voltage, 1),
                        "current": round(meter_data.a.current, 3),
                        "pf": round(meter_data.a.pf, 2),
                        "freq": round(meter_data.a.freq, 1),
                        "energy_kwh": round(meter_data.a.energy_act / 1000.0, 3),
                        "energy_ret_kwh": round(meter_data.a.energy_act_ret / 1000.0, 3),
                    },
                    "b": {
                        "power": round(meter_data.b.act_power, 1),
                        "voltage": round(meter_data.b.voltage, 1),
                        "current": round(meter_data.b.current, 3),
                        "pf": round(meter_data.b.pf, 2),
                        "freq": round(meter_data.b.freq, 1),
                        "energy_kwh": round(meter_data.b.energy_act / 1000.0, 3),
                        "energy_ret_kwh": round(meter_data.b.energy_act_ret / 1000.0, 3),
                    },
                    "c": {
                        "power": round(meter_data.c.act_power, 1),
                        "voltage": round(meter_data.c.voltage, 1),
                        "current": round(meter_data.c.current, 3),
                        "pf": round(meter_data.c.pf, 2),
                        "freq": round(meter_data.c.freq, 1),
                        "energy_kwh": round(meter_data.c.energy_act / 1000.0, 3),
                        "energy_ret_kwh": round(meter_data.c.energy_act_ret / 1000.0, 3),
                    },
                }
            },
            "automation": auto_info,
        })

    async def handle_api_debug(self, request: web.Request) -> web.Response:
        """Return the recent incoming request history."""
        return web.json_response({
            "requests": list(self.shelly_service.recent_requests)
        })

    async def handle_api_ha_entities(self, request: web.Request) -> web.Response:
        """Return available Home Assistant entities for Solarbank & sensor selection."""
        if not self.ha_client:
            return web.json_response({"entities": []})

        all_states = await self.ha_client.get_all_entities()
        filtered: List[Dict[str, Any]] = []

        for item in all_states:
            eid = item.get("entity_id", "")
            domain = eid.split(".")[0] if "." in eid else ""
            if domain in ("number", "sensor", "select", "switch", "input_number"):
                attrs = item.get("attributes") or {}
                name = attrs.get("friendly_name") or eid
                unit = attrs.get("unit_of_measurement") or ""
                state = item.get("state")

                is_anker = any(k in eid.lower() for k in ("anker", "solarbank", "solix"))
                is_power = any(k in eid.lower() or k in unit.lower() for k in ("power", "watt", "leistung", "w"))
                is_battery = any(k in eid.lower() or "%" in unit for k in ("battery", "akku", "soc"))

                filtered.append({
                    "entity_id": eid,
                    "domain": domain,
                    "friendly_name": name,
                    "state": state,
                    "unit": unit,
                    "is_anker": is_anker,
                    "is_power": is_power,
                    "is_battery": is_battery,
                })

        filtered.sort(key=lambda x: (not x["is_anker"], not x["is_power"], x["entity_id"]))
        return web.json_response({"entities": filtered})

    async def handle_api_automation_get(self, request: web.Request) -> web.Response:
        """Get current automation status and configuration."""
        if not self.automation_controller:
            return web.json_response({"error": "Automation controller not initialized"}, status=503)
        return web.json_response(self.automation_controller.get_status_dict())

    async def handle_api_automation_post(self, request: web.Request) -> web.Response:
        """Update and save automation configuration."""
        if not self.automation_controller:
            return web.json_response({"error": "Automation controller not initialized"}, status=503)
        try:
            body = await request.json()
            self.automation_controller.save_config(body)
            return web.json_response({
                "success": True,
                "config": asdict(self.automation_controller.config),
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_dashboard(self, request: web.Request) -> web.Response:
        """Render modern, interactive dashboard HTML with Visual Automation Builder."""
        ingress_path = request.headers.get("X-Ingress-Path", "").rstrip("/")

        html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SMA Home Manager & Solarbank Control Hub</title>
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: #131b2e;
      --card-border: #1e293b;
      --card-hover: #1e2942;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #38bdf8;
      --primary-glow: rgba(56, 189, 248, 0.2);
      --success: #22c55e;
      --warning: #f59e0b;
      --danger: #ef4444;
      --solar: #eab308;
      --badge-import: #3b82f6;
      --badge-export: #10b981;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 1.25rem;
      line-height: 1.5;
    }}
    .container {{ max-width: 1250px; margin: 0 auto; }}

    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.25rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--card-border);
      flex-wrap: wrap;
      gap: 1rem;
    }}
    .title-group h1 {{ font-size: 1.4rem; font-weight: 700; color: var(--primary); display: flex; align-items: center; gap: 0.5rem; }}
    .title-group p {{ font-size: 0.85rem; color: var(--text-muted); }}

    .badge {{
      display: inline-block;
      padding: 0.25rem 0.6rem;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
    }}
    .badge-ok {{ background: rgba(34, 197, 94, 0.15); color: var(--success); border: 1px solid var(--success); }}
    .badge-stale {{ background: rgba(239, 68, 68, 0.15); color: var(--danger); border: 1px solid var(--danger); }}
    .badge-auto-on {{ background: rgba(34, 197, 94, 0.2); color: var(--success); border: 1px solid var(--success); }}
    .badge-auto-off {{ background: rgba(148, 163, 184, 0.2); color: var(--text-muted); border: 1px solid var(--text-muted); }}

    /* Tab Navigation */
    .tabs {{
      display: flex;
      gap: 0.5rem;
      margin-bottom: 1.25rem;
      border-bottom: 1px solid var(--card-border);
      padding-bottom: 0.5rem;
      flex-wrap: wrap;
    }}
    .tab-btn {{
      background: transparent;
      border: 1px solid transparent;
      color: var(--text-muted);
      padding: 0.6rem 1.1rem;
      border-radius: 8px;
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .tab-btn:hover {{ background: var(--card-bg); color: var(--text); }}
    .tab-btn.active {{
      background: var(--card-bg);
      color: var(--primary);
      border-color: var(--primary);
      box-shadow: 0 0 12px var(--primary-glow);
    }}

    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}

    /* Grid & Cards */
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 1.25rem;
      margin-bottom: 1.25rem;
    }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 1.25rem;
      position: relative;
    }}
    .card-title {{
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 0.5rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}

    /* Big Hero Net Gauge */
    .net-hero {{
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 1rem 0;
    }}
    .net-val {{
      font-size: 3rem;
      font-weight: 800;
      letter-spacing: -0.03em;
      line-height: 1.1;
    }}
    .net-desc {{
      font-size: 0.95rem;
      font-weight: 600;
      margin-top: 0.3rem;
    }}
    .val-import {{ color: var(--badge-import); }}
    .val-export {{ color: var(--badge-export); }}
    .val-zero {{ color: var(--text-muted); }}

    /* Phase Metrics */
    .metric-row {{
      display: flex;
      justify-content: space-between;
      padding: 0.4rem 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
      font-size: 0.9rem;
    }}
    .metric-row:last-child {{ border-bottom: none; }}
    .metric-val {{ font-weight: 600; }}

    /* Mode Selection Cards */
    .mode-cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      gap: 1rem;
      margin: 1rem 0;
    }}
    .mode-card {{
      background: #0f172a;
      border: 2px solid var(--card-border);
      border-radius: 10px;
      padding: 1rem;
      cursor: pointer;
      transition: all 0.2s ease;
      position: relative;
    }}
    .mode-card:hover {{ border-color: var(--primary); }}
    .mode-card.selected {{
      border-color: var(--primary);
      background: rgba(56, 189, 248, 0.08);
      box-shadow: 0 0 15px rgba(56, 189, 248, 0.15);
    }}
    .mode-icon {{ font-size: 1.5rem; margin-bottom: 0.4rem; }}
    .mode-name {{ font-weight: 700; font-size: 1rem; margin-bottom: 0.3rem; color: var(--text); }}
    .mode-desc {{ font-size: 0.8rem; color: var(--text-muted); line-height: 1.4; }}

    /* Form Inputs */
    .form-group {{
      margin-bottom: 1.25rem;
    }}
    .form-label {{
      display: block;
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 0.4rem;
    }}
    .form-help {{
      font-size: 0.75rem;
      color: var(--text-muted);
      margin-top: 0.25rem;
    }}
    select, input[type="text"], input[type="number"] {{
      width: 100%;
      background: #0b1120;
      border: 1px solid var(--card-border);
      color: var(--text);
      padding: 0.6rem 0.8rem;
      border-radius: 8px;
      font-size: 0.9rem;
      outline: none;
      transition: border-color 0.2s;
    }}
    select:focus, input:focus {{ border-color: var(--primary); }}

    /* Switches */
    .toggle-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.75rem 0;
    }}
    .switch {{
      position: relative;
      display: inline-block;
      width: 50px;
      height: 26px;
    }}
    .switch input {{ opacity: 0; width: 0; height: 0; }}
    .slider {{
      position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
      background-color: #334155; transition: .3s; border-radius: 34px;
    }}
    .slider:before {{
      position: absolute; content: ""; height: 18px; width: 18px; left: 4px; bottom: 4px;
      background-color: white; transition: .3s; border-radius: 50%;
    }}
    input:checked + .slider {{ background-color: var(--success); }}
    input:checked + .slider:before {{ transform: translateX(24px); }}

    /* Buttons */
    .btn {{
      background: var(--primary);
      color: #090d16;
      border: none;
      padding: 0.7rem 1.4rem;
      border-radius: 8px;
      font-size: 0.95rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s ease;
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .btn:hover {{ background: #7dd3fc; }}
    .btn-secondary {{
      background: #334155;
      color: var(--text);
    }}
    .btn-secondary:hover {{ background: #475569; }}

    /* Tables & Logs */
    .log-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
    }}
    .log-table th, .log-table td {{
      padding: 0.5rem 0.6rem;
      text-align: left;
      border-bottom: 1px solid var(--card-border);
    }}
    .log-table th {{ color: var(--text-muted); font-weight: 600; }}
    .badge-action {{
      padding: 0.15rem 0.4rem;
      border-radius: 4px;
      font-size: 0.7rem;
      font-weight: 600;
    }}
    .action-UPDATED {{ background: rgba(34, 197, 94, 0.2); color: var(--success); }}
    .action-SKIPPED_DEADBAND {{ background: rgba(148, 163, 184, 0.2); color: var(--text-muted); }}
    .action-CUTOFF_MIN_SOC {{ background: rgba(245, 158, 11, 0.2); color: var(--warning); }}
    .action-ERROR {{ background: rgba(239, 68, 68, 0.2); color: var(--danger); }}

    /* Status Banner */
    .status-banner {{
      background: rgba(56, 189, 248, 0.08);
      border: 1px solid var(--primary);
      border-radius: 10px;
      padding: 0.75rem 1rem;
      margin-bottom: 1.25rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.5rem;
    }}
    .status-info {{ display: flex; align-items: center; gap: 0.75rem; }}

    #toast {{
      position: fixed;
      bottom: 20px;
      right: 20px;
      background: var(--success);
      color: #090d16;
      padding: 0.75rem 1.25rem;
      border-radius: 8px;
      font-weight: 700;
      display: none;
      box-shadow: 0 4px 15px rgba(0,0,0,0.4);
      z-index: 999;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="title-group">
        <h1><span>⚡</span> SMA SHM2 & Anker Solarbank Hub</h1>
        <p>Echtzeit-Netzmessung & visuelle Solarbank-Nulleinspeisung</p>
      </div>
      <div style="display: flex; gap: 0.5rem; align-items: center;">
        <span id="badge-sma" class="badge badge-ok">SMA: Verbunden</span>
        <span id="badge-auto" class="badge badge-auto-off">Automation: Inaktiv</span>
      </div>
    </header>

    <!-- Top Live Status Banner -->
    <div class="status-banner">
      <div class="status-info">
        <span style="font-size: 1.2rem;">🤖</span>
        <div>
          <div style="font-weight: 700; font-size: 0.9rem;" id="regler-status-title">Regelung: Inaktiv</div>
          <div style="font-size: 0.75rem; color: var(--text-muted);" id="regler-status-sub">Zielentität nicht konfiguriert</div>
        </div>
      </div>
      <div style="font-size: 0.85rem; font-weight: 600;">
        Aktueller Sollwert: <span id="regler-target-val" style="color: var(--primary);">0 W</span>
      </div>
    </div>

    <!-- Navigation Tabs -->
    <div class="tabs">
      <button class="tab-btn active" onclick="switchTab('tab-live')">📊 Live-Monitor (SMA)</button>
      <button class="tab-btn" onclick="switchTab('tab-automation')">⚙️ Automations-Builder</button>
      <button class="tab-btn" onclick="switchTab('tab-log')">📋 Regelungs-Protokoll</button>
      <button class="tab-btn" onclick="switchTab('tab-shelly')">🔍 Shelly RPC Monitor</button>
    </div>

    <!-- TAB 1: LIVE MONITOR -->
    <div id="tab-live" class="tab-content active">
      <div class="grid">
        <!-- Hero: Net Grid Power -->
        <div class="card">
          <div class="card-title">
            <span>Netzanschluss (Gesamt)</span>
            <span id="net-badge" class="badge badge-import">Bezug</span>
          </div>
          <div class="net-hero">
            <div id="net-val" class="net-val val-import">+0.0 W</div>
            <div id="net-desc" class="net-desc" style="color: var(--badge-import);">Netzbezug</div>
          </div>
          <div class="metric-row">
            <span style="color: var(--text-muted);">Gesamtstrom</span>
            <span class="metric-val" id="total-current">0.00 A</span>
          </div>
          <div class="metric-row">
            <span style="color: var(--text-muted);">Scheinleistung</span>
            <span class="metric-val" id="total-aprt">0.0 VA</span>
          </div>
        </div>

        <!-- Solarbank / Akku Telemetrie -->
        <div class="card">
          <div class="card-title">
            <span>Anker Solarbank Status</span>
            <span class="badge badge-ok" id="solarbank-state-badge">Aktiv</span>
          </div>
          <div class="net-hero">
            <div id="sb-battery-val" style="font-size: 2.5rem; font-weight: 800; color: var(--solar);">-- %</div>
            <div class="net-desc" style="color: var(--text-muted);">Akkustand (SoC)</div>
          </div>
          <div class="metric-row">
            <span style="color: var(--text-muted);">Aktuelle Einspeisung</span>
            <span class="metric-val" id="sb-power-val" style="color: var(--primary);">-- W</span>
          </div>
          <div class="metric-row">
            <span style="color: var(--text-muted);">Solarerzeugung (PV)</span>
            <span class="metric-val" id="sb-solar-val" style="color: var(--solar);">-- W</span>
          </div>
        </div>

        <!-- Zählerstände -->
        <div class="card">
          <div class="card-title">Energiezähler (SHM2)</div>
          <div style="padding: 0.5rem 0;">
            <div class="metric-row">
              <span style="color: var(--text-muted);">Gesamtbezug</span>
              <span class="metric-val" id="energy-import" style="color: var(--badge-import);">0.000 kWh</span>
            </div>
            <div class="metric-row">
              <span style="color: var(--text-muted);">Gesamteinspeisung</span>
              <span class="metric-val" id="energy-export" style="color: var(--badge-export);">0.000 kWh</span>
            </div>
            <div class="metric-row">
              <span style="color: var(--text-muted);">Daten-Aktualität</span>
              <span class="metric-val" id="data-age">0.0s</span>
            </div>
            <div class="metric-row">
              <span style="color: var(--text-muted);">SMA Seriennummer</span>
              <span class="metric-val" id="shm-serial">--</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Phase Cards L1, L2, L3 -->
      <div class="grid">
        <div class="card">
          <div class="card-title"><span>Phase L1</span><span id="p1-power" style="font-weight:700;">+0 W</span></div>
          <div class="metric-row"><span>Spannung</span><span class="metric-val" id="p1-voltage">0 V</span></div>
          <div class="metric-row"><span>Strom</span><span class="metric-val" id="p1-current">0.00 A</span></div>
          <div class="metric-row"><span>Power Factor</span><span class="metric-val" id="p1-pf">1.00</span></div>
          <div class="metric-row"><span>Frequenz</span><span class="metric-val" id="p1-freq">50.0 Hz</span></div>
        </div>
        <div class="card">
          <div class="card-title"><span>Phase L2</span><span id="p2-power" style="font-weight:700;">+0 W</span></div>
          <div class="metric-row"><span>Spannung</span><span class="metric-val" id="p2-voltage">0 V</span></div>
          <div class="metric-row"><span>Strom</span><span class="metric-val" id="p2-current">0.00 A</span></div>
          <div class="metric-row"><span>Power Factor</span><span class="metric-val" id="p2-pf">1.00</span></div>
          <div class="metric-row"><span>Frequenz</span><span class="metric-val" id="p2-freq">50.0 Hz</span></div>
        </div>
        <div class="card">
          <div class="card-title"><span>Phase L3</span><span id="p3-power" style="font-weight:700;">+0 W</span></div>
          <div class="metric-row"><span>Spannung</span><span class="metric-val" id="p3-voltage">0 V</span></div>
          <div class="metric-row"><span>Strom</span><span class="metric-val" id="p3-current">0.00 A</span></div>
          <div class="metric-row"><span>Power Factor</span><span class="metric-val" id="p3-pf">1.00</span></div>
          <div class="metric-row"><span>Frequenz</span><span class="metric-val" id="p3-freq">50.0 Hz</span></div>
        </div>
      </div>
    </div>

    <!-- TAB 2: VISUAL AUTOMATION BUILDER -->
    <div id="tab-automation" class="tab-content">
      <div class="card" style="margin-bottom: 1.25rem;">
        <div class="toggle-row">
          <div>
            <div style="font-size: 1.1rem; font-weight: 700;">Solarbank Nulleinspeisungs-Automatik</div>
            <div style="font-size: 0.8rem; color: var(--text-muted);">
              Aktiviert die dynamische Steuerung der Anker Solarbank basierend auf SMA Home Manager Messwerten.
            </div>
          </div>
          <label class="switch">
            <input type="checkbox" id="auto-enabled">
            <span class="slider"></span>
          </label>
        </div>
      </div>

      <!-- Mode Cards -->
      <div class="card" style="margin-bottom: 1.25rem;">
        <div class="card-title">Wähle deinen Optimierungs-Modus</div>
        <div class="mode-cards">
          <div class="mode-card selected" id="card-zero_export" onclick="selectMode('zero_export')">
            <div class="mode-icon">🎯</div>
            <div class="mode-name">Dynamische Nulleinspeisung</div>
            <div class="mode-desc">
              Gleicht deinen aktuellen Netzbezug im Sekundentakt 1:1 aus, um Zählerbezug und Netzeinspeisung auf 0 W zu halten.
            </div>
          </div>

          <div class="mode-card" id="card-peak_shaving" onclick="selectMode('peak_shaving')">
            <div class="mode-icon">⚡</div>
            <div class="mode-name">Spitzenlast-Kappung</div>
            <div class="mode-desc">
              Schont den Akku und speist erst ein, wenn Großverbraucher (z. B. Herd, Waschmaschine) über eine definierte Schwelle ziehen.
            </div>
          </div>

          <div class="mode-card" id="card-constant_base" onclick="selectMode('constant_base')">
            <div class="mode-icon">🌙</div>
            <div class="mode-name">Nacht-Grundlast</div>
            <div class="mode-desc">
              Gibt nachts oder bei Dunkelheit eine feste Grundlast (z. B. 150 W) ab, um den Akku gleichmäßig über die Nacht zu verteilen.
            </div>
          </div>

          <div class="mode-card" id="card-solar_priority" onclick="selectMode('solar_priority')">
            <div class="mode-icon">🔋</div>
            <div class="mode-name">Solar-Ladepriorität</div>
            <div class="mode-desc">
              Hält die Einspeisung zurück, bis der Akku auf den Ziel-Ladestand (z. B. 80%) geladen ist, und schaltet dann auf Nulleinspeisung um.
            </div>
          </div>
        </div>
      </div>

      <!-- Entity Selection Form -->
      <div class="grid">
        <div class="card">
          <div class="card-title">Home Assistant Entitäten</div>

          <div class="form-group">
            <label class="form-label" for="sel-target-entity">Solarbank Leistungs-Regler (Sollwert) *</label>
            <select id="sel-target-entity">
              <option value="">Lade Entitäten...</option>
            </select>
            <div class="form-help">Wähle die Entität, über die Home Assistant die Ausgangsleistung der Solarbank setzt (z. B. number.*_preset_discharge_power).</div>
          </div>

          <div class="form-group">
            <label class="form-label" for="sel-battery-entity">Akkustand Sensor (SoC %)</label>
            <select id="sel-battery-entity">
              <option value="">Lade Entitäten...</option>
            </select>
            <div class="form-help">Wichtig für den Batterieschutz (z. B. sensor.*_battery_percentage).</div>
          </div>

          <div class="form-group">
            <label class="form-label" for="sel-solar-entity">Solarerzeugung Sensor (Optional)</label>
            <select id="sel-solar-entity">
              <option value="">-- Keine / Optional --</option>
            </select>
            <div class="form-help">Aktuelle PV-Leistung für Nacht- und Lade-Prioritäts-Modi.</div>
          </div>

          <div class="form-group">
            <label class="form-label" for="sel-current-power-entity">Aktuelle Einspeiseleistung Sensor (Optional)</label>
            <select id="sel-current-power-entity">
              <option value="">-- Keine / Nutze letzten Sollwert --</option>
            </select>
            <div class="form-help">Rückmeldung der tatsächlichen Ist-Einspeisung.</div>
          </div>
        </div>

        <div class="card">
          <div class="card-title">Regelungsparameter & Schutzgrenzen</div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
            <div class="form-group">
              <label class="form-label">Min. Leistung (W)</label>
              <input type="number" id="inp-min-power" min="0" max="800" step="10" value="0">
            </div>
            <div class="form-group">
              <label class="form-label">Max. Leistung (W)</label>
              <input type="number" id="inp-max-power" min="0" max="1200" step="50" value="800">
            </div>
          </div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
            <div class="form-group">
              <label class="form-label">Hysterese (W)</label>
              <input type="number" id="inp-deadband" min="5" max="100" step="5" value="20">
              <div class="form-help">Verhindert zu häufige Min-Anpassungen.</div>
            </div>
            <div class="form-group">
              <label class="form-label">Regelintervall (s)</label>
              <input type="number" id="inp-interval" min="2" max="60" step="1" value="5">
              <div class="form-help">Sekunden zwischen Regeleingriffen.</div>
            </div>
          </div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
            <div class="form-group">
              <label class="form-label">Mindest-Akkustand (%)</label>
              <input type="number" id="inp-min-soc" min="0" max="50" step="5" value="10">
              <div class="form-help">Stoppt Entladung unterhalb.</div>
            </div>
            <div class="form-group" id="group-threshold">
              <label class="form-label">Spitzenlast-Schwelle (W)</label>
              <input type="number" id="inp-threshold" min="200" max="3000" step="100" value="1000">
              <div class="form-help">Nur für Spitzenlast-Modus.</div>
            </div>
          </div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
            <div class="form-group" id="group-base-power">
              <label class="form-label">Feste Grundlast (W)</label>
              <input type="number" id="inp-base-power" min="50" max="800" step="25" value="150">
              <div class="form-help">Nur für Nacht-Grundlast-Modus.</div>
            </div>
            <div class="form-group" id="group-target-soc">
              <label class="form-label">Ziel-Akkustand (%)</label>
              <input type="number" id="inp-target-soc" min="50" max="100" step="5" value="80">
              <div class="form-help">Für Lade-Prioritäts-Modus.</div>
            </div>
          </div>

          <div style="margin-top: 1rem;">
            <button class="btn" onclick="saveAutomationConfig()">
              <span>💾</span> Einstellungen speichern & anwenden
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 3: REGELUNGS-PROTOKOLL -->
    <div id="tab-log" class="tab-content">
      <div class="card">
        <div class="card-title">Live-Protokoll der letzten Regeleingriffe</div>
        <div style="overflow-x: auto;">
          <table class="log-table">
            <thead>
              <tr>
                <th>Zeit</th>
                <th>Modus</th>
                <th>Netzleistung</th>
                <th>Akku SoC</th>
                <th>Ist &rarr; Soll</th>
                <th>Aktion</th>
                <th>Begründung</th>
              </tr>
            </thead>
            <tbody id="decision-log-tbody">
              <tr><td colspan="7" style="color: var(--text-muted); text-align: center;">Noch keine Regeleingriffe protokolliert...</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB 4: SHELLY RPC MONITOR -->
    <div id="tab-shelly" class="tab-content">
      <div class="card" style="margin-bottom: 1.25rem;">
        <div class="card-title">Shelly Pro 3EM Identität</div>
        <div class="metric-row"><span style="color: var(--text-muted);">Geräte-ID</span><span class="metric-val" id="dev-id">--</span></div>
        <div class="metric-row"><span style="color: var(--text-muted);">Modell</span><span class="metric-val">SPEM-003CEBEU (Gen 2)</span></div>
        <div class="metric-row"><span style="color: var(--text-muted);">MAC-Adresse</span><span class="metric-val" id="dev-mac">--</span></div>
        <div class="metric-row"><span style="color: var(--text-muted);">Firmware-Version</span><span class="metric-val" id="dev-fw">--</span></div>
        <div class="metric-row"><span style="color: var(--text-muted);">mDNS Ankündigung</span><span class="metric-val">_shelly._tcp & _http._tcp</span></div>
      </div>

      <div class="card">
        <div class="card-title">Eingehende HTTP & UDP Anfragen</div>
        <div style="overflow-x: auto;">
          <table class="log-table">
            <thead>
              <tr>
                <th>Zeit</th>
                <th>Typ</th>
                <th>Client IP</th>
                <th>Methode</th>
                <th>Pfad / RPC</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody id="rpc-log-tbody">
              <tr><td colspan="6" style="color: var(--text-muted); text-align: center;">Warte auf Anfragen...</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <div id="toast">Einstellungen gespeichert!</div>

  <script>
    const ingressPath = "{ingress_path}";
    let selectedMode = "zero_export";
    let entitiesLoaded = false;

    function switchTab(tabId) {{
      document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      event.currentTarget.classList.add('active');
      document.getElementById(tabId).classList.add('active');

      if (tabId === 'tab-automation' && !entitiesLoaded) {{
        loadEntities();
      }}
    }}

    function selectMode(mode) {{
      selectedMode = mode;
      document.querySelectorAll('.mode-card').forEach(c => c.classList.remove('selected'));
      const activeCard = document.getElementById('card-' + mode);
      if (activeCard) activeCard.classList.add('selected');
    }}

    function showToast(msg) {{
      const t = document.getElementById('toast');
      t.innerText = msg;
      t.style.display = 'block';
      setTimeout(() => {{ t.style.display = 'none'; }}, 3000);
    }}

    async function loadEntities() {{
      try {{
        const res = await fetch(ingressPath + '/api/ha/entities');
        const data = await res.json();
        const entities = data.entities || [];

        populateSelect('sel-target-entity', entities.filter(e => e.domain === 'number' || e.domain === 'input_number'));
        populateSelect('sel-battery-entity', entities.filter(e => e.is_battery || e.unit === '%'));
        populateSelect('sel-solar-entity', entities.filter(e => e.is_power || e.domain === 'sensor'), true);
        populateSelect('sel-current-power-entity', entities.filter(e => e.is_power || e.domain === 'sensor'), true);

        entitiesLoaded = true;
        loadAutomationConfig();
      }} catch (e) {{
        console.error('Failed to load HA entities:', e);
      }}
    }}

    function populateSelect(selectId, items, allowEmpty = false) {{
      const sel = document.getElementById(selectId);
      sel.innerHTML = allowEmpty ? '<option value="">-- Keine / Optional --</option>' : '<option value="">-- Bitte wählen --</option>';

      items.forEach(item => {{
        const opt = document.createElement('option');
        opt.value = item.entity_id;
        const prefix = item.is_anker ? '⭐ [Anker] ' : '';
        const unit = item.unit ? ` (${{item.unit}})` : '';
        opt.innerText = `${{prefix}}${{item.friendly_name}} (${{item.entity_id}})${{unit}}`;
        sel.appendChild(opt);
      }});
    }}

    async function loadAutomationConfig() {{
      try {{
        const res = await fetch(ingressPath + '/api/automation');
        const data = await res.json();
        if (!data.config) return;

        const cfg = data.config;
        document.getElementById('auto-enabled').checked = cfg.enabled;
        selectMode(cfg.mode || 'zero_export');

        if (cfg.target_entity) document.getElementById('sel-target-entity').value = cfg.target_entity;
        if (cfg.battery_entity) document.getElementById('sel-battery-entity').value = cfg.battery_entity;
        if (cfg.solar_entity) document.getElementById('sel-solar-entity').value = cfg.solar_entity;
        if (cfg.current_power_entity) document.getElementById('sel-current-power-entity').value = cfg.current_power_entity;

        document.getElementById('inp-min-power').value = cfg.min_power_w;
        document.getElementById('inp-max-power').value = cfg.max_power_w;
        document.getElementById('inp-deadband').value = cfg.deadband_w;
        document.getElementById('inp-interval').value = cfg.update_interval_s;
        document.getElementById('inp-min-soc').value = cfg.min_soc_pct;
        document.getElementById('inp-threshold').value = cfg.threshold_w;
        document.getElementById('inp-base-power').value = cfg.base_power_w;
        document.getElementById('inp-target-soc').value = cfg.target_soc_pct;
      }} catch (e) {{
        console.error('Failed to load automation config:', e);
      }}
    }}

    async function saveAutomationConfig() {{
      const payload = {{
        enabled: document.getElementById('auto-enabled').checked,
        mode: selectedMode,
        target_entity: document.getElementById('sel-target-entity').value,
        battery_entity: document.getElementById('sel-battery-entity').value,
        solar_entity: document.getElementById('sel-solar-entity').value,
        current_power_entity: document.getElementById('sel-current-power-entity').value,
        min_power_w: parseFloat(document.getElementById('inp-min-power').value) || 0,
        max_power_w: parseFloat(document.getElementById('inp-max-power').value) || 800,
        deadband_w: parseFloat(document.getElementById('inp-deadband').value) || 20,
        update_interval_s: parseFloat(document.getElementById('inp-interval').value) || 5,
        min_soc_pct: parseFloat(document.getElementById('inp-min-soc').value) || 10,
        threshold_w: parseFloat(document.getElementById('inp-threshold').value) || 1000,
        base_power_w: parseFloat(document.getElementById('inp-base-power').value) || 150,
        target_soc_pct: parseFloat(document.getElementById('inp-target-soc').value) || 80,
      }};

      try {{
        const res = await fetch(ingressPath + '/api/automation', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload)
        }});
        if (res.ok) {{
          showToast('✅ Automations-Einstellungen gespeichert!');
          fetchStatus();
        }} else {{
          showToast('❌ Fehler beim Speichern');
        }}
      }} catch (e) {{
        showToast('❌ Netzwerkfehler beim Speichern');
      }}
    }}

    async function fetchStatus() {{
      try {{
        const res = await fetch(ingressPath + '/api/status');
        const data = await res.json();

        // 1. SMA Telemetry
        const m = data.measurements;
        const s = data.source;
        const pTot = m.total_act_power;

        const netVal = document.getElementById('net-val');
        const netDesc = document.getElementById('net-desc');
        const netBadge = document.getElementById('net-badge');

        if (pTot > 0) {{
          netVal.innerText = `+${{pTot.toFixed(1)}} W`;
          netVal.className = 'net-val val-import';
          netDesc.innerText = 'Netzbezug (Verbrauch aus dem Netz)';
          netDesc.style.color = 'var(--badge-import)';
          netBadge.innerText = 'BEZUG';
          netBadge.className = 'badge badge-import';
        }} else if (pTot < 0) {{
          netVal.innerText = `${{pTot.toFixed(1)}} W`;
          netVal.className = 'net-val val-export';
          netDesc.innerText = 'Netzeinspeisung (Überschuss ins Netz)';
          netDesc.style.color = 'var(--badge-export)';
          netBadge.innerText = 'EINSPEISUNG';
          netBadge.className = 'badge badge-export';
        }} else {{
          netVal.innerText = '0.0 W';
          netVal.className = 'net-val val-zero';
          netDesc.innerText = 'Ausgeglichen (Nulleinspeisung)';
          netDesc.style.color = 'var(--text-muted)';
          netBadge.innerText = '0 WATT';
          netBadge.className = 'badge badge-ok';
        }}

        document.getElementById('total-current').innerText = `${{m.total_current.toFixed(2)}} A`;
        document.getElementById('total-aprt').innerText = `${{m.total_aprt_power.toFixed(1)}} VA`;
        document.getElementById('energy-import').innerText = `${{m.total_act_energy_kwh.toFixed(3)}} kWh`;
        document.getElementById('energy-export').innerText = `${{m.total_act_ret_energy_kwh.toFixed(3)}} kWh`;
        document.getElementById('data-age').innerText = `${{s.data_age_sec}}s`;
        document.getElementById('shm-serial').innerText = s.serial || 'N/A';

        // Phase Cards
        const p = m.phases;
        document.getElementById('p1-power').innerText = `${{p.a.power > 0 ? '+' : ''}}${{p.a.power.toFixed(1)}} W`;
        document.getElementById('p1-voltage').innerText = `${{p.a.voltage.toFixed(1)}} V`;
        document.getElementById('p1-current').innerText = `${{p.a.current.toFixed(2)}} A`;
        document.getElementById('p1-pf').innerText = p.a.pf.toFixed(2);
        document.getElementById('p1-freq').innerText = `${{p.a.freq.toFixed(1)}} Hz`;

        document.getElementById('p2-power').innerText = `${{p.b.power > 0 ? '+' : ''}}${{p.b.power.toFixed(1)}} W`;
        document.getElementById('p2-voltage').innerText = `${{p.b.voltage.toFixed(1)}} V`;
        document.getElementById('p2-current').innerText = `${{p.b.current.toFixed(2)}} A`;
        document.getElementById('p2-pf').innerText = p.b.pf.toFixed(2);
        document.getElementById('p2-freq').innerText = `${{p.b.freq.toFixed(1)}} Hz`;

        document.getElementById('p3-power').innerText = `${{p.c.power > 0 ? '+' : ''}}${{p.c.power.toFixed(1)}} W`;
        document.getElementById('p3-voltage').innerText = `${{p.c.voltage.toFixed(1)}} V`;
        document.getElementById('p3-current').innerText = `${{p.c.current.toFixed(2)}} A`;
        document.getElementById('p3-pf').innerText = p.c.pf.toFixed(2);
        document.getElementById('p3-freq').innerText = `${{p.c.freq.toFixed(1)}} Hz`;

        // Device Info
        const d = data.device;
        document.getElementById('dev-id').innerText = d.id;
        document.getElementById('dev-mac').innerText = d.mac;
        document.getElementById('dev-fw').innerText = d.ver;

        // Automation Status
        const auto = data.automation;
        if (auto) {{
          const isEnabled = auto.config && auto.config.enabled;
          const badgeAuto = document.getElementById('badge-auto');
          badgeAuto.innerText = isEnabled ? 'Automation: Aktiv' : 'Automation: Inaktiv';
          badgeAuto.className = isEnabled ? 'badge badge-auto-on' : 'badge badge-auto-off';

          document.getElementById('regler-status-title').innerText = isEnabled ? `Regelung: Aktiv (${{auto.config.mode}})` : 'Regelung: Pausiert';
          document.getElementById('regler-status-sub').innerText = auto.state.current_status || '';
          document.getElementById('regler-target-val').innerText = `${{auto.state.last_target_power}} W`;
          document.getElementById('sb-power-val').innerText = `${{auto.state.last_target_power}} W`;

          // Decision Log
          const tbody = document.getElementById('decision-log-tbody');
          if (auto.recent_decisions && auto.recent_decisions.length > 0) {{
            tbody.innerHTML = auto.recent_decisions.slice().reverse().map(entry => `
              <tr>
                <td>${{entry.time_str}}</td>
                <td><b>${{entry.mode}}</b></td>
                <td style="color:${{entry.grid_power_w > 0 ? 'var(--badge-import)' : 'var(--badge-export)'}}">${{entry.grid_power_w > 0 ? '+' : ''}}${{entry.grid_power_w}} W</td>
                <td>${{entry.battery_soc_pct !== null ? entry.battery_soc_pct + '%' : '--'}}</td>
                <td>${{entry.current_power_w}} &rarr; <b>${{entry.target_power_w}} W</b></td>
                <td><span class="badge-action action-${{entry.action}}">${{entry.action}}</span></td>
                <td style="color:var(--text-muted);">${{entry.reason}}</td>
              </tr>
            `).join('');
          }}
        }}

      }} catch (e) {{
        console.error('Status fetch failed:', e);
      }}
    }}

    async function fetchRpcDebugLog() {{
      try {{
        const res = await fetch(ingressPath + '/api/debug');
        const data = await res.json();
        const tbody = document.getElementById('rpc-log-tbody');
        if (data.requests && data.requests.length > 0) {{
          tbody.innerHTML = data.requests.slice().reverse().map(r => `
            <tr>
              <td>${{r.time_str}}</td>
              <td><b>${{r.protocol}}</b></td>
              <td>${{r.client_ip}}</td>
              <td>${{r.http_method}}</td>
              <td><code>${{r.rpc_method || r.path}}</code></td>
              <td><span class="badge ${{r.response_status === 200 ? 'badge-ok' : 'badge-stale'}}">${{r.response_status}}</span></td>
            </tr>
          `).join('');
        }}
      }} catch (e) {{
        console.error('Debug log fetch failed:', e);
      }}
    }}

    // Initial load
    fetchStatus();
    loadEntities();
    setInterval(fetchStatus, 1500);
    setInterval(fetchRpcDebugLog, 3000);
  </script>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    async def start(self) -> None:
        """Start web server instances for port 80 and ingress port."""
        if web is None:
            raise RuntimeError("aiohttp is required to run the WebServer")

        self.app = web.Application()
        self.setup_routes(self.app)

        # 1. Main HTTP server on configured http_port (e.g. 80)
        self.runner_http = web.AppRunner(self.app)
        await self.runner_http.setup()
        site_http = web.TCPSite(self.runner_http, "0.0.0.0", self.config.http_port)
        try:
            await site_http.start()
            logger.info("Shelly HTTP API listening on 0.0.0.0:%d", self.config.http_port)
        except OSError as e:
            logger.error("Failed to bind HTTP server to port %d: %s", self.config.http_port, e)
            if getattr(e, "errno", None) in (48, 98):
                logger.critical(
                    "Port %d is already in use by another service on this host! "
                    "With 'host_network: true', no other service (e.g. Nginx, deCONZ, Pi-hole, AdGuard) may use port %d.",
                    self.config.http_port, self.config.http_port
                )
            raise

        # 2. Ingress HTTP server on ingress_port (if different from http_port)
        if self.config.ingress_port and self.config.ingress_port != self.config.http_port:
            self.runner_ingress = web.AppRunner(self.app)
            await self.runner_ingress.setup()
            site_ingress = web.TCPSite(self.runner_ingress, "0.0.0.0", self.config.ingress_port)
            try:
                await site_ingress.start()
                logger.info("Add-on Ingress dashboard listening on 0.0.0.0:%d", self.config.ingress_port)
            except OSError as e:
                logger.error(
                    "Could not bind Ingress port %d: %s. Another service is using this port!",
                    self.config.ingress_port,
                    e,
                )
                raise

    async def stop(self) -> None:
        """Cleanly shutdown HTTP servers."""
        if self.runner_ingress:
            await self.runner_ingress.cleanup()
            self.runner_ingress = None

        if self.runner_http:
            await self.runner_http.cleanup()
            self.runner_http = None

        logger.info("Web server stopped")
