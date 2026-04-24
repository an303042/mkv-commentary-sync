# -*- mode: python ; coding: utf-8 -*-
#
# Build:  pyinstaller mkvsyncdub.spec
# Output: dist/mkvsyncdub.exe
#
# console=False  →  no console window for GUI launches.
# CLI mode allocates its own console window at runtime via AllocConsole().

from pathlib import Path
import sys

project_root = Path.cwd()
icons_dir = project_root / "assets" / "icons"
icon_datas = (
    [(str(path), "assets/icons") for path in sorted(icons_dir.iterdir()) if path.is_file()]
    if icons_dir.is_dir()
    else []
)

if sys.platform == "win32":
    build_icon = icons_dir / "app_icon.ico"
elif sys.platform == "darwin":
    build_icon = icons_dir / "app_icon.icns"
else:
    build_icon = None

build_icon = str(build_icon) if build_icon and build_icon.is_file() else None

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=icon_datas,
    hiddenimports=[
        "scipy.signal",
        "scipy.io",
        "scipy.io.wavfile",
        "scipy._lib.array_api_compat.numpy.fft",
        "numpy",
        "rich",
        "rich.console",
        "rich.table",
        "core.detect_offset",
        "core.mux",
        "core.track_utils",
        "core.downloader",
        "gui.main_window",
        "gui.worker",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="mkvsyncdub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon=build_icon,
)
