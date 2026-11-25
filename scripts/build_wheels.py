"""Turn esbuild's published npm binaries into Python wheels.

Pipeline: read the pinned version, fetch npm metadata, download every tarball
and check it against npm's sha512 ``dist.integrity``, extract the binary and
check its OS, architecture, linking and macOS minimum against the platform
table, then write one reproducible wheel per platform.

Every platform is fetched and checked before anything is written, so a
failure anywhere leaves the output directory untouched.

Standard library plus ``wheel`` only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from platforms import BY_NPM_NAME, PLATFORMS, Platform
from wheel.wheelfile import WheelFile

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT / "src" / "esbuild_bin"
REGISTRY = "https://registry.npmjs.org"
LICENSE_MEMBER = "package/LICENSE.md"
MINIMUM_EPOCH = 315532800  # 1980-01-01, the earliest timestamp a zip can store

# Wheel metadata. Kept in sync with pyproject.toml by tests/test_build.py.
DIST_NAME = "esbuild-bin"
SUMMARY = (
    "The official esbuild binary, repackaged as Python wheels. "
    "Unofficial; not affiliated with the esbuild project."
)
REQUIRES_PYTHON = ">=3.9"
LICENSE_EXPRESSION = "MIT"
PROJECT_URLS = (
    ("Homepage", "https://github.com/RemiDesgrange/python-esbuild"),
    ("Upstream", "https://github.com/evanw/esbuild"),
    ("Changelog", "https://github.com/evanw/esbuild/blob/main/CHANGELOG.md"),
)

Fetch = Callable[[str], bytes]


class BuildError(Exception):
    """Anything that must stop the build before a wheel is written."""


# --------------------------------------------------------------------------- fetch + verify


def http_fetch(url: str) -> bytes:
    if not url.startswith("https://"):
        raise BuildError(f"refusing non-HTTPS URL: {url}")
    # S310: the scheme is checked just above.
    request = urllib.request.Request(url, headers={"User-Agent": "esbuild-bin-build"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        data: bytes = response.read()
    return data


def metadata_url(package: str, version: str) -> str:
    return f"{REGISTRY}/{urllib.parse.quote(package, safe='@/')}/{version}"


def parse_integrity(integrity: str) -> bytes:
    algorithm, _, encoded = integrity.partition("-")
    if algorithm != "sha512" or not encoded:
        raise BuildError(f"expected a sha512 integrity string, got {integrity!r}")
    digest = base64.b64decode(encoded, validate=True)
    if len(digest) != 64:
        raise BuildError(f"malformed sha512 integrity string: {integrity!r}")
    return digest


def fetch_verified_tarball(fetch: Fetch, package: str, version: str) -> bytes:
    """Download ``package@version`` and verify it against npm's ``dist.integrity``."""
    meta = json.loads(fetch(metadata_url(package, version)))
    if meta.get("name") != package or meta.get("version") != version:
        raise BuildError(f"registry returned {meta.get('name')}@{meta.get('version')}")
    dist = meta.get("dist") or {}
    tarball_url, integrity = dist.get("tarball"), dist.get("integrity")
    if not isinstance(tarball_url, str) or not isinstance(integrity, str):
        raise BuildError(f"{package}@{version}: metadata has no dist.tarball/dist.integrity")
    if not tarball_url.startswith(REGISTRY + "/"):
        raise BuildError(f"{package}@{version}: unexpected tarball host in {tarball_url}")
    expected = parse_integrity(integrity)
    data = fetch(tarball_url)
    actual = hashlib.sha512(data).digest()
    if actual != expected:
        raise BuildError(
            f"{package}@{version}: sha512 mismatch for {tarball_url}\n"
            f"  expected {base64.b64encode(expected).decode()}\n"
            f"  got      {base64.b64encode(actual).decode()}"
        )
    return data


