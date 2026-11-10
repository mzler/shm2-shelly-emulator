"""Shelly Gen2 RPC over UDP listener (Port 1010)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Tuple

from src.shelly_service import ShellyService

logger = logging.getLogger(__name__)


class UdpRpcProtocol(asyncio.DatagramProtocol):
    """Asyncio UDP datagram protocol handling Shelly Gen2 JSON-RPC frames."""

    def __init__(self, shelly_service: ShellyService) -> None:
        self.shelly_service = shelly_service
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        client_ip, client_port = addr
        raw_text = data.decode("utf-8", errors="replace").strip()

        try:
            req_json = json.loads(raw_text)
        except Exception as e:
            logger.warning("[UDP RPC] Invalid JSON from %s:%d: %s (raw: %r)", client_ip, client_port, e, raw_text[:100])
            err_resp = {
                "id": None,
                "src": self.shelly_service.config.device_id,
                "error": {"code": -32700, "message": "Parse error"}
            }
            resp_bytes = json.dumps(err_resp).encode("utf-8")
            if self.transport:
                self.transport.sendto(resp_bytes, addr)
            self.shelly_service.record_request(
                protocol="UDP",
                client_ip=client_ip,
                client_port=client_port,
                http_method="UDP",
                path=f"udp://:{self.shelly_service.config.udp_port}",
                rpc_method="<parse_error>",
                params=None,
                body=raw_text,
                response_status=400,
                response_data=err_resp,
            )
            return

        req_id = req_json.get("id")
        method = req_json.get("method", "")
        params = req_json.get("params") or {}

        success, result_or_err = self.shelly_service.execute_rpc(method, params)

        if success:
            resp_payload: Dict[str, Any] = {
                "id": req_id,
                "src": self.shelly_service.config.device_id,
                "result": result_or_err,
            }
            status_code = 200
        else:
            resp_payload = {
                "id": req_id,
                "src": self.shelly_service.config.device_id,
                "error": result_or_err,
            }
            status_code = 404 if result_or_err.get("code") == -32601 else 500

        resp_bytes = json.dumps(resp_payload).encode("utf-8")
        if self.transport:
            self.transport.sendto(resp_bytes, addr)

        self.shelly_service.record_request(
            protocol="UDP",
            client_ip=client_ip,
            client_port=client_port,
            http_method="UDP",
            path=f"udp://:{self.shelly_service.config.udp_port}",
            rpc_method=method,
            params=params,
            body=raw_text,
            response_status=status_code,
            response_data=resp_payload,
        )


class UdpRpcServer:
    """Manages the lifecycle of the UDP RPC listener on port 1010."""

    def __init__(self, shelly_service: ShellyService, port: int = 1010) -> None:
        self.shelly_service = shelly_service
        self.port = port
        self.transport: Optional[asyncio.DatagramTransport] = None
        self._running = False

    async def start(self) -> None:
        """Bind UDP listener."""
        if self._running:
            return

        loop = asyncio.get_running_loop()
        try:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: UdpRpcProtocol(self.shelly_service),
                local_addr=("0.0.0.0", self.port),
            )
            self.transport = transport
            self._running = True
            logger.info("Shelly UDP RPC server listening on 0.0.0.0:%d", self.port)
        except Exception as e:
            logger.error("Failed to start UDP RPC server on port %d: %s", self.port, e)
            raise

    async def stop(self) -> None:
        """Stop UDP listener."""
        self._running = False
        if self.transport:
            self.transport.close()
            self.transport = None
        logger.info("Shelly UDP RPC server stopped")
