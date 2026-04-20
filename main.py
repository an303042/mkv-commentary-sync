#!/usr/bin/env python3
"""
mkvsyncdub — MKV Commentary Sync Tool

Launches the GUI when called with no arguments; runs the CLI pipeline otherwise.
"""

import argparse
import os
import sys


def _attach_console_if_needed() -> None:
    """When running as a windowed exe, spin up a console for CLI output."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import ctypes
    ctypes.windll.kernel32.AllocConsole()
    sys.stdout = open("CONOUT$", "w", encoding="utf-8")
    sys.stderr = open("CONOUT$", "w", encoding="utf-8")
    sys.stdin  = open("CONIN$",  "r", encoding="utf-8")


def _run_cli(args: argparse.Namespace) -> int:
    _attach_console_if_needed()
    from rich.console import Console
    from rich.table import Table

    from core.detect_offset import detect_offset
    from core.mux import run_mux
    from core.track_utils import identify_tracks

    console = Console()

    # ── Validate inputs ───────────────────────────────────────────────────────
    for label, path in [("source", args.source), ("target", args.target)]:
        if not os.path.isfile(path):
            console.print(f"[red]✗ {label.capitalize()} file not found: {path!r}[/red]")
            return 1

    # ── Track selection ───────────────────────────────────────────────────────
    try:
        tracks = identify_tracks(args.source, args.mkvmerge_path)
    except RuntimeError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        return 1

    if not tracks:
        console.print("[red]✗ No audio tracks found in source file.[/red]")
        return 1

    track_id: int
    if args.track_id is None:
        table = Table(title="Audio tracks in source file")
        table.add_column("Track ID", justify="center")
        table.add_column("Language")
        table.add_column("Codec")
        table.add_column("Channels", justify="center")
        table.add_column("Name")
        for t in tracks:
            table.add_row(
                str(t.track_id),
                t.language,
                t.codec,
                str(t.channels) if t.channels else "",
                t.name,
            )
        console.print(table)
        try:
            raw = input("Select track ID to mux: ").strip()
            track_id = int(raw)
        except (ValueError, EOFError):
            console.print("[red]✗ Invalid track ID.[/red]")
            return 1
    else:
        track_id = args.track_id

    valid_ids = [t.track_id for t in tracks]
    if track_id not in valid_ids:
        console.print(
            f"[red]✗ Track ID {track_id} not found in source. "
            f"Valid IDs: {valid_ids}[/red]"
        )
        return 1

    # ── Default output path ───────────────────────────────────────────────────
    output = args.output
    if output is None:
        base, _ = os.path.splitext(args.target)
        output = base + "_with_commentary.mkv"

    # ── Detect offset ─────────────────────────────────────────────────────────
    def progress(msg: str) -> None:
        if msg.startswith("✓"):
            console.print(f"[green]{msg}[/green]")
        elif msg.startswith("⚠"):
            console.print(f"[yellow]{msg}[/yellow]")
        elif msg.startswith("✗"):
            console.print(f"[red]{msg}[/red]")
        elif args.verbose:
            console.print(msg)
        else:
            console.print(msg)

    try:
        offset_ms = detect_offset(
            source_path=args.source,
            target_path=args.target,
            sample_start=float(args.sample_start),
            sample_duration=float(args.sample_duration),
            sample_rate=args.sample_rate,
            ffmpeg_path=args.ffmpeg_path,
            ffprobe_path=args.ffmpeg_path.replace("ffmpeg", "ffprobe"),
            mkvmerge_path=args.mkvmerge_path,
            progress=progress,
        )
    except RuntimeError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        return 1

    # ── Large offset warning ──────────────────────────────────────────────────
    if abs(offset_ms) > 30_000:
        console.print(
            f"[yellow]⚠ Large offset detected ({offset_ms:+d} ms). "
            "Continue? [y/N] [/yellow]",
            end="",
        )
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = "n"
        if answer != "y":
            console.print("[yellow]Aborted.[/yellow]")
            return 0

    # ── Mux ───────────────────────────────────────────────────────────────────
    try:
        run_mux(
            target_path=args.target,
            source_path=args.source,
            track_id=track_id,
            offset_ms=offset_ms,
            output_path=output,
            mkvmerge_path=args.mkvmerge_path,
            dry_run=args.dry_run,
            progress=progress,
        )
    except RuntimeError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        return 1

    if not args.dry_run:
        console.print(f"[green]✓ Done! Output: {output}[/green]")
        try:
            out_tracks = identify_tracks(output, args.mkvmerge_path)
            console.print("Tracks in output:")
            for t in out_tracks:
                ch = f"  {t.channels}ch" if t.channels else ""
                name = f"  {t.name!r}" if t.name else ""
                console.print(f"  Track {t.track_id}: {t.language}  {t.codec}{ch}{name}")
        except Exception:
            pass

    return 0


def _run_gui() -> int:
    # Import Qt only when launching GUI so that headless CLI usage doesn't
    # require PySide6 to be importable.
    from PySide6.QtWidgets import QApplication
    from gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("MKV Commentary Sync")
    win = MainWindow()
    win.show()
    return app.exec()


def main() -> None:
    # If called with no arguments, launch GUI
    if len(sys.argv) == 1:
        sys.exit(_run_gui())

    parser = argparse.ArgumentParser(
        prog="mkvsyncdub",
        description="Detect audio offset between two MKV editions and mux a track with correct sync.",
    )
    parser.add_argument("--source", required=True, metavar="PATH",
                        help="MKV file that contains the extra track (e.g. commentary)")
    parser.add_argument("--target", required=True, metavar="PATH",
                        help="MKV file to mux the track into")
    parser.add_argument("--track-id", type=int, default=None, metavar="INTEGER",
                        help="Track ID in the source file to mux (from mkvmerge --identify)")
    parser.add_argument("--output", default=None, metavar="PATH",
                        help="Output file path [default: <target>_with_commentary.mkv]")
    parser.add_argument("--sample-duration", type=int, default=300, metavar="INT",
                        help="Seconds of audio to use for correlation [default: 300]")
    parser.add_argument("--sample-rate", type=int, default=8000, metavar="INT",
                        help="Sample rate for correlation audio in Hz [default: 8000]")
    parser.add_argument("--sample-start", type=int, default=120, metavar="INT",
                        help="Seconds into the film to start the sample [default: 120]")
    parser.add_argument("--ffmpeg-path", default="ffmpeg", metavar="PATH",
                        help="Path to ffmpeg binary [default: ffmpeg]")
    parser.add_argument("--mkvmerge-path", default="mkvmerge", metavar="PATH",
                        help="Path to mkvmerge binary [default: mkvmerge]")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print detected offset and mkvmerge command without executing")
    parser.add_argument("--verbose", action="store_true",
                        help="Print detailed progress")

    args = parser.parse_args()
    sys.exit(_run_cli(args))


if __name__ == "__main__":
    main()
