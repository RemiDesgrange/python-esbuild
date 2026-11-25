from __future__ import annotations

import base64
import csv
import hashlib
import io
import tomllib
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest
from build_wheels import (
    DIST_NAME,
    LICENSE_EXPRESSION,
    REQUIRES_PYTHON,
    ROOT,
    SUMMARY,
    BuildError,
    Fetch,
    build,
    check_versions_match,
    inspect_binary,
    parse_integrity,
    platform_tag,
    read_pinned_version,
    set_version,
    wheel_version,
)
from fakes import LICENSE, FakeRegistry, binary_for, elf, macho, pe, registry_for
from platforms import BY_NPM_NAME, PLATFORMS, Platform

VERSION = read_pinned_version()
EPOCH = 1764000000


def run_build(
    out: Path,
    *,
    platforms: Sequence[Platform] = PLATFORMS,
    fetch: Fetch | None = None,
    post: int | None = None,
) -> list[Path]:
    return build(
        out,
        esbuild_version=VERSION,
        platforms=platforms,
        post=post,
        fetch=fetch or registry_for(VERSION, list(platforms)),
        epoch=EPOCH,
        readme="# esbuild-bin\n",
        log=lambda _msg: None,
    )


# --------------------------------------------------------------------------- sanity checks


@pytest.mark.parametrize("platform", PLATFORMS, ids=lambda p: p.npm_name)
def test_inspection_matches_every_platform(platform: Platform) -> None:
    tag = platform_tag(platform, binary_for(platform))
    assert tag == platform.tag_template.format(macos="12_0")


def test_dynamically_linked_linux_binary_fails() -> None:
    with pytest.raises(BuildError, match="dynamically linked"):
        platform_tag(BY_NPM_NAME["linux-x64"], elf("x86_64", dynamic=True))


def test_architecture_mismatch_fails() -> None:
    with pytest.raises(BuildError, match="binary is aarch64, expected x86_64"):
        platform_tag(BY_NPM_NAME["linux-x64"], elf("aarch64"))
    with pytest.raises(BuildError, match="binary is x86_64, expected arm64"):
        platform_tag(BY_NPM_NAME["darwin-arm64"], macho("x86_64"))
    with pytest.raises(BuildError, match="binary is i686, expected x86_64"):
        platform_tag(BY_NPM_NAME["win32-x64"], pe("i686"))


def test_os_mismatch_fails() -> None:
    with pytest.raises(BuildError, match="binary is for windows, expected linux"):
        platform_tag(BY_NPM_NAME["linux-x64"], pe("x86_64"))


def test_ppc64_endianness_is_checked() -> None:
    assert inspect_binary(elf("ppc64le")).arch == "ppc64le"
    with pytest.raises(BuildError, match="binary is ppc64, expected ppc64le"):
        platform_tag(BY_NPM_NAME["linux-ppc64"], elf("ppc64"))


def test_macos_tag_follows_the_binary_minimum() -> None:
    assert platform_tag(BY_NPM_NAME["darwin-arm64"], macho("arm64", minos=(13, 3))) == (
        "macosx_13_3_arm64"
    )
    legacy = macho("x86_64", minos=(10, 13), legacy=True)
    assert platform_tag(BY_NPM_NAME["darwin-x64"], legacy) == "macosx_10_13_x86_64"


def test_unknown_and_truncated_binaries_fail() -> None:
    with pytest.raises(BuildError, match="unrecognised executable format"):
        inspect_binary(b"#!/bin/sh\n")
    with pytest.raises(BuildError, match="truncated"):
        inspect_binary(elf("x86_64")[:30])
    with pytest.raises(BuildError, match="universal"):
        inspect_binary(b"\xca\xfe\xba\xbe" + bytes(32))


# --------------------------------------------------------------------------- integrity


def test_parse_integrity_rejects_other_algorithms() -> None:
    with pytest.raises(BuildError, match="sha512"):
        parse_integrity("sha1-" + base64.b64encode(b"x" * 20).decode())
    with pytest.raises(BuildError, match="malformed"):
        parse_integrity("sha512-" + base64.b64encode(b"short").decode())


def test_wrong_hash_aborts_and_writes_nothing(tmp_path: Path) -> None:
    registry = registry_for(VERSION, list(PLATFORMS))
    registry.corrupt_tarball("@esbuild/win32-arm64", VERSION)  # a late platform
    out = tmp_path / "dist"
    with pytest.raises(BuildError, match="sha512 mismatch"):
        run_build(out, fetch=registry)
    assert not out.exists() or list(out.iterdir()) == []


def test_failed_sanity_check_writes_nothing(tmp_path: Path) -> None:
    bad = {"linux-s390x": elf("x86_64")}
    registry = registry_for(VERSION, list(PLATFORMS), overrides=bad)
    out = tmp_path / "dist"
    with pytest.raises(BuildError):
        run_build(out, fetch=registry)
    assert not out.exists() or list(out.iterdir()) == []


