"""Audio extraction and cross-correlation offset detection."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

import numpy as np
import scipy.io.wavfile
from scipy.signal import correlate, correlation_lags


class CancellationError(Exception):
    pass


@dataclass
class SyncResult:
    offset_ms: int
    # mkvmerge --sync o/p ratio; 1.0 = no drift (pure delay)
    drift_factor: float = 1.0
    # source track duration in ms; required when drift_factor != 1.0
    source_duration_ms: int = 0


# NCC below this means the lag reading is too noisy to trust — exclude the sample.
# Note: for long samples (300s @ 8kHz = 2.4M pts) the noise floor is ~0.0006,
# so even 0.02 is ~33σ above noise and statistically meaningful.
# Stereo vs 5.1 downmix or different audio masters can suppress NCC to 0.02–0.05
# even when the audio is genuinely the same content.
CONFIDENCE_MINIMUM = 0.02
# NCC below this is worth a warning but the sample is still usable
CONFIDENCE_THRESHOLD = 0.5
# Linear-fit residuals must be within this to trust the model
CONSISTENCY_TOLERANCE_MS = 50
# Slope magnitude below this (ms/s) is treated as a constant offset, not drift
DRIFT_THRESHOLD_MS_PER_S = 0.5


def _ms_to_hms(ms: int) -> str:
    s = abs(ms) // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _seconds_to_hms(secs: float) -> str:
    return _ms_to_hms(int(secs * 1000))


def _stretch_from_offset_slope(slope_ms_per_s: float) -> float:
    """Convert measured offset drift (ms/s) into mkvmerge's timestamp ratio."""
    denom = 1.0 - slope_ms_per_s / 1000.0
    if abs(denom) < 1e-9:
        raise RuntimeError(
            "Detected drift is too large to express safely as a timestamp ratio."
        )
    return 1.0 / denom


