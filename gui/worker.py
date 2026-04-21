"""QThread worker — runs the full pipeline and emits progress signals."""

import threading

from PySide6.QtCore import QThread, Signal

from core.detect_offset import CancellationError


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
    offset_detected = Signal(int)   # emitted with the final offset_ms after analysis
    # Emitted when abs(offset) > 30 000 ms; worker blocks until
    # set_large_offset_response() is called from the main thread.
    large_offset_query = Signal(int)
    finished = Signal(bool, str)    # (success, output_path_or_error)

    def __init__(self, params: WorkerParams, parent=None):
        super().__init__(parent)
        self.params = params
        self._cancel_event = threading.Event()
        self._proceed_event = threading.Event()
        self._proceed_ok = True

    def cancel(self) -> None:
        """Cancel the running pipeline. Safe to call from any thread."""
        self._proceed_ok = False
        self._proceed_event.set()   # unblock any large-offset wait
        self._cancel_event.set()

    def set_large_offset_response(self, ok: bool) -> None:
        """Called from the main thread to answer a large-offset confirmation."""
        self._proceed_ok = ok
        self._proceed_event.set()

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

            # Validate track exists in source
            tracks = identify_tracks(p.source_path, p.mkvmerge_path)
            valid_ids = [t.track_id for t in tracks]
            if p.track_id not in valid_ids:
                raise RuntimeError(
                    f"Track ID {p.track_id} not found in source file.\n"
                    f"Valid audio track IDs: {valid_ids}"
                )

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
                cancel_event=self._cancel_event,
            )

            self.offset_detected.emit(offset_ms)

            # Large offset: pause and ask the user before proceeding to mux
            if abs(offset_ms) > 30_000:
                self._proceed_event.clear()
                self._proceed_ok = True
                self.large_offset_query.emit(offset_ms)
                self._proceed_event.wait()
                if not self._proceed_ok or self._cancel_event.is_set():
                    self._log("⚠ Aborted by user.")
                    self.finished.emit(False, "aborted")
                    return

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
                cancel_event=self._cancel_event,
            )

            # List output tracks
            self._log("Tracks in output file:")
            out_tracks = identify_tracks(p.output_path, p.mkvmerge_path)
            for t in out_tracks:
                ch = f"{t.channels}ch" if t.channels else ""
                name = (' "' + t.name + '"') if t.name else ""
                self._log(f"  Track {t.track_id}: {t.language}  {t.codec}  {ch}{name}")

            self.finished.emit(True, p.output_path)

        except CancellationError:
            self._log("⚠ Cancelled.")
            self.finished.emit(False, "cancelled")
        except Exception as exc:
            self._log(f"✗ {exc}")
            self.finished.emit(False, str(exc))
