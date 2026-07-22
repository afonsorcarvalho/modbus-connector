import unittest
from unittest.mock import patch

from drivers.rs_ws_n01_2d import (
    to_signed16, raw_to_humidity, raw_to_temperature, BAUD_CODES,
    RSWSN012D, REG_ADDRESS, REG_BAUD,
    crc16 as _crc16, build_parser,
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


class FakeSerial:
    """Serial mockada: grava o que foi escrito e devolve uma resposta fixa."""
    def __init__(self, response=b""):
        self.written = b""
        self._response = response

    def set_response(self, resp):
        self._response = resp

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.written = data
        return len(data)

    def flush(self):
        pass

    def read(self, n):
        resp, self._response = self._response, b""
        return resp

    def close(self):
        pass


def dev_with_serial(fake, **kwargs):
    with patch("drivers.rs_ws_n01_2d.open_serial", return_value=fake):
        return RSWSN012D(port="/dev/null", **kwargs)


def fc06_frame(addr, reg, value):
    body = bytes([addr, 0x06, reg >> 8, reg & 0xFF, value >> 8, value & 0xFF])
    return body + _crc16(body)


class TestConfig(unittest.TestCase):
    def test_read_config_parses_baud(self):
        dev = make_dev(address=2)
        with patch.object(dev, "_read_config_raw", return_value=[2, 2]):
            cfg = dev.read_config()
        self.assertEqual(cfg["address"], 2)
        self.assertEqual(cfg["baud_code"], 2)
        self.assertEqual(cfg["baud"], 9600)

    def test_read_config_unknown_baud(self):
        dev = make_dev()
        with patch.object(dev, "_read_config_raw", return_value=[5, 9]):
            cfg = dev.read_config()
        self.assertEqual(cfg["address"], 5)
        self.assertIsNone(cfg["baud"])

    def test_set_baud_writes_correct_frame(self):
        fake = FakeSerial()
        dev = dev_with_serial(fake, address=2)
        expected = fc06_frame(2, REG_BAUD, 2)  # 9600 -> código 2
        fake.set_response(expected)
        dev.set_baud(9600)
        self.assertEqual(fake.written, expected)

    def test_set_address_writes_correct_frame(self):
        fake = FakeSerial()
        dev = dev_with_serial(fake, address=2)
        expected = fc06_frame(2, REG_ADDRESS, 7)
        fake.set_response(expected)
        dev.set_address(7)
        self.assertEqual(fake.written, expected)

    def test_set_baud_rejects_unknown(self):
        dev = make_dev()
        with self.assertRaises(ValueError):
            dev.set_baud(1200)

    def test_set_address_rejects_out_of_range(self):
        dev = make_dev()
        with self.assertRaises(ValueError):
            dev.set_address(300)
        with self.assertRaises(ValueError):
            dev.set_address(0)

    def test_write_register_bad_echo_raises(self):
        fake = FakeSerial()
        dev = dev_with_serial(fake, address=2)
        fake.set_response(b"\x02\x06\x00\x00\x00\x00\x00\x00")  # eco errado
        with self.assertRaises(RuntimeError):
            dev.set_address(7)


class TestCLIParser(unittest.TestCase):
    def test_defaults(self):
        args = build_parser().parse_args(["-p", "/dev/ttyUSB1"])
        self.assertEqual(args.baud, 4800)
        self.assertEqual(args.address, 1)
        self.assertEqual(args.function, 3)
        self.assertFalse(args.show_config)

    def test_config_flags(self):
        args = build_parser().parse_args(
            ["-p", "/dev/ttyUSB1", "--set-baud", "9600"])
        self.assertEqual(args.set_baud, 9600)

    def test_set_baud_choices(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                ["-p", "/dev/ttyUSB1", "--set-baud", "1200"])


if __name__ == "__main__":
    unittest.main()
