"""The platform table: the single source of truth for what gets built.

Facts here are expectations. ``build_wheels.py`` re-derives OS, architecture,
static linking and the macOS minimum from each binary and fails on mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass

UNIX_BINARY = "package/bin/esbuild"
WINDOWS_BINARY = "package/esbuild.exe"


@dataclass(frozen=True)
class Platform:
    npm_name: str  # package name under the @esbuild scope, e.g. "linux-x64"
    binary_path: str  # member path inside the npm tarball
    os: str  # "linux" | "darwin" | "windows"
    arch: str  # normalized architecture, checked against the binary header
    tag_template: str  # wheel platform tag; "{macos}" is filled from the Mach-O header
    tier: int

    @property
    def script_name(self) -> str:
        return "esbuild.exe" if self.os == "windows" else "esbuild"


def _linux(npm: str, arch: str, tag_arch: str, tier: int) -> Platform:
    tags = f"manylinux_2_17_{tag_arch}.manylinux2014_{tag_arch}.musllinux_1_1_{tag_arch}"
    return Platform(npm, UNIX_BINARY, "linux", arch, tags, tier)


PLATFORMS: tuple[Platform, ...] = (
    _linux("linux-x64", "x86_64", "x86_64", 1),
    _linux("linux-arm64", "aarch64", "aarch64", 1),
    Platform("darwin-arm64", UNIX_BINARY, "darwin", "arm64", "macosx_{macos}_arm64", 1),
    Platform("darwin-x64", UNIX_BINARY, "darwin", "x86_64", "macosx_{macos}_x86_64", 1),
    Platform("win32-x64", WINDOWS_BINARY, "windows", "x86_64", "win_amd64", 1),
    Platform("win32-arm64", WINDOWS_BINARY, "windows", "arm64", "win_arm64", 1),
    _linux("linux-arm", "armv7l", "armv7l", 2),
    _linux("linux-ia32", "i686", "i686", 2),
    _linux("linux-ppc64", "ppc64le", "ppc64le", 2),
    _linux("linux-s390x", "s390x", "s390x", 2),
    Platform("win32-ia32", WINDOWS_BINARY, "windows", "i686", "win32", 2),
)

BY_NPM_NAME: dict[str, Platform] = {p.npm_name: p for p in PLATFORMS}
