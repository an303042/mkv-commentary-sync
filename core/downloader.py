"""Download helpers for optional external binaries."""

import os
import sys
import webbrowser
import zipfile
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple

FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
MKVTOOLNIX_URL = "https://mkvtoolnix.download/downloads.html#windows"


def get_tools_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "tools"
    return Path(__file__).parent.parent / "tools"


def download_ffmpeg(
    progress: Optional[Callable[[str], None]] = None,
    progress_pct: Optional[Callable[[int], None]] = None,
) -> Tuple[str, str]:
    """
    Download the ffmpeg essentials build and extract ffmpeg.exe + ffprobe.exe
    into the tools directory.  Returns (ffmpeg_path, ffprobe_path).
    """
    tools_dir = get_tools_dir()
    tools_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tools_dir / "_ffmpeg_download.zip"

    def _reporthook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0 and progress_pct:
            pct = min(99, int(block_num * block_size * 100 / total_size))
            progress_pct(pct)

    if progress:
        progress("Downloading ffmpeg (~75 MB)…")
    try:
        urllib.request.urlretrieve(FFMPEG_URL, str(zip_path), _reporthook)
    except Exception as exc:
        raise RuntimeError(f"Download failed: {exc}") from exc

    if progress:
        progress("Extracting…")

    ffmpeg_path: Optional[Path] = None
    ffprobe_path: Optional[Path] = None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                basename = os.path.basename(name).lower()
                if basename in ("ffmpeg.exe", "ffprobe.exe") and "/bin/" in name.lower():
                    target = tools_dir / os.path.basename(name)
                    with zf.open(name) as src, open(target, "wb") as dst:
                        dst.write(src.read())
                    if basename == "ffmpeg.exe":
                        ffmpeg_path = target
                    else:
                        ffprobe_path = target
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass

    if ffmpeg_path is None:
        raise RuntimeError(
            "ffmpeg.exe not found in the downloaded archive. "
            "Try installing manually from https://ffmpeg.org/download.html"
        )

    if progress_pct:
        progress_pct(100)
    if progress:
        progress(f"✓ ffmpeg installed to: {tools_dir}")

    return str(ffmpeg_path), str(ffprobe_path or tools_dir / "ffprobe.exe")


def open_mkvtoolnix_page() -> None:
    webbrowser.open(MKVTOOLNIX_URL)