def extract_member(tarball: bytes, member: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        try:
            info = tar.getmember(member)
        except KeyError:
            names = ", ".join(sorted(tar.getnames()))
            raise BuildError(f"{member} not found in tarball (contains: {names})") from None
        if not info.isfile():
            raise BuildError(f"{member} is not a regular file in the tarball")
        handle = tar.extractfile(info)
        if handle is None:
            raise BuildError(f"cannot read {member} from the tarball")
        return handle.read()


# --------------------------------------------------------------------------- binary inspection


@dataclass(frozen=True)
class BinaryInfo:
    os: str
    arch: str
    static: bool | None = None  # ELF only
    macos_min: tuple[int, int] | None = None  # Mach-O only


ELF_MACHINES = {3: "i686", 40: "armv7l", 62: "x86_64", 183: "aarch64", 22: "s390x"}
EM_PPC64 = 21
PT_DYNAMIC, PT_INTERP = 2, 3

MACHO_CPU = {0x01000007: "x86_64", 0x0100000C: "arm64"}
LC_VERSION_MIN_MACOSX, LC_BUILD_VERSION = 0x24, 0x32
PLATFORM_MACOS = 1

PE_MACHINES = {0x8664: "x86_64", 0xAA64: "arm64", 0x14C: "i686"}


def inspect_binary(data: bytes) -> BinaryInfo:
    """Read OS, architecture, linking and macOS minimum from the executable header."""
    try:
        if data[:4] == b"\x7fELF":
            return _inspect_elf(data)
        if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"):
            return _inspect_macho(data)
        if data[:4] in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
            raise BuildError("universal (fat) Mach-O binaries are not supported")
        if data[:2] == b"MZ":
            return _inspect_pe(data)
    except struct.error as exc:
        raise BuildError(f"truncated executable header: {exc}") from None
    raise BuildError(f"unrecognised executable format (magic {data[:4].hex()})")


def _inspect_elf(data: bytes) -> BinaryInfo:
    ei_class, ei_data = data[4], data[5]
    if ei_class not in (1, 2) or ei_data not in (1, 2):
        raise BuildError(f"bad ELF identification (class={ei_class}, data={ei_data})")
    end = "<" if ei_data == 1 else ">"
    (machine,) = struct.unpack_from(end + "H", data, 18)
    if ei_class == 2:
        (phoff,) = struct.unpack_from(end + "Q", data, 0x20)
        phentsize, phnum = struct.unpack_from(end + "HH", data, 0x36)
    else:
        (phoff,) = struct.unpack_from(end + "I", data, 0x1C)
        phentsize, phnum = struct.unpack_from(end + "HH", data, 0x2A)
    segment_types = {
        struct.unpack_from(end + "I", data, phoff + i * phentsize)[0] for i in range(phnum)
    }
    static = PT_INTERP not in segment_types and PT_DYNAMIC not in segment_types

    if machine == EM_PPC64:
        arch = "ppc64le" if ei_data == 1 else "ppc64"
    elif machine in ELF_MACHINES:
        arch = ELF_MACHINES[machine]
    else:
        raise BuildError(f"unknown ELF machine {machine}")
    return BinaryInfo("linux", arch, static=static)


def _inspect_macho(data: bytes) -> BinaryInfo:
    end = "<" if data[:4] == b"\xcf\xfa\xed\xfe" else ">"
    _magic, cputype, _sub, _ftype, ncmds, _size = struct.unpack_from(end + "IiIIII", data, 0)
    arch = MACHO_CPU.get(cputype & 0xFFFFFFFF)
    if arch is None:
        raise BuildError(f"unknown Mach-O cputype {cputype:#x}")

    macos_min: tuple[int, int] | None = None
    offset = 32  # sizeof(mach_header_64)
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from(end + "II", data, offset)
        if cmd == LC_BUILD_VERSION:
            platform, minos = struct.unpack_from(end + "II", data, offset + 8)
            if platform != PLATFORM_MACOS:
                raise BuildError(f"Mach-O LC_BUILD_VERSION platform is {platform}, not macOS")
            macos_min = (minos >> 16, (minos >> 8) & 0xFF)
            break
        if cmd == LC_VERSION_MIN_MACOSX:
            (version,) = struct.unpack_from(end + "I", data, offset + 8)
            macos_min = (version >> 16, (version >> 8) & 0xFF)
            break
        if cmdsize <= 0:
            raise BuildError("corrupt Mach-O load command")
        offset += cmdsize
    if macos_min is None:
        raise BuildError("Mach-O binary declares no minimum macOS version")
    return BinaryInfo("darwin", arch, macos_min=macos_min)


def _inspect_pe(data: bytes) -> BinaryInfo:
    (pe_offset,) = struct.unpack_from("<I", data, 0x3C)
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise BuildError("MZ executable without a PE signature")
    (machine,) = struct.unpack_from("<H", data, pe_offset + 4)
    if machine not in PE_MACHINES:
        raise BuildError(f"unknown PE machine {machine:#x}")
    return BinaryInfo("windows", PE_MACHINES[machine])


def platform_tag(platform: Platform, binary: bytes) -> str:
    """Check ``binary`` against ``platform`` and return the wheel platform tag."""
    info = inspect_binary(binary)
    where = f"@esbuild/{platform.npm_name}"
    if info.os != platform.os:
        raise BuildError(f"{where}: binary is for {info.os}, expected {platform.os}")
    if info.arch != platform.arch:
        raise BuildError(f"{where}: binary is {info.arch}, expected {platform.arch}")
    if platform.os == "linux" and not info.static:
        raise BuildError(
            f"{where}: binary is dynamically linked; the manylinux+musllinux tag would be wrong"
        )
    macos = ""
    if platform.os == "darwin":
        if info.macos_min is None:
            raise BuildError(f"{where}: no minimum macOS version found")
        major, minor = info.macos_min
        macos = f"{major}_{minor}"
    return platform.tag_template.format(macos=macos)


# --------------------------------------------------------------------------- wheel writing


@dataclass(frozen=True)
class PreparedWheel:
    platform: Platform
    tag: str
    binary: bytes


def read_pinned_version(path: Path = ROOT / "ESBUILD_VERSION") -> str:
    version = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise BuildError(f"{path.name} must hold a plain X.Y.Z version, got {version!r}")
    return version


def wheel_version(esbuild_version: str, post: int | None) -> str:
    return esbuild_version if post is None else f"{esbuild_version}.post{post}"


def source_date_epoch() -> int:
    value = os.environ.get("SOURCE_DATE_EPOCH")
    if value is None:
        try:
            value = subprocess.run(
                ["git", "log", "-1", "--format=%ct"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise BuildError("set SOURCE_DATE_EPOCH (could not read the commit time)") from exc
    return max(int(value), MINIMUM_EPOCH)


def package_files(esbuild_version: str) -> list[tuple[str, bytes]]:
    files = [
        (f"esbuild_bin/{p.name}", p.read_bytes())
        for p in sorted(PACKAGE_DIR.iterdir())
        if p.is_file() and (p.suffix == ".py" or p.name == "py.typed") and p.name != "_version.py"
    ]
    version_py = (
        f'# Generated by scripts/build_wheels.py.\n__version__ = "{esbuild_version}"\n'
    ).encode()
    files.append(("esbuild_bin/_version.py", version_py))
    return files


def metadata_text(version: str, readme: str) -> str:
    lines = [
        "Metadata-Version: 2.4",
        f"Name: {DIST_NAME}",
        f"Version: {version}",
        f"Summary: {SUMMARY}",
        *(f"Project-URL: {label}, {url}" for label, url in PROJECT_URLS),
        f"License-Expression: {LICENSE_EXPRESSION}",
        "License-File: LICENSE.md",
        f"Requires-Python: {REQUIRES_PYTHON}",
        "Description-Content-Type: text/markdown",
    ]
    return "\n".join(lines) + "\n\n" + readme


def wheel_text(tag: str) -> str:
    lines = [
        "Wheel-Version: 1.0",
        "Generator: esbuild-bin (scripts/build_wheels.py)",
        "Root-Is-Purelib: false",
        *(f"Tag: py3-none-{t}" for t in tag.split(".")),
    ]
    return "\n".join(lines) + "\n"


@contextmanager
def _env(name: str, value: str) -> Iterator[None]:
    old = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            del os.environ[name]
        else:
            os.environ[name] = old


def write_wheel(
    out_dir: Path,
    prepared: PreparedWheel,
    *,
    esbuild_version: str,
    version: str,
    license_text: bytes,
    readme: str,
    epoch: int,
) -> Path:
    normalized = DIST_NAME.replace("-", "_")
    path = out_dir / f"{normalized}-{version}-py3-none-{prepared.tag}.whl"
    dist_info = f"{normalized}-{version}.dist-info"
    data_dir = f"{normalized}-{version}.data"
    date_time = time.gmtime(epoch)[:6]

    entries: list[tuple[str, bytes, int]] = [
        (name, content, 0o644) for name, content in package_files(esbuild_version)
    ]
    entries.append((f"{data_dir}/scripts/{prepared.platform.script_name}", prepared.binary, 0o755))
    entries.append((f"{dist_info}/licenses/LICENSE.md", license_text, 0o644))
    entries.append((f"{dist_info}/METADATA", metadata_text(version, readme).encode(), 0o644))
    entries.append((f"{dist_info}/WHEEL", wheel_text(prepared.tag).encode(), 0o644))
    entries.sort(key=lambda e: e[0])

    # WheelFile stamps RECORD from SOURCE_DATE_EPOCH, so pin it while writing.
    with _env("SOURCE_DATE_EPOCH", str(epoch)), WheelFile(path, "w", zipfile.ZIP_DEFLATED) as whl:
        for name, content, mode in entries:
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix, so external_attr carries the mode everywhere
            info.external_attr = (stat.S_IFREG | mode) << 16
            whl.writestr(info, content)
    return path


# --------------------------------------------------------------------------- orchestration


def build(
    out_dir: Path,
    *,
    esbuild_version: str,
    platforms: Sequence[Platform] = PLATFORMS,
    post: int | None = None,
    fetch: Fetch = http_fetch,
    epoch: int | None = None,
    readme: str | None = None,
    log: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr),
) -> list[Path]:
    """Build one wheel per platform into ``out_dir``. Writes nothing if any check fails."""
    if epoch is None:
        epoch = source_date_epoch()
    if readme is None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
    version = wheel_version(esbuild_version, post)

    log(f"esbuild {esbuild_version}: fetching license from the main package")
    license_text = extract_member(
        fetch_verified_tarball(fetch, "esbuild", esbuild_version), LICENSE_MEMBER
    )

    prepared: list[PreparedWheel] = []
    for platform in platforms:
        log(f"@esbuild/{platform.npm_name}: fetching and verifying")
        tarball = fetch_verified_tarball(fetch, f"@esbuild/{platform.npm_name}", esbuild_version)
        binary = extract_member(tarball, platform.binary_path)
        tag = platform_tag(platform, binary)
        prepared.append(PreparedWheel(platform, tag, binary))

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with tempfile.TemporaryDirectory(dir=out_dir) as staging:
        staged = [
            write_wheel(
                Path(staging),
                p,
                esbuild_version=esbuild_version,
                version=version,
                license_text=license_text,
                readme=readme,
                epoch=epoch,
            )
            for p in prepared
        ]
        for path in staged:
            target = out_dir / path.name
            shutil.move(str(path), str(target))
            written.append(target)
            log(f"wrote {target}")
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output directory")
    parser.add_argument("--post", type=int, help="packaging-only release number (.postN)")
    parser.add_argument(
        "--platform",
        action="append",
        choices=sorted(BY_NPM_NAME),
        help="npm platform to build (repeatable; default: all)",
    )
    args = parser.parse_args(argv)
    platforms = [BY_NPM_NAME[n] for n in args.platform] if args.platform else list(PLATFORMS)
    try:
        build(
            args.out,
            esbuild_version=read_pinned_version(),
            platforms=platforms,
            post=args.post,
        )
    except BuildError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
