import math
import unittest

from common.filters import (
    BLOCK_METHODS, simple_mean, median, trimmed_mean, reduce,
    reject_outliers, block_stats, EWMA,
)


class TestBlockReducers(unittest.TestCase):
    def test_simple_mean(self):
        self.assertAlmostEqual(simple_mean([2, 4, 6]), 4.0)

    def test_median_odd(self):
        self.assertEqual(median([3, 1, 2]), 2)

    def test_median_even(self):
        self.assertEqual(median([1, 2, 3, 4]), 2.5)

    def test_trimmed_mean_drops_extremes(self):
        # 0 e 100 são descartados (trim=0.1 de 10 -> k=1); média de 1..8
        xs = [0, 1, 2, 3, 4, 5, 6, 7, 8, 100]
        self.assertAlmostEqual(trimmed_mean(xs, trim=0.1), 4.5)

    def test_trimmed_mean_zero_trim_is_mean(self):
        self.assertAlmostEqual(trimmed_mean([1, 2, 3], trim=0.0), 2.0)

    def test_trimmed_mean_small_list_falls_back_to_median(self):
        # trim grande esvaziaria; cai para mediana
        self.assertEqual(trimmed_mean([5, 5, 5], trim=0.4), 5)

    def test_trimmed_mean_rejects_bad_trim(self):
        with self.assertRaises(ValueError):
            trimmed_mean([1, 2, 3], trim=0.6)

    def test_reduce_dispatch(self):
        xs = [1, 2, 3, 4, 100]
        self.assertAlmostEqual(reduce(xs, "mean"), 22.0)
        self.assertEqual(reduce(xs, "median"), 3)

    def test_reduce_unknown_method(self):
        with self.assertRaises(ValueError):
            reduce([1, 2, 3], "kalman")

    def test_block_methods_constant(self):
        self.assertEqual(BLOCK_METHODS, ("mean", "median", "trimmed"))


class TestRejectOutliers(unittest.TestCase):
    def test_removes_spike(self):
        # dados com dispersão (MAD != 0); o 500 é um pico grosseiro
        xs = [100, 102, 98, 101, 99, 103, 500]
        kept = reject_outliers(xs, k=3.0)
        self.assertNotIn(500, kept)
        self.assertEqual(len(kept), 6)

    def test_keeps_clean_data(self):
        xs = [10, 11, 9, 10, 12, 8]
        self.assertEqual(sorted(reject_outliers(xs)), sorted(xs))

    def test_mad_zero_returns_all(self):
        xs = [7, 7, 7, 7]
        self.assertEqual(reject_outliers(xs), xs)

    def test_short_list_unchanged(self):
        self.assertEqual(reject_outliers([1, 100]), [1, 100])

    def test_never_empties(self):
        # mesmo com dados patológicos, nunca devolve lista vazia
        self.assertTrue(len(reject_outliers([1, 2, 3, 4, 5])) >= 1)


class TestBlockStats(unittest.TestCase):
    def test_stats_basic(self):
        st = block_stats([2, 4, 6])
        self.assertEqual(st["n"], 3)
        self.assertAlmostEqual(st["mean"], 4.0)
        self.assertEqual(st["median"], 4)
        self.assertAlmostEqual(st["s"], 2.0)  # stdev amostral de 2,4,6
        self.assertAlmostEqual(st["u"], 2.0 / math.sqrt(3))
        self.assertEqual(st["min"], 2)
        self.assertEqual(st["max"], 6)

    def test_stats_single_sample(self):
        st = block_stats([5])
        self.assertEqual(st["n"], 1)
        self.assertEqual(st["s"], 0.0)
        self.assertEqual(st["u"], 0.0)

    def test_stats_empty_raises(self):
        with self.assertRaises(ValueError):
            block_stats([])


class TestEWMA(unittest.TestCase):
    def test_first_sample_initializes(self):
        f = EWMA(alpha=0.5)
        self.assertIsNone(f.value)
        self.assertEqual(f.update(10), 10)
        self.assertEqual(f.value, 10)

    def test_smoothing(self):
        f = EWMA(alpha=0.5)
        f.update(10)                    # estado = 10
        self.assertEqual(f.update(20), 15)   # 0.5*20 + 0.5*10

    def test_initial_value(self):
        f = EWMA(alpha=0.5, initial=0)
        self.assertEqual(f.update(10), 5)    # 0.5*10 + 0.5*0

    def test_reset(self):
        f = EWMA(alpha=0.5)
        f.update(10)
        f.reset()
        self.assertIsNone(f.value)
        self.assertEqual(f.update(99), 99)

    def test_invalid_alpha(self):
        with self.assertRaises(ValueError):
            EWMA(alpha=0)
        with self.assertRaises(ValueError):
            EWMA(alpha=1.5)


if __name__ == "__main__":
    unittest.main()
