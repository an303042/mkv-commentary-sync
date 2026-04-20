"""mkvmerge command construction and execution."""

import re
import subprocess
from typing import Callable, List, Optional

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
    track_id: int,
    offset_ms: int,
    output_path: str,
    mkvmerge_path: str = "mkvmerge",
) -> List[str]:
    return [
        mkvmerge_path,
        "-o", output_path,
        target_path,
        "--sync", f"{track_id}:{offset_ms}",
        "--audio-tracks", str(track_id),
        "--no-video",
        "--no-subtitles",
        source_path,
    ]


def run_mux(
    target_path: str,
    source_path: str,
    track_id: int,
    offset_ms: int,
    output_path: str,
    mkvmerge_path: str = "mkvmerge",
    dry_run: bool = False,
    progress: Optional[Callable[[str], None]] = None,
    progress_pct: Optional[Callable[[int], None]] = None,
) -> None:
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    cmd = build_mkvmerge_command(
        target_path, source_path, track_id, offset_ms, output_path, mkvmerge_path
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
        )
    except FileNotFoundError:
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
    if proc.returncode == 1 and stderr_out:
        log(f"⚠ mkvmerge: {stderr_out}")

    log(f"✓ Done! Output: {output_path}")
