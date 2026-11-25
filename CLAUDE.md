# esbuild-bin

PyPI wheels that ship the official, unmodified esbuild binary from npm.

## Commands

- `uv sync`: install the dev tools (ruff, mypy, pytest, wheel). No package build backend is involved.
- `uv run ruff check . && uv run ruff format --check .`: lint and format check.
- `uv run mypy`: strict type check of `src`, `scripts`, `tests`.
- `uv run pytest`: offline tests (fake npm registry, locator).
- `uv run python scripts/build_wheels.py --out dist/`: real build. Downloads from npm and writes one wheel per platform.
- `ESBUILD_BIN_INSTALLED_TEST=1 python -m pytest tests/test_installed.py`: run in an env where a built wheel is installed.
- `uv run --isolated --python 3.9 pytest`: check that the code still runs on the minimum supported Python.

## Layout

- `ESBUILD_VERSION`: the single pinned upstream version.
- `scripts/platforms.py`: the platform table, the single source of truth for npm names and wheel tags.
- `scripts/build_wheels.py`: download, verify, inspect and write the wheels.
- `src/esbuild_bin/`: the runtime package copied into every wheel. `_version.py` and `pyproject.toml` repeat `ESBUILD_VERSION`; the build refuses to run if they differ (`check_upstream.py --write` updates all three, then run `uv lock`).

## Guardrails for Claude Code

- **Verify, never assume.** Tarball paths, linking, architecture and macOS minimum are checked by code on every build. Facts in this plan come from 0.28.2 and may change.
- **Never modify the binary.** No stripping, no signing, no patching. The bytes inside the wheel must hash to what is inside the verified npm tarball.
- **Never commit a binary or a built wheel.** `dist/` and download caches go in `.gitignore`.
- **Keep the API frozen.** `find_esbuild_bin()`, `__version__` and `python -m esbuild_bin` are the whole surface. Do not add option builders, typed configs or helpers that wrap esbuild flags; propose them in an issue instead.
- **No network at import or install time.** The package must work offline once installed.
- **Use subprocess with argument lists only, never `shell=True`.** On Windows, test paths containing spaces.
- **Keep the build script on the standard library plus `wheel`.** No other dependencies.
- **Write tests first for the sanity checks.** They are what stops a mislabelled wheel from reaching PyPI.
- **Releases are human-triggered.** Automation may open PRs, but only a pushed tag publishes.
- **When upstream and this plan disagree, upstream wins.** Note the discrepancy in the PR description.
