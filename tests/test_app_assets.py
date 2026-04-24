import tempfile
import unittest
from pathlib import Path

from core.app_assets import find_window_icon


class AppAssetsTests(unittest.TestCase):
    def test_prefers_png_for_runtime_window_icon(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            icons_dir = root / "assets" / "icons"
            icons_dir.mkdir(parents=True)
            ico_path = icons_dir / "app_icon.ico"
            png_path = icons_dir / "app_icon.png"
            ico_path.write_bytes(b"ico")
            png_path.write_bytes(b"png")

            self.assertEqual(find_window_icon([root]), png_path)

    def test_returns_none_when_no_icon_assets_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertIsNone(find_window_icon([Path(temp_dir)]))


if __name__ == "__main__":
    unittest.main()
