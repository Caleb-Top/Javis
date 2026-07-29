import unittest
from unittest.mock import patch

from PIL import Image

from tools.desktop import screenshot


class DesktopCaptureFallbackTests(unittest.TestCase):
    def test_pillow_fallback_is_used_when_mss_capture_fails(self):
        image = Image.new("RGB", (320, 180), color=(12, 34, 56))

        with (
            patch("mss.mss", side_effect=OSError("BitBlt failed")),
            patch("PIL.ImageGrab.grab", return_value=image) as grab,
        ):
            result = screenshot()

        self.assertTrue(result.success)
        self.assertTrue(result.image)
        grab.assert_called_once_with(all_screens=True)


if __name__ == "__main__":
    unittest.main()
