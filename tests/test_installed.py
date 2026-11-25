"""Checks against a real, installed esbuild-bin wheel.

Run in an environment where the wheel is installed:
``ESBUILD_BIN_INSTALLED_TEST=1 python -m pytest tests/test_installed.py``.
Everything runs in ``python -I`` subprocesses, so the source tree is never
imported by mistake.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ESBUILD_BIN_INSTALLED_TEST") != "1",
    reason="set ESBUILD_BIN_INSTALLED_TEST=1 to test an installed wheel",
)

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_VERSION = (ROOT / "ESBUILD_VERSION").read_text().strip()
PYTHON = [sys.executable, "-I"]


def run(
    args: list[str], *, stdin: str | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, input=stdin, capture_output=True, text=True, timeout=60, env=env, check=False
    )


@pytest.fixture(scope="module")
def installed() -> dict[str, str]:
    code = (
        "import json, esbuild_bin; print(json.dumps({'exe': esbuild_bin.find_esbuild_bin(),"
        " 'version': esbuild_bin.__version__, 'file': esbuild_bin.__file__}))"
    )
    result = run([*PYTHON, "-c", code])
    assert result.returncode == 0, result.stderr
    info: dict[str, str] = json.loads(result.stdout)
    assert not Path(info["file"]).resolve().is_relative_to(ROOT / "src"), info["file"]
    return info


def test_version_constant(installed: dict[str, str]) -> None:
    assert installed["version"] == EXPECTED_VERSION


def test_found_binary_reports_pinned_version(installed: dict[str, str]) -> None:
    result = run([installed["exe"], "--version"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == EXPECTED_VERSION


def test_binary_is_on_path_in_the_environment(installed: dict[str, str]) -> None:
    scripts = str(Path(installed["exe"]).parent)
    found = shutil.which("esbuild", path=scripts)
    assert found is not None
    assert os.path.samefile(found, installed["exe"])


def test_module_runner_version() -> None:
    result = run([*PYTHON, "-m", "esbuild_bin", "--version"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == EXPECTED_VERSION


def test_stdin_transform(installed: dict[str, str]) -> None:
    result = run([installed["exe"], "--minify"], stdin="let a = 1\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
    via_module = run([*PYTHON, "-m", "esbuild_bin", "--minify"], stdin="let a = 1\n")
    assert via_module.returncode == 0, via_module.stderr
    assert via_module.stdout == result.stdout


def test_syntax_error_exit_code_passes_through() -> None:
    result = run([*PYTHON, "-m", "esbuild_bin", "--loader=js"], stdin="let = ;\n")
    assert result.returncode != 0
    assert "error" in result.stderr.lower()


def test_override_path_with_spaces(installed: dict[str, str], tmp_path: Path) -> None:
    spaced = tmp_path / "dir with spaces"
    spaced.mkdir()
    copy = spaced / Path(installed["exe"]).name
    shutil.copy2(installed["exe"], copy)
    env = {**os.environ, "ESBUILD_BINARY_PATH": str(copy)}
    result = run([*PYTHON, "-m", "esbuild_bin", "--version"], env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == EXPECTED_VERSION
