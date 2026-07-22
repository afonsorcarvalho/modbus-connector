import unittest
from unittest.mock import patch

from drivers.rs_ws_n01_2d import (
    to_signed16, raw_to_humidity, raw_to_temperature, BAUD_CODES,
)


class TestConversions(unittest.TestCase):
    def test_to_signed16_positive(self):
        self.assertEqual(to_signed16(243), 243)

    def test_to_signed16_negative(self):
        # 0xFFEC = 65516 -> -20
        self.assertEqual(to_signed16(0xFFEC), -20)

    def test_humidity_scaled(self):
        self.assertAlmostEqual(raw_to_humidity(495), 49.5)

    def test_temperature_positive(self):
        self.assertAlmostEqual(raw_to_temperature(243), 24.3)

    def test_temperature_negative(self):
        # -2.0 °C -> raw -20 -> 0xFFEC
        self.assertAlmostEqual(raw_to_temperature(0xFFEC), -2.0)

    def test_baud_table(self):
        self.assertEqual(BAUD_CODES, {2400: 0, 4800: 1, 9600: 2})


if __name__ == "__main__":
    unittest.main()
