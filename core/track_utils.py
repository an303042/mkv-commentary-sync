import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from .tool_paths import resolve_tool_path

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


@dataclass
class AudioTrack:
    track_id: int
    language: str
    codec: str
    channels: Optional[int]
    name: str


def identify_tracks(
    mkv_path: str,
    mkvmerge_path: str = "mkvmerge",
) -> List[AudioTrack]:
    mkvmerge_path = resolve_tool_path(mkvmerge_path, "mkvmerge")
    try:
        result = subprocess.run(
            [mkvmerge_path, "--identify", "--identification-format", "json", mkv_path],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=_NO_WINDOW,
        )
    except (FileNotFoundError, OSError):
        raise RuntimeError(
            f"mkvmerge not found at '{mkvmerge_path}'. "
            "Install MKVToolNix: https://mkvtoolnix.download/"
        )

    if result.returncode != 0:
        raise RuntimeError(f"mkvmerge --identify failed:\n{result.stderr.strip()}")

    data = json.loads(result.stdout)
    tracks: List[AudioTrack] = []
    for track in data.get("tracks", []):
        if track.get("type") == "audio":
            props = track.get("properties", {})
            codec = props.get("codec_id", track.get("codec", "unknown"))
            # Simplify codec IDs like A_AC3 → AC-3
            codec = _simplify_codec(codec)
            tracks.append(
                AudioTrack(
                    track_id=track["id"],
                    language=props.get("language", "und"),
                    codec=codec,
                    channels=props.get("audio_channels"),
                    name=props.get("track_name", ""),
                )
            )
    return tracks


def _simplify_codec(codec_id: str) -> str:
    table = {
        "A_AC3": "AC-3",
        "A_EAC3": "E-AC-3",
        "A_DTS": "DTS",
        "A_AAC": "AAC",
        "A_TRUEHD": "TrueHD",
        "A_FLAC": "FLAC",
        "A_PCM/INT/LIT": "PCM",
        "A_PCM/INT/BIG": "PCM",
        "A_VORBIS": "Vorbis",
        "A_OPUS": "Opus",
        "A_MP3": "MP3",
        "A_MP2": "MP2",
    }
    return table.get(codec_id, codec_id)


def get_file_duration(mkv_path: str, ffprobe_path: str = "ffprobe") -> float:
    ffprobe_path = resolve_tool_path(ffprobe_path, "ffprobe")
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                mkv_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=_NO_WINDOW,
        )
    except (FileNotFoundError, OSError):
        raise RuntimeError(
            f"ffprobe not found at '{ffprobe_path}'. "
            "Install ffmpeg: https://ffmpeg.org/download.html"
        )

    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{result.stderr.strip()}")

    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(f"Could not parse duration from ffprobe output: {result.stdout.strip()!r}")


def get_frame_rate(mkv_path: str, ffprobe_path: str = "ffprobe") -> float:
    ffprobe_path = resolve_tool_path(ffprobe_path, "ffprobe")
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate",
                "-of", "default=noprint_wrappers=1",
                mkv_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=_NO_WINDOW,
        )
    except (FileNotFoundError, OSError):
        raise RuntimeError(
            f"ffprobe not found at '{ffprobe_path}'. "
            "Install ffmpeg: https://ffmpeg.org/download.html"
        )

    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{result.stderr.strip()}")

    line = result.stdout.strip()
    if "=" in line:
        line = line.split("=", 1)[1]
    line = line.strip()
    if not line:
        raise RuntimeError(f"No video stream found in {mkv_path!r}")
    if "/" in line:
        num, den = line.split("/", 1)
        return float(num) / float(den)
    return float(line)


def check_tool(path: str) -> bool:
    """Return True if the binary at `path` is executable."""
    path = resolve_tool_path(path, path or "")
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            timeout=10,
            creationflags=_NO_WINDOW,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False
