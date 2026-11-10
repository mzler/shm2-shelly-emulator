"""SMA Speedwire multicast listener and Energy Meter (EMETER) datagram decoder."""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from typing import Any, Callable, Dict, Optional, Tuple

from src.models import MeterData, PhaseMeasurement

logger = logging.getLogger(__name__)

# Official SMA Speedwire identifiers
SMA_MAGIC = b"SMA\x00"
PROTOCOL_EMETER = 0x6069
DEFAULT_MCAST_IP = "239.12.255.254"
DEFAULT_MCAST_PORT = 9522


def serial_matches(configured: str, actual_decimal: int) -> bool:
    """Check if the received device serial matches the configured filter.

    Accepts decimal string (e.g. '3002850058') or hex string (e.g. '0xb2fbdb0a' or 'b2fbdb0a').
    """
    if not configured:
        return True

    trimmed = configured.strip()
    if not trimmed:
        return True

    actual_str = str(actual_decimal)
    if trimmed == actual_str:
        return True

    # Check hex formats
    hex_candidate = trimmed.lower()
    if hex_candidate.startswith("0x"):
        hex_candidate = hex_candidate[2:]

    try:
        if int(hex_candidate, 16) == actual_decimal:
            return True
    except ValueError:
        pass

    try:
        if int(trimmed, 10) == actual_decimal:
            return True
    except ValueError:
        pass

    return False


