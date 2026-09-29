# Developing spiflash

Contributor notes. For what the package does, see the
[README](https://github.com/mithro/spiflash/blob/main/README.md); for how
versions reach PyPI and apt, see
[RELEASING.md](https://github.com/mithro/spiflash/blob/main/RELEASING.md).

## The documentation site

<https://spiflash.readthedocs.io/> is built from {repo}`docs/` by Sphinx (Furo
theme, MyST Markdown). The vendor, chip, opcode and data issues pages are not in git:
{repo}`docs/_ext/spiflash_pages.py` writes them from the installed package's
data at the start of every build, so the site always matches the release.
The API reference ({repo}`docs/api.md`) is generated the same way, by
autosummary, from every module of `spiflash` and of
{repo}`tools/spiflash_extract/`: a new module or function appears in it
without anything to update (its generated pages are not in git either).

```sh
uv sync --group docs
uv run sphinx-build -W -b html docs docs/_build/html   # what CI runs
uv run docs/check_links.py                             # and this
```

{repo}`.readthedocs.yaml` builds it on Read the Docs, and
{repo}`.github/workflows/docs.yml` builds it (warnings are errors) on every push
and pull request, then runs {repo}`docs/check_links.py`: anything a reader
would expect to click (a URL, a file name, a path, a GitHub `owner/repo`) must
be a link. {repo}`docs/check_link_targets.py` goes further and asks every
external link whether it resolves; it needs the network, so run it by hand
before a release rather than in CI.

Links in the site's pages use three roles, which also work in docstrings
(`:repo:` and so on):

- ``{repo}`tools/sources.toml` `` links to a file or directory in this
  repository;
- ``{upstream}`linux:drivers/mtd/spi-nor/core.c` `` links to a file in an
  upstream, at the commit the data was read from;
- ``{github}`mithro/apt-repo-action` `` links to a GitHub repository.

The top-level README and RELEASING are read on GitHub and PyPI as well, so they
use plain Markdown links with absolute URLs instead.

## Setup and gates

```sh
uv sync --group dev --group docs
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest
```

Those four are what the `test` job of {repo}`.github/workflows/deb.yml` runs,
on Python 3.11 to 3.14. A green run is what "mergeable" means, and on `main`
what publishes. `uv run ruff format` applies the formatting.

The code carries no `# noqa`, no `type: ignore` and no per-file lint ignores:
when a rule fires, the code changes. The rules chosen, and the four families
left out (and why), are listed in {repo}`pyproject.toml`.

## Layout

| Path | What it is |
|---|---|
| {repo}`src/spiflash/` | the package: {repo}`model.py <src/spiflash/model.py>` (`Record`, `Flash`), {repo}`db.py <src/spiflash/db.py>` (loading, lookup, find), {repo}`opcodes.py <src/spiflash/opcodes.py>` (the named operations), {repo}`sfdp.py <src/spiflash/sfdp.py>` (the JESD216 decoder), {repo}`vendors.py <src/spiflash/vendors.py>` (one name per vendor), {repo}`units.py <src/spiflash/units.py>`, {repo}`cli.py <src/spiflash/cli.py>` |
| {repo}`src/spiflash/data/` | generated: {repo}`records.json <src/spiflash/data/records.json>`, {repo}`manufacturers.json <src/spiflash/data/manufacturers.json>`, {repo}`sources.json <src/spiflash/data/sources.json>`; imported: {repo}`datasheets.json <src/spiflash/data/datasheets.json>` |
| {repo}`tools/sources.toml` | the upstream commits the data is built from |
| {repo}`tools/update_db.py` | fetch, extract, write the data |
| {repo}`tools/import_datasheets.py` | write the datasheet links from a datasheet manifest |
| {repo}`tools/spiflash_extract/cparse.py` | just enough C: find tables, split initialisers, evaluate integer expressions (`SZ_16M`, `BIT(3)`, `64 * 1024`) |
| {repo}`tools/spiflash_extract/record.py` | the common record every extractor writes |
| {repo}`tools/spiflash_extract/ops.py` | each record's opcodes, values checked against {repo}`src/spiflash/opcodes.py` |
| {repo}`linux.py <tools/spiflash_extract/linux.py>`, {repo}`uboot.py <tools/spiflash_extract/uboot.py>`, {repo}`flashrom.py <tools/spiflash_extract/flashrom.py>`, {repo}`openocd.py <tools/spiflash_extract/openocd.py>`, {repo}`openfpgaloader.py <tools/spiflash_extract/openfpgaloader.py>`, {repo}`qemu.py <tools/spiflash_extract/qemu.py>` | one extractor per upstream format |
| {repo}`tools/spiflash_extract/fetch.py` | sparse, blobless, depth-1 fetch of one commit |
| {repo}`tests/fixtures/` | cut-down copies of each upstream file, verbatim |
| {repo}`docs/` | the Read the Docs site, and {repo}`docs/_ext/spiflash_pages.py`, which writes its generated pages: {repo}`opcode_pages.py <docs/_ext/opcode_pages.py>` the operation pages, {repo}`opcode_timing.py <docs/_ext/opcode_timing.py>` their WaveDrom diagrams, {repo}`issue_checks.py <docs/_ext/issue_checks.py>` and {repo}`issue_pages.py <docs/_ext/issue_pages.py>` the data issues checks and pages, {repo}`page_markup.py <docs/_ext/page_markup.py>` the Markdown helpers they share |

The extractors are not in the wheel; the sdist carries them so the data can be
rebuilt from it.

## Updating the data

```sh
uv run tools/update_db.py            # rebuild from the pins in tools/sources.toml
uv run tools/update_db.py --latest   # move the pins to each upstream's HEAD, then rebuild
uv run tools/update_db.py --check    # fail if the committed data differs
```

Commit {repo}`tools/sources.toml` and {repo}`src/spiflash/data/` together. The
data files hold one record per line, so `git diff` reads chip by chip.

{repo}`.github/workflows/upstream.yml` runs `--check` on every change to the
data or the tools, and `--latest --check` weekly. It is deliberately not part
of {repo}`deb.yml <.github/workflows/deb.yml>`: a release should not wait on six
servers.

## Updating the datasheet links

{repo}`src/spiflash/data/datasheets.json` is not built from the upstreams.
It comes from a separate, local-only collection of the datasheets themselves
(their publishers' copyrighted documents, so it is not published), whose
`datasheets.jsonl` lists each document: where it was downloaded, its title,
revision and date, the part numbers and chip ids it covers, and which of those
ids its own text gives. Only the links and what they cover are shipped:

```sh
uv run tools/import_datasheets.py ../spiflash-pdfs/datasheets.jsonl
```

It refuses a manifest naming a chip id the database does not have, so after
{repo}`tools/update_db.py` drops or renames an id, fix the manifest first.

## When an upstream changes its format

An extractor that meets something it does not understand raises, naming the
file and line, rather than guessing: an unknown probe in flashrom, an `.id`
that is not `SNOR_ID(...)` in Linux, an identifier the expression evaluator has
no value for. Fix the extractor, and add the new shape to the miniature tree in
{repo}`tests/fixtures/`, which {repo}`tests/test_extract.py` reads.

A new vendor spelling fails `test_vendor_spellings_all_canonical`: add it to
{repo}`src/spiflash/vendors.py`.

## Adding a source

1. A `[name]` table in {repo}`tools/sources.toml`: `url`, `branch`, `commit`,
   the `paths` to fetch, and the `license` of those files.
2. A module in {repo}`tools/spiflash_extract/` with
   `extract(root) -> list[Record]`, built with
   {py:func}`spiflash_extract.record.make`, and an entry in
   {py:data}`update_db.EXTRACTORS`.
3. Tests on a miniature tree in {repo}`tests/fixtures/` and
   {repo}`tests/test_extract.py`.
4. Its place in the `Source` enum ({repo}`src/spiflash/enums.py`; the members
   are in priority order, and each has a label) and a badge colour in
   {repo}`docs/_static/spiflash.css`; a row in the README and
   {repo}`docs/SOURCES.md`; the source sets in {repo}`tests/test_db.py`; and
   any new vendor spelling in {repo}`src/spiflash/vendors.py`.

## Building the Debian package locally

The CI build is {github}`mithro/apt-repo-action`'s `build-deb`:
`dpkg-buildpackage -A` in a `debian:<suite>` container, after its
[`scripts/deb-version.py`](https://github.com/mithro/apt-repo-action/blob/main/scripts/deb-version.py)
writes the Debian changelog. By hand, from a clean clone:

```sh
docker run --rm -v "$PWD:/work/src" -v /path/to/apt-repo-action:/a:ro -w /work/src debian:trixie bash -ec '
  apt-get update && apt-get install -y --no-install-recommends build-essential debhelper dpkg-dev fakeroot git python3
  apt-get build-dep -y ./ && git config --global --add safe.directory "*"
  python3 /a/scripts/deb-version.py --suite trixie --write-changelog
  dpkg-buildpackage -us -uc -A'
```

The package's own tests run during the build ({repo}`debian/rules`), and
{repo}`packaging/install-test.sh` is the smoke test CI runs in a clean
container.