def extract_audio_segment(
    mkv_path: str,
    start: float,
    duration: float,
    sample_rate: int,
    output_path: str,
    ffmpeg_path: str = "ffmpeg",
    cancel_event: Optional[threading.Event] = None,
    audio_index: int = 0,
) -> None:
    try:
        proc = subprocess.Popen(
            [
                ffmpeg_path,
                "-y",
                "-ss", str(start),
                "-t", str(duration),
                "-i", mkv_path,
                "-map", f"0:a:{audio_index}",
                "-ac", "1",
                "-ar", str(sample_rate),
                output_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"ffmpeg not found at '{ffmpeg_path}'. "
            "Install ffmpeg: https://ffmpeg.org/download.html"
        )

    # Drain stderr in a background thread to prevent pipe-buffer deadlock
    # (ffmpeg is verbose; the buffer fills and proc.wait() never returns).
    stderr_holder: list[str] = []

    def _drain() -> None:
        stderr_holder.append(proc.communicate()[1])

    drain_thread = threading.Thread(target=_drain, daemon=True)
    drain_thread.start()

    while True:
        drain_thread.join(timeout=0.25)
        if not drain_thread.is_alive():
            break
        if cancel_event and cancel_event.is_set():
            proc.terminate()
            drain_thread.join(timeout=5)
            raise CancellationError()

    if proc.returncode != 0:
        stderr_text = stderr_holder[0] if stderr_holder else ""
        raise RuntimeError(f"ffmpeg audio extraction failed:\n{stderr_text[-2000:]}")


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


def _track_label(mkv_path: str, mkvmerge_path: str, audio_index: int = 0) -> str:
    try:
        from .track_utils import identify_tracks
        tracks = identify_tracks(mkv_path, mkvmerge_path)
        if not tracks or audio_index >= len(tracks):
            return "no audio track found"
        t = tracks[audio_index]
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
    cancel_event: Optional[threading.Event] = None,
    src_ref_audio_index: int = 0,
    tgt_ref_audio_index: int = 0,
    min_ncc: float = CONFIDENCE_MINIMUM,
) -> SyncResult:
    """
    Run multi-point cross-correlation to find timing offset and optional linear drift.

    Returns a SyncResult where:
      offset_ms > 0  →  source audio starts later than target
                        (mkvmerge --sync track:+offset_ms delays the track)
      drift_factor   →  mkvmerge o/p ratio for uniform speed correction; 1.0 = none
    """
    from .track_utils import get_file_duration, get_frame_rate

    def log(msg: str) -> None:
        if progress:
            progress(msg)

    def check_cancel() -> None:
        if cancel_event and cancel_event.is_set():
            raise CancellationError()

    # ── Frame-rate check (hint only, not a hard gate) ─────────────────────────
    check_cancel()
    src_fps = get_frame_rate(source_path, ffprobe_path)
    tgt_fps = get_frame_rate(target_path, ffprobe_path)
    fps_mismatch = abs(src_fps - tgt_fps) > 0.01
    # Expected drift from fps ratio in ms/s.
    # Positive means the source edition runs faster than the target and
    # therefore needs its timestamps expanded (slowed down) to match.
    fps_predicted_slope = (src_fps / tgt_fps - 1.0) * 1000.0 if tgt_fps else 0.0
    if fps_mismatch:
        log(
            f"⚠ Frame rate mismatch: source {src_fps:.3f} fps / target {tgt_fps:.3f} fps "
            f"(predicted drift {fps_predicted_slope:+.3f} ms/s). "
            "Sampling more points to detect linear drift."
        )
    else:
        log(f"✓ Frame rates match: {src_fps:.3f} fps")

    # ── Reference track info ──────────────────────────────────────────────────
    src_ref = _track_label(source_path, mkvmerge_path, src_ref_audio_index)
    tgt_ref = _track_label(target_path, mkvmerge_path, tgt_ref_audio_index)
    log(f"Reference tracks — source: {src_ref}  /  target: {tgt_ref}")

    # ── Determine sample points ───────────────────────────────────────────────
    src_dur = get_file_duration(source_path, ffprobe_path)
    tgt_dur = get_file_duration(target_path, ffprobe_path)
    shorter_dur = min(src_dur, tgt_dur)
    source_duration_ms = round(src_dur * 1000)

    points = [
        sample_start,
        shorter_dur * 0.25,
        shorter_dur * 0.40,
        shorter_dur * 0.60,
        shorter_dur * 0.75,
    ]
    points = [p for p in points if p + sample_duration <= shorter_dur]
    if not points:
        raise RuntimeError(
            "Files are too short to extract sample points with the given settings."
        )

    log(f"Sampling {len(points)} points…")

    # Each entry: (start_seconds, label, offset_ms)
    good_samples: List[Tuple[float, str, int]] = []

    # Unique temp dir per run: prefix includes a sanitised fragment of the
    # source filename so it's identifiable in task manager / temp dir listings.
    src_stem = re.sub(r"[^\w]", "_", os.path.splitext(os.path.basename(source_path))[0])[:20]
    tmpdir = tempfile.mkdtemp(prefix=f"dubsync_{src_stem}_")

    try:
        for i, start in enumerate(points):
            check_cancel()

            time_label = _seconds_to_hms(start)
            log(f"⟳ Point {i+1} ({time_label})…")

            src_wav = os.path.join(tmpdir, f"src_{i}.wav")
            tgt_wav = os.path.join(tmpdir, f"tgt_{i}.wav")

            extract_audio_segment(source_path, start, sample_duration, sample_rate, src_wav, ffmpeg_path, cancel_event, src_ref_audio_index)
            extract_audio_segment(target_path, start, sample_duration, sample_rate, tgt_wav, ffmpeg_path, cancel_event, tgt_ref_audio_index)

            _, src_data = _load_wav_mono(src_wav)
            _, tgt_data = _load_wav_mono(tgt_wav)

            # Threshold calibrated for int16 PCM (range ±32767)
            silence_threshold = 50.0
            src_rms = _rms(src_data)
            tgt_rms = _rms(tgt_data)
            if src_rms < silence_threshold:
                log(f"  ⚠ Source audio near-silence at this point (RMS {src_rms:.1f})")
            if tgt_rms < silence_threshold:
                log(f"  ⚠ Target audio near-silence at this point (RMS {tgt_rms:.1f})")

            lag, confidence = _normalized_xcorr(tgt_data, src_data)
            offset_ms = round((lag / sample_rate) * 1000)

            if confidence < min_ncc:
                log(
                    f"  ✗ NCC {confidence:.3f} below minimum ({min_ncc:.2f}) — excluded "
                    f"(tentative: {offset_ms:+d} ms). "
                    "Lower Min. NCC in Advanced if all points fail."
                )
            else:
                good_samples.append((start, time_label, offset_ms))
                conf_display = f"NCC {confidence:.3f}" + ("" if confidence >= CONFIDENCE_THRESHOLD else " ⚠")
                log(f"  → {offset_ms:+d} ms  ({conf_display})")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if not good_samples:
        raise RuntimeError(
            "All sample points were below the minimum NCC threshold — audio segments "
            "appear to be silent or corrupt.\n"
            "Try adjusting Sample Start / Sample Duration to land on sections with dialogue."
        )

    # ── Single usable point: return as constant offset ────────────────────────
    if len(good_samples) == 1:
        final_offset = good_samples[0][2]
        log(f"✓ Offset: {final_offset:+d} ms (single usable sample point)")
        if abs(final_offset) > 30_000:
            log(f"⚠ Large offset ({final_offset:+d} ms) — verify before proceeding.")
        return SyncResult(offset_ms=final_offset, source_duration_ms=source_duration_ms)

    # ── Fit linear model: offset(t) = slope * t + intercept ──────────────────
    ts = np.array([s[0] for s in good_samples], dtype=np.float64)       # seconds
    os_arr = np.array([s[2] for s in good_samples], dtype=np.float64)   # ms

    slope, intercept = np.polyfit(ts, os_arr, 1)   # slope in ms/s
    residuals = os_arr - (slope * ts + intercept)
    max_residual = float(np.max(np.abs(residuals)))

    # When fps metadata predicts drift and the measured slope is in the same
    # ballpark, noisy low-NCC readings can produce residuals of 100–200ms while
    # still being on the correct line.  Relax the tolerance in that case.
    slope_consistent_with_fps = (
        fps_mismatch
        and abs(slope) > DRIFT_THRESHOLD_MS_PER_S
        and fps_predicted_slope != 0.0
        and abs(slope - fps_predicted_slope) <= abs(fps_predicted_slope) * 3.0
    )
    effective_tolerance = CONSISTENCY_TOLERANCE_MS * 4 if slope_consistent_with_fps else CONSISTENCY_TOLERANCE_MS

    if max_residual > effective_tolerance:
        detail = "\n".join(
            f"  Point {i+1} ({lbl}): {off:+d} ms"
            for i, (_, lbl, off) in enumerate(good_samples)
        )
        hint = (
            "\n\nThe fps metadata suggests drift, but the offsets do not fit a line — "
            "the editions likely differ mid-film (extended scene, alternate cut)."
            if fps_mismatch else
            "\n\nEditions likely differ mid-film (extended scene, alternate cut). "
            "A single delay cannot fix sync."
        )
        raise RuntimeError(
            f"Inconsistent offsets (max residual {max_residual:.0f} ms, "
            f"tolerance {effective_tolerance:.0f} ms):\n{detail}{hint}"
        )

    if max_residual > CONSISTENCY_TOLERANCE_MS:
        log(
            f"⚠ Noisy readings (max residual {max_residual:.0f} ms) — "
            "drift correction accepted because slope matches fps metadata. "
            "Result may be off by a few hundred ms; verify in a player."
        )

    # ── Constant offset (no meaningful drift) ────────────────────────────────
    if abs(slope) < DRIFT_THRESHOLD_MS_PER_S:
        final_offset = round(float(np.median(os_arr)))
        log(f"✓ Offset: {final_offset:+d} ms")
        if abs(final_offset) > 30_000:
            log(f"⚠ Large offset ({final_offset:+d} ms) — verify before proceeding.")
        return SyncResult(offset_ms=final_offset, source_duration_ms=source_duration_ms)

    # ── Linear drift (PAL/NTSC-style uniform speed mismatch) ─────────────────
    #
    # The measured relationship is: offset_ms(t) = slope * t + intercept
    # where slope (ms/s) reflects a uniform speed difference between editions.
    #
    # To correct, mkvmerge --sync TID:d,o/p scales source timestamps by o/p,
    # producing: output_timestamp = source_timestamp * (o/p) + d
    #
    # We need output = source * stretch + base_delay.
    #
    # With a measured offset model of:
    #   offset_ms(t) = slope * t + intercept
    #
    # and mkvmerge applying:
    #   output_timestamp = source_timestamp * stretch + base_delay
    #
    # the exact relationship is:
    #   slope_ms_per_s = 1000 * (1 - 1 / stretch)
    #   => stretch = 1 / (1 - slope / 1000)
    #
    # Using 1 - slope / 1000 is only the first-order approximation and flips
    # the correction direction for common 24.000 <-> 23.976 cases.
    #   d = round(intercept)
    #
    # Direction note: if slope > 0 then the source needs to drift later over
    # time to stay aligned, which means its timestamps must be expanded
    # (stretch > 1.0). This matches typical 24.000 -> 23.976 retiming.
    base_offset = round(intercept)
    stretch = _stretch_from_offset_slope(float(slope))

    log(
        f"✓ Linear drift: base {base_offset:+d} ms, {slope:+.2f} ms/s "
        f"(stretch {stretch:.6f}, max residual {max_residual:.0f} ms)"
    )
    if abs(base_offset) > 30_000:
        log(f"⚠ Large base offset ({base_offset:+d} ms) — verify before proceeding.")

    return SyncResult(
        offset_ms=base_offset,
        drift_factor=stretch,
        source_duration_ms=source_duration_ms,
    )
