"""Shelly Pro 3EM Gen2 RPC emulation service, method registry, and debug request logger."""

from __future__ import annotations

import collections
import datetime
import json
import logging
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

from src.config import AppConfig
from src.models import MeterData

logger = logging.getLogger(__name__)


class RequestLogEntry:
    """Record of an incoming RPC/HTTP request for discovery and debugging."""

    def __init__(
        self,
        protocol: str,
        client_ip: str,
        client_port: int,
        http_method: str,
        path: str,
        rpc_method: str,
        params: Any,
        body: str,
        response_status: int,
        response_data: Any,
    ) -> None:
        self.timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        self.protocol = protocol
        self.client_ip = client_ip
        self.client_port = client_port
        self.http_method = http_method
        self.path = path
        self.rpc_method = rpc_method
        self.params = params
        self.body = body
        self.response_status = response_status
        self.response_data = response_data

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "protocol": self.protocol,
            "client_ip": self.client_ip,
            "client_port": self.client_port,
            "http_method": self.http_method,
            "path": self.path,
            "rpc_method": self.rpc_method,
            "params": self.params,
            "body": self.body,
            "response_status": self.response_status,
            "response_data": self.response_data,
        }


class ShellyService:
    """Handles Shelly Pro 3EM RPC method dispatch, status calculations, and request tracing."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.start_time = time.time()
        self.current_meter_data = MeterData()
        self._stale_warning_logged = False

        # Ring buffer of recent requests for debugging & discovery
        self.recent_requests: Deque[Dict[str, Any]] = collections.deque(maxlen=100)

        # Registered Gen2 RPC methods
        self.methods: Dict[str, Any] = {
            "Shelly.GetDeviceInfo": self.handle_shelly_get_device_info,
            "Shelly.GetStatus": self.handle_shelly_get_status,
            "Shelly.GetConfig": self.handle_shelly_get_config,
            "Shelly.ListMethods": self.handle_shelly_list_methods,
            "Shelly.CheckForUpdate": self.handle_shelly_check_for_update,
            "EM.GetStatus": self.handle_em_get_status,
            "EM.GetConfig": self.handle_em_get_config,
            "EMData.GetStatus": self.handle_emdata_get_status,
            "Sys.GetStatus": self.handle_sys_get_status,
            "Sys.GetConfig": self.handle_sys_get_config,
            "Wifi.GetStatus": self.handle_wifi_get_status,
            "Wifi.GetConfig": self.handle_wifi_get_config,
            "Eth.GetStatus": self.handle_eth_get_status,
            "Eth.GetConfig": self.handle_eth_get_config,
            "Cloud.GetStatus": self.handle_cloud_get_status,
            "BLE.GetStatus": self.handle_ble_get_status,
        }

    def update_meter_data(self, data: MeterData) -> None:
        """Update current electrical measurements."""
        self.current_meter_data = data
        self._stale_warning_logged = False

    def get_effective_meter_data(self) -> Tuple[MeterData, bool]:
        """Return meter data with sign inversion applied and stale status."""
        data = self.current_meter_data
        is_stale = data.is_stale(self.config.stale_timeout)

        if is_stale:
            age = time.time() - data.timestamp if data.timestamp > 0 else 999.0
            if not self._stale_warning_logged:
                logger.warning(
                    "[STALE WARNING] No new grid meter data received for %.1fs (timeout: %.1fs). Returning stale error flags.",
                    age,
                    self.config.stale_timeout,
                )
                self._stale_warning_logged = True
        else:
            self._stale_warning_logged = False

        if self.config.invert_sign:
            data = data.apply_inversion()

        return data, is_stale

    def record_request(
        self,
        protocol: str,
        client_ip: str,
        client_port: int,
        http_method: str,
        path: str,
        rpc_method: str,
        params: Any,
        body: str,
        response_status: int,
        response_data: Any,
    ) -> None:
        """Log incoming request to ring buffer and stdout/log if debugging is enabled."""
        entry = RequestLogEntry(
            protocol=protocol,
            client_ip=client_ip,
            client_port=client_port,
            http_method=http_method,
            path=path,
            rpc_method=rpc_method,
            params=params,
            body=body,
            response_status=response_status,
            response_data=response_data,
        )
        self.recent_requests.appendleft(entry.to_dict())

        if self.config.debug_requests:
            logger.info(
                "[%s DEBUG] %s %s from %s:%d | method=%s params=%s -> code=%d",
                protocol,
                http_method,
                path,
                client_ip,
                client_port,
                rpc_method or "N/A",
                params if params is not None else "{}",
                response_status,
            )

    # -------------------------------------------------------------------------
    # RPC Method Handlers
    # -------------------------------------------------------------------------

    def handle_shelly_get_device_info(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement Shelly.GetDeviceInfo for Shelly Pro 3EM (Gen 2)."""
        return {
            "name": self.config.device_name,
            "id": self.config.device_id,
            "mac": self.config.mac_clean,
            "model": self.config.model,
            "gen": 2,
            "fw_id": f"20241011-114455/{self.config.firmware_version}-g6d2a586",
            "ver": self.config.firmware_version,
            "app": "Pro3EM",
            "auth_en": False,
            "auth_domain": None,
            "profile": "triphase",
            "cloud": {
                "connected": False
            },
            "connectivity": {
                "wifi": {
                    "sta_ip": "192.168.1.100",
                    "status": "got ip",
                    "ssid": "LocalNetwork",
                    "rssi": -55
                },
                "ethernet": {
                    "ip": "192.168.1.100",
                    "status": "connected"
                }
            }
        }

    def handle_em_get_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement EM.GetStatus (id: 0) returning instantaneous phase measurements."""
        data, is_stale = self.get_effective_meter_data()
        return data.to_em_status(is_stale_flag=is_stale)

    def handle_em_get_config(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement EM.GetConfig (id: 0) returning component configuration."""
        return {
            "id": 0,
            "name": None,
            "blink_mode_selector": None,
            "phase_selector": 0,
            "phase_sequence_detection_enabled": True,
            "ct_type": "120A"
        }

    def handle_emdata_get_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement EMData.GetStatus (id: 0) returning energy counters in Wh."""
        data, _ = self.get_effective_meter_data()
        return data.to_emdata_status()

    def handle_sys_get_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement Sys.GetStatus returning uptime and system health."""
        uptime = int(time.time() - self.start_time)
        now_dt = datetime.datetime.now()
        return {
            "mac": self.config.mac_clean,
            "restart_required": False,
            "time": now_dt.strftime("%H:%M"),
            "unixtime": int(time.time()),
            "uptime": uptime,
            "ram_size": 262144,
            "ram_free": 178200,
            "fs_size": 524288,
            "fs_free": 245760,
            "cfg_rev": 1,
            "kvs_rev": 0,
            "schedule_rev": 0,
            "webhook_rev": 0,
            "device": {
                "name": self.config.device_name,
                "mac": self.config.mac_clean,
                "model": self.config.model,
                "gen": 2,
                "firmware": self.config.firmware_version,
                "com_id": 0
            },
            "can_update": False,
            "ota": {
                "version": self.config.firmware_version,
                "status": "idle"
            }
        }

    def handle_wifi_get_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement Wifi.GetStatus."""
        return {
            "sta_ip": "192.168.1.100",
            "status": "got ip",
            "ssid": "LocalNetwork",
            "rssi": -55
        }

    def handle_eth_get_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement Eth.GetStatus."""
        return {
            "ip": "192.168.1.100"
        }

    def handle_cloud_get_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement Cloud.GetStatus."""
        return {
            "connected": False
        }

    def handle_ble_get_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement BLE.GetStatus."""
        return {}

    def handle_shelly_get_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement Shelly.GetStatus returning aggregated status of all components."""
        data, is_stale = self.get_effective_meter_data()
        return {
            "ble": {},
            "cloud": {"connected": False},
            "em:0": data.to_em_status(is_stale_flag=is_stale),
            "emdata:0": data.to_emdata_status(),
            "eth": {"ip": "192.168.1.100"},
            "modbus": {},
            "mqtt": {"connected": False},
            "sys": self.handle_sys_get_status(params),
            "wifi": self.handle_wifi_get_status(params),
        }

    def handle_shelly_get_config(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement Shelly.GetConfig returning aggregated configuration."""
        return {
            "ble": {"enable": False},
            "cloud": {"enable": False},
            "em:0": self.handle_em_get_config(params),
            "eth": {"enable": True, "ipv4mode": "dhcp"},
            "sys": self.handle_sys_get_config(params),
            "wifi": self.handle_wifi_get_config(params),
        }

    def handle_sys_get_config(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement Sys.GetConfig."""
        return {
            "device": {
                "name": self.config.device_name,
                "mac": self.config.mac_clean,
                "profile": "triphase"
            },
            "location": {"tz": "Europe/Berlin"},
            "rpc_udp": {"listen_port": self.config.udp_port}
        }

    def handle_wifi_get_config(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement Wifi.GetConfig."""
        return {
            "ap": {"enable": False},
            "sta": {"enable": True, "ipv4mode": "dhcp"}
        }

    def handle_eth_get_config(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement Eth.GetConfig."""
        return {
            "enable": True,
            "ipv4mode": "dhcp"
        }

    def handle_shelly_list_methods(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement Shelly.ListMethods returning available method names."""
        return {
            "methods": sorted(list(self.methods.keys()))
        }

    def handle_shelly_check_for_update(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Implement Shelly.CheckForUpdate."""
        return {
            "status": "idle",
            "has_update": False,
            "version": self.config.firmware_version
        }

    # -------------------------------------------------------------------------
    # Dispatching & JSON-RPC execution
    # -------------------------------------------------------------------------

    def execute_rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Tuple[bool, Any]:
        """Execute a method by name.

        Returns (success: bool, result_or_error_dict).
        """
        if params is None:
            params = {}

        handler = self.methods.get(method)
        if not handler:
            error = {
                "code": -32601,
                "message": f"Method '{method}' not found",
                "data": {"method": method}
            }
            return False, error

        try:
            result = handler(params)
            return True, result
        except Exception as e:
            logger.exception("Error executing RPC method %s: %s", method, e)
            error = {
                "code": -32000,
                "message": f"Server error executing '{method}': {str(e)}"
            }
            return False, error
