"""QThread worker — runs the full pipeline and emits progress signals."""

from PySide6.QtCore import QThread, Signal


class WorkerParams:
    def __init__(
        self,
        source_path: str,
        target_path: str,
        track_id: int,
        output_path: str,
        sample_start: int = 120,
        sample_duration: int = 300,
        sample_rate: int = 8000,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        mkvmerge_path: str = "mkvmerge",
        dry_run: bool = False,
    ):
        self.source_path = source_path
        self.target_path = target_path
        self.track_id = track_id
        self.output_path = output_path
        self.sample_start = sample_start
        self.sample_duration = sample_duration
        self.sample_rate = sample_rate
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.mkvmerge_path = mkvmerge_path
        self.dry_run = dry_run


class PipelineWorker(QThread):
    # (message, level) where level is "info" | "success" | "warning" | "error"
    log = Signal(str, str)
    mux_progress = Signal(int)      # 0–100 percent during mkvmerge
    finished = Signal(bool, str)    # (success, output_path_or_error)

    def __init__(self, params: WorkerParams, parent=None):
        super().__init__(parent)
        self.params = params

    def _log(self, msg: str) -> None:
        level = "info"
        if msg.startswith("✓"):
            level = "success"
        elif msg.startswith("⚠"):
            level = "warning"
        elif msg.startswith("✗") or "failed" in msg.lower() or "error" in msg.lower():
            level = "error"
        self.log.emit(msg, level)

    def run(self) -> None:
        p = self.params
        try:
            from core.detect_offset import detect_offset
            from core.mux import run_mux
            from core.track_utils import identify_tracks

            # Validate track exists in source (silent — no need to surface this step)
            tracks = identify_tracks(p.source_path, p.mkvmerge_path)
            valid_ids = [t.track_id for t in tracks]
            if p.track_id not in valid_ids:
                raise RuntimeError(
                    f"Track ID {p.track_id} not found in source file.\n"
                    f"Valid audio track IDs: {valid_ids}"
                )

            # Detect offset (includes frame-rate check)
            offset_ms = detect_offset(
                source_path=p.source_path,
                target_path=p.target_path,
                sample_start=float(p.sample_start),
                sample_duration=float(p.sample_duration),
                sample_rate=p.sample_rate,
                ffmpeg_path=p.ffmpeg_path,
                ffprobe_path=p.ffprobe_path,
                mkvmerge_path=p.mkvmerge_path,
                progress=self._log,
            )

            # Large offset warning — in GUI we log it and continue
            if abs(offset_ms) > 30_000:
                self._log(
                    f"⚠ Large offset detected ({offset_ms:+d} ms). "
                    "Proceeding — verify the result after muxing."
                )

            # Mux
            self._log(f"Muxing track {p.track_id} with offset {offset_ms:+d} ms…")
            run_mux(
                target_path=p.target_path,
                source_path=p.source_path,
                track_id=p.track_id,
                offset_ms=offset_ms,
                output_path=p.output_path,
                mkvmerge_path=p.mkvmerge_path,
                dry_run=p.dry_run,
                progress=self._log,
                progress_pct=lambda pct: self.mux_progress.emit(pct),
            )

            # List output tracks
            self._log("Tracks in output file:")
            out_tracks = identify_tracks(p.output_path, p.mkvmerge_path)
            for t in out_tracks:
                ch = f"{t.channels}ch" if t.channels else ""
                name = (' "' + t.name + '"') if t.name else ""
                self._log(f"  Track {t.track_id}: {t.language}  {t.codec}  {ch}{name}")

            self.finished.emit(True, p.output_path)

        except Exception as exc:
            self._log(f"✗ {exc}")
            self.finished.emit(False, str(exc))
