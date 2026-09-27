# Developing spiflash

Contributor notes. For what the package does, see the
[README](https://github.com/mithro/spiflash/blob/main/README.md); for how
versions reach PyPI and apt, see
[RELEASING.md](https://github.com/mithro/spiflash/blob/main/RELEASING.md).

## The documentation site

<https://spiflash.readthedocs.io/> is built from `docs/` by Sphinx (Furo theme,
MyST Markdown). The vendor and chip pages are not in git:
`docs/_ext/spiflash_pages.py` writes them from the installed package's data
at the start of every build, so the site always matches the release.
The API reference (`docs/api.md`) is generated the same way, by autosummary,
from every module of `spiflash` and of `tools/spiflash_extract`: a new module
or function appears in it without anything to update (`docs/_autosummary/` is
not in git either).

```sh
uv sync --group docs
uv run sphinx-build -W -b html docs docs/_build/html   # what CI runs
```

`.readthedocs.yaml` builds it on Read the Docs, and `.github/workflows/docs.yml`
builds it (warnings are errors) on every push and pull request.

## Setup and gates

```sh
uv sync --group dev --group docs
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest
```

Those four are what the `test` job of `.github/workflows/deb.yml` runs, on
Python 3.11 to 3.14. A green run is what "mergeable" means, and on `main` what
publishes. `uv run ruff format` applies the formatting.

The code carries no `# noqa`, no `type: ignore` and no per-file lint ignores:
when a rule fires, the code changes. The rules chosen, and the four families
left out (and why), are listed in `pyproject.toml`.

## Layout

```
src/spiflash/          the package: model.py (Record, Flash), db.py (loading,
                       lookup, find), opcodes.py (the named operations),
                       vendors.py (one name per vendor), cli.py
src/spiflash/data/     generated: records.json, manufacturers.json, sources.json
tools/sources.toml     the upstream commits the data is built from
tools/update_db.py     fetch, extract, write the data
tools/spiflash_extract/
    cparse.py          just enough C: find tables, split initialisers,
                       evaluate integer expressions (SZ_16M, BIT(3), 64 * 1024)
    record.py          the common record every extractor writes
    ops.py             each record's opcodes, values checked against
                       src/spiflash/opcodes.py
    linux.py uboot.py flashrom.py openocd.py openfpgaloader.py
    fetch.py           sparse, blobless, depth-1 fetch of one commit
```

The extractors are not in the wheel; the sdist carries them so the data can be
rebuilt from it.

## Updating the data

```sh
uv run tools/update_db.py            # rebuild from the pins in tools/sources.toml
uv run tools/update_db.py --latest   # move the pins to each upstream's HEAD, then rebuild
uv run tools/update_db.py --check    # fail if the committed data differs
```

Commit `tools/sources.toml` and `src/spiflash/data/` together. The data files
hold one record per line, so `git diff` reads chip by chip.

`.github/workflows/upstream.yml` runs `--check` on every change to the data or
the tools, and `--latest --check` weekly. It is deliberately not part of
`deb.yml`: a release should not wait on six servers.

## When an upstream changes its format

An extractor that meets something it does not understand raises, naming the
file and line, rather than guessing: an unknown probe in flashrom, an `.id`
that is not `SNOR_ID(...)` in Linux, an identifier the expression evaluator has
no value for. Fix the extractor, and add the new shape to the miniature tree in
`tests/test_extract.py`, which holds a cut-down copy of each format.

A new vendor spelling fails `test_vendor_spellings_all_canonical`: add it to
`src/spiflash/vendors.py`.

## Adding a source

1. A `[name]` table in `tools/sources.toml`: `url`, `branch`, `commit`, the
   `paths` to fetch, and the `license` of those files.
2. `tools/spiflash_extract/<name>.py` with `extract(root) -> list[Record]`,
   built with `record.make(...)`, and an entry in `update_db.EXTRACTORS`.
3. Tests on a miniature tree in `tests/test_extract.py`.
4. Its place in `SOURCE_PRIORITY` (`src/spiflash/model.py`), and a row in the
   README and `docs/SOURCES.md`.

## Building the Debian package locally

The CI build is `mithro/apt-repo-action`'s `build-deb`: `dpkg-buildpackage -A`
in a `debian:<suite>` container, after its `scripts/deb-version.py` writes
`debian/changelog`. By hand, from a clean clone:

```sh
docker run --rm -v "$PWD:/work/src" -v /path/to/apt-repo-action:/a:ro -w /work/src debian:trixie bash -ec '
  apt-get update && apt-get install -y --no-install-recommends build-essential debhelper dpkg-dev fakeroot git python3
  apt-get build-dep -y ./ && git config --global --add safe.directory "*"
  python3 /a/scripts/deb-version.py --suite trixie --write-changelog
  dpkg-buildpackage -us -uc -A'
```

The package's own tests run during the build (`debian/rules`), and
`packaging/install-test.sh` is the smoke test CI runs in a clean container.
