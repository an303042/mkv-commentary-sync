"""Helpers for locating app icon assets in source and frozen builds."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence
import sys


ICON_DIR = Path("assets") / "icons"
WINDOW_ICON_FILENAMES = ("app_icon.png", "app_icon.ico", "app_icon.icns")


def project_root() -> Path:
    """Return the repository root when running from source."""
    return Path(__file__).resolve().parent.parent


def runtime_root() -> Path:
    """Return the root directory containing bundled runtime assets."""
    if getattr(sys, "frozen", False):
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root)
        return Path(sys.executable).resolve().parent
    return project_root()


def icon_search_roots() -> list[Path]:
    """Search bundled assets first, then fall back to the repo checkout."""
    roots = [runtime_root()]
    source_root = project_root()
    if source_root not in roots:
        roots.append(source_root)
    return roots


def find_window_icon(search_roots: Sequence[Path] | None = None) -> Path | None:
    """Return the best available icon file for the running GUI."""
    roots = [Path(root) for root in (search_roots or icon_search_roots())]
    for root in roots:
        icons_dir = root / ICON_DIR
        for filename in WINDOW_ICON_FILENAMES:
            candidate = icons_dir / filename
            if candidate.is_file():
                return candidate
    return None
