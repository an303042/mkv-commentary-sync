"""Audio extraction and cross-correlation offset detection."""

import os
import subprocess
import tempfile
from typing import Callable, Optional, Tuple

import numpy as np
import scipy.io.wavfile
from scipy.signal import correlate, correlation_lags


# NCC below this almost certainly means broken or silent audio — exclude the sample
CONFIDENCE_MINIMUM = 0.05
# NCC below this is worth a warning but the sample is still usable
CONFIDENCE_THRESHOLD = 0.5
# Offsets must agree within ±50 ms across sample points
CONSISTENCY_TOLERANCE_MS = 50


def _ms_to_hms(ms: int) -> str:
    s = abs(ms) // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _seconds_to_hms(secs: float) -> str:
    return _ms_to_hms(int(secs * 1000))


def extract_audio_segment(
    mkv_path: str,
    start: float,
    duration: float,
    sample_rate: int,
    output_path: str,
    ffmpeg_path: str = "ffmpeg",
) -> None:
    try:
        result = subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-ss", str(start),
                "-t", str(duration),
                "-i", mkv_path,
                "-map", "0:a:0",
                "-ac", "1",
                "-ar", str(sample_rate),
                output_path,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"ffmpeg not found at '{ffmpeg_path}'. "
            "Install ffmpeg: https://ffmpeg.org/download.html"
        )

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed:\n{result.stderr[-2000:]}")


def _load_wav_mono(path: str) -> Tuple[int, np.ndarray]:
    rate, data = scipy.io.wavfile.read(path)
    if data.ndim > 1:
        data = data[:, 0]
    return rate, data.astype(np.float64)


def _normalized_xcorr(a: np.ndarray, b: np.ndarray) -> Tuple[int, float]:
    """
    Cross-correlate a (target) and b (source).  Returns (lag_samples, confidence).

    lag > 0  →  source starts later than target  →  positive offset_ms
    lag < 0  →  source starts earlier than target
    """
    a = a - np.mean(a)
    b = b - np.mean(b)

    corr = correlate(a, b, mode="full")
    lags = correlation_lags(len(a), len(b), mode="full")

    norm = np.sqrt(np.dot(a, a) * np.dot(b, b))
    if norm < 1e-10:
        return 0, 0.0

    corr_norm = corr / norm
    peak_idx = int(np.argmax(corr_norm))
    lag = int(lags[peak_idx])
    confidence = float(corr_norm[peak_idx])
    return lag, confidence


def _rms(data: np.ndarray) -> float:
    return float(np.sqrt(np.mean(data ** 2))) if len(data) else 0.0


def _track0_label(mkv_path: str, mkvmerge_path: str) -> str:
    try:
        from .track_utils import identify_tracks
        tracks = identify_tracks(mkv_path, mkvmerge_path)
        if not tracks:
            return "no audio tracks found"
        t = tracks[0]
        ch = f"{t.channels}ch" if t.channels else ""
        name = f' "{t.name}"' if t.name else ""
        return f"track {t.track_id}  {t.language}  {t.codec}  {ch}{name}".strip()
    except Exception:
        return "unknown"


