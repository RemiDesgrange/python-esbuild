"""Locate the esbuild executable installed alongside this package.

Modelled on ruff's ``find_ruff_bin``: the binary is installed as a wheel
"script", so it lands wherever the installer put scripts for this scheme.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from fnmatch import fnmatch

ENV_VAR = "ESBUILD_BINARY_PATH"
EXE_NAME = "esbuild" + (sysconfig.get_config_var("EXE") or "")


def find_esbuild_bin() -> str:
    """Return the path of the esbuild executable.

    Checks, in order: ``$ESBUILD_BINARY_PATH``; the scripts directory of the
    running interpreter; the user scheme's scripts directory (``pip install
    --user``); the scripts directory next to the package (``--prefix`` and
    ``--target`` installs). Raises ``FileNotFoundError`` listing every path tried.
    """
    override = os.environ.get(ENV_VAR)
    if override:
        if not _is_executable(override):
            raise FileNotFoundError(
                f"{ENV_VAR} is set to {override!r}, which is not an executable file"
            )
        return override

    tried: list[str] = []
    for directory in _candidate_dirs():
        if not directory:
            continue
        path = os.path.join(directory, EXE_NAME)
        if path in tried:
            continue
        tried.append(path)
        if _is_executable(path):
            return path

    locations = "\n".join(f"  - {p}" for p in tried)
    raise FileNotFoundError(
        f"Could not find the esbuild binary. Tried:\n{locations}\n"
        f"Set {ENV_VAR} to the path of an esbuild executable to override."
    )


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _candidate_dirs() -> list[str | None]:
    module_dir = _module_dir()
    if sys.platform == "win32":
        # --prefix: <prefix>/Lib/site-packages/esbuild_bin -> <prefix>/Scripts
        prefix_dir = _join(_strip_suffix(module_dir, "Lib/site-packages/esbuild_bin"), "Scripts")
    else:
        # --prefix: <prefix>/lib/python3.X/site-packages/esbuild_bin -> <prefix>/bin
        prefix_dir = _join(
            _strip_suffix(module_dir, "lib/python*/site-packages/esbuild_bin"), "bin"
        )
    return [
        sysconfig.get_path("scripts"),
        _user_scripts_dir(),
        prefix_dir,
        # --target: <target>/esbuild_bin -> <target>/bin
        _join(_strip_suffix(module_dir, "esbuild_bin"), "bin"),
    ]


def _module_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _user_scripts_dir() -> str | None:
    scheme = sysconfig.get_preferred_scheme("user")
    try:
        return sysconfig.get_path("scripts", scheme=scheme)
    except KeyError:
        return None


def _strip_suffix(path: str, pattern: str) -> str | None:
    """Drop trailing components of ``path`` that match ``pattern`` (``/``-separated, globs)."""
    parts = path.split(os.sep)
    pattern_parts = pattern.split("/")
    if len(parts) <= len(pattern_parts):
        return None
    tail = parts[-len(pattern_parts) :]
    if not all(fnmatch(part, want) for part, want in zip(tail, pattern_parts, strict=True)):
        return None
    return os.sep.join(parts[: -len(pattern_parts)])


def _join(path: str | None, child: str) -> str | None:
    return None if path is None else os.path.join(path, child)
