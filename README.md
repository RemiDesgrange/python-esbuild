# esbuild-bin

The official [esbuild](https://esbuild.github.io/) executable, repackaged as Python wheels.

```sh
pip install "esbuild-bin>=0.28,<0.29"
esbuild --version
```

Requires Python 3.11 or newer. After installing, any Python project has a working `esbuild`: no Node.js, no npm, no compiler, and no downloads at install or run time. It works with Django, Flask, FastAPI, MkDocs, Sphinx or a plain CI script.

> **Unofficial.** This is an independent repackaging. It is not affiliated with or endorsed by the esbuild project. The binary inside each wheel is byte-for-byte the one esbuild publishes to npm. Report esbuild bugs [upstream](https://github.com/evanw/esbuild/issues), and packaging bugs [here](https://github.com/RemiDesgrange/python-esbuild/issues).

## Versioning: pin a minor range

The wheel version is the esbuild version: `esbuild-bin==0.28.2` contains esbuild 0.28.2. esbuild is still 0.x, and **minor releases can change behaviour**, so pin a minor range:

```toml
dependencies = ["esbuild-bin>=0.28,<0.29"]
```

Every upstream release gets its own release here; none are skipped. Packaging-only fixes ship as `.postN` (for example `0.28.2.post1`) and never change the binary. Read the [esbuild changelog](https://github.com/evanw/esbuild/blob/main/CHANGELOG.md) before moving to a new minor.

## Three ways to call it

**1. On `PATH`.** The binary is installed as a script, so inside the environment it is just `esbuild`:

```sh
esbuild app.js --bundle --minify --outfile=out.js
```

**2. As a module.** Arguments are passed through unchanged, and so is the exit code:

```sh
python -m esbuild_bin app.js --bundle --minify --outfile=out.js
```

**3. From Python (recommended for libraries).** `find_esbuild_bin()` returns the binary's path. Build the argument list yourself, following `esbuild --help`:

```python
import subprocess
from esbuild_bin import find_esbuild_bin

subprocess.run(
    [find_esbuild_bin(), "app.js", "--bundle", "--minify", "--outfile=out.js"],
    check=True,
)
```

There is deliberately no Python wrapper around esbuild's options. `esbuild_bin.__version__` is the esbuild version the wheel carries.

`find_esbuild_bin()` looks, in order, at `$ESBUILD_BINARY_PATH`; the current environment's scripts directory; the user scripts directory (`pip install --user`); and the scripts directory next to the package (`pip install --target` / `--prefix`). If none has the binary, it raises `FileNotFoundError` listing every path it tried.

## `ESBUILD_BINARY_PATH` and unsupported platforms

`ESBUILD_BINARY_PATH` is esbuild's own override variable. If it is set, `find_esbuild_bin()` and `python -m esbuild_bin` use that executable and nothing else; if it doesn't point to an executable file, they raise an error. On a platform without a wheel (see below), `pip install` fails with "no matching distribution". In that case, install esbuild another way and point `ESBUILD_BINARY_PATH` at it.

## Platforms

| Platform | Wheel tag | Tested in CI |
| --- | --- | --- |
| Linux x86_64 (glibc and musl) | `manylinux_2_17_x86_64`, `musllinux_1_1_x86_64` | yes, including Alpine |
| Linux aarch64 (glibc and musl) | `manylinux_2_17_aarch64`, `musllinux_1_1_aarch64` | yes |
| macOS arm64, 12.0+ | `macosx_12_0_arm64` | yes |
| macOS x86_64, 12.0+ | `macosx_12_0_x86_64` | yes |
| Windows x64 | `win_amd64` | yes |
| Windows arm64 | `win_arm64` | yes |
| Linux armv7l, i686, ppc64le, s390x | `manylinux_2_17_*`, `musllinux_1_1_*` | tags only |
| Windows x86 | `win32` | tags only |

The Linux binaries are statically linked, so one wheel serves both glibc and musl. The build checks every binary's OS, architecture, linking and minimum macOS version, and refuses to ship a wheel whose tag doesn't match its binary.

## Verifying a wheel

The release pipeline is designed so that every step can be checked:

- **Input.** Every npm tarball is checked against npm's `dist.integrity` sha512 before anything is extracted. The binary is never modified: no stripping, signing or patching.
- **Provenance.** Releases are published only from GitHub Actions, using PyPI trusted publishing with [PEP 740](https://peps.python.org/pep-0740/) attestations. PyPI shows each file's attestation, which links it to the workflow run that built it.
- **Reproducibility.** Wheels have fixed timestamps and a fixed entry order, so you can rebuild a release and compare hashes:

  ```sh
  git clone https://github.com/RemiDesgrange/python-esbuild && cd python-esbuild
  git checkout v0.28.2
  uv sync --locked
  uv run python scripts/build_wheels.py --out dist   # add --post N for a vN.postM tag
  sha256sum dist/*.whl   # compare with the hashes shown on PyPI
  ```

  Timestamps come from `SOURCE_DATE_EPOCH`, which defaults to the tagged commit's time.

## License

The packaging code is MIT-licensed (see `LICENSE`). esbuild is MIT-licensed, Copyright (c) 2020 Evan Wallace. Its license ships in every wheel as `esbuild_bin-*.dist-info/licenses/LICENSE.md`.
