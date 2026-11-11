"""Unit tests for AutomationController."""

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import AppConfig
from src.controller import AutomationConfig, AutomationController
from src.models import MeterData, PhaseMeasurement
from src.shelly_service import ShellyService


class TestAutomationController(unittest.IsolatedAsyncioTestCase):
    """Test cases for the intelligent Solarbank automation controller."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "test_automation.json"

        self.app_config = AppConfig(data_source="speedwire")
        self.shelly_service = ShellyService(self.app_config)

        self.mock_ha_client = MagicMock()
        self.mock_ha_client.set_number_value = AsyncMock(return_value=True)
        self.mock_ha_client.get_entity_state = AsyncMock(return_value=None)

        self.controller = AutomationController(
            ha_client=self.mock_ha_client,
            shelly_service=self.shelly_service,
            config_file=self.config_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _feed_meter_data(self, total_power: float) -> None:
        data = MeterData(
            source="speedwire",
            timestamp=time.time(),
            total_act_power=total_power,
            a=PhaseMeasurement(act_power=total_power / 3, voltage=230.0, current=1.0),
            b=PhaseMeasurement(act_power=total_power / 3, voltage=230.0, current=1.0),
            c=PhaseMeasurement(act_power=total_power / 3, voltage=230.0, current=1.0),
        )
        self.shelly_service.update_meter_data(data)

    def test_save_and_load_config(self) -> None:
        """Verify configuration persists cleanly to JSON file."""
        self.controller.config.enabled = True
        self.controller.config.mode = "peak_shaving"
        self.controller.config.target_entity = "number.solarbank_preset"
        self.controller.config.max_power_w = 600.0
        self.controller.config.threshold_w = 1200.0
        self.controller.save_config()

        # New controller instance reading same file
        new_ctrl = AutomationController(
            ha_client=self.mock_ha_client,
            shelly_service=self.shelly_service,
            config_file=self.config_path,
        )
        self.assertTrue(new_ctrl.config.enabled)
        self.assertEqual(new_ctrl.config.mode, "peak_shaving")
        self.assertEqual(new_ctrl.config.target_entity, "number.solarbank_preset")
        self.assertEqual(new_ctrl.config.max_power_w, 600.0)
        self.assertEqual(new_ctrl.config.threshold_w, 1200.0)

    async def test_zero_export_regulation(self) -> None:
        """Verify dynamic zero-export follows grid consumption."""
        self.controller.config.enabled = True
        self.controller.config.mode = "zero_export"
        self.controller.config.target_entity = "number.solarbank_preset"
        self.controller.config.min_power_w = 0.0
        self.controller.config.max_power_w = 800.0
        self.controller.config.deadband_w = 10.0

        # Current Solarbank output: 100 W
        self.controller.config.current_power_entity = "sensor.solarbank_output"
        self.mock_ha_client.get_entity_state.side_effect = lambda entity_id: (
            {"state": "100.0"} if entity_id == "sensor.solarbank_output" else None
        )

        # Net grid import is 350 W -> Target should be 100 + 350 = 450 W
        self._feed_meter_data(350.0)

        await self.controller.evaluate_once()

        self.mock_ha_client.set_number_value.assert_called_with("number.solarbank_preset", 450.0)
        self.assertEqual(self.controller.last_target_power, 450.0)
        self.assertGreaterEqual(len(self.controller.decisions_log), 1)
        last_decision = self.controller.decisions_log[-1]
        self.assertEqual(last_decision["action"], "UPDATED")
        self.assertEqual(last_decision["target_power_w"], 450.0)

    async def test_min_soc_protection(self) -> None:
        """Verify battery discharge is stopped when SoC is below minimum threshold."""
        self.controller.config.enabled = True
        self.controller.config.mode = "zero_export"
        self.controller.config.target_entity = "number.solarbank_preset"
        self.controller.config.battery_entity = "sensor.battery_soc"
        self.controller.config.min_soc_pct = 15.0
        self.controller.config.min_power_w = 0.0
        self.controller.last_target_power = 250.0  # was discharging

        # Battery is at 12% (below 15%)
        self.mock_ha_client.get_entity_state.side_effect = lambda entity_id: (
            {"state": "12.0"} if entity_id == "sensor.battery_soc" else None
        )

        # Net grid import 500 W (would normally discharge 500 W)
        self._feed_meter_data(500.0)

        await self.controller.evaluate_once()

        # Target power should be forced to min_power_w (0.0 W)
        self.mock_ha_client.set_number_value.assert_called_with("number.solarbank_preset", 0.0)
        last_decision = self.controller.decisions_log[-1]
        self.assertEqual(last_decision["action"], "CUTOFF_MIN_SOC")
        self.assertEqual(last_decision["target_power_w"], 0.0)

    async def test_deadband_suppression(self) -> None:
        """Verify small fluctuations inside the deadband do not trigger API calls."""
        self.controller.config.enabled = True
        self.controller.config.mode = "zero_export"
        self.controller.config.target_entity = "number.solarbank_preset"
        self.controller.config.deadband_w = 30.0
        self.controller.last_target_power = 200.0

        # Current discharge is 200 W, grid import is only 10 W -> target would be 210 W (delta 10 W < deadband 30 W)
        self._feed_meter_data(10.0)

        await self.controller.evaluate_once()

        # set_number_value should NOT have been called
        self.mock_ha_client.set_number_value.assert_not_called()
        last_decision = self.controller.decisions_log[-1]
        self.assertEqual(last_decision["action"], "SKIPPED_DEADBAND")

    async def test_peak_shaving_mode(self) -> None:
        """Verify peak shaving discharges only when draw exceeds threshold."""
        self.controller.config.enabled = True
        self.controller.config.mode = "peak_shaving"
        self.controller.config.target_entity = "number.solarbank_preset"
        self.controller.config.threshold_w = 1000.0
        self.controller.config.max_power_w = 800.0

        # Grid is at 1400 W (> 1000 W threshold) -> needs 400 W shaving
        self._feed_meter_data(1400.0)

        await self.controller.evaluate_once()

        self.mock_ha_client.set_number_value.assert_called_with("number.solarbank_preset", 400.0)

    async def test_constant_base_mode(self) -> None:
        """Verify constant base load injection when conditions match."""
        self.controller.config.enabled = True
        self.controller.config.mode = "constant_base"
        self.controller.config.target_entity = "number.solarbank_preset"
        self.controller.config.base_power_w = 150.0

        # Grid has regular consumption
        self._feed_meter_data(250.0)

        await self.controller.evaluate_once()

        self.mock_ha_client.set_number_value.assert_called_with("number.solarbank_preset", 150.0)


if __name__ == "__main__":
    unittest.main()
