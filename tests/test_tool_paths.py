import sys
import tempfile
import unittest
from pathlib import Path

from core.tool_paths import resolve_tool_path, sibling_tool_path


class ToolPathTests(unittest.TestCase):
    def test_bare_command_stays_bare_for_path_lookup(self) -> None:
        self.assertEqual(resolve_tool_path("mkvmerge", "mkvmerge"), "mkvmerge")

    def test_wrapping_quotes_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / ("mkvmerge.exe" if sys.platform == "win32" else "mkvmerge")
            tool.touch()
            self.assertEqual(resolve_tool_path(f'"{tool}"', "mkvmerge"), str(tool))

    def test_install_directory_resolves_to_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe_name = "mkvmerge.exe" if sys.platform == "win32" else "mkvmerge"
            tool = Path(tmp) / exe_name
            tool.touch()
            self.assertEqual(resolve_tool_path(tmp, "mkvmerge"), str(tool))

    def test_bin_directory_layout_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
            tool = bin_dir / exe_name
            tool.touch()
            self.assertEqual(resolve_tool_path(tmp, "ffmpeg"), str(tool))

    def test_sibling_tool_uses_resolved_executable_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ffmpeg_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
            ffprobe_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
            ffmpeg = Path(tmp) / ffmpeg_name
            ffmpeg.touch()
            self.assertEqual(
                sibling_tool_path(tmp, "ffmpeg", "ffprobe"),
                str(Path(tmp) / ffprobe_name),
            )


if __name__ == "__main__":
    unittest.main()
