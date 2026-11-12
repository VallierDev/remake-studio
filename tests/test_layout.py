import unittest
from unittest.mock import patch

import numpy as np

from finishing import inspect_frame_layout


class Capture:
    def __init__(self, frame):
        self.frame = frame

    def set(self, *_):
        pass

    def read(self):
        return True, self.frame.copy()

    def release(self):
        pass


class LayoutTests(unittest.TestCase):
    def test_large_blank_panel_is_rejected(self):
        rng = np.random.default_rng(4)
        frame = rng.integers(20, 150, (320, 180, 3), dtype=np.uint8)
        frame[:76] = 220
        with patch("cv2.VideoCapture", return_value=Capture(frame)):
            report = inspect_frame_layout("fixture")
        self.assertEqual(len(report["large_flat_bands"]), 8)
        self.assertEqual(report["large_flat_bands"][0]["side"], "top")

    def test_full_frame_and_plain_background_without_seam_pass(self):
        rng = np.random.default_rng(5)
        for frame in (rng.integers(0, 255, (320, 180, 3), dtype=np.uint8),
                      np.full((320, 180, 3), 180, dtype=np.uint8)):
            with patch("cv2.VideoCapture", return_value=Capture(frame)):
                self.assertEqual(inspect_frame_layout("fixture")["large_flat_bands"], [])


if __name__ == "__main__":
    unittest.main()
