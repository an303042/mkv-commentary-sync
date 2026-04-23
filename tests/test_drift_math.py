import math
import unittest

from core.detect_offset import _stretch_from_offset_slope


class DriftMathTests(unittest.TestCase):
    def test_positive_slope_expands_timestamps(self) -> None:
        # 24.000 -> 23.976 requires slowing the source down by 1000/999.
        stretch = _stretch_from_offset_slope(1.0)
        self.assertGreater(stretch, 1.0)
        self.assertTrue(math.isclose(stretch, 1000.0 / 999.0, rel_tol=1e-9))

    def test_negative_slope_compresses_timestamps(self) -> None:
        stretch = _stretch_from_offset_slope(-1.0)
        self.assertLess(stretch, 1.0)
        self.assertTrue(math.isclose(stretch, 1000.0 / 1001.0, rel_tol=1e-9))


if __name__ == "__main__":
    unittest.main()
