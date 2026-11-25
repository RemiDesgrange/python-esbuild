from __future__ import annotations

import json
from pathlib import Path

import check_upstream
import pytest
from build_wheels import ROOT, read_pinned_version
from check_upstream import changelog_url, main, next_release


def test_next_release_never_skips_and_ignores_prereleases() -> None:
    published = ["0.28.0", "0.28.2", "0.29.0-beta.1", "0.28.10", "0.28.3", "0.29.0"]
    assert next_release("0.28.2", published) == "0.28.3"
    assert next_release("0.28.3", published) == "0.28.10"
    assert next_release("0.28.10", published) == "0.29.0"
    assert next_release("0.29.0", published) is None


def test_changelog_anchor() -> None:
    assert changelog_url("0.28.3").endswith("CHANGELOG.md#0283")


def test_main_writes_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    current = read_pinned_version()
    major, minor, patch = (int(x) for x in current.split("."))
    upcoming = f"{major}.{minor}.{patch + 1}"
    packument = {
        "versions": {current: {}, upcoming: {}},
        "dist-tags": {"latest": upcoming},
    }
    output = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(check_upstream, "ROOT", tmp_path)  # --write must not touch the repo
    monkeypatch.setattr(
        check_upstream, "read_pinned_version", lambda: read_pinned_version(ROOT / "ESBUILD_VERSION")
    )

    assert main(["--write"], fetch=lambda _url: json.dumps(packument).encode()) == 0
    assert capsys.readouterr().out.strip() == upcoming
    assert (tmp_path / "ESBUILD_VERSION").read_text() == upcoming + "\n"
    assert f"version={upcoming}\n" in output.read_text()
