"""Audio extraction and cross-correlation offset detection."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from itertools import combinations
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

import numpy as np
import scipy.io.wavfile
from scipy.signal import correlate, correlation_lags

from .tool_paths import resolve_tool_path


class CancellationError(Exception):
    pass


@dataclass
class SyncResult:
    offset_ms: int
    # mkvmerge --sync o/p ratio; 1.0 = no drift (pure delay)
    drift_factor: float = 1.0
    # source track duration in ms; required when drift_factor != 1.0
    source_duration_ms: int = 0


@dataclass(frozen=True)
class CorrelationMetrics:
    """Measurements used to judge whether a correlation peak is credible."""

    lag_samples: int
    peak_ncc: float
    runner_up_ncc: float
    peak_prominence: float


@dataclass(frozen=True)
class OffsetSample:
    """One provisional offset reading from a point in the file."""

    start: float
    label: str
    offset_ms: int
    ncc: float
    prominence: float


# NCC below this means the lag reading is too noisy to trust — exclude the sample.
# Note: for long samples (300s @ 8kHz = 2.4M pts) the noise floor is ~0.0006,
# so even 0.02 is ~33σ above noise and statistically meaningful.
# Stereo vs 5.1 downmix or different audio masters can suppress NCC to 0.02–0.05
# even when the audio is genuinely the same content.
CONFIDENCE_MINIMUM = 0.02
# NCC below this is worth a warning but the sample is still usable
CONFIDENCE_THRESHOLD = 0.5
# Automatic mode uses a low hard floor, then relies on peak distinctness and
# agreement across time instead of pretending that one NCC value fits all media.
AUTOMATIC_NCC_FLOOR = 0.01
AUTOMATIC_SILENCE_RMS = 50.0
AUTOMATIC_DISTINCTIVE_PROMINENCE = 0.10
AUTOMATIC_STRONG_NCC = 0.10
PEAK_EXCLUSION_SECONDS = 1.0
# Linear-fit residuals must be within this to trust the model
CONSISTENCY_TOLERANCE_MS = 50
# Slope magnitude below this (ms/s) is treated as a constant offset, not drift
DRIFT_THRESHOLD_MS_PER_S = 0.5
MIN_RELIABLE_SAMPLES = 3


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
    ffmpeg_path = resolve_tool_path(ffmpeg_path, "ffmpeg")
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
            errors="replace",
            creationflags=_NO_WINDOW,
        )
    except (FileNotFoundError, OSError):
        raise RuntimeError(
            f"ffmpeg not found at '{ffmpeg_path}'. "
            "Install ffmpeg: https://ffmpeg.org/download.html"
        )

    # Drain stderr in a background thread to prevent pipe-buffer deadlock
    # (ffmpeg is verbose; the buffer fills and proc.wait() never returns).
    stderr_holder: list[str] = []
    stderr_read_errors: list[str] = []

    def _drain() -> None:
        try:
            stderr_holder.append(proc.communicate()[1] or "")
        except Exception as exc:
            stderr_read_errors.append(
                f"Could not read ffmpeg diagnostics: {type(exc).__name__}: {exc}"
            )

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
        stderr_text = stderr_holder[0].strip() if stderr_holder else ""
        if not stderr_text:
            stderr_text = (
                stderr_read_errors[0]
                if stderr_read_errors
                else "ffmpeg produced no diagnostic output."
            )
        raise RuntimeError(
            f"ffmpeg audio extraction failed (exit code {proc.returncode}).\n"
            f"Input: {mkv_path}\n"
            f"Selected audio stream: 0:a:{audio_index}\n"
            f"{stderr_text[-2000:]}"
        )


def _load_wav_mono(path: str) -> Tuple[int, np.ndarray]:
    rate, data = scipy.io.wavfile.read(path)
    if data.ndim > 1:
        data = data[:, 0]
    return rate, data.astype(np.float64)


def _correlation_metrics(
    a: np.ndarray,
    b: np.ndarray,
    peak_exclusion_samples: int = 1,
) -> CorrelationMetrics:
    """
    Cross-correlate a (target) and b (source), including peak distinctness.

    lag > 0  →  source starts later than target  →  positive offset_ms
    lag < 0  →  source starts earlier than target

    The runner-up excludes a neighbourhood around the winning lag so that the
    natural width of one peak is not mistaken for a competing alignment.
    """
    a = a - np.mean(a)
    b = b - np.mean(b)

    corr = correlate(a, b, mode="full")
    lags = correlation_lags(len(a), len(b), mode="full")

    norm = np.sqrt(np.dot(a, a) * np.dot(b, b))
    if norm < 1e-10:
        return CorrelationMetrics(0, 0.0, 0.0, 0.0)

    peak_idx = int(np.argmax(corr))
    lag = int(lags[peak_idx])
    peak_ncc = float(corr[peak_idx] / norm)

    radius = max(1, int(peak_exclusion_samples))
    before = corr[:max(0, peak_idx - radius)]
    after = corr[min(len(corr), peak_idx + radius + 1):]
    runner_values = []
    if before.size:
        runner_values.append(float(np.max(before)))
    if after.size:
        runner_values.append(float(np.max(after)))
    runner_up_ncc = max(runner_values) / norm if runner_values else 0.0
    prominence = max(
        0.0,
        min(1.0, (peak_ncc - runner_up_ncc) / max(abs(peak_ncc), 1e-12)),
    )
    return CorrelationMetrics(lag, peak_ncc, runner_up_ncc, prominence)


def _normalized_xcorr(a: np.ndarray, b: np.ndarray) -> Tuple[int, float]:
    """Backward-compatible wrapper returning the winning lag and NCC only."""
    metrics = _correlation_metrics(a, b)
    return metrics.lag_samples, metrics.peak_ncc


def _rms(data: np.ndarray) -> float:
    return float(np.sqrt(np.mean(data ** 2))) if len(data) else 0.0


def _require_reliable_sample_count(passed: int, attempted: int) -> None:
    """Reject an offset that is supported by too few sample points."""
    if passed < MIN_RELIABLE_SAMPLES:
        raise RuntimeError(
            f"Only {passed}/{attempted} sample points met the minimum NCC threshold — "
            f"need at least {MIN_RELIABLE_SAMPLES} for reliable offset detection.\n"
            "Try adjusting Sample Start / Sample Duration to avoid silent passages, "
            "or lower Min. NCC in Advanced if the files use different audio masters."
        )


def _sample_evidence(sample: OffsetSample) -> float:
    """Rank equally sized timing consensuses by NCC and peak distinctness."""
    return max(sample.ncc, 0.0) * (0.25 + 0.75 * sample.prominence)


def _model_tolerance(
    slope: float,
    fps_mismatch: bool,
    fps_predicted_slope: float,
) -> float:
    slope_consistent_with_fps = (
        fps_mismatch
        and abs(slope) > DRIFT_THRESHOLD_MS_PER_S
        and fps_predicted_slope != 0.0
        and abs(slope - fps_predicted_slope) <= abs(fps_predicted_slope) * 3.0
    )
    return (
        CONSISTENCY_TOLERANCE_MS * 4
        if slope_consistent_with_fps
        else CONSISTENCY_TOLERANCE_MS
    )


def _select_automatic_consensus(
    samples: List[OffsetSample],
    fps_mismatch: bool = False,
    fps_predicted_slope: float = 0.0,
) -> List[OffsetSample]:
    """Choose the best offset/drift consensus while rejecting unsafe conflicts."""
    _require_reliable_sample_count(len(samples), len(samples))

    models: List[Tuple[float, float]] = []
    # Constant-offset candidates are important: a pair-derived line would
    # otherwise overfit tiny timing noise into artificial drift.
    for sample in samples:
        models.append((0.0, float(sample.offset_ms)))
    for left, right in combinations(samples, 2):
        delta_t = right.start - left.start
        if abs(delta_t) < 1e-9:
            continue
        slope = (right.offset_ms - left.offset_ms) / delta_t
        intercept = left.offset_ms - slope * left.start
        models.append((slope, intercept))

    best: Optional[List[OffsetSample]] = None
    best_score: Optional[Tuple[int, float, float]] = None
    for slope, intercept in models:
        tolerance = _model_tolerance(slope, fps_mismatch, fps_predicted_slope)
        inliers = [
            sample
            for sample in samples
            if abs(sample.offset_ms - (slope * sample.start + intercept)) <= tolerance
        ]
        if len(inliers) < MIN_RELIABLE_SAMPLES:
            continue
        residual_sum = sum(
            abs(sample.offset_ms - (slope * sample.start + intercept))
            for sample in inliers
        )
        score = (
            len(inliers),
            sum(_sample_evidence(sample) for sample in inliers),
            -residual_sum,
        )
        if best_score is None or score > best_score:
            best = inliers
            best_score = score

    if best is None:
        detail = "\n".join(
            f"  {sample.label}: {sample.offset_ms:+d} ms "
            f"(NCC {sample.ncc:.3f}, separation {sample.prominence:.0%})"
            for sample in samples
        )
        raise RuntimeError(
            "Automatic validation could not find three consistent offset readings:\n"
            f"{detail}\n\nThe files may use different edits, or the sampled passages may "
            "contain repetitive/uncorrelated audio."
        )

    ts = np.array([sample.start for sample in best], dtype=np.float64)
    offsets = np.array([sample.offset_ms for sample in best], dtype=np.float64)
    slope, intercept = np.polyfit(ts, offsets, 1)
    tolerance = _model_tolerance(float(slope), fps_mismatch, fps_predicted_slope)

    excluded = [sample for sample in samples if sample not in best]
    strong_conflicts = [
        sample
        for sample in excluded
        if sample.ncc >= AUTOMATIC_STRONG_NCC
        and sample.prominence >= AUTOMATIC_DISTINCTIVE_PROMINENCE
        and abs(sample.offset_ms - (slope * sample.start + intercept)) > tolerance
    ]
    if strong_conflicts:
        detail = "\n".join(
            f"  {sample.label}: {sample.offset_ms:+d} ms "
            f"(NCC {sample.ncc:.3f}, separation {sample.prominence:.0%})"
            for sample in strong_conflicts
        )
        raise RuntimeError(
            "Strong correlation readings contradict the main timing model:\n"
            f"{detail}\n\nThe editions likely differ partway through; a single delay or "
            "uniform drift correction would be unsafe."
        )

    return best


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
    automatic_ncc: bool = True,
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
    # A user-selected start can coincide with a percentage point. Counting the
    # same audio twice would create fake independent support for a result.
    points = list(dict.fromkeys(round(p, 6) for p in points))
    if not points:
        raise RuntimeError(
            "Files are too short to extract sample points with the given settings."
        )

    if automatic_ncc:
        log(
            "NCC validation: automatic (audio energy, peak distinctness, and "
            "cross-point timing agreement)."
        )
    else:
        log(f"NCC validation: manual threshold {min_ncc:.3f}.")
    log(f"Sampling {len(points)} points…")

    # In automatic mode these are provisional until cross-point consensus is
    # evaluated. Manual mode retains the old fixed-threshold behaviour.
    good_samples: List[OffsetSample] = []

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

            src_rms = _rms(src_data)
            tgt_rms = _rms(tgt_data)
            if src_rms < AUTOMATIC_SILENCE_RMS:
                log(f"  ⚠ Source audio near-silence at this point (RMS {src_rms:.1f})")
            if tgt_rms < AUTOMATIC_SILENCE_RMS:
                log(f"  ⚠ Target audio near-silence at this point (RMS {tgt_rms:.1f})")

            metrics = _correlation_metrics(
                tgt_data,
                src_data,
                peak_exclusion_samples=round(sample_rate * PEAK_EXCLUSION_SECONDS),
            )
            offset_ms = round((metrics.lag_samples / sample_rate) * 1000)
            sample = OffsetSample(
                start=start,
                label=time_label,
                offset_ms=offset_ms,
                ncc=metrics.peak_ncc,
                prominence=metrics.peak_prominence,
            )

            if automatic_ncc and (
                src_rms < AUTOMATIC_SILENCE_RMS
                or tgt_rms < AUTOMATIC_SILENCE_RMS
            ):
                log("  ✗ Excluded near-silent sample from automatic validation.")
            elif automatic_ncc and metrics.peak_ncc < AUTOMATIC_NCC_FLOOR:
                log(
                    f"  ✗ NCC {metrics.peak_ncc:.3f} below automatic safety floor "
                    f"({AUTOMATIC_NCC_FLOOR:.2f}) — excluded "
                    f"(tentative: {offset_ms:+d} ms)."
                )
            elif automatic_ncc:
                good_samples.append(sample)
                distinction = (
                    "distinct"
                    if metrics.peak_prominence >= AUTOMATIC_DISTINCTIVE_PROMINENCE
                    else "ambiguous ⚠"
                )
                log(
                    f"  → {offset_ms:+d} ms  (NCC {metrics.peak_ncc:.3f}, "
                    f"peak separation {metrics.peak_prominence:.0%}, {distinction})"
                )
            elif metrics.peak_ncc < min_ncc:
                log(
                    f"  ✗ NCC {metrics.peak_ncc:.3f} below manual minimum "
                    f"({min_ncc:.3f}) — excluded (tentative: {offset_ms:+d} ms)."
                )
            else:
                good_samples.append(sample)
                conf_display = f"NCC {metrics.peak_ncc:.3f}" + (
                    "" if metrics.peak_ncc >= CONFIDENCE_THRESHOLD else " ⚠"
                )
                log(f"  → {offset_ms:+d} ms  ({conf_display})")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if automatic_ncc:
        candidate_count = len(good_samples)
        if candidate_count < MIN_RELIABLE_SAMPLES:
            raise RuntimeError(
                f"Only {candidate_count}/{len(points)} sample points were usable in "
                "automatic validation — need at least 3.\nTry adjusting Sample Start / "
                "Sample Duration to avoid silence, or use manual NCC validation in "
                "Advanced if you understand the risk."
            )
        good_samples = _select_automatic_consensus(
            good_samples,
            fps_mismatch=fps_mismatch,
            fps_predicted_slope=fps_predicted_slope,
        )
        if len(good_samples) < candidate_count:
            log(
                f"⚠ Automatic validation discarded {candidate_count - len(good_samples)} "
                "weak/ambiguous outlier(s); the remaining readings agree."
            )
    else:
        _require_reliable_sample_count(len(good_samples), len(points))

    # ── Fit linear model: offset(t) = slope * t + intercept ──────────────────
    ts = np.array([s.start for s in good_samples], dtype=np.float64)       # seconds
    os_arr = np.array([s.offset_ms for s in good_samples], dtype=np.float64)   # ms

    slope, intercept = np.polyfit(ts, os_arr, 1)   # slope in ms/s
    residuals = os_arr - (slope * ts + intercept)
    max_residual = float(np.max(np.abs(residuals)))

    # When fps metadata predicts drift and the measured slope is in the same
    # ballpark, noisy low-NCC readings can produce residuals of 100–200ms while
    # still being on the correct line.  Relax the tolerance in that case.
    effective_tolerance = _model_tolerance(
        float(slope), fps_mismatch, fps_predicted_slope
    )

    if max_residual > effective_tolerance:
        detail = "\n".join(
            f"  Point {i+1} ({sample.label}): {sample.offset_ms:+d} ms"
            for i, sample in enumerate(good_samples)
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
