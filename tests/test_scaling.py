import unittest

from common.scaling import map_range, MapSpec, parse_map_arg, resolve_maps


class TestMapRange(unittest.TestCase):
    def test_linear_4_20_to_0_10(self):
        self.assertAlmostEqual(map_range(4, 4, 20, 0, 10), 0.0)
        self.assertAlmostEqual(map_range(20, 4, 20, 0, 10), 10.0)
        self.assertAlmostEqual(map_range(12, 4, 20, 0, 10), 5.0)

    def test_extrapolates_without_clamp(self):
        self.assertAlmostEqual(map_range(2, 4, 20, 0, 10), -1.25)

    def test_clamp_limits_output(self):
        self.assertAlmostEqual(map_range(2, 4, 20, 0, 10, clamp=True), 0.0)
        self.assertAlmostEqual(map_range(30, 4, 20, 0, 10, clamp=True), 10.0)

    def test_inverted_input_range(self):
        # 20 mA -> 0, 4 mA -> 100 (escala invertida)
        self.assertAlmostEqual(map_range(20, 20, 4, 0, 100), 0.0)
        self.assertAlmostEqual(map_range(4, 20, 4, 0, 100), 100.0)

    def test_equal_input_bounds_raises(self):
        with self.assertRaises(ValueError):
            map_range(5, 4, 4, 0, 10)


class TestMapSpec(unittest.TestCase):
    def test_apply(self):
        spec = MapSpec({1, 4, 6}, 4, 20, 0, 10, "bar")
        self.assertAlmostEqual(spec.apply(12), 5.0)
        self.assertEqual(spec.unit, "bar")

    def test_apply_clamp(self):
        spec = MapSpec({1}, 4, 20, 0, 10, "bar", clamp=True)
        self.assertAlmostEqual(spec.apply(2), 0.0)


class TestParseMapArg(unittest.TestCase):
    def test_full(self):
        spec = parse_map_arg("1,4,6:4:20:0:10:bar")
        self.assertEqual(spec.channels, {1, 4, 6})
        self.assertEqual(spec.in_min, 4.0)
        self.assertEqual(spec.in_max, 20.0)
        self.assertEqual(spec.out_min, 0.0)
        self.assertEqual(spec.out_max, 10.0)
        self.assertEqual(spec.unit, "bar")

    def test_without_unit(self):
        spec = parse_map_arg("2:4:20:0:100")
        self.assertEqual(spec.channels, {2})
        self.assertEqual(spec.unit, "")

    def test_clamp_flag_propagates(self):
        spec = parse_map_arg("1:4:20:0:10:bar", clamp=True)
        self.assertTrue(spec.clamp)

    def test_too_few_fields(self):
        with self.assertRaises(ValueError):
            parse_map_arg("1:4:20:0")

    def test_too_many_fields(self):
        with self.assertRaises(ValueError):
            parse_map_arg("1:4:20:0:10:bar:extra")

    def test_non_numeric(self):
        with self.assertRaises(ValueError):
            parse_map_arg("1:quatro:20:0:10")

    def test_bad_channel(self):
        with self.assertRaises(ValueError):
            parse_map_arg("x,y:4:20:0:10")


class TestResolveMaps(unittest.TestCase):
    def test_maps_channels(self):
        a = parse_map_arg("1,4:4:20:0:10:bar")
        b = parse_map_arg("2:4:20:0:100:%")
        resolved = resolve_maps([a, b])
        self.assertIs(resolved[1], a)
        self.assertIs(resolved[4], a)
        self.assertIs(resolved[2], b)

    def test_duplicate_channel_raises(self):
        a = parse_map_arg("1,2:4:20:0:10:bar")
        b = parse_map_arg("2:4:20:0:100:%")
        with self.assertRaises(ValueError):
            resolve_maps([a, b])


if __name__ == "__main__":
    unittest.main()
