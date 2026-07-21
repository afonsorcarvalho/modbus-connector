import unittest
from unittest.mock import patch

from drivers.n4aib16 import N4AIB16
from common.scaling import parse_map_arg


def make_dev(**kwargs):
    """Cria um N4AIB16 sem abrir porta serial (open_serial mockado)."""
    with patch("drivers.n4aib16.open_serial", return_value=object()):
        return N4AIB16(port="/dev/null", **kwargs)


def frame(ch1):
    """Um frame de 16 canais com CH1=ch1 e o resto zero."""
    return [ch1] + [0] * 15


class TestReadChannelsFiltered(unittest.TestCase):
    def test_single_read_backward_compatible(self):
        dev = make_dev()
        dev.read_raw = lambda: frame(400)
        chans = dev.read_channels()  # defaults: samples=1
        self.assertEqual(chans[0]["raw"], 400)
        self.assertAlmostEqual(chans[0]["value"], 4.0)
        self.assertEqual(chans[0]["unit"], "mA")
        self.assertNotIn("stats", chans[0])
        self.assertNotIn("mA", chans[0])  # sem map: campo físico não é duplicado

    def test_block_median_reduces(self):
        dev = make_dev()
        frames = iter([frame(400), frame(500), frame(402)])  # 500 é spike
        dev.read_raw = lambda: next(frames)
        chans = dev.read_channels(samples=3, method="median")
        # mediana de [400,500,402] = 402 -> 4.02 mA
        self.assertAlmostEqual(chans[0]["value"], 4.02)

    def test_reject_then_reduce(self):
        dev = make_dev()
        frames = iter([frame(400), frame(401), frame(399),
                       frame(400), frame(9000)])  # 9000 é outlier
        dev.read_raw = lambda: next(frames)
        chans = dev.read_channels(samples=5, method="mean", reject=True)
        # sem o 9000, média ~ 400 -> ~4.0 mA (não puxado para cima)
        self.assertLess(chans[0]["value"], 4.1)

    def test_discards_comm_failure(self):
        dev = make_dev()
        seq = [frame(400), "fail", frame(402), frame(404)]
        it = iter(seq)

        def fake_read():
            v = next(it)
            if v == "fail":
                raise RuntimeError("timeout")
            return v

        dev.read_raw = fake_read
        chans = dev.read_channels(samples=3, method="mean")
        # 3 leituras boas: 400,402,404 -> 402 -> 4.02 mA
        self.assertAlmostEqual(chans[0]["value"], 4.02)

    def test_with_stats(self):
        dev = make_dev()
        frames = iter([frame(400), frame(402), frame(404)])
        dev.read_raw = lambda: next(frames)
        chans = dev.read_channels(samples=3, method="mean", with_stats=True)
        st = chans[0]["stats"]
        self.assertEqual(st["n"], 3)
        self.assertIn("u", st)
        self.assertGreater(st["s"], 0)

    def test_map_applied_per_channel(self):
        dev = make_dev()
        dev.read_raw = lambda: frame(1200)  # 12.00 mA
        spec = parse_map_arg("1:4:20:0:10:bar")
        chans = dev.read_channels(maps=[spec])
        self.assertAlmostEqual(chans[0]["value"], 5.0)  # 12 mA -> 5 bar
        self.assertEqual(chans[0]["unit"], "bar")
        self.assertAlmostEqual(chans[0]["mA"], 12.0)  # físico preservado
        # canal sem map continua em mA
        self.assertEqual(chans[1]["unit"], "mA")

    def test_ewma_smooths_between_calls(self):
        dev = make_dev(ewma_alpha=0.5)
        frames = iter([frame(400), frame(800)])  # 4.0 mA depois 8.0 mA
        dev.read_raw = lambda: next(frames)
        first = dev.read_channels()[0]["value"]
        second = dev.read_channels()[0]["value"]
        self.assertAlmostEqual(first, 4.0)
        self.assertAlmostEqual(second, 6.0)  # 0.5*8 + 0.5*4
        dev.reset_filters()
        self.assertTrue(hasattr(dev, "reset_filters"))


if __name__ == "__main__":
    unittest.main()