def detect_offset(
    source_path: str,
    target_path: str,
    sample_start: float = 120.0,
    sample_duration: float = 300.0,
    sample_rate: int = 8000,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    mkvmerge_path: str = "mkvmerge",
    progress: Optional[Callable[[str], None]] = None,
) -> int:
    """
    Run 3-point cross-correlation to find the timing offset in milliseconds.

    Returns offset_ms where:
      positive  →  source audio starts later than target
                   (mkvmerge --sync track:+offset_ms delays the track)
      negative  →  source audio starts earlier than target
    """
    from .track_utils import get_file_duration, get_frame_rate

    def log(msg: str) -> None:
        if progress:
            progress(msg)

    # ── Frame-rate sanity check ───────────────────────────────────────────────
    src_fps = get_frame_rate(source_path, ffprobe_path)
    tgt_fps = get_frame_rate(target_path, ffprobe_path)
    if abs(src_fps - tgt_fps) > 0.01:
        raise RuntimeError(
            f"Frame rate mismatch:\n"
            f"  source: {src_fps:.3f} fps\n"
            f"  target: {tgt_fps:.3f} fps\n\n"
            "This usually indicates a PAL vs. NTSC speed difference — no single offset\n"
            "can fix sync. This tool cannot handle this case."
        )
    log(f"✓ Frame rates match: {src_fps:.3f} fps")

    # ── Reference track info ──────────────────────────────────────────────────
    src_ref = _track0_label(source_path, mkvmerge_path)
    tgt_ref = _track0_label(target_path, mkvmerge_path)
    log(f"Reference tracks — source: {src_ref}  /  target: {tgt_ref}")

    # ── Determine sample points ───────────────────────────────────────────────
    src_dur = get_file_duration(source_path, ffprobe_path)
    tgt_dur = get_file_duration(target_path, ffprobe_path)
    shorter_dur = min(src_dur, tgt_dur)

    points = [
        sample_start,
        shorter_dur * 0.40,
        shorter_dur * 0.75,
    ]
    points = [p for p in points if p + sample_duration <= shorter_dur]
    if not points:
        raise RuntimeError(
            "Files are too short to extract sample points with the given settings."
        )

    log(f"Sampling {len(points)} points…")

    offsets_ms: list[int] = []
    point_labels: list[str] = []
    tmpdir = tempfile.gettempdir()

    for i, start in enumerate(points):
        time_label = _seconds_to_hms(start)
        point_labels.append(time_label)
        log(f"⟳ Point {i+1} ({time_label})…")

        src_wav = os.path.join(tmpdir, f"_dubsync_src_{i}.wav")
        tgt_wav = os.path.join(tmpdir, f"_dubsync_tgt_{i}.wav")

        try:
            extract_audio_segment(source_path, start, sample_duration, sample_rate, src_wav, ffmpeg_path)
            extract_audio_segment(target_path, start, sample_duration, sample_rate, tgt_wav, ffmpeg_path)

            _, src_data = _load_wav_mono(src_wav)
            _, tgt_data = _load_wav_mono(tgt_wav)

            silence_threshold = 50.0
            src_rms = _rms(src_data)
            tgt_rms = _rms(tgt_data)
            if src_rms < silence_threshold:
                log(f"  ⚠ Source audio near-silence at this point (RMS {src_rms:.1f})")
            if tgt_rms < silence_threshold:
                log(f"  ⚠ Target audio near-silence at this point (RMS {tgt_rms:.1f})")

            lag, confidence = _normalized_xcorr(tgt_data, src_data)
            offset_ms = round((lag / sample_rate) * 1000)

            if confidence < CONFIDENCE_MINIMUM:
                log(f"  ✗ NCC {confidence:.2f} — audio appears silent or corrupt, excluded")
            else:
                offsets_ms.append(offset_ms)
                quality = "" if confidence >= CONFIDENCE_THRESHOLD else f"  ⚠ low NCC: {confidence:.2f}"
                ncc_str = f"{confidence:.2f}" if confidence >= CONFIDENCE_THRESHOLD else ""
                conf_display = f"NCC {confidence:.2f}" if confidence >= CONFIDENCE_THRESHOLD else f"NCC {confidence:.2f} ⚠"
                log(f"  → {offset_ms:+d} ms  ({conf_display})")
        finally:
            for p in (src_wav, tgt_wav):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    if not offsets_ms:
        raise RuntimeError(
            "All sample points were below the minimum NCC threshold — audio segments "
            "appear to be silent or corrupt.\n"
            "Try adjusting Sample Start / Sample Duration to land on sections with dialogue."
        )

    # ── Consistency check ─────────────────────────────────────────────────────
    if len(offsets_ms) > 1:
        spread = max(offsets_ms) - min(offsets_ms)
        if spread > CONSISTENCY_TOLERANCE_MS:
            detail = "\n".join(
                f"  Point {i+1} ({lbl}): {off:+d} ms"
                for i, (lbl, off) in enumerate(zip(point_labels, offsets_ms))
            )
            raise RuntimeError(
                f"Inconsistent offsets detected:\n{detail}\n\n"
                "The offset is not constant — editions likely differ mid-film "
                "(extended scene, alternate cut). A single delay cannot fix sync."
            )

    final_offset = int(np.median(offsets_ms))
    log(f"✓ Offset: {final_offset:+d} ms")

    if abs(final_offset) > 30_000:
        log(f"⚠ Large offset ({final_offset:+d} ms) — verify before proceeding.")

    return final_offset
