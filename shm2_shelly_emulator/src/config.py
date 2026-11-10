"""Configuration loader and device identity manager."""

from __future__ import annotations

import json
import logging
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def generate_stable_mac(storage_dir: Path) -> str:
    """Load or generate a persistent Shelly Pro 3EM MAC address.

    Uses Shelly/Allterco OUIs (such as 34:98:7A, C8:F0:9E, EC:64:C9, 08:3A:8D).
    """
    mac_file = storage_dir / "emulated_mac.txt"
    if mac_file.exists():
        try:
            stored_mac = mac_file.read_text(encoding="utf-8").strip()
            if re.match(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$", stored_mac):
                return stored_mac.upper().replace("-", ":")
        except Exception as e:
            logger.warning("Could not read saved MAC from %s: %s", mac_file, e)

    # Shelly / Allterco typical OUI: 34:98:7A or C8:F0:9E
    oui = random.choice([[0x34, 0x98, 0x7A], [0xC8, 0xF0, 0x9E], [0x08, 0x3A, 0x8D]])
    nic = [random.randint(0x00, 0xFF) for _ in range(3)]
    mac = ":".join(f"{b:02X}" for b in (oui + nic))

    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        mac_file.write_text(mac, encoding="utf-8")
        logger.info("Generated new stable MAC address: %s (stored in %s)", mac, mac_file)
    except Exception as e:
        logger.warning("Could not persist generated MAC to %s: %s", mac_file, e)

    return mac


@dataclass
class AppConfig:
    """Full application configuration."""

    data_source: str = "speedwire"  # "speedwire" or "homeassistant"
    shm2_serial: str = ""
    network_interface: str = ""
    multicast_ip: str = "239.12.255.254"
    multicast_port: int = 9522

    http_port: int = 80
    udp_port: int = 1010
    ingress_port: int = 8099

    invert_sign: bool = False
    stale_timeout: float = 10.0
    log_level: str = "INFO"
    debug_requests: bool = True

    device_name: str = "Shelly Pro 3EM"
    model: str = "SPEM-003CEBEU"
    firmware_version: str = "1.4.4"
    emulated_mac: str = ""
    device_id: str = ""

    ha_url: str = ""
    ha_token: str = ""
    ha_entities: Dict[str, str] = field(default_factory=lambda: {
        "total_power": "sensor.grid_power",
        "power_l1": "sensor.grid_power_l1",
        "power_l2": "sensor.grid_power_l2",
        "power_l3": "sensor.grid_power_l3",
        "voltage_l1": "sensor.grid_voltage_l1",
        "voltage_l2": "sensor.grid_voltage_l2",
        "voltage_l3": "sensor.grid_voltage_l3",
        "current_l1": "",
        "current_l2": "",
        "current_l3": "",
        "energy_import": "sensor.grid_energy_import",
        "energy_export": "sensor.grid_energy_export",
    })

    @property
    def mac_clean(self) -> str:
        """Hex MAC without colons, e.g. 34987A5BCD12."""
        return self.emulated_mac.replace(":", "").replace("-", "").upper()

    @property
    def mac_lower(self) -> str:
        """Lowercase hex MAC without colons, e.g. 34987a5bcd12."""
        return self.mac_clean.lower()

    @classmethod
    def load(cls, options_path: Optional[str] = None) -> AppConfig:
        """Load configuration from options.json, env vars, or defaults."""
        raw_options: Dict[str, Any] = {}

        # 1. Try specified path or Home Assistant add-on options location
        paths_to_check = [
            Path(options_path) if options_path else None,
            Path("/data/options.json"),
            Path("options.json"),
        ]

        for p in paths_to_check:
            if p and p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        raw_options = json.load(f)
                    logger.info("Loaded configuration from %s", p)
                    break
                except Exception as e:
                    logger.warning("Failed to load options from %s: %s", p, e)

        # Storage directory for persistent files
        storage_dir = Path("/data") if Path("/data").is_dir() else Path("./data")

        # Config fields
        data_source = str(raw_options.get("data_source", os.getenv("DATA_SOURCE", "speedwire"))).lower()
        shm2_serial = str(raw_options.get("shm2_serial", os.getenv("SHM2_SERIAL", ""))).strip()
        network_interface = str(raw_options.get("network_interface", os.getenv("NETWORK_INTERFACE", ""))).strip()
        multicast_ip = str(raw_options.get("multicast_ip", os.getenv("MULTICAST_IP", "239.12.255.254"))).strip()
        multicast_port = int(raw_options.get("multicast_port", os.getenv("MULTICAST_PORT", 9522)))

        http_port = int(raw_options.get("http_port", os.getenv("HTTP_PORT", 80)))
        udp_port = int(raw_options.get("udp_port", os.getenv("UDP_PORT", 1010)))
        ingress_port = int(raw_options.get("ingress_port", os.getenv("INGRESS_PORT", 8099)))

        invert_sign = bool(raw_options.get("invert_sign", os.getenv("INVERT_SIGN", "false").lower() in ("true", "1", "yes")))
        stale_timeout = float(raw_options.get("stale_timeout", os.getenv("STALE_TIMEOUT", 10.0)))
        log_level = str(raw_options.get("log_level", os.getenv("LOG_LEVEL", "INFO"))).upper()
        debug_requests = bool(raw_options.get("debug_requests", os.getenv("DEBUG_REQUESTS", "true").lower() in ("true", "1", "yes")))

        device_name = str(raw_options.get("device_name", os.getenv("DEVICE_NAME", "Shelly Pro 3EM")))
        firmware_version = str(raw_options.get("firmware_version", os.getenv("FIRMWARE_VERSION", "1.4.4")))

        # MAC Address
        configured_mac = str(raw_options.get("emulated_mac", os.getenv("EMULATED_MAC", ""))).strip()
        if configured_mac and re.match(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$", configured_mac):
            emulated_mac = configured_mac.upper().replace("-", ":")
        else:
            emulated_mac = generate_stable_mac(storage_dir)

        mac_clean = emulated_mac.replace(":", "").replace("-", "").lower()
        device_id = f"shellypro3em-{mac_clean}"

        # Home Assistant settings
        supervisor_token = os.getenv("SUPERVISOR_TOKEN", "")
        ha_url = str(raw_options.get("ha_url", os.getenv("HA_URL", ""))).strip()
        if not ha_url:
            if supervisor_token:
                ha_url = "http://supervisor/core/api"
            else:
                ha_url = "http://homeassistant.local:8123/api"

        ha_token = str(raw_options.get("ha_token", os.getenv("HA_TOKEN", supervisor_token))).strip()

        ha_entities = raw_options.get("ha_entities") or {}
        default_entities = {
            "total_power": "sensor.grid_power",
            "power_l1": "sensor.grid_power_l1",
            "power_l2": "sensor.grid_power_l2",
            "power_l3": "sensor.grid_power_l3",
            "voltage_l1": "sensor.grid_voltage_l1",
            "voltage_l2": "sensor.grid_voltage_l2",
            "voltage_l3": "sensor.grid_voltage_l3",
            "current_l1": "",
            "current_l2": "",
            "current_l3": "",
            "energy_import": "sensor.grid_energy_import",
            "energy_export": "sensor.grid_energy_export",
        }
        merged_entities = {**default_entities, **ha_entities}

        return cls(
            data_source=data_source,
            shm2_serial=shm2_serial,
            network_interface=network_interface,
            multicast_ip=multicast_ip,
            multicast_port=multicast_port,
            http_port=http_port,
            udp_port=udp_port,
            ingress_port=ingress_port,
            invert_sign=invert_sign,
            stale_timeout=stale_timeout,
            log_level=log_level,
            debug_requests=debug_requests,
            device_name=device_name,
            firmware_version=firmware_version,
            emulated_mac=emulated_mac,
            device_id=device_id,
            ha_url=ha_url,
            ha_token=ha_token,
            ha_entities=merged_entities,
        )
