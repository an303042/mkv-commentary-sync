import unittest
from unittest.mock import MagicMock, patch

from core.detect_offset import extract_audio_segment


class AudioExtractionErrorTests(unittest.TestCase):
    @patch("core.detect_offset.resolve_tool_path", return_value="ffmpeg")
    @patch("core.detect_offset.subprocess.Popen")
    def test_failure_reports_context_when_ffmpeg_stderr_is_empty(
        self, popen: MagicMock, _resolve: MagicMock
    ) -> None:
        proc = popen.return_value
        proc.communicate.return_value = (None, "")
        proc.returncode = 1

        with self.assertRaises(RuntimeError) as raised:
            extract_audio_segment(
                "source.mkv", 120, 30, 8000, "sample.wav", audio_index=2
            )

        message = str(raised.exception)
        self.assertIn("exit code 1", message)
        self.assertIn("Input: source.mkv", message)
        self.assertIn("Selected audio stream: 0:a:2", message)
        self.assertIn("ffmpeg produced no diagnostic output", message)
        self.assertEqual("replace", popen.call_args.kwargs["errors"])

    @patch("core.detect_offset.resolve_tool_path", return_value="ffmpeg")
    @patch("core.detect_offset.subprocess.Popen")
    def test_failure_reports_stderr_reader_error(
        self, popen: MagicMock, _resolve: MagicMock
    ) -> None:
        proc = popen.return_value
        proc.communicate.side_effect = UnicodeDecodeError(
            "utf-8", b"\xff", 0, 1, "invalid start byte"
        )
        proc.returncode = 1

        with self.assertRaises(RuntimeError) as raised:
            extract_audio_segment(
                "source.mkv", 120, 30, 8000, "sample.wav", audio_index=0
            )

        message = str(raised.exception)
        self.assertIn("Could not read ffmpeg diagnostics", message)
        self.assertIn("UnicodeDecodeError", message)


if __name__ == "__main__":
    unittest.main()
