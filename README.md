# MKV Commentary Sync

Adds a commentary track from one MKV edition of a film to another edition that lacks it, with the timing offset automatically detected and corrected.

Different releases of the same film often have slightly different runtimes — longer studio logos, alternate intros, minor editorial changes. Naively dropping a commentary track into the wrong edition causes it to drift out of sync. This tool measures the exact offset between the two files and muxes the track at the right delay. No re-encoding at any stage.

---

## External requirements

The tool requires two external binaries. **These are not bundled** — they must be installed separately.

| Tool | Purpose | Install |
|---|---|---|
| **ffmpeg** | Audio extraction and frame-rate detection | `winget install Gyan.FFmpeg` |
| **MKVToolNix** (`mkvmerge`) | Track identification and muxing | `winget install MKVToolNix.MKVToolNix` |

Both must be on PATH, or their paths set manually in Advanced options.

If you're using the standalone `.exe`, the GUI will detect missing tools on launch and offer a **Download ffmpeg** button (automated) and a **Get MKVToolNix** button (opens the download page in your browser).

---

## Running from source

```
pip install -r requirements.txt
```

**GUI** (no arguments):
```
python main.py
```

**CLI:**
```
python main.py --source <source.mkv> --target <target.mkv> [options]
```

---

## Building the standalone exe

```
build.bat
```

Output: `dist/mkvsyncdub.exe` (~60 MB). Requires Python + PyInstaller to build, but the resulting exe runs standalone with no Python installation needed.

### CLI from the exe

The exe is built without an attached console so the GUI launches cleanly. When invoked with arguments (CLI mode), it allocates a console window automatically:

```
mkvsyncdub.exe --source commentary_edition.mkv --target other_edition.mkv
```

A console window will appear for the duration of the CLI run and close when it exits. This is expected behaviour — it is not a bug.

---

## CLI options

If `--track-id` is omitted, the tool lists available audio tracks and prompts you to choose.

| Option | Default | Description |
|---|---|---|
| `--source PATH` | — | MKV with the commentary track |
| `--target PATH` | — | MKV to mux the track into |
| `--track-id INT` | (prompt) | Track ID to mux (from `mkvmerge --identify`) |
| `--output PATH` | `<target>_with_commentary.mkv` | Output file path |
| `--sample-start INT` | `120` | Seconds into the film to begin sampling (skips logos) |
| `--sample-duration INT` | `300` | Seconds of audio to use per sample point |
| `--sample-rate INT` | `8000` | Sample rate for correlation audio (Hz) |
| `--ffmpeg-path PATH` | `ffmpeg` | Path to ffmpeg binary |
| `--mkvmerge-path PATH` | `mkvmerge` | Path to mkvmerge binary |
| `--dry-run` | — | Print the detected offset and mkvmerge command without executing |
| `--verbose` | — | Print detailed progress |

---

## How it works

**Step 1 — Frame rate check.** ffprobe reads the video frame rate from both files. A mismatch (> 0.01 fps) usually means a PAL/NTSC speed difference; the tool aborts because no single fixed offset can correct that.

**Step 2 — 3-point cross-correlation.** The tool extracts a short mono audio segment from both files at three evenly-spaced points (beginning, 40%, and 75% through the shorter file). For each point it runs a normalized cross-correlation to find the lag at which the two waveforms best align, giving an offset in milliseconds.

**Step 3 — Consistency check.** If all three offsets agree within ±50 ms, their median is used as the final offset. If they diverge, the editions likely differ mid-film (an extended scene, alternate cut, etc.) and a single fixed offset won't work.

**Step 4 — Mux.** mkvmerge stream-copies all tracks from the target, then appends the selected track from the source with `--sync <id>:<offset_ms>` to shift its timestamps. No audio or video is decoded or re-encoded.

### Sign convention

| Offset | Meaning | mkvmerge effect |
|---|---|---|
| Positive | Source content starts later than target | Commentary delayed to match |
| Negative | Source content starts earlier than target | Commentary advanced to match |

### Notes on correlation confidence

The normalized cross-correlation (NCC) value reflects how similar the two audio waveforms are at each sample point. Different Blu-ray masterings of the same film often have different loudness, EQ, or dynamic range processing, which can push NCC well below 0.5 while still producing the correct lag. The tool accepts any sample with NCC > 0.05 and relies on the 3-point consistency check as the primary quality gate.

If all three points fail (NCC ≤ 0.05), the audio segments are likely silent. Adjust **Sample Start** in Advanced options to land on a section of the film with dialogue.

---

## Adding a second commentary track

Run the tool twice, using the first output as the target for the second run:

```bash
python main.py --source src.mkv --target target.mkv --track-id 2 --output pass1.mkv
python main.py --source src.mkv --target pass1.mkv --track-id 3 --output final.mkv
```

---

## Project structure

```
mkv-commentary-sync/
├── main.py                 # Entry point — GUI if no args, CLI otherwise
├── core/
│   ├── detect_offset.py    # Audio extraction + cross-correlation
│   ├── downloader.py       # ffmpeg auto-download, MKVToolNix browser link
│   ├── track_utils.py      # mkvmerge --identify parsing, ffprobe helpers
│   └── mux.py              # mkvmerge command construction + execution
├── gui/
│   ├── main_window.py      # PySide6 main window
│   └── worker.py           # QThread pipeline worker
├── mkvsyncdub.spec         # PyInstaller build spec
├── build.bat               # One-step build script (Windows)
└── requirements.txt
```
