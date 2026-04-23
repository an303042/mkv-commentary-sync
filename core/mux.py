"""mkvmerge command construction and execution."""

import re
import subprocess
import sys
import threading
from typing import Callable, List, Optional, Sequence

from .detect_offset import CancellationError
from .tool_paths import resolve_tool_path

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Lines from mkvmerge stdout that add no value to the user
_FILTER_PATTERNS = [
    re.compile(r"^'.+': Using the demultiplexer"),
    re.compile(r"^'.+' track \d+: Using the output module"),
    re.compile(r"^'.+': Using the output module"),
    re.compile(r"^The file '.+' has been opened for writing\."),
]
_PROGRESS_RE = re.compile(r"^Progress: (\d+)%$")


def _dispatch_mkvmerge_line(
    line: str,
    progress: Optional[Callable[[str], None]],
    progress_pct: Optional[Callable[[int], None]],
) -> None:
    if not line:
        return
    m = _PROGRESS_RE.match(line)
    if m:
        if progress_pct:
            progress_pct(int(m.group(1)))
        return
    for pat in _FILTER_PATTERNS:
        if pat.match(line):
            return
    if progress:
        progress(line)


def build_mkvmerge_command(
    target_path: str,
    source_path: str,
    track_ids: Sequence[int],
    offset_ms: int,
    output_path: str,
    mkvmerge_path: str = "mkvmerge",
    drift_factor: float = 1.0,
    source_duration_ms: int = 0,
) -> List[str]:
    mkvmerge_path = resolve_tool_path(mkvmerge_path, "mkvmerge")
    cmd = [mkvmerge_path, "-o", output_path, target_path]
    for tid in track_ids:
        if drift_factor != 1.0 and source_duration_ms > 0:
            o = round(source_duration_ms * drift_factor)
            cmd += ["--sync", f"{tid}:{offset_ms},{o}/{source_duration_ms}"]
        else:
            cmd += ["--sync", f"{tid}:{offset_ms}"]
    cmd += [
        "--audio-tracks", ",".join(str(tid) for tid in track_ids),
        "--no-video",
        "--no-subtitles",
        source_path,
    ]
    return cmd


def run_mux(
    target_path: str,
    source_path: str,
    track_ids: Sequence[int],
    offset_ms: int,
    output_path: str,
    mkvmerge_path: str = "mkvmerge",
    dry_run: bool = False,
    progress: Optional[Callable[[str], None]] = None,
    progress_pct: Optional[Callable[[int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    drift_factor: float = 1.0,
    source_duration_ms: int = 0,
) -> None:
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    cmd = build_mkvmerge_command(
        target_path, source_path, track_ids, offset_ms, output_path, mkvmerge_path,
        drift_factor=drift_factor,
        source_duration_ms=source_duration_ms,
    )

    if dry_run:
        cmd_str = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        log(f"mkvmerge command:\n  {cmd_str}")
        log("(dry-run: skipping execution)")
        return

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0,
            creationflags=_NO_WINDOW,
        )
    except (FileNotFoundError, OSError):
        raise RuntimeError(
            f"mkvmerge not found at '{mkvmerge_path}'. "
            "Install MKVToolNix: https://mkvtoolnix.download/"
        )

    # Stream stdout, splitting on both \r and \n
    buf = ""
    while True:
        chunk = proc.stdout.read(256)
        if not chunk:
            break
        if cancel_event and cancel_event.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise CancellationError()
        buf += chunk
        parts = re.split(r"[\r\n]+", buf)
        for line in parts[:-1]:
            _dispatch_mkvmerge_line(line.strip(), progress, progress_pct)
        buf = parts[-1]
    if buf.strip():
        _dispatch_mkvmerge_line(buf.strip(), progress, progress_pct)

    proc.wait()
    stderr_out = proc.stderr.read().strip()

    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"mkvmerge failed (exit {proc.returncode}):\n{stderr_out}"
        )
    if proc.returncode == 1:
        if stderr_out:
            log(f"⚠ mkvmerge warnings:\n{stderr_out}")
        log("⚠ Muxing completed with warnings — verify the output in VLC or MKVToolNix before discarding originals.")

    log(f"✓ Done! Output: {output_path}")
