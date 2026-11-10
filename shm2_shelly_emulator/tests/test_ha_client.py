"""Unit tests for Home Assistant entity parsing and bridge logic."""

import unittest
from unittest.mock import AsyncMock, patch

from src.ha_client import HomeAssistantClient, parse_float_state


class TestHomeAssistantClient(unittest.IsolatedAsyncioTestCase):
    """Test suite for Home Assistant entity mapping to MeterData."""

    def test_parse_float_state(self):
        """Verify parsing of various Home Assistant entity state strings."""
        self.assertEqual(parse_float_state({"state": "123.45"}), 123.45)
        self.assertEqual(parse_float_state({"state": "-450.0"}), -450.0)
        self.assertEqual(parse_float_state({"state": "0"}), 0.0)
        self.assertEqual(parse_float_state({"state": "unavailable"}), 0.0)
        self.assertEqual(parse_float_state({"state": "unknown"}), 0.0)
        self.assertEqual(parse_float_state({"state": ""}), 0.0)
        self.assertEqual(parse_float_state(None), 0.0)
        self.assertEqual(parse_float_state({"state": "invalid_num"}), 0.0)
        self.assertEqual(parse_float_state({"state": "unavailable"}, default=230.0), 230.0)

    async def test_fetch_meter_data_mocked(self):
        """Verify construct of MeterData from mocked HA entity states."""
        client = HomeAssistantClient(
            api_url="http://supervisor/core/api",
            token="fake_token",
            entity_map={
                "total_power": "sensor.grid_power",
                "power_l1": "sensor.grid_power_l1",
                "power_l2": "sensor.grid_power_l2",
                "power_l3": "sensor.grid_power_l3",
                "voltage_l1": "sensor.grid_voltage_l1",
                "voltage_l2": "sensor.grid_voltage_l2",
                "voltage_l3": "sensor.grid_voltage_l3",
                "energy_import": "sensor.grid_energy_import",
                "energy_export": "sensor.grid_energy_export",
            },
        )

        fake_states = {
            "sensor.grid_power": {"state": "1200.0"},
            "sensor.grid_power_l1": {"state": "600.0"},
            "sensor.grid_power_l2": {"state": "400.0"},
            "sensor.grid_power_l3": {"state": "200.0"},
            "sensor.grid_voltage_l1": {"state": "230.0"},
            "sensor.grid_voltage_l2": {"state": "231.0"},
            "sensor.grid_voltage_l3": {"state": "229.0"},
            "sensor.grid_energy_import": {"state": "150000.0"},
            "sensor.grid_energy_export": {"state": "25000.0"},
        }

        async def fake_get_state(session, entity_id):
            return fake_states.get(entity_id)

        client._get_entity_state = AsyncMock(side_effect=fake_get_state)
        client._session = AsyncMock()

        meter_data = await client.fetch_meter_data()
        self.assertIsNotNone(meter_data)
        self.assertEqual(meter_data.source, "homeassistant")
        self.assertAlmostEqual(meter_data.total_act_power, 1200.0, places=1)
        self.assertAlmostEqual(meter_data.a.act_power, 600.0, places=1)
        self.assertAlmostEqual(meter_data.b.act_power, 400.0, places=1)
        self.assertAlmostEqual(meter_data.c.act_power, 200.0, places=1)
        self.assertAlmostEqual(meter_data.a.voltage, 230.0, places=1)

        # Current should be calculated from P/V (600 / 230 ≈ 2.609 A)
        self.assertAlmostEqual(meter_data.a.current, 600.0 / 230.0, places=3)
        self.assertAlmostEqual(meter_data.total_act_energy, 150000.0, places=1)
        self.assertAlmostEqual(meter_data.total_act_ret_energy, 25000.0, places=1)


if __name__ == "__main__":
    unittest.main()
