from __future__ import annotations

import os
import stat
import sys
import sysconfig
from pathlib import Path

import pytest

from esbuild_bin import _find
from esbuild_bin._find import EXE_NAME, find_esbuild_bin


def make_exe(directory: Path, *, executable: bool = True) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / EXE_NAME
    path.write_bytes(b"fake")
    mode = 0o755 if executable else 0o644
    path.chmod(mode | stat.S_IFREG)
    return path


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Point every lookup location at an empty temp directory."""
    layout = {
        "scripts": tmp_path / "venv" / "bin",
        "user": tmp_path / "home" / ".local" / "bin",
        "module": tmp_path / "target" / "esbuild_bin",
    }
    monkeypatch.delenv(_find.ENV_VAR, raising=False)

    def get_path(name: str, scheme: str | None = None, **kw: object) -> str:
        assert name == "scripts"
        return str(layout["user"] if scheme else layout["scripts"])

    monkeypatch.setattr(sysconfig, "get_path", get_path)
    monkeypatch.setattr(_find, "_module_dir", lambda: str(layout["module"]))
    return layout


def test_env_override_wins(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    make_exe(dirs["scripts"])
    custom = make_exe(dirs["scripts"].parent / "custom dir with spaces")
    monkeypatch.setenv("ESBUILD_BINARY_PATH", str(custom))
    assert find_esbuild_bin() == str(custom)


@pytest.mark.skipif(sys.platform == "win32", reason="no executable bit on Windows")
def test_non_executable_override_raises(
    dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    make_exe(dirs["scripts"])  # would be found if we fell through
    bad = make_exe(dirs["scripts"].parent / "other", executable=False)
    monkeypatch.setenv("ESBUILD_BINARY_PATH", str(bad))
    with pytest.raises(FileNotFoundError, match="ESBUILD_BINARY_PATH"):
        find_esbuild_bin()


def test_missing_override_raises(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    make_exe(dirs["scripts"])
    monkeypatch.setenv("ESBUILD_BINARY_PATH", str(dirs["scripts"] / "nope"))
    with pytest.raises(FileNotFoundError, match="not an executable file"):
        find_esbuild_bin()


def test_empty_override_is_ignored(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    exe = make_exe(dirs["scripts"])
    monkeypatch.setenv("ESBUILD_BINARY_PATH", "")
    assert find_esbuild_bin() == str(exe)


def test_scripts_dir_before_user_dir(dirs: dict[str, Path]) -> None:
    make_exe(dirs["user"])
    exe = make_exe(dirs["scripts"])
    assert find_esbuild_bin() == str(exe)


def test_user_dir_before_target_dir(dirs: dict[str, Path]) -> None:
    make_exe(dirs["module"].parent / "bin")
    exe = make_exe(dirs["user"])
    assert find_esbuild_bin() == str(exe)


def test_target_install(dirs: dict[str, Path]) -> None:
    exe = make_exe(dirs["module"].parent / "bin")
    assert find_esbuild_bin() == str(exe)


def test_prefix_install(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = dirs["module"].parent.parent / "prefix"
    if sys.platform == "win32":
        module = prefix / "Lib" / "site-packages" / "esbuild_bin"
        scripts = prefix / "Scripts"
    else:
        module = prefix / "lib" / "python3.12" / "site-packages" / "esbuild_bin"
        scripts = prefix / "bin"
    monkeypatch.setattr(_find, "_module_dir", lambda: str(module))
    exe = make_exe(scripts)
    assert find_esbuild_bin() == str(exe)


def test_error_lists_every_path_tried(dirs: dict[str, Path]) -> None:
    with pytest.raises(FileNotFoundError) as excinfo:
        find_esbuild_bin()
    message = str(excinfo.value)
    for key in ("scripts", "user"):
        assert os.path.join(str(dirs[key]), EXE_NAME) in message
    assert os.path.join(str(dirs["module"].parent / "bin"), EXE_NAME) in message
    assert "ESBUILD_BINARY_PATH" in message


def test_strip_suffix() -> None:
    sep = os.sep
    path = sep.join(["", "p", "lib", "python3.12", "site-packages", "esbuild_bin"])
    assert _find._strip_suffix(path, "lib/python*/site-packages/esbuild_bin") == sep + "p"
    assert _find._strip_suffix(path, "Lib/site-packages/esbuild_bin") is None
    assert _find._strip_suffix("esbuild_bin", "esbuild_bin") is None
