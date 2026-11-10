"""Unit tests for SMA Speedwire packet decoding and OBIS metric extraction."""

import unittest

from src.speedwire import parse_speedwire_packet, serial_matches
from tests.sample_packets import SAMPLE_EM_1_2_4, SAMPLE_SHM2_2_3_4


class TestSpeedwireParser(unittest.TestCase):
    """Test suite verifying parser accuracy against recorded SMA EM & SHM2 datagrams."""

    def test_parse_sma_em_1_2_4(self):
        """Verify decoding of a real SMA Energy Meter 1.2.4.R packet (grid consumption)."""
        data = parse_speedwire_packet(SAMPLE_EM_1_2_4)
        self.assertIsNotNone(data)
        self.assertEqual(data.serial, "1900204523")
        self.assertEqual(data.source, "speedwire")

        # Total Power: Positive (Import / Buy)
        self.assertAlmostEqual(data.total_act_power, 3457.9, places=1)

        # Per Phase Active Power
        self.assertAlmostEqual(data.a.act_power, 3208.0, places=1)
        self.assertAlmostEqual(data.b.act_power, 183.6, places=1)
        self.assertAlmostEqual(data.c.act_power, 66.3, places=1)
        self.assertAlmostEqual(
            data.a.act_power + data.b.act_power + data.c.act_power,
            data.total_act_power,
            places=1,
        )

        # Voltages
        self.assertAlmostEqual(data.a.voltage, 224.5, places=1)
        self.assertAlmostEqual(data.b.voltage, 230.7, places=1)
        self.assertAlmostEqual(data.c.voltage, 230.4, places=1)

        # Currents
        self.assertAlmostEqual(data.a.current, 14.407, places=3)
        self.assertAlmostEqual(data.b.current, 0.954, places=3)
        self.assertAlmostEqual(data.c.current, 0.486, places=3)
        self.assertAlmostEqual(data.total_current, 15.847, places=3)

        # Energy counters
        self.assertGreater(data.total_act_energy, 26000000.0)
        self.assertGreater(data.total_act_ret_energy, 10000000.0)

    def test_parse_shm2_2_3_4(self):
        """Verify decoding of a real Sunny Home Manager 2.0 (2.3.4.R) packet (PV export)."""
        data = parse_speedwire_packet(SAMPLE_SHM2_2_3_4)
        self.assertIsNotNone(data)
        self.assertEqual(data.serial, "3002850058")

        # Total Power: Negative (Export / PV Feed-in)
        self.assertAlmostEqual(data.total_act_power, -4876.8, places=1)

        # Per Phase Active Power
        self.assertAlmostEqual(data.a.act_power, -1695.3, places=1)
        self.assertAlmostEqual(data.b.act_power, -1572.2, places=1)
        self.assertAlmostEqual(data.c.act_power, -1609.3, places=1)
        self.assertAlmostEqual(
            data.a.act_power + data.b.act_power + data.c.act_power,
            data.total_act_power,
            places=1,
        )

        # Grid frequency
        self.assertAlmostEqual(data.a.freq, 49.98, places=2)

        # Voltages & Currents
        self.assertAlmostEqual(data.a.voltage, 229.6, places=1)
        self.assertAlmostEqual(data.a.current, 7.445, places=3)
        self.assertAlmostEqual(data.b.voltage, 233.1, places=1)
        self.assertAlmostEqual(data.b.current, 6.850, places=3)
        self.assertAlmostEqual(data.c.voltage, 231.5, places=1)
        self.assertAlmostEqual(data.c.current, 7.017, places=3)

    def test_serial_filter(self):
        """Test optional serial number filtering matching decimal and hex."""
        # Decimal matching
        data = parse_speedwire_packet(SAMPLE_EM_1_2_4, filter_serial="1900204523")
        self.assertIsNotNone(data)

        # Hex matching with 0x prefix
        data_hex = parse_speedwire_packet(SAMPLE_EM_1_2_4, filter_serial="0x7142d1eb")
        self.assertIsNotNone(data_hex)

        # Hex matching without 0x prefix
        data_hex_no_prefix = parse_speedwire_packet(SAMPLE_EM_1_2_4, filter_serial="7142d1eb")
        self.assertIsNotNone(data_hex_no_prefix)

        # Non-matching serial discarded
        data_wrong = parse_speedwire_packet(SAMPLE_EM_1_2_4, filter_serial="999999999")
        self.assertIsNone(data_wrong)

    def test_serial_matches_helper(self):
        self.assertTrue(serial_matches("", 12345))
        self.assertTrue(serial_matches("12345", 12345))
        self.assertTrue(serial_matches("0x3039", 12345))
        self.assertFalse(serial_matches("54321", 12345))

    def test_sign_inversion(self):
        """Verify that invert_sign negates active power and swaps energy counters."""
        data = parse_speedwire_packet(SAMPLE_EM_1_2_4)
        orig_power = data.total_act_power  # +3457.9
        orig_import = data.total_act_energy
        orig_export = data.total_act_ret_energy

        inverted = data.apply_inversion()
        self.assertAlmostEqual(inverted.total_act_power, -orig_power, places=1)
        self.assertAlmostEqual(inverted.a.act_power, -data.a.act_power, places=1)
        self.assertAlmostEqual(inverted.total_act_energy, orig_export, places=1)
        self.assertAlmostEqual(inverted.total_act_ret_energy, orig_import, places=1)

    def test_malformed_packets(self):
        """Verify graceful rejection of invalid or corrupted packets."""
        # Truncated packet (<28 bytes)
        self.assertIsNone(parse_speedwire_packet(b"SMA\x00short"))

        # Missing magic bytes
        bad_magic = b"XXX\x00" + SAMPLE_EM_1_2_4[4:]
        self.assertIsNone(parse_speedwire_packet(bad_magic))

        # Wrong protocol ID (change 0x6069 to 0x1234 at bytes 16..18)
        bad_proto = SAMPLE_EM_1_2_4[:16] + b"\x12\x34" + SAMPLE_EM_1_2_4[18:]
        self.assertIsNone(parse_speedwire_packet(bad_proto))


if __name__ == "__main__":
    unittest.main()
