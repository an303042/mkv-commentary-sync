import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.track_utils import check_tool
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

    def test_check_tool_uses_expected_executable_name_for_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe_name = "mkvmerge.exe" if sys.platform == "win32" else "mkvmerge"
            tool = Path(tmp) / exe_name
            tool.touch()
            with patch("core.track_utils.subprocess.run") as run:
                run.return_value.returncode = 0
                self.assertTrue(check_tool(tmp, "mkvmerge"))
                self.assertEqual(run.call_args.args[0][0], str(tool))

    def test_check_tool_uses_single_dash_version_for_ffmpeg(self) -> None:
        with patch("core.track_utils.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(check_tool("ffmpeg", "ffmpeg"))
            self.assertEqual(run.call_args.args[0], ["ffmpeg", "-version"])

    def test_check_tool_uses_double_dash_version_for_mkvmerge(self) -> None:
        with patch("core.track_utils.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(check_tool("mkvmerge", "mkvmerge"))
            self.assertEqual(run.call_args.args[0], ["mkvmerge", "--version"])


if __name__ == "__main__":
    unittest.main()
