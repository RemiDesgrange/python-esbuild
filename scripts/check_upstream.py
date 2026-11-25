"""Find the next esbuild release after the pinned one (used by bump.yml).

Releases are never skipped: if several came out since the pin, this picks the
oldest of them, so every upstream version gets its own release here. Pre-releases
are ignored.

Prints the next version (or nothing). With ``--write`` it also updates
ESBUILD_VERSION. In GitHub Actions it sets the ``version``, ``latest`` and
``changelog`` step outputs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable, Sequence

from build_wheels import REGISTRY, ROOT, Fetch, http_fetch, read_pinned_version

CHANGELOG = "https://github.com/evanw/esbuild/blob/main/CHANGELOG.md"
RELEASE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse(version: str) -> tuple[int, int, int] | None:
    match = RELEASE.fullmatch(version)
    if match is None:
        return None
    major, minor, patch = (int(g) for g in match.groups())
    return major, minor, patch


def next_release(current: str, published: Iterable[str]) -> str | None:
    pinned = parse(current)
    if pinned is None:
        raise ValueError(f"not a release version: {current!r}")
    newer = sorted((key, v) for v in published if (key := parse(v)) is not None and key > pinned)
    return newer[0][1] if newer else None


def changelog_url(version: str) -> str:
    return f"{CHANGELOG}#{version.replace('.', '')}"


def published_versions(fetch: Fetch) -> tuple[list[str], str]:
    packument = json.loads(fetch(f"{REGISTRY}/esbuild"))
    return list(packument["versions"]), str(packument["dist-tags"]["latest"])


def main(argv: Sequence[str] | None = None, fetch: Fetch = http_fetch) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="update ESBUILD_VERSION")
    args = parser.parse_args(argv)

    current = read_pinned_version()
    versions, latest = published_versions(fetch)
    upcoming = next_release(current, versions)
    if upcoming is None:
        print(f"esbuild {current} is current (npm latest: {latest})", file=sys.stderr)
    else:
        print(f"esbuild {current} -> {upcoming} (npm latest: {latest})", file=sys.stderr)
        print(upcoming)
        if args.write:
            (ROOT / "ESBUILD_VERSION").write_text(upcoming + "\n", encoding="utf-8")

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"version={upcoming or ''}\n")
            fh.write(f"latest={latest}\n")
            fh.write(f"changelog={changelog_url(upcoming) if upcoming else ''}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
