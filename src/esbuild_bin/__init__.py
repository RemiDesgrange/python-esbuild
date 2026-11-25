"""The official esbuild binary, packaged for Python.

The public API is ``find_esbuild_bin()``, ``__version__`` (the esbuild version
this wheel carries) and ``python -m esbuild_bin <args>``.
"""

from esbuild_bin._find import find_esbuild_bin
from esbuild_bin._version import __version__

__all__ = ["__version__", "find_esbuild_bin"]
