"""mDNS / Zeroconf announcement for Shelly Pro 3EM discovery."""

from __future__ import annotations

import logging
import socket
from typing import List, Optional

try:
    from zeroconf import IPVersion, ServiceInfo, Zeroconf
except ImportError:
    Zeroconf = None  # type: ignore
    ServiceInfo = None  # type: ignore
    IPVersion = None  # type: ignore

from src.config import AppConfig

logger = logging.getLogger(__name__)


def get_local_ip(interface: str = "") -> str:
    """Determine the local IPv4 address for mDNS announcement."""
    if interface:
        try:
            import fcntl
            import struct
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            return socket.inet_ntoa(
                fcntl.ioctl(
                    s.fileno(),
                    0x8915,  # SIOCGIFADDR
                    struct.pack("256s", interface[:15].encode("utf-8")),
                )[20:24]
            )
        except Exception as e:
            logger.debug("Could not resolve IP for interface %s: %s", interface, e)

    # Standard routing lookup
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        pass

    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


class MdnsService:
    """Manages Zeroconf / mDNS announcements for _shelly._tcp and _http._tcp."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.zeroconf: Optional[Zeroconf] = None
        self.services: List[ServiceInfo] = []
        self._running = False

    async def start(self) -> None:
        """Register mDNS services."""
        if Zeroconf is None:
            logger.warning("zeroconf library is not installed; mDNS discovery announcement is disabled")
            return

        if self._running:
            return

        local_ip = get_local_ip(self.config.network_interface)
        ip_bytes = socket.inet_aton(local_ip)
        device_id = self.config.device_id
        server_host = f"{device_id}.local."

        logger.info(
            "Registering mDNS services for %s (IP: %s, HTTP port: %d)",
            device_id,
            local_ip,
            self.config.http_port,
        )

        try:
            self.zeroconf = Zeroconf(ip_version=IPVersion.V4Only)

            # 1. _shelly._tcp service announcement
            shelly_txt = {
                "gen": "2",
                "app": "Pro3EM",
                "ver": self.config.firmware_version,
                "id": device_id,
                "model": self.config.model,
                "profile": "triphase",
            }
            shelly_info = ServiceInfo(
                type_="_shelly._tcp.local.",
                name=f"{device_id}._shelly._tcp.local.",
                addresses=[ip_bytes],
                port=self.config.http_port,
                properties={k.encode(): v.encode() for k, v in shelly_txt.items()},
                server=server_host,
            )

            # 2. _http._tcp service announcement
            http_txt = {
                "gen": "2",
                "app": "Pro3EM",
                "id": device_id,
                "path": "/",
            }
            http_info = ServiceInfo(
                type_="_http._tcp.local.",
                name=f"{device_id}._http._tcp.local.",
                addresses=[ip_bytes],
                port=self.config.http_port,
                properties={k.encode(): v.encode() for k, v in http_txt.items()},
                server=server_host,
            )

            await self.zeroconf.async_register_service(shelly_info)
            await self.zeroconf.async_register_service(http_info)

            self.services = [shelly_info, http_info]
            self._running = True
            logger.info("mDNS announcement active (_shelly._tcp and _http._tcp)")
        except Exception as e:
            logger.error("Failed to register mDNS services: %s", e)
            if self.zeroconf:
                try:
                    await self.zeroconf.async_close()
                except Exception:
                    pass
                self.zeroconf = None

    async def stop(self) -> None:
        """Unregister all mDNS announcements."""
        if not self._running or not self.zeroconf:
            return

        self._running = False
        try:
            for s in self.services:
                await self.zeroconf.async_unregister_service(s)
            await self.zeroconf.async_close()
            logger.info("mDNS services unregistered and stopped")
        except Exception as e:
            logger.warning("Error stopping mDNS services: %s", e)
        finally:
            self.zeroconf = None
            self.services.clear()
