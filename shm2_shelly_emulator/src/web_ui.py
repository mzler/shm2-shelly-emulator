"""HTTP server and Ingress web dashboard for Shelly Pro 3EM emulator."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

try:
    from aiohttp import web
except ImportError:
    web = None  # type: ignore

from src.config import AppConfig
from src.shelly_service import ShellyService

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
    """HTTP web server handling Shelly JSON-RPC requests and the Ingress dashboard."""

    def __init__(self, config: AppConfig, shelly_service: ShellyService) -> None:
        self.config = config
        self.shelly_service = shelly_service
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
            status_code = 200  # In JSON-RPC 2.0, errors are returned with HTTP 200 payload

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
        """Handle GET /rpc/<Method>?<params> (e.g. GET /rpc/EM.GetStatus?id=0)."""
        client_ip = request.remote or "unknown"
        method = request.match_info.get("method", "")
        params = parse_query_params(request.query)

        success, result_or_err = self.shelly_service.execute_rpc(method, params)

        # GET /rpc/<Method> returns bare result object directly
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
        """Handle POST /rpc/<Method> with parameter body."""
        client_ip = request.remote or "unknown"
        method = request.match_info.get("method", "")
        body_text = await request.text()

        try:
            params = json.loads(body_text) if body_text else {}
        except Exception:
            params = {}

        # If body is a full JSON-RPC payload with its own method, use that
        if isinstance(params, dict) and "method" in params:
            method = params["method"]
            params = params.get("params") or {}

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

        # Default info
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
            }
        })

    async def handle_api_debug(self, request: web.Request) -> web.Response:
        """Return the recent incoming request history."""
        return web.json_response({
            "requests": list(self.shelly_service.recent_requests)
        })

    async def handle_dashboard(self, request: web.Request) -> web.Response:
        """Render modern, interactive dashboard HTML."""
        ingress_path = request.headers.get("X-Ingress-Path", "").rstrip("/")

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SHM2 Shelly Pro 3EM Emulator</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --card-border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #38bdf8;
      --success: #22c55e;
      --warning: #f59e0b;
      --danger: #ef4444;
      --badge-import: #3b82f6;
      --badge-export: #10b981;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 1.5rem;
      line-height: 1.5;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.5rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--card-border);
      flex-wrap: wrap;
      gap: 1rem;
    }}
    h1 {{ font-size: 1.5rem; font-weight: 700; color: var(--primary); }}
    .badge {{
      display: inline-block;
      padding: 0.25rem 0.6rem;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
    }}
    .badge-ok {{ background: rgba(34, 197, 94, 0.2); color: var(--success); border: 1px solid var(--success); }}
    .badge-stale {{ background: rgba(239, 68, 68, 0.2); color: var(--danger); border: 1px solid var(--danger); }}
    .badge-src {{ background: rgba(56, 189, 248, 0.2); color: var(--primary); border: 1px solid var(--primary); }}

    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 1.25rem;
      margin-bottom: 1.5rem;
    }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 0.75rem;
      padding: 1.25rem;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    }}
    .card-title {{
      font-size: 0.875rem;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 0.75rem;
    }}
    .stat-hero {{
      font-size: 2.25rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      margin: 0.25rem 0;
    }}
    .stat-sub {{ font-size: 0.875rem; color: var(--text-muted); }}

    .phase-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.5rem;
      margin-top: 0.75rem;
      font-size: 0.875rem;
    }}
    .phase-val {{ font-weight: 600; color: var(--text); }}
    .phase-label {{ color: var(--text-muted); }}

    /* Table styling */
    .table-container {{ overflow-x: auto; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.8125rem;
      text-align: left;
    }}
    th {{
      background: rgba(15, 23, 42, 0.5);
      color: var(--text-muted);
      padding: 0.6rem 0.75rem;
      border-bottom: 1px solid var(--card-border);
      font-weight: 600;
    }}
    td {{
      padding: 0.6rem 0.75rem;
      border-bottom: 1px solid var(--card-border);
      vertical-align: top;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}
    tr:hover td {{ background: rgba(255, 255, 255, 0.02); }}

    .curl-box {{
      background: #090d16;
      border: 1px solid var(--card-border);
      border-radius: 0.5rem;
      padding: 0.75rem;
      font-family: ui-monospace, monospace;
      font-size: 0.8125rem;
      color: var(--primary);
      margin-top: 0.5rem;
      word-break: break-all;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>SHM2 Shelly Emulator</h1>
        <div class="stat-sub">Emulating Shelly Pro 3EM (Gen 2) &bull; Model: {self.config.model} &bull; ID: <span id="dev-id">{self.config.device_id}</span></div>
      </div>
      <div style="display: flex; gap: 0.5rem; align-items: center;">
        <span id="badge-source" class="badge badge-src">{self.config.data_source.upper()}</span>
        <span id="badge-status" class="badge badge-ok">LIVE</span>
      </div>
    </header>

    <!-- Main Live Stats Cards -->
    <div class="grid">
      <!-- Total Power Card -->
      <div class="card">
        <div class="card-title">Total Active Grid Power</div>
        <div id="hero-power" class="stat-hero">-- W</div>
        <div id="hero-direction" class="stat-sub">Waiting for measurements...</div>
        <div style="margin-top: 1rem; border-top: 1px solid var(--card-border); padding-top: 0.75rem;">
          <div style="display: flex; justify-content: space-between; font-size: 0.875rem;">
            <span class="phase-label">Total Current:</span>
            <span id="hero-current" class="phase-val">-- A</span>
          </div>
          <div style="display: flex; justify-content: space-between; font-size: 0.875rem; margin-top: 0.25rem;">
            <span class="phase-label">Apparent Power:</span>
            <span id="hero-aprt" class="phase-val">-- VA</span>
          </div>
        </div>
      </div>

      <!-- Energy Counters Card -->
      <div class="card">
        <div class="card-title">Accumulated Energy (EMData)</div>
        <div style="margin-top: 0.5rem;">
          <div style="margin-bottom: 0.75rem;">
            <div class="phase-label">Total Grid Import (Purchase):</div>
            <div id="energy-import" style="font-size: 1.25rem; font-weight: 700; color: var(--primary);">-- kWh</div>
          </div>
          <div style="margin-bottom: 0.75rem;">
            <div class="phase-label">Total Grid Export (Feed-in):</div>
            <div id="energy-export" style="font-size: 1.25rem; font-weight: 700; color: var(--success);">-- kWh</div>
          </div>
          <div class="stat-sub">Sign Inversion: <strong>{'ENABLED' if self.config.invert_sign else 'OFF'}</strong></div>
        </div>
      </div>

      <!-- Device Information Card -->
      <div class="card">
        <div class="card-title">Device & Network Identity</div>
        <div class="phase-grid">
          <div><span class="phase-label">Device MAC:</span><br><span id="dev-mac" class="phase-val">{self.config.emulated_mac}</span></div>
          <div><span class="phase-label">Firmware:</span><br><span class="phase-val">{self.config.firmware_version}</span></div>
          <div><span class="phase-label">HTTP Port:</span><br><span class="phase-val">{self.config.http_port}</span></div>
          <div><span class="phase-label">UDP RPC Port:</span><br><span class="phase-val">{self.config.udp_port}</span></div>
          <div><span class="phase-label">SHM2 Serial:</span><br><span id="dev-serial" class="phase-val">--</span></div>
          <div><span class="phase-label">Data Age:</span><br><span id="dev-age" class="phase-val">-- s</span></div>
        </div>
        <div class="curl-box">
          curl http://&lt;IP&gt;/rpc/EM.GetStatus?id=0
        </div>
      </div>
    </div>

    <!-- Per Phase Breakdown -->
    <div class="grid">
      <!-- Phase A / L1 -->
      <div class="card">
        <div class="card-title" style="color: #60a5fa;">Phase A (L1)</div>
        <div id="p1-power" class="stat-hero" style="font-size: 1.75rem;">-- W</div>
        <div class="phase-grid">
          <div><span class="phase-label">Voltage:</span> <span id="p1-v" class="phase-val">-- V</span></div>
          <div><span class="phase-label">Current:</span> <span id="p1-i" class="phase-val">-- A</span></div>
          <div><span class="phase-label">Power Factor:</span> <span id="p1-pf" class="phase-val">--</span></div>
          <div><span class="phase-label">Frequency:</span> <span id="p1-freq" class="phase-val">-- Hz</span></div>
          <div><span class="phase-label">Import:</span> <span id="p1-e" class="phase-val">-- kWh</span></div>
          <div><span class="phase-label">Export:</span> <span id="p1-er" class="phase-val">-- kWh</span></div>
        </div>
      </div>

      <!-- Phase B / L2 -->
      <div class="card">
        <div class="card-title" style="color: #a78bfa;">Phase B (L2)</div>
        <div id="p2-power" class="stat-hero" style="font-size: 1.75rem;">-- W</div>
        <div class="phase-grid">
          <div><span class="phase-label">Voltage:</span> <span id="p2-v" class="phase-val">-- V</span></div>
          <div><span class="phase-label">Current:</span> <span id="p2-i" class="phase-val">-- A</span></div>
          <div><span class="phase-label">Power Factor:</span> <span id="p2-pf" class="phase-val">--</span></div>
          <div><span class="phase-label">Frequency:</span> <span id="p2-freq" class="phase-val">-- Hz</span></div>
          <div><span class="phase-label">Import:</span> <span id="p2-e" class="phase-val">-- kWh</span></div>
          <div><span class="phase-label">Export:</span> <span id="p2-er" class="phase-val">-- kWh</span></div>
        </div>
      </div>

      <!-- Phase C / L3 -->
      <div class="card">
        <div class="card-title" style="color: #f472b6;">Phase C (L3)</div>
        <div id="p3-power" class="stat-hero" style="font-size: 1.75rem;">-- W</div>
        <div class="phase-grid">
          <div><span class="phase-label">Voltage:</span> <span id="p3-v" class="phase-val">-- V</span></div>
          <div><span class="phase-label">Current:</span> <span id="p3-i" class="phase-val">-- A</span></div>
          <div><span class="phase-label">Power Factor:</span> <span id="p3-pf" class="phase-val">--</span></div>
          <div><span class="phase-label">Frequency:</span> <span id="p3-freq" class="phase-val">-- Hz</span></div>
          <div><span class="phase-label">Import:</span> <span id="p3-e" class="phase-val">-- kWh</span></div>
          <div><span class="phase-label">Export:</span> <span id="p3-er" class="phase-val">-- kWh</span></div>
        </div>
      </div>
    </div>

    <!-- Live Request Log & Discovery Monitor -->
    <div class="card" style="margin-top: 1.5rem;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem;">
        <div class="card-title" style="margin-bottom: 0;">Incoming Request Log &amp; Discovery Monitor (Anker / Solix / App)</div>
        <span class="stat-sub" id="req-count">0 recorded</span>
      </div>
      <div class="stat-sub" style="margin-bottom: 0.75rem;">
        Every HTTP and UDP request is captured here in real-time so you can observe what endpoints the Anker app or Solarbank queries.
      </div>
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Proto</th>
              <th>Source</th>
              <th>Method / Path</th>
              <th>Params / Body</th>
              <th>Code</th>
              <th>Response Summary</th>
            </tr>
          </thead>
          <tbody id="debug-table">
            <tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No requests recorded yet. Query the emulator to see logs.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <script>
    const basePath = "{ingress_path}";

    async function fetchStatus() {{
      try {{
        const resp = await fetch(basePath + '/api/status');
        if (!resp.ok) return;
        const data = await resp.json();

        // Status badge
        const badge = document.getElementById('badge-status');
        if (data.source.is_stale) {{
          badge.textContent = 'STALE (' + data.source.data_age_sec + 's)';
          badge.className = 'badge badge-stale';
        }} else {{
          badge.textContent = 'LIVE';
          badge.className = 'badge badge-ok';
        }}

        // Total Hero Power
        const pTot = data.measurements.total_act_power;
        const heroP = document.getElementById('hero-power');
        const heroDir = document.getElementById('hero-direction');
        heroP.textContent = pTot.toFixed(1) + ' W';
        if (pTot > 0) {{
          heroP.style.color = 'var(--primary)';
          heroDir.textContent = 'Importing from Grid (Grid Consumption)';
        }} else if (pTot < 0) {{
          heroP.style.color = 'var(--success)';
          heroDir.textContent = 'Exporting to Grid (PV Feed-in)';
        }} else {{
          heroP.style.color = 'var(--text)';
          heroDir.textContent = 'Balanced / Zero Net Grid Power';
        }}

        document.getElementById('hero-current').textContent = data.measurements.total_current.toFixed(3) + ' A';
        document.getElementById('hero-aprt').textContent = data.measurements.total_aprt_power.toFixed(1) + ' VA';

        // Energy
        document.getElementById('energy-import').textContent = data.measurements.total_act_energy_kwh.toFixed(3) + ' kWh';
        document.getElementById('energy-export').textContent = data.measurements.total_act_ret_energy_kwh.toFixed(3) + ' kWh';

        // Info
        document.getElementById('dev-serial').textContent = data.source.serial;
        document.getElementById('dev-age').textContent = data.source.data_age_sec + ' s';

        // Phases
        const pa = data.measurements.phases.a;
        document.getElementById('p1-power').textContent = pa.power.toFixed(1) + ' W';
        document.getElementById('p1-v').textContent = pa.voltage.toFixed(1) + ' V';
        document.getElementById('p1-i').textContent = pa.current.toFixed(3) + ' A';
        document.getElementById('p1-pf').textContent = pa.pf.toFixed(2);
        document.getElementById('p1-freq').textContent = pa.freq.toFixed(1) + ' Hz';
        document.getElementById('p1-e').textContent = pa.energy_kwh.toFixed(3) + ' kWh';
        document.getElementById('p1-er').textContent = pa.energy_ret_kwh.toFixed(3) + ' kWh';

        const pb = data.measurements.phases.b;
        document.getElementById('p2-power').textContent = pb.power.toFixed(1) + ' W';
        document.getElementById('p2-v').textContent = pb.voltage.toFixed(1) + ' V';
        document.getElementById('p2-i').textContent = pb.current.toFixed(3) + ' A';
        document.getElementById('p2-pf').textContent = pb.pf.toFixed(2);
        document.getElementById('p2-freq').textContent = pb.freq.toFixed(1) + ' Hz';
        document.getElementById('p2-e').textContent = pb.energy_kwh.toFixed(3) + ' kWh';
        document.getElementById('p2-er').textContent = pb.energy_ret_kwh.toFixed(3) + ' kWh';

        const pc = data.measurements.phases.c;
        document.getElementById('p3-power').textContent = pc.power.toFixed(1) + ' W';
        document.getElementById('p3-v').textContent = pc.voltage.toFixed(1) + ' V';
        document.getElementById('p3-i').textContent = pc.current.toFixed(3) + ' A';
        document.getElementById('p3-pf').textContent = pc.pf.toFixed(2);
        document.getElementById('p3-freq').textContent = pc.freq.toFixed(1) + ' Hz';
        document.getElementById('p3-e').textContent = pc.energy_kwh.toFixed(3) + ' kWh';
        document.getElementById('p3-er').textContent = pc.energy_ret_kwh.toFixed(3) + ' kWh';

      }} catch (e) {{
        console.error('Error fetching live status:', e);
      }}
    }}

    async function fetchDebugLog() {{
      try {{
        const resp = await fetch(basePath + '/api/debug');
        if (!resp.ok) return;
        const data = await resp.json();
        const tbody = document.getElementById('debug-table');
        document.getElementById('req-count').textContent = data.requests.length + ' recorded';

        if (data.requests.length === 0) {{
          tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No requests recorded yet.</td></tr>';
          return;
        }}

        let rows = '';
        for (const r of data.requests.slice(0, 50)) {{
          const isErr = r.response_status >= 400;
          const statusStyle = isErr ? 'color: var(--danger); font-weight: bold;' : 'color: var(--success);';
          const protoBadge = r.protocol === 'UDP' ? '<span class="badge" style="background:#475569;">UDP</span>' : '<span class="badge" style="background:#0284c7;">HTTP</span>';
          const methodDesc = r.rpc_method ? r.rpc_method : r.path;
          const respSummary = typeof r.response_data === 'object' ? JSON.stringify(r.response_data).slice(0, 60) + '...' : String(r.response_data);

          rows += `<tr>
            <td style="color: var(--text-muted);">${{r.timestamp}}</td>
            <td>${{protoBadge}}</td>
            <td>${{r.client_ip}}</td>
            <td><strong>${{methodDesc}}</strong><br><span style="color:var(--text-muted);font-size:0.75rem;">${{r.http_method}} ${{r.path}}</span></td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;">${{JSON.stringify(r.params || '')}}</td>
            <td style="${{statusStyle}}">${{r.response_status}}</td>
            <td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;color:var(--text-muted);">${{respSummary}}</td>
          </tr>`;
        }}
        tbody.innerHTML = rows;
      }} catch (e) {{
        console.error('Error fetching debug log:', e);
      }}
    }}

    fetchStatus();
    fetchDebugLog();
    setInterval(fetchStatus, 1000);
    setInterval(fetchDebugLog, 2000);
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
                logger.warning("Could not bind Ingress port %d: %s", self.config.ingress_port, e)

    async def stop(self) -> None:
        """Cleanly shutdown HTTP servers."""
        if self.runner_ingress:
            await self.runner_ingress.cleanup()
            self.runner_ingress = None

        if self.runner_http:
            await self.runner_http.cleanup()
            self.runner_http = None

        logger.info("Web server stopped")
