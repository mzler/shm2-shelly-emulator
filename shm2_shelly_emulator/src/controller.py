"""Intelligent Automation Controller for SMA SHM2 & Anker SOLIX Solarbank."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path
import time
from typing import Any, Deque, Dict, List, Optional

from src.ha_client import HomeAssistantClient, parse_float_state
from src.shelly_service import ShellyService

logger = logging.getLogger(__name__)


@dataclass
class AutomationConfig:
    """Configuration for Solarbank Automation."""

    enabled: bool = False
    mode: str = "zero_export"  # zero_export, peak_shaving, constant_base, solar_priority

    target_entity: str = ""        # e.g. number.solarbank_preset_discharge_power
    battery_entity: str = ""       # e.g. sensor.solarbank_battery_percentage
    solar_entity: str = ""         # e.g. sensor.solarbank_solar_power
    current_power_entity: str = "" # e.g. sensor.solarbank_output_power

    min_power_w: float = 0.0
    max_power_w: float = 800.0
    deadband_w: float = 20.0
    update_interval_s: float = 5.0

    min_soc_pct: float = 10.0
    threshold_w: float = 1000.0    # For peak_shaving
    base_power_w: float = 150.0     # For constant_base
    target_soc_pct: float = 80.0    # For solar_priority


@dataclass
class AutomationDecision:
    """Record of a regulation decision for the live UI monitor."""

    timestamp: float
    time_str: str
    mode: str
    grid_power_w: float
    battery_soc_pct: Optional[float]
    solar_power_w: Optional[float]
    current_power_w: float
    target_power_w: float
    action: str  # UPDATED, SKIPPED_DEADBAND, CUTOFF_MIN_SOC, PAUSED, ERROR
    reason: str


class AutomationController:
    """Manages dynamic grid-following and intelligent Solarbank control."""

    def __init__(
        self,
        ha_client: HomeAssistantClient,
        shelly_service: ShellyService,
        config_file: Optional[Path] = None,
    ) -> None:
        self.ha_client = ha_client
        self.shelly_service = shelly_service

        if config_file:
            self.config_file = config_file
        else:
            storage_dir = Path("/data") if Path("/data").is_dir() else Path("./data")
            storage_dir.mkdir(parents=True, exist_ok=True)
            self.config_file = storage_dir / "automation_config.json"

        self.config = AutomationConfig()
        self.load_config()

        self._running = False
        self._task: Optional[asyncio.Task] = None

        self.last_target_power: float = 0.0
        self.last_sent_time: float = 0.0
        self.current_status: str = "Bereit (Inaktiv)"
        self.decisions_log: Deque[Dict[str, Any]] = deque(maxlen=50)

    def load_config(self) -> None:
        """Load automation settings from persistent JSON file."""
        if not self.config_file.exists():
            return
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.config = AutomationConfig(
                    enabled=bool(data.get("enabled", False)),
                    mode=str(data.get("mode", "zero_export")),
                    target_entity=str(data.get("target_entity", "")),
                    battery_entity=str(data.get("battery_entity", "")),
                    solar_entity=str(data.get("solar_entity", "")),
                    current_power_entity=str(data.get("current_power_entity", "")),
                    min_power_w=float(data.get("min_power_w", 0.0)),
                    max_power_w=float(data.get("max_power_w", 800.0)),
                    deadband_w=float(data.get("deadband_w", 20.0)),
                    update_interval_s=float(data.get("update_interval_s", 5.0)),
                    min_soc_pct=float(data.get("min_soc_pct", 10.0)),
                    threshold_w=float(data.get("threshold_w", 1000.0)),
                    base_power_w=float(data.get("base_power_w", 150.0)),
                    target_soc_pct=float(data.get("target_soc_pct", 80.0)),
                )
            logger.info("Loaded automation config from %s: mode=%s, enabled=%s", self.config_file, self.config.mode, self.config.enabled)
        except Exception as e:
            logger.warning("Could not load automation config from %s: %s", self.config_file, e)

    def save_config(self, new_data: Optional[Dict[str, Any]] = None) -> None:
        """Update and persist automation configuration."""
        if new_data:
            for field, value in new_data.items():
                if hasattr(self.config, field):
                    target_type = type(getattr(self.config, field))
                    try:
                        if target_type == bool:
                            setattr(self.config, field, bool(value))
                        else:
                            setattr(self.config, field, target_type(value))
                    except (ValueError, TypeError):
                        pass

        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(asdict(self.config), f, indent=2)
            logger.info("Saved automation configuration to %s", self.config_file)
        except Exception as e:
            logger.error("Failed to save automation configuration: %s", e)

    def get_status_dict(self) -> Dict[str, Any]:
        """Return full automation state for the web UI."""
        return {
            "config": asdict(self.config),
            "state": {
                "running": self._running,
                "current_status": self.current_status,
                "last_target_power": round(self.last_target_power, 1),
                "last_sent_sec_ago": round(time.time() - self.last_sent_time, 1) if self.last_sent_time > 0 else None,
            },
            "recent_decisions": list(self.decisions_log),
        }

    async def start(self) -> None:
        """Start the background control loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._control_loop())
        logger.info("Solarbank Automation Controller loop started")

    async def stop(self) -> None:
        """Stop the background control loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.current_status = "Gestoppt"
        logger.info("Solarbank Automation Controller stopped")

    def _record_decision(
        self,
        grid_w: float,
        battery_soc: Optional[float],
        solar_w: Optional[float],
        current_w: float,
        target_w: float,
        action: str,
        reason: str,
    ) -> None:
        """Append a decision entry to the live log."""
        decision = AutomationDecision(
            timestamp=time.time(),
            time_str=time.strftime("%H:%M:%S"),
            mode=self.config.mode,
            grid_power_w=round(grid_w, 1),
            battery_soc_pct=round(battery_soc, 1) if battery_soc is not None else None,
            solar_power_w=round(solar_w, 1) if solar_w is not None else None,
            current_power_w=round(current_w, 1),
            target_power_w=round(target_w, 1),
            action=action,
            reason=reason,
        )
        self.decisions_log.append(asdict(decision))

    async def evaluate_once(self) -> None:
        """Run a single evaluation and regulation step."""
        if not self.config.enabled:
            self.current_status = "Pausiert (Automation im UI deaktiviert)"
            return

        if not self.config.target_entity:
            self.current_status = "Inaktiv (Keine Solarbank-Zielentität ausgewählt)"
            return

        # 1. Read SMA grid telemetry
        meter_data, is_stale = self.shelly_service.get_effective_meter_data()
        if is_stale or meter_data.timestamp <= 0:
            self.current_status = "Wartet auf SMA-Messdaten (Daten veraltet oder Zähler offline)"
            return

        grid_power = meter_data.total_act_power  # Positive = Import, Negative = Export

        # 2. Read optional battery and solar states from Home Assistant
        battery_soc: Optional[float] = None
        if self.config.battery_entity:
            b_state = await self.ha_client.get_entity_state(self.config.battery_entity)
            if b_state:
                battery_soc = parse_float_state(b_state, default=-1.0)
                if battery_soc < 0:
                    battery_soc = None

        solar_power: Optional[float] = None
        if self.config.solar_entity:
            s_state = await self.ha_client.get_entity_state(self.config.solar_entity)
            if s_state:
                solar_power = parse_float_state(s_state, default=0.0)

        current_power = self.last_target_power
        if self.config.current_power_entity:
            c_state = await self.ha_client.get_entity_state(self.config.current_power_entity)
            if c_state:
                current_power = parse_float_state(c_state, default=current_power)

        # 3. Check Battery Min SoC Protection
        if battery_soc is not None and battery_soc <= self.config.min_soc_pct:
            raw_target = self.config.min_power_w
            target_w = max(0.0, raw_target)
            reason = f"Batterieschutz: Akkustand ({battery_soc:.0f}%) <= Minimum ({self.config.min_soc_pct:.0f}%)"
            self.current_status = f"Akkuschutz aktiv ({battery_soc:.0f}%)"

            if self.last_target_power != target_w:
                await self._send_target_power(target_w, grid_power, battery_soc, solar_power, current_power, "CUTOFF_MIN_SOC", reason)
            else:
                self._record_decision(grid_power, battery_soc, solar_power, current_power, target_w, "CUTOFF_MIN_SOC", reason)
            return

        # 4. Calculate target based on selected mode
        mode = self.config.mode
        calculated_target = 0.0
        mode_reason = ""

        if mode == "zero_export":
            # Dynamic Grid Following (Zero-Export)
            # Ideal: Target = Current Output + Grid Import (Positive = Import, Negative = Export)
            ideal_power = current_power + grid_power
            calculated_target = max(self.config.min_power_w, min(ideal_power, self.config.max_power_w))
            direction = "Netzbezug" if grid_power > 0 else "Netzeinspeisung"
            mode_reason = f"Nulleinspeisung: {direction} {abs(grid_power):.0f} W -> Ziel {calculated_target:.0f} W"

        elif mode == "peak_shaving":
            # Peak Shaving: Only discharge when grid import > threshold
            if grid_power > self.config.threshold_w:
                excess = grid_power - self.config.threshold_w
                calculated_target = max(self.config.min_power_w, min(excess, self.config.max_power_w))
                mode_reason = f"Spitzenlast ({grid_power:.0f} W > {self.config.threshold_w:.0f} W) -> Pufferung {calculated_target:.0f} W"
            else:
                calculated_target = self.config.min_power_w
                mode_reason = f"Verbrauch unter Schwelle ({grid_power:.0f} W <= {self.config.threshold_w:.0f} W) -> 0 W"

        elif mode == "constant_base":
            # Constant Base Load
            is_night = solar_power is not None and solar_power < 10.0
            if is_night or grid_power > 50.0:
                calculated_target = max(self.config.min_power_w, min(self.config.base_power_w, self.config.max_power_w))
                mode_reason = f"Grundlast: {calculated_target:.0f} W fest eingespeist"
            else:
                calculated_target = self.config.min_power_w
                mode_reason = "Grundlast pausiert (Solarerzeugung aktiv)"

        elif mode == "solar_priority":
            # Solar Charging Priority
            if battery_soc is not None and battery_soc < self.config.target_soc_pct:
                calculated_target = self.config.min_power_w
                mode_reason = f"Ladepriorität: Akku ({battery_soc:.0f}%) noch unter Zielwert ({self.config.target_soc_pct:.0f}%)"
            else:
                ideal_power = current_power + grid_power
                calculated_target = max(self.config.min_power_w, min(ideal_power, self.config.max_power_w))
                mode_reason = f"Ladepriorität erreicht -> Nulleinspeisung {calculated_target:.0f} W"

        else:
            calculated_target = self.config.min_power_w
            mode_reason = f"Unbekannter Modus '{mode}'"

        # 5. Check Deadband / Hysteresis
        delta = abs(calculated_target - self.last_target_power)
        is_zero_turnoff = (calculated_target == self.config.min_power_w and self.last_target_power > self.config.min_power_w)

        if delta < self.config.deadband_w and not is_zero_turnoff:
            self.current_status = f"Aktiv ({mode}): Regelabweichung {delta:.0f} W innerhalb Hysterese ({self.config.deadband_w:.0f} W)"
            self._record_decision(
                grid_power,
                battery_soc,
                solar_power,
                current_power,
                calculated_target,
                "SKIPPED_DEADBAND",
                f"Hysterese: Delta {delta:.0f} W < {self.config.deadband_w:.0f} W",
            )
        else:
            self.current_status = f"Aktiv ({mode}): Setze Solarbank auf {calculated_target:.0f} W"
            await self._send_target_power(
                calculated_target,
                grid_power,
                battery_soc,
                solar_power,
                current_power,
                "UPDATED",
                mode_reason,
            )

    async def _control_loop(self) -> None:
        """Main periodic optimization and regulation loop."""
        while self._running:
            interval = max(1.0, self.config.update_interval_s)
            try:
                await self.evaluate_once()
            except Exception as e:
                logger.error("Error in automation control loop: %s", e)
            await asyncio.sleep(interval)

    async def _send_target_power(
        self,
        target_w: float,
        grid_w: float,
        battery_soc: Optional[float],
        solar_w: Optional[float],
        current_w: float,
        action: str,
        reason: str,
    ) -> None:
        """Send command to Home Assistant and record decision."""
        success = await self.ha_client.set_number_value(self.config.target_entity, target_w)
        if success:
            self.last_target_power = target_w
            self.last_sent_time = time.time()
            self._record_decision(grid_w, battery_soc, solar_w, current_w, target_w, action, reason)
            logger.info("Solarbank regulation: Target=%.0f W | %s", target_w, reason)
        else:
            self._record_decision(
                grid_w,
                battery_soc,
                solar_w,
                current_w,
                target_w,
                "ERROR",
                f"Fehler beim Aufruf von {self.config.target_entity} mit Wert {target_w:.0f} W",
            )
