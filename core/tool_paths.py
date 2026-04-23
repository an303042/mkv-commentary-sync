"""Helpers for resolving user-configured external tool paths."""

import os
import sys
from pathlib import Path


def _strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


def _platform_executable_name(executable_name: str) -> str:
    if sys.platform == "win32" and not executable_name.lower().endswith(".exe"):
        return f"{executable_name}.exe"
    return executable_name


def _looks_like_path(value: str) -> bool:
    separators = [os.sep]
    if os.altsep:
        separators.append(os.altsep)
    return any(sep in value for sep in separators) or value.startswith(".")


def resolve_tool_path(configured_path: str, executable_name: str) -> str:
    """
    Resolve a user-provided tool setting into something subprocess can execute.

    Users often paste an install folder (for example
    C:\\Program Files\\MKVToolNix) when a field asks for an executable path.
    Accept that, as well as quoted paths copied from Windows Explorer.
    """
    raw = _strip_wrapping_quotes(configured_path or "") or executable_name
    expanded = os.path.expandvars(os.path.expanduser(raw))

    # Leave bare commands alone so the OS can find them on PATH.
    if not _looks_like_path(expanded):
        return expanded

    path = Path(expanded)
    exe_name = _platform_executable_name(executable_name)
    candidate_names = [executable_name]
    if exe_name != executable_name:
        candidate_names.append(exe_name)

    if path.is_dir():
        candidates = []
        for name in candidate_names:
            candidates.append(path / name)
            candidates.append(path / "bin" / name)
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return str(path / exe_name)

    if sys.platform == "win32" and path.suffix == "":
        exe_candidate = path.with_name(f"{path.name}.exe")
        if exe_candidate.is_file():
            return str(exe_candidate)

    return str(path)


def sibling_tool_path(
    configured_path: str,
    executable_name: str,
    sibling_executable_name: str,
) -> str:
    """Return the sibling executable path for a configured tool path."""
    resolved = resolve_tool_path(configured_path, executable_name)
    if not _looks_like_path(resolved):
        return sibling_executable_name
    return str(Path(resolved).with_name(_platform_executable_name(sibling_executable_name)))