def parse_speedwire_packet(data: bytes, filter_serial: Optional[str] = None) -> Optional[MeterData]:
    """Parse an SMA Speedwire Energy Meter datagram into MeterData.

    Returns None if:
    - Packet is not an SMA packet or protocol is not 0x6069
    - Packet does not match filter_serial (if specified)
    - Packet is malformed/truncated
    """
    if len(data) < 28:
        return None

    # 1. Magic header check
    if not data.startswith(SMA_MAGIC):
        return None

    # 2. Protocol ID check at offset 16..18
    protocol_id = struct.unpack("!H", data[16:18])[0]
    if protocol_id != PROTOCOL_EMETER:
        return None

    # 3. Serial number at offset 20..24
    serial = struct.unpack("!I", data[20:24])[0]
    if filter_serial and not serial_matches(filter_serial, serial):
        logger.debug("Discarding packet from serial %s (filter is %s)", serial, filter_serial)
        return None

    # 4. OBIS Channel decoding starting at offset 28
    offset = 28
    channels: Dict[int, Any] = {}

    while offset + 4 <= len(data):
        obis = struct.unpack("!I", data[offset:offset + 4])[0]
        offset += 4

        type_byte = obis & 0x0000FF00
        if type_byte == 0x00000400:
            if offset + 4 > len(data):
                break
            val = struct.unpack("!I", data[offset:offset + 4])[0]
            offset += 4
        elif type_byte == 0x00000800:
            if offset + 8 > len(data):
                break
            val = struct.unpack("!Q", data[offset:offset + 8])[0]
            offset += 8
        elif obis == 0x90000000:  # Firmware version block
            if offset + 4 > len(data):
                break
            val = data[offset:offset + 4]
            offset += 4
        else:
            # End of OBIS block or unknown marker
            break

        channels[obis] = val

    if not channels:
        return None

    # 5. Extract Totals
    # Active power: 0.1 W units
    total_buy = channels.get(0x00010400, 0) / 10.0
    total_sell = channels.get(0x00020400, 0) / 10.0
    total_act_power = total_buy - total_sell

    # Active energy: Ws units -> convert to Wh (1 Wh = 3600 Ws)
    total_act_energy = channels.get(0x00010800, 0) / 3600.0
    total_act_ret_energy = channels.get(0x00020800, 0) / 3600.0

    # Grid frequency: 0.001 Hz units
    freq = channels.get(0x000E0400, 0) / 1000.0
    if freq <= 0.0:
        freq = 50.0

    # 6. Extract Phase 1 (L1 / Phase A)
    p1_buy = channels.get(0x00150400, 0) / 10.0
    p1_sell = channels.get(0x00160400, 0) / 10.0
    p1_act = p1_buy - p1_sell
    p1_v = channels.get(0x00200400, 0) / 1000.0  # mV -> V
    p1_i = channels.get(0x001F0400, 0) / 1000.0  # mA -> A
    p1_pf = channels.get(0x00210400, 0) / 1000.0  # 0.001 -> [-1.0 .. 1.0]
    p1_aprt = channels.get(0x001D0400, 0) / 10.0  # 0.1 VA -> VA
    if p1_aprt == 0.0 and p1_v > 0 and p1_i > 0:
        p1_aprt = p1_v * p1_i
    if p1_pf == 0.0 and p1_aprt > 0:
        p1_pf = min(1.0, max(-1.0, p1_act / p1_aprt))
    p1_e_act = channels.get(0x00150800, 0) / 3600.0
    p1_e_ret = channels.get(0x00160800, 0) / 3600.0

    # 7. Extract Phase 2 (L2 / Phase B)
    p2_buy = channels.get(0x00290400, 0) / 10.0
    p2_sell = channels.get(0x002A0400, 0) / 10.0
    p2_act = p2_buy - p2_sell
    p2_v = channels.get(0x00340400, 0) / 1000.0
    p2_i = channels.get(0x00330400, 0) / 1000.0
    p2_pf = channels.get(0x00350400, 0) / 1000.0
    p2_aprt = channels.get(0x00310400, 0) / 10.0
    if p2_aprt == 0.0 and p2_v > 0 and p2_i > 0:
        p2_aprt = p2_v * p2_i
    if p2_pf == 0.0 and p2_aprt > 0:
        p2_pf = min(1.0, max(-1.0, p2_act / p2_aprt))
    p2_e_act = channels.get(0x00290800, 0) / 3600.0
    p2_e_ret = channels.get(0x002A0800, 0) / 3600.0

    # 8. Extract Phase 3 (L3 / Phase C)
    p3_buy = channels.get(0x003D0400, 0) / 10.0
    p3_sell = channels.get(0x003E0400, 0) / 10.0
    p3_act = p3_buy - p3_sell
    p3_v = channels.get(0x00480400, 0) / 1000.0
    p3_i = channels.get(0x00470400, 0) / 1000.0
    p3_pf = channels.get(0x00490400, 0) / 1000.0
    p3_aprt = channels.get(0x00450400, 0) / 10.0
    if p3_aprt == 0.0 and p3_v > 0 and p3_i > 0:
        p3_aprt = p3_v * p3_i
    if p3_pf == 0.0 and p3_aprt > 0:
        p3_pf = min(1.0, max(-1.0, p3_act / p3_aprt))
    p3_e_act = channels.get(0x003D0800, 0) / 3600.0
    p3_e_ret = channels.get(0x003E0800, 0) / 3600.0

    # Total apparent power
    total_aprt = channels.get(0x00090400, 0) / 10.0
    if total_aprt == 0.0:
        total_aprt = p1_aprt + p2_aprt + p3_aprt

    total_current = p1_i + p2_i + p3_i

    return MeterData(
        timestamp=time.time(),
        source="speedwire",
        serial=str(serial),
        a=PhaseMeasurement(
            current=p1_i,
            voltage=p1_v if p1_v > 0 else 230.0,
            act_power=p1_act,
            aprt_power=p1_aprt,
            pf=p1_pf,
            freq=freq,
            energy_act=p1_e_act,
            energy_act_ret=p1_e_ret,
        ),
        b=PhaseMeasurement(
            current=p2_i,
            voltage=p2_v if p2_v > 0 else 230.0,
            act_power=p2_act,
            aprt_power=p2_aprt,
            pf=p2_pf,
            freq=freq,
            energy_act=p2_e_act,
            energy_act_ret=p2_e_ret,
        ),
        c=PhaseMeasurement(
            current=p3_i,
            voltage=p3_v if p3_v > 0 else 230.0,
            act_power=p3_act,
            aprt_power=p3_aprt,
            pf=p3_pf,
            freq=freq,
            energy_act=p3_e_act,
            energy_act_ret=p3_e_ret,
        ),
        total_current=total_current,
        total_act_power=total_act_power,
        total_aprt_power=total_aprt,
        total_act_energy=total_act_energy,
        total_act_ret_energy=total_act_ret_energy,
    )


