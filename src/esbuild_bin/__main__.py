"""``python -m esbuild_bin <args>``: run the bundled esbuild binary with ``args``."""

from __future__ import annotations

import os
import subprocess
import sys

from esbuild_bin._find import find_esbuild_bin


def main() -> int:
    exe = find_esbuild_bin()
    argv = [exe, *sys.argv[1:]]
    if sys.platform == "win32":
        # No exec on Windows; forward the child's exit code instead.
        try:
            return subprocess.run(argv, check=False).returncode
        except KeyboardInterrupt:
            return 130
    os.execv(exe, argv)  # noqa: S606 (argument list, no shell)
    return 1  # unreachable


if __name__ == "__main__":
    sys.exit(main())
