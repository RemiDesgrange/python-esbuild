"""Tiny fake executables and a fake npm registry for offline build tests."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import struct
import tarfile

from build_wheels import REGISTRY, metadata_url
from platforms import Platform

ELF_MACHINE = {"x86_64": 62, "aarch64": 183, "armv7l": 40, "i686": 3, "s390x": 22}
MACHO_CPU = {"x86_64": 0x01000007, "arm64": 0x0100000C}
PE_MACHINE = {"x86_64": 0x8664, "arm64": 0xAA64, "i686": 0x14C}


def elf(arch: str, *, dynamic: bool = False) -> bytes:
    """A 64-bit (or 32-bit for arm/i686) ELF header plus program headers."""
    is64 = arch not in ("armv7l", "i686")
    big_endian = arch in ("s390x", "ppc64")
    end = ">" if big_endian else "<"
    machine = 21 if arch.startswith("ppc64") else ELF_MACHINE[arch]
    segment_types = [6, 1, 3, 2] if dynamic else [6, 1]  # PHDR, LOAD, (INTERP, DYNAMIC)
    ident = b"\x7fELF" + bytes([2 if is64 else 1, 2 if big_endian else 1, 1]) + bytes(9)
    if is64:
        header = ident + struct.pack(
            end + "HHIQQQIHHHHHH", 2, machine, 1, 0, 64, 0, 0, 64, 56, len(segment_types), 0, 0, 0
        )
        phdrs = b"".join(struct.pack(end + "II", t, 0) + bytes(48) for t in segment_types)
    else:
        header = ident + struct.pack(
            end + "HHIIIIIHHHHHH", 2, machine, 1, 0, 52, 0, 0, 52, 32, len(segment_types), 0, 0, 0
        )
        phdrs = b"".join(struct.pack(end + "I", t) + bytes(28) for t in segment_types)
    return header + phdrs + b"fake esbuild " + arch.encode()


def macho(arch: str, *, minos: tuple[int, int] = (12, 0), legacy: bool = False) -> bytes:
    version = (minos[0] << 16) | (minos[1] << 8)
    segment = struct.pack("<II", 0x19, 72) + bytes(64)  # LC_SEGMENT_64, skipped by the parser
    if legacy:
        version_cmd = struct.pack("<IIII", 0x24, 16, version, version)
    else:
        version_cmd = struct.pack("<IIIIII", 0x32, 24, 1, version, version, 0)
    cmds = segment + version_cmd
    header = struct.pack("<IiIIIIII", 0xFEEDFACF, MACHO_CPU[arch], 0, 2, 2, len(cmds), 0, 0)
    return header + cmds + b"fake esbuild " + arch.encode()


def pe(arch: str) -> bytes:
    dos = b"MZ" + bytes(0x3A) + struct.pack("<I", 0x40)
    return dos + b"PE\0\0" + struct.pack("<H", PE_MACHINE[arch]) + bytes(18) + b"fake esbuild"


def binary_for(platform: Platform) -> bytes:
    if platform.os == "linux":
        return elf(platform.arch)
    if platform.os == "darwin":
        return macho(platform.arch)
    return pe(platform.arch)


def tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeRegistry:
    """Serves npm-style metadata and tarballs from memory; records requested URLs."""

    def __init__(self) -> None:
        self.responses: dict[str, bytes] = {}
        self.requested: list[str] = []

    def publish(self, package: str, version: str, files: dict[str, bytes]) -> None:
        data = tarball(files)
        url = f"{REGISTRY}/{package}/-/{package.rsplit('/', 1)[-1]}-{version}.tgz"
        integrity = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
        meta = {
            "name": package,
            "version": version,
            "dist": {"tarball": url, "integrity": integrity},
        }
        self.responses[metadata_url(package, version)] = json.dumps(meta).encode()
        self.responses[url] = data

    def corrupt_tarball(self, package: str, version: str) -> None:
        meta = json.loads(self.responses[metadata_url(package, version)])
        self.responses[meta["dist"]["tarball"]] += b"tampered"

    def __call__(self, url: str) -> bytes:
        self.requested.append(url)
        try:
            return self.responses[url]
        except KeyError:
            raise AssertionError(f"unexpected fetch: {url}") from None


LICENSE = b"MIT License\n\nCopyright (c) 2020 Evan Wallace\n"


def registry_for(
    version: str, platforms: list[Platform], overrides: dict[str, bytes] | None = None
) -> FakeRegistry:
    registry = FakeRegistry()
    registry.publish("esbuild", version, {"package/LICENSE.md": LICENSE})
    for p in platforms:
        binary = (overrides or {}).get(p.npm_name, binary_for(p))
        registry.publish(
            f"@esbuild/{p.npm_name}",
            version,
            {p.binary_path: binary, "package/package.json": b"{}", "package/README.md": b"x"},
        )
    return registry