def test_missing_binary_in_tarball_fails(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.publish("esbuild", VERSION, {"package/LICENSE.md": LICENSE})
    registry.publish("@esbuild/linux-x64", VERSION, {"package/esbuild": elf("x86_64")})
    with pytest.raises(BuildError, match="package/bin/esbuild not found"):
        run_build(tmp_path, platforms=[BY_NPM_NAME["linux-x64"]], fetch=registry)


# --------------------------------------------------------------------------- wheel contents


def test_builds_one_wheel_per_platform_with_expected_tags(tmp_path: Path) -> None:
    wheels = run_build(tmp_path)
    names = sorted(w.name for w in wheels)
    expected = sorted(
        f"esbuild_bin-{VERSION}-py3-none-{p.tag_template.format(macos='12_0')}.whl"
        for p in PLATFORMS
    )
    assert names == expected
    assert sorted(p.name for p in tmp_path.iterdir()) == expected  # no staging leftovers


@pytest.mark.parametrize("npm_name", ["linux-x64", "darwin-arm64", "win32-x64"])
def test_wheel_contents(tmp_path: Path, npm_name: str) -> None:
    platform = BY_NPM_NAME[npm_name]
    (wheel,) = run_build(tmp_path, platforms=[platform])
    dist_info = f"esbuild_bin-{VERSION}.dist-info"
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        script = f"esbuild_bin-{VERSION}.data/scripts/{platform.script_name}"
        assert script in names
        info = zf.getinfo(script)
        assert (info.external_attr >> 16) & 0o777 == 0o755
        assert zf.read(script) == binary_for(platform)  # unmodified bytes

        assert zf.read(f"{dist_info}/licenses/LICENSE.md") == LICENSE
        assert {"esbuild_bin/__init__.py", "esbuild_bin/__main__.py", "esbuild_bin/py.typed"} <= (
            set(names)
        )
        assert f'__version__ = "{VERSION}"' in zf.read("esbuild_bin/_version.py").decode()

        wheel_meta = zf.read(f"{dist_info}/WHEEL").decode()
        assert "Root-Is-Purelib: false" in wheel_meta
        tags = [line.split(": ")[1] for line in wheel_meta.splitlines() if line.startswith("Tag:")]
        expected = platform.tag_template.format(macos="12_0").split(".")
        assert tags == [f"py3-none-{t}" for t in expected]

        metadata = zf.read(f"{dist_info}/METADATA").decode()
        assert "Metadata-Version: 2.4" in metadata
        assert "License-Expression: MIT" in metadata
        assert "License-File: LICENSE.md" in metadata
        assert f"Version: {VERSION}\n" in metadata

        assert names == [*sorted(names[:-1]), f"{dist_info}/RECORD"]
        record = list(csv.reader(io.StringIO(zf.read(f"{dist_info}/RECORD").decode())))
        assert {row[0] for row in record} == set(names)
        for name, digest, size in record:
            if name.endswith("RECORD"):
                continue
            data = zf.read(name)
            algo, _, value = digest.partition("=")
            assert algo == "sha256"
            encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
            assert value == encoded.decode()
            assert int(size) == len(data)


def test_post_release_changes_only_wheel_version(tmp_path: Path) -> None:
    (wheel,) = run_build(tmp_path, platforms=[BY_NPM_NAME["linux-x64"]], post=1)
    assert wheel.name.startswith(f"esbuild_bin-{VERSION}.post1-py3-none-")
    with zipfile.ZipFile(wheel) as zf:
        assert f'__version__ = "{VERSION}"' in zf.read("esbuild_bin/_version.py").decode()
        assert f"esbuild_bin-{VERSION}.post1.data/scripts/esbuild" in zf.namelist()


def test_build_is_reproducible(tmp_path: Path) -> None:
    first = run_build(tmp_path / "a")
    second = run_build(tmp_path / "b")
    assert [w.name for w in first] == [w.name for w in second]
    for a, b in zip(first, second, strict=True):
        assert hashlib.sha256(a.read_bytes()).digest() == hashlib.sha256(b.read_bytes()).digest()


def test_build_ignores_ambient_source_date_epoch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "400000000")
    (a,) = run_build(tmp_path / "a", platforms=[BY_NPM_NAME["linux-x64"]])
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "500000000")
    (b,) = run_build(tmp_path / "b", platforms=[BY_NPM_NAME["linux-x64"]])
    assert a.read_bytes() == b.read_bytes()


# --------------------------------------------------------------------------- misc


def test_version_helpers(tmp_path: Path) -> None:
    assert wheel_version("0.28.2", None) == "0.28.2"
    assert wheel_version("0.28.2", 3) == "0.28.2.post3"
    assert read_pinned_version() == (ROOT / "ESBUILD_VERSION").read_text().strip()
    bad = tmp_path / "ESBUILD_VERSION"
    bad.write_text("v0.28\n")
    with pytest.raises(BuildError):
        read_pinned_version(bad)


def test_repo_versions_match_esbuild_version() -> None:
    check_versions_match(read_pinned_version())


def copy_version_files(dest: Path) -> None:
    for rel in ("ESBUILD_VERSION", "pyproject.toml", "src/esbuild_bin/_version.py"):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_text((ROOT / rel).read_text())


def test_version_mismatch_is_detected(tmp_path: Path) -> None:
    copy_version_files(tmp_path)
    version_py = tmp_path / "src/esbuild_bin/_version.py"
    version_py.write_text(version_py.read_text().replace(VERSION, "0.0.1"))
    with pytest.raises(BuildError, match=r"_version\.py declares '0\.0\.1'"):
        check_versions_match(VERSION, tmp_path)


def test_set_version_updates_every_declaration(tmp_path: Path) -> None:
    copy_version_files(tmp_path)
    set_version("1.2.3", tmp_path)
    assert read_pinned_version(tmp_path / "ESBUILD_VERSION") == "1.2.3"
    check_versions_match("1.2.3", tmp_path)
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert pyproject.count("1.2.3") == 1  # only the project version, nothing else rewritten


def test_build_refuses_mismatched_versions(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="must match ESBUILD_VERSION"):
        build(tmp_path, esbuild_version="9.9.9", fetch=registry_for("9.9.9", []), epoch=EPOCH)


def test_wheel_metadata_matches_pyproject() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["name"] == DIST_NAME
    assert project["description"] == SUMMARY
    assert project["requires-python"] == REQUIRES_PYTHON
    assert project["license"] == LICENSE_EXPRESSION
    assert project["version"] == read_pinned_version()