class SpeedwireReceiver:
    """Asynchronous multicast UDP receiver for SMA Speedwire datagrams."""

    def __init__(
        self,
        mcast_ip: str = DEFAULT_MCAST_IP,
        mcast_port: int = DEFAULT_MCAST_PORT,
        interface: str = "",
        serial_filter: str = "",
        on_data_callback: Optional[Callable[[MeterData], None]] = None,
    ) -> None:
        self.mcast_ip = mcast_ip
        self.mcast_port = mcast_port
        self.interface = interface
        self.serial_filter = serial_filter
        self.on_data_callback = on_data_callback

        self._running = False
        self._sock: Optional[socket.socket] = None
        self._task: Optional[asyncio.Task] = None

        # Telemetry stats
        self.packets_received = 0
        self.packets_parsed = 0
        self.packets_dropped = 0
        self.last_packet_time: float = 0.0
        self.last_serial: str = ""

    def _create_socket(self) -> socket.socket:
        """Create and bind multicast UDP socket with membership."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # SO_REUSEPORT if supported by platform
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass

        # Determine interface binding IP
        bind_ip = "0.0.0.0"
        interface_ip = socket.INADDR_ANY

        if self.interface:
            logger.info("Attempting to bind multicast to interface: %s", self.interface)
            # Find IP of interface if possible
            try:
                import fcntl
                interface_bytes = self.interface[:15].encode("utf-8")
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                ip_str = socket.inet_ntoa(
                    fcntl.ioctl(
                        s.fileno(),
                        0x8915,  # SIOCGIFADDR
                        struct.pack("256s", interface_bytes),
                    )[20:24]
                )
                bind_ip = ip_str
                interface_ip = socket.inet_aton(ip_str)
                logger.info("Resolved interface %s to IP %s", self.interface, ip_str)
            except Exception as e:
                logger.warning("Could not resolve IP for interface %s: %s (using 0.0.0.0)", self.interface, e)

        # Bind to multicast port
        sock.bind(("", self.mcast_port))

        # Join multicast group
        try:
            mreq = struct.pack("4s4s", socket.inet_aton(self.mcast_ip), interface_ip if isinstance(interface_ip, bytes) else struct.pack("=I", interface_ip))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError as e:
            # Fallback simple group join
            logger.warning("Interface-specific join failed (%s); trying INADDR_ANY", e)
            mreq = struct.pack("4s4s", socket.inet_aton(self.mcast_ip), socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        sock.setblocking(False)
        return sock

    async def start(self) -> None:
        """Start listening for Speedwire packets in the asyncio event loop."""
        if self._running:
            return

        self._running = True
        try:
            self._sock = self._create_socket()
            logger.info(
                "Speedwire receiver started on %s:%d (interface: %s, filter: %s)",
                self.mcast_ip,
                self.mcast_port,
                self.interface or "all",
                self.serial_filter or "none",
            )
        except Exception as e:
            logger.error("Failed to create Speedwire socket on port %d: %s", self.mcast_port, e)
            self._running = False
            raise

        self._task = asyncio.create_task(self._listen_loop())

    async def stop(self) -> None:
        """Stop listening and close socket."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        logger.info("Speedwire receiver stopped")

    async def _listen_loop(self) -> None:
        """Asynchronous packet receive loop."""
        loop = asyncio.get_running_loop()
        bufsize = 2048

        while self._running and self._sock:
            try:
                data = await loop.sock_recv(self._sock, bufsize)
                self.packets_received += 1
                self.last_packet_time = time.time()

                meter_data = parse_speedwire_packet(data, self.serial_filter)
                if meter_data:
                    self.packets_parsed += 1
                    self.last_serial = meter_data.serial
                    if self.on_data_callback:
                        self.on_data_callback(meter_data)
                else:
                    self.packets_dropped += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error("Error receiving/parsing Speedwire packet: %s", e)
                    await asyncio.sleep(0.5)
