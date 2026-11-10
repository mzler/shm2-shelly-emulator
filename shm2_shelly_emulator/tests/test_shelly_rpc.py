"""Unit tests for Shelly Pro 3EM JSON-RPC methods and UDP RPC protocol."""

import json
import time
import unittest
from unittest.mock import MagicMock

from src.config import AppConfig
from src.models import MeterData, PhaseMeasurement
from src.shelly_service import ShellyService
from src.udp_rpc import UdpRpcProtocol
from src.web_ui import parse_query_params


class TestShellyRpcService(unittest.TestCase):
    """Test suite verifying Shelly Pro 3EM Gen2 RPC emulation compliance."""

    def setUp(self):
        self.config = AppConfig(
            device_id="shellypro3em-34987a5bcd12",
            emulated_mac="34:98:7A:5B:CD:12",
            device_name="Shelly Pro 3EM Test",
            model="SPEM-003CEBEU",
            firmware_version="1.4.4",
            invert_sign=False,
            stale_timeout=5.0,
            debug_requests=True,
        )
        self.service = ShellyService(self.config)

        # Populate realistic test measurements
        self.test_data = MeterData(
            timestamp=time.time(),
            source="speedwire",
            serial="3002850058",
            a=PhaseMeasurement(
                current=2.5,
                voltage=230.0,
                act_power=575.0,
                aprt_power=575.0,
                pf=1.0,
                freq=50.0,
                energy_act=12000.0,
                energy_act_ret=500.0,
            ),
            b=PhaseMeasurement(
                current=1.0,
                voltage=232.0,
                act_power=-232.0,  # Export on L2
                aprt_power=232.0,
                pf=-1.0,
                freq=50.0,
                energy_act=8000.0,
                energy_act_ret=2000.0,
            ),
            c=PhaseMeasurement(
                current=0.5,
                voltage=231.0,
                act_power=115.5,
                aprt_power=115.5,
                pf=1.0,
                freq=50.0,
                energy_act=5000.0,
                energy_act_ret=100.0,
            ),
            total_current=4.0,
            total_act_power=458.5,
            total_aprt_power=922.5,
            total_act_energy=25000.0,
            total_act_ret_energy=2600.0,
        )
        self.service.update_meter_data(self.test_data)

    def test_shelly_get_device_info(self):
        """Verify Shelly.GetDeviceInfo attributes."""
        info = self.service.handle_shelly_get_device_info({})
        self.assertEqual(info["id"], "shellypro3em-34987a5bcd12")
        self.assertEqual(info["mac"], "34987A5BCD12")
        self.assertEqual(info["model"], "SPEM-003CEBEU")
        self.assertEqual(info["gen"], 2)
        self.assertEqual(info["app"], "Pro3EM")
        self.assertEqual(info["ver"], "1.4.4")
        self.assertEqual(info["profile"], "triphase")
        self.assertFalse(info["auth_en"])

    def test_em_get_status(self):
        """Verify EM.GetStatus returns instantaneous 3-phase and total metrics."""
        em = self.service.handle_em_get_status({})
        self.assertEqual(em["id"], 0)

        # L1 / Phase A
        self.assertAlmostEqual(em["a_current"], 2.5, places=3)
        self.assertAlmostEqual(em["a_voltage"], 230.0, places=1)
        self.assertAlmostEqual(em["a_act_power"], 575.0, places=1)
        self.assertAlmostEqual(em["a_aprt_power"], 575.0, places=1)
        self.assertAlmostEqual(em["a_pf"], 1.0, places=2)
        self.assertAlmostEqual(em["a_freq"], 50.0, places=1)

        # L2 / Phase B
        self.assertAlmostEqual(em["b_act_power"], -232.0, places=1)

        # L3 / Phase C
        self.assertAlmostEqual(em["c_act_power"], 115.5, places=1)

        # Totals
        self.assertAlmostEqual(em["total_current"], 4.0, places=3)
        self.assertAlmostEqual(em["total_act_power"], 458.5, places=1)
        self.assertAlmostEqual(em["total_aprt_power"], 922.5, places=1)
        self.assertEqual(em["errors"], [])

    def test_em_get_config(self):
        """Verify EM.GetConfig contains required component settings."""
        cfg = self.service.handle_em_get_config({})
        self.assertEqual(cfg["id"], 0)
        self.assertEqual(cfg["phase_selector"], 0)
        self.assertEqual(cfg["ct_type"], "120A")
        self.assertTrue(cfg["phase_sequence_detection_enabled"])

    def test_emdata_get_status(self):
        """Verify EMData.GetStatus returns accumulated Wh energy counters."""
        emdata = self.service.handle_emdata_get_status({})
        self.assertEqual(emdata["id"], 0)
        self.assertAlmostEqual(emdata["a_total_act_energy"], 12000.0, places=2)
        self.assertAlmostEqual(emdata["a_total_act_ret_energy"], 500.0, places=2)
        self.assertAlmostEqual(emdata["b_total_act_energy"], 8000.0, places=2)
        self.assertAlmostEqual(emdata["b_total_act_ret_energy"], 2000.0, places=2)
        self.assertAlmostEqual(emdata["total_act"], 25000.0, places=2)
        self.assertAlmostEqual(emdata["total_act_ret"], 2600.0, places=2)

    def test_sys_and_wifi_get_status(self):
        """Verify Sys.GetStatus and Wifi.GetStatus."""
        sys_status = self.service.handle_sys_get_status({})
        self.assertEqual(sys_status["mac"], "34987A5BCD12")
        self.assertFalse(sys_status["restart_required"])
        self.assertGreaterEqual(sys_status["uptime"], 0)

        wifi_status = self.service.handle_wifi_get_status({})
        self.assertEqual(wifi_status["status"], "got ip")
        self.assertIn("sta_ip", wifi_status)

    def test_shelly_get_status_bundle(self):
        """Verify Shelly.GetStatus bundles em:0, emdata:0, sys, wifi."""
        status = self.service.handle_shelly_get_status({})
        self.assertIn("em:0", status)
        self.assertIn("emdata:0", status)
        self.assertIn("sys", status)
        self.assertIn("wifi", status)
        self.assertEqual(status["em:0"]["id"], 0)
        self.assertEqual(status["emdata:0"]["id"], 0)

    def test_stale_detection(self):
        """Verify that stale data causes warning flag in EM.GetStatus."""
        # Set timestamp to 20 seconds in the past
        old_data = MeterData(
            timestamp=time.time() - 20.0,
            source="speedwire",
            total_act_power=100.0,
        )
        self.service.update_meter_data(old_data)

        em = self.service.handle_em_get_status({})
        self.assertIn("data_stale", em["errors"])
        self.assertIn("power_meter_failure", em["errors"])

    def test_sign_inversion_option(self):
        """Verify invert_sign option flips active power and swaps energy counters."""
        self.config.invert_sign = True
        em = self.service.handle_em_get_status({})
        # Original was +458.5 W, inverted should be -458.5 W
        self.assertAlmostEqual(em["total_act_power"], -458.5, places=1)
        self.assertAlmostEqual(em["a_act_power"], -575.0, places=1)
        # Original L2 was -232.0 W, inverted should be +232.0 W
        self.assertAlmostEqual(em["b_act_power"], 232.0, places=1)

        emdata = self.service.handle_emdata_get_status({})
        # Total act energy (import) should now be original export (2600.0)
        self.assertAlmostEqual(emdata["total_act"], 2600.0, places=2)
        # Total act ret energy (export) should now be original import (25000.0)
        self.assertAlmostEqual(emdata["total_act_ret"], 25000.0, places=2)

    def test_unknown_rpc_method(self):
        """Verify unknown methods return JSON-RPC -32601 error instead of crashing."""
        success, err = self.service.execute_rpc("SomeUnknown.Method", {"foo": "bar"})
        self.assertFalse(success)
        self.assertEqual(err["code"], -32601)
        self.assertIn("SomeUnknown.Method", err["message"])

    def test_request_recorder(self):
        """Verify request logging into the circular debug deque."""
        self.service.record_request(
            protocol="HTTP",
            client_ip="192.168.1.55",
            client_port=45678,
            http_method="GET",
            path="/rpc/EM.GetStatus?id=0",
            rpc_method="EM.GetStatus",
            params={"id": 0},
            body="",
            response_status=200,
            response_data={"id": 0},
        )
        self.assertEqual(len(self.service.recent_requests), 1)
        entry = self.service.recent_requests[0]
        self.assertEqual(entry["client_ip"], "192.168.1.55")
        self.assertEqual(entry["rpc_method"], "EM.GetStatus")
        self.assertEqual(entry["response_status"], 200)

    def test_udp_rpc_protocol(self):
        """Verify UDP RPC handler processing and responses."""
        protocol = UdpRpcProtocol(self.service)
        mock_transport = MagicMock()
        protocol.connection_made(mock_transport)

        # 1. Valid request: EM.GetStatus
        req = json.dumps({"id": 101, "method": "EM.GetStatus", "params": {"id": 0}}).encode("utf-8")
        protocol.datagram_received(req, ("192.168.1.77", 1010))

        mock_transport.sendto.assert_called()
        call_args = mock_transport.sendto.call_args[0]
        sent_data = json.loads(call_args[0].decode("utf-8"))
        sent_addr = call_args[1]

        self.assertEqual(sent_addr, ("192.168.1.77", 1010))
        self.assertEqual(sent_data["id"], 101)
        self.assertEqual(sent_data["src"], "shellypro3em-34987a5bcd12")
        self.assertIn("result", sent_data)
        self.assertEqual(sent_data["result"]["id"], 0)

        # 2. Unknown method over UDP
        req_unknown = json.dumps({"id": 102, "method": "NonExistent.Method"}).encode("utf-8")
        protocol.datagram_received(req_unknown, ("192.168.1.77", 1010))
        sent_err = json.loads(mock_transport.sendto.call_args[0][0].decode("utf-8"))
        self.assertEqual(sent_err["id"], 102)
        self.assertIn("error", sent_err)
        self.assertEqual(sent_err["error"]["code"], -32601)

        # 3. Invalid JSON over UDP
        protocol.datagram_received(b"{not valid json", ("192.168.1.77", 1010))
        sent_parse_err = json.loads(mock_transport.sendto.call_args[0][0].decode("utf-8"))
        self.assertEqual(sent_parse_err["error"]["code"], -32700)

    def test_query_param_parser(self):
        """Verify type conversion for GET query parameters."""
        raw = {"id": "0", "active": "true", "off": "false", "volts": "230.5", "name": "test"}
        parsed = parse_query_params(raw)
        self.assertEqual(parsed["id"], 0)
        self.assertTrue(parsed["active"])
        self.assertFalse(parsed["off"])
        self.assertEqual(parsed["volts"], 230.5)
        self.assertEqual(parsed["name"], "test")


if __name__ == "__main__":
    unittest.main()
