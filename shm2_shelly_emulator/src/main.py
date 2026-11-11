"""Main entry point for the SHM2 Shelly Pro 3EM Emulator add-on."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from typing import Optional

from src.config import AppConfig
from src.controller import AutomationController
from src.ha_client import HomeAssistantClient
from src.mdns_service import MdnsService
from src.models import MeterData
from src.shelly_service import ShellyService
from src.speedwire import SpeedwireReceiver
from src.udp_rpc import UdpRpcServer
from src.web_ui import WebServer

logger = logging.getLogger("shm2_emulator")


async def periodic_logger_task(shelly_service: ShellyService, interval: float = 10.0) -> None:
    """Print periodic live measurements and connection status to the add-on log."""
    while True:
        await asyncio.sleep(interval)
        data, is_stale = shelly_service.get_effective_meter_data()
        age = time.time() - data.timestamp if data.timestamp > 0 else 999.0

        if data.timestamp <= 0:
            logger.info("[STATUS] Waiting for first grid telemetry packet from data source (%s)...", shelly_service.config.data_source)
            continue

        direction = "IMPORT" if data.total_act_power > 0 else "EXPORT" if data.total_act_power < 0 else "ZERO"
        stale_status = "STALE!" if is_stale else "OK"

        logger.info(
            "[LIVE %s] Net: %+.1f W (%s) | L1: %+.1f W (%.1f V, %.2f A) | L2: %+.1f W (%.1f V, %.2f A) | L3: %+.1f W (%.1f V, %.2f A) | Age: %.1fs | Status: %s",
            data.source.upper(),
            data.total_act_power,
            direction,
            data.a.act_power, data.a.voltage, data.a.current,
            data.b.act_power, data.b.voltage, data.b.current,
            data.c.act_power, data.c.voltage, data.c.current,
            age,
            stale_status,
        )


async def async_main() -> None:
    """Main application loop."""
    config = AppConfig.load()

    # Configure logging
    log_level = getattr(logging, config.log_level, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger.info("=" * 70)
    logger.info("Starting SHM2 Shelly Pro 3EM Emulator")
    logger.info("Device ID:      %s", config.device_id)
    logger.info("Emulated MAC:   %s", config.emulated_mac)
    logger.info("Model:          %s (Gen 2)", config.model)
    logger.info("Firmware:       %s", config.firmware_version)
    logger.info("Data Source:    %s", config.data_source)
    if config.shm2_serial:
        logger.info("SHM2 Serial Filter: %s", config.shm2_serial)
    logger.info("HTTP Port:      %d", config.http_port)
    logger.info("UDP RPC Port:   %d", config.udp_port)
    logger.info("Sign Invert:    %s", "YES" if config.invert_sign else "NO (Positive=Import, Negative=Export)")
    logger.info("Stale Timeout:  %.1f s", config.stale_timeout)
    logger.info("Debug Requests: %s", "ENABLED" if config.debug_requests else "DISABLED")
    logger.info("=" * 70)

    shelly_service = ShellyService(config)

    # 1. Initialize Home Assistant client (used for Solarbank automation & entity discovery)
    ha_client = HomeAssistantClient(
        api_url=config.ha_url,
        token=config.ha_token,
        entity_map=config.ha_entities,
        poll_interval=1.0,
        on_data_callback=shelly_service.update_meter_data if config.data_source == "homeassistant" else None,
    )

    # Initialize grid telemetry data source
    speedwire_receiver: Optional[SpeedwireReceiver] = None

    if config.data_source == "speedwire":
        speedwire_receiver = SpeedwireReceiver(
            mcast_ip=config.multicast_ip,
            mcast_port=config.multicast_port,
            interface=config.network_interface,
            serial_filter=config.shm2_serial,
            on_data_callback=shelly_service.update_meter_data,
        )
        await speedwire_receiver.start()
    elif config.data_source == "homeassistant":
        await ha_client.start()
    else:
        logger.error("Unknown data_source '%s'. Valid values: 'speedwire', 'homeassistant'", config.data_source)
        sys.exit(1)

    # 2. Initialize Solarbank Intelligent Automation Controller
    automation_controller = AutomationController(ha_client, shelly_service)
    await automation_controller.start()

    # 3. Start UDP RPC on port 1010
    udp_server = UdpRpcServer(shelly_service, port=config.udp_port)
    await udp_server.start()

    # 4. Start HTTP server on port 80 & Ingress port
    web_server = WebServer(config, shelly_service, automation_controller, ha_client)
    await web_server.start()

    # 5. Start mDNS announcement (_shelly._tcp and _http._tcp)
    mdns_service = MdnsService(config)
    await mdns_service.start()

    # 6. Start periodic logger
    logger_task = asyncio.create_task(periodic_logger_task(shelly_service, interval=10.0))

    # Graceful shutdown event
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def signal_handler() -> None:
        logger.info("Received termination signal, shutting down gracefully...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Signal handling on Windows / limited environments
            pass

    logger.info("SHM2 Shelly Emulator running. Press Ctrl+C to terminate.")

    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        logger.info("Shutting down background services...")
        logger_task.cancel()
        await automation_controller.stop()
        await mdns_service.stop()
        await web_server.stop()
        await udp_server.stop()
        if speedwire_receiver:
            await speedwire_receiver.stop()
        await ha_client.stop()
        logger.info("SHM2 Shelly Emulator stopped cleanly.")


def main() -> None:
    """Synchronous launcher."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
