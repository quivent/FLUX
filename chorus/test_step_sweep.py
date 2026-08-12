import pathlib
import tempfile
import unittest

from PIL import Image

from chorus import step_sweep


class StepSweepRecoveryTest(unittest.TestCase):
    def test_partial_png_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "state.png"
            path.write_bytes(b"not a png")

            self.assertFalse(step_sweep.usable_image(path))
            self.assertFalse(path.exists())
            self.assertEqual(len(list((path.parent / "_corrupt").glob("state.png.*.corrupt"))), 1)

    def test_valid_png_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "state.png"
            Image.new("RGB", (4, 4), "white").save(path)

            self.assertTrue(step_sweep.usable_image(path))
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
