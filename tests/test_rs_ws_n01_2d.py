import unittest
from unittest.mock import patch

from drivers.rs_ws_n01_2d import (
    to_signed16, raw_to_humidity, raw_to_temperature, BAUD_CODES,
    RSWSN012D,
)
from common.scaling import parse_map_arg


def make_dev(**kwargs):
    """Cria um RSWSN012D sem abrir porta serial (open_serial mockado)."""
    with patch("drivers.rs_ws_n01_2d.open_serial", return_value=object()):
        return RSWSN012D(port="/dev/null", **kwargs)


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


class TestReadMeasurements(unittest.TestCase):
    def test_single_read(self):
        dev = make_dev()
        dev.read_raw = lambda: [495, 243]
        m = dev.read_measurements()
        self.assertEqual(m[0]["name"], "humidity")
        self.assertEqual(m[0]["register"], 0x0000)
        self.assertAlmostEqual(m[0]["value"], 49.5)
        self.assertEqual(m[0]["unit"], "%RH")
        self.assertEqual(m[1]["name"], "temperature")
        self.assertEqual(m[1]["register"], 0x0001)
        self.assertAlmostEqual(m[1]["value"], 24.3)
        self.assertEqual(m[1]["unit"], "°C")

    def test_negative_temperature(self):
        dev = make_dev()
        dev.read_raw = lambda: [400, 0xFFEC]  # -2.0 °C
        m = dev.read_measurements()
        self.assertAlmostEqual(m[1]["value"], -2.0)

    def test_block_median_reduces_spike(self):
        dev = make_dev()
        frames = iter([[495, 243], [495, 900], [495, 244]])  # spike na temp
        dev.read_raw = lambda: next(frames)
        m = dev.read_measurements(samples=3, method="median")
        self.assertAlmostEqual(m[1]["value"], 24.4)  # mediana 243,900,244 = 244

    def test_reject_outlier(self):
        dev = make_dev()
        frames = iter([[495, 243], [495, 244], [495, 242],
                       [495, 243], [495, 5000]])  # outlier
        dev.read_raw = lambda: next(frames)
        m = dev.read_measurements(samples=5, method="mean", reject=True)
        self.assertLess(m[1]["value"], 25.0)

    def test_discards_comm_failure(self):
        dev = make_dev()
        seq = [[495, 243], "fail", [495, 245], [495, 247]]
        it = iter(seq)

        def fake_read():
            v = next(it)
            if v == "fail":
                raise RuntimeError("timeout")
            return v

        dev.read_raw = fake_read
        m = dev.read_measurements(samples=3, method="mean")
        self.assertAlmostEqual(m[1]["value"], 24.5)  # 243,245,247 -> 245

    def test_with_stats(self):
        dev = make_dev()
        frames = iter([[495, 243], [495, 245], [495, 247]])
        dev.read_raw = lambda: next(frames)
        m = dev.read_measurements(samples=3, method="mean", with_stats=True)
        st = m[1]["stats"]
        self.assertEqual(st["n"], 3)
        self.assertGreater(st["s"], 0)
        self.assertIn("u", st)

    def test_ewma_smooths(self):
        dev = make_dev(ewma_alpha=0.5)
        frames = iter([[400, 200], [800, 400]])  # temp 20 -> 40
        dev.read_raw = lambda: next(frames)
        first = dev.read_measurements()[1]["value"]
        second = dev.read_measurements()[1]["value"]
        self.assertAlmostEqual(first, 20.0)
        self.assertAlmostEqual(second, 30.0)  # 0.5*40 + 0.5*20

    def test_map_by_index(self):
        dev = make_dev()
        dev.read_raw = lambda: [495, 243]
        spec = parse_map_arg("2:0:50:32:122:degF")  # °C->°F linear
        m = dev.read_measurements(maps=[spec])
        self.assertAlmostEqual(m[1]["value"], 75.74)  # 24.3°C -> 75.74°F
        self.assertEqual(m[1]["unit"], "degF")
        self.assertAlmostEqual(m[1]["°C"], 24.3)  # físico preservado
        self.assertEqual(m[0]["unit"], "%RH")     # umidade sem map


if __name__ == "__main__":
    unittest.main()
