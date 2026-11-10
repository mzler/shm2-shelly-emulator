"""Home Assistant REST / Supervisor API client for entity polling."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore

from src.models import MeterData, PhaseMeasurement

logger = logging.getLogger(__name__)


def parse_float_state(state_dict: Optional[Dict[str, Any]], default: float = 0.0) -> float:
    """Extract numeric state from Home Assistant entity state JSON."""
    if not state_dict or not isinstance(state_dict, dict):
        return default
    state_str = state_dict.get("state")
    if state_str in (None, "unavailable", "unknown", ""):
        return default
    try:
        return float(state_str)
    except (ValueError, TypeError):
        return default


class HomeAssistantClient:
    """Polls Home Assistant entities via Supervisor API / REST API."""

    def __init__(
        self,
        api_url: str,
        token: str,
        entity_map: Dict[str, str],
        poll_interval: float = 1.0,
        on_data_callback: Optional[Callable[[MeterData], None]] = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.entity_map = entity_map
        self.poll_interval = poll_interval
        self.on_data_callback = on_data_callback

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[Any] = None

        self.polls_count = 0
        self.success_count = 0
        self.error_count = 0
        self.last_poll_time = 0.0

    async def start(self) -> None:
        """Start background polling task."""
        if aiohttp is None:
            raise RuntimeError("aiohttp is required for Home Assistant polling")

        if self._running:
            return

        self._running = True
        logger.info(
            "Starting Home Assistant entity poller (URL: %s, interval: %.1fs)",
            self.api_url,
            self.poll_interval,
        )
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop poller and close HTTP session."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._session:
            await self._session.close()
            self._session = None

        logger.info("Home Assistant client stopped")

    async def _get_entity_state(self, session: Any, entity_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single entity state from Home Assistant REST API."""
        if not entity_id:
            return None

        # Build endpoint: if api_url ends with /api, endpoint is /states/<entity_id>
        url = f"{self.api_url}/states/{entity_id}"
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 404:
                    logger.debug("Entity %s not found in Home Assistant", entity_id)
                else:
                    logger.warning("Home Assistant returned HTTP %d for entity %s", resp.status, entity_id)
        except Exception as e:
            logger.debug("Error fetching entity %s: %s", entity_id, e)
        return None

    async def fetch_meter_data(self) -> Optional[MeterData]:
        """Poll all mapped entities and construct a MeterData object."""
        if self._session is None:
            self._session = aiohttp.ClientSession()

        # Map of entity keys to fetch
        tasks = {}
        for key, entity_id in self.entity_map.items():
            if entity_id:
                tasks[key] = self._get_entity_state(self._session, entity_id)

        if not tasks:
            logger.warning("No Home Assistant entities configured in ha_entities option")
            return None

        keys = list(tasks.keys())
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        states: Dict[str, Optional[Dict[str, Any]]] = {}

        for key, res in zip(keys, results):
            if isinstance(res, Exception):
                logger.debug("Failed to fetch entity %s: %s", key, res)
                states[key] = None
            else:
                states[key] = res

        # Extract values
        p_tot_raw = parse_float_state(states.get("total_power"))
        p1 = parse_float_state(states.get("power_l1"))
        p2 = parse_float_state(states.get("power_l2"))
        p3 = parse_float_state(states.get("power_l3"))

        # If total is 0 or not given, sum up phases
        if "total_power" not in tasks or p_tot_raw == 0.0:
            p_tot = p1 + p2 + p3
        else:
            p_tot = p_tot_raw
            # If phases not configured, distribute or assign
            if "power_l1" not in tasks and "power_l2" not in tasks and "power_l3" not in tasks:
                p1 = p_tot / 3.0
                p2 = p_tot / 3.0
                p3 = p_tot / 3.0

        v1 = parse_float_state(states.get("voltage_l1"), default=230.0)
        v2 = parse_float_state(states.get("voltage_l2"), default=230.0)
        v3 = parse_float_state(states.get("voltage_l3"), default=230.0)

        # Current
        i1 = parse_float_state(states.get("current_l1"))
        if i1 == 0.0 and v1 > 0:
            i1 = abs(p1) / v1

        i2 = parse_float_state(states.get("current_l2"))
        if i2 == 0.0 and v2 > 0:
            i2 = abs(p2) / v2

        i3 = parse_float_state(states.get("current_l3"))
        if i3 == 0.0 and v3 > 0:
            i3 = abs(p3) / v3

        total_current = i1 + i2 + i3

        # Energy counters
        e_imp = parse_float_state(states.get("energy_import"))
        e_exp = parse_float_state(states.get("energy_export"))

        # Apparent power estimate
        s1 = v1 * i1
        s2 = v2 * i2
        s3 = v3 * i3
        total_s = s1 + s2 + s3

        # Power factors
        pf1 = min(1.0, max(-1.0, p1 / s1)) if s1 > 0 else 1.0
        pf2 = min(1.0, max(-1.0, p2 / s2)) if s2 > 0 else 1.0
        pf3 = min(1.0, max(-1.0, p3 / s3)) if s3 > 0 else 1.0

        return MeterData(
            timestamp=time.time(),
            source="homeassistant",
            serial="HA-ENTITY-BRIDGE",
            a=PhaseMeasurement(
                current=i1,
                voltage=v1,
                act_power=p1,
                aprt_power=s1,
                pf=pf1,
                freq=50.0,
                energy_act=e_imp / 3.0 if e_imp > 0 else 0.0,
                energy_act_ret=e_exp / 3.0 if e_exp > 0 else 0.0,
            ),
            b=PhaseMeasurement(
                current=i2,
                voltage=v2,
                act_power=p2,
                aprt_power=s2,
                pf=pf2,
                freq=50.0,
                energy_act=e_imp / 3.0 if e_imp > 0 else 0.0,
                energy_act_ret=e_exp / 3.0 if e_exp > 0 else 0.0,
            ),
            c=PhaseMeasurement(
                current=i3,
                voltage=v3,
                act_power=p3,
                aprt_power=s3,
                pf=pf3,
                freq=50.0,
                energy_act=e_imp / 3.0 if e_imp > 0 else 0.0,
                energy_act_ret=e_exp / 3.0 if e_exp > 0 else 0.0,
            ),
            total_current=total_current,
            total_act_power=p_tot,
            total_aprt_power=total_s,
            total_act_energy=e_imp,
            total_act_ret_energy=e_exp,
        )

    async def _poll_loop(self) -> None:
        """Polling loop executing at poll_interval."""
        while self._running:
            self.polls_count += 1
            self.last_poll_time = time.time()
            try:
                meter_data = await self.fetch_meter_data()
                if meter_data:
                    self.success_count += 1
                    if self.on_data_callback:
                        self.on_data_callback(meter_data)
                else:
                    self.error_count += 1
            except Exception as e:
                self.error_count += 1
                logger.error("Error in Home Assistant polling loop: %s", e)

            await asyncio.sleep(self.poll_interval)
