"""The source pages: a page per upstream, and their index."""

from __future__ import annotations

from pathlib import Path

import spiflash
from issue_checks import find
from issue_pages import generate_all as issue_pages
from page_markup import chip_slug, source_badge_html
from source_pages import PARSERS, SUMMARY, commit_link, generate_all
from spiflash.enums import Source

NOTES = Path(__file__).resolve().parent.parent / "docs" / "_source_notes"


def _pages() -> dict[str, str]:
    db = spiflash.database()
    slugs = {id(f): chip_slug(f) for f in db.flashes}
    return generate_all(db, slugs, find(db))


def test_a_page_per_source() -> None:
    pages = _pages()
    assert set(pages) == {"index.md", *(f"{s}.md" for s in Source)}
    index = pages["index.md"]
    for s in Source:
        assert f"](../sources/{s}.md)" in index
        assert f"](../issues/source-{s}.md)" in index
        assert f"\n{s}\n" in index  # in the toctree
    # Every source has its prose, its one-line summary and its parser.
    assert set(SUMMARY) == set(PARSERS) == set(Source)
    assert {p.stem for p in NOTES.glob("*.md")} == {str(s) for s in Source}


def test_linux_page() -> None:
    db = spiflash.database()
    page = _pages()["linux.md"]
    assert page.startswith("# Linux\n")
    assert "{sfsrcme}`linux`" in page
    assert "```{include} ../_source_notes/linux.md" in page
    # Where it came from: the files read at the pinned commit, and the parser.
    assert "{upstream}`linux:drivers/mtd/spi-nor/`" in page
    assert commit_link(db.sources["linux"]) in page
    assert "{repo}`tools/spiflash_extract/linux.py`" in page
    # What it gives, and what it does not.
    linux = [r for r in db.records if r.source == "linux"]
    assert f"{len(linux)} entries" in page
    assert "SPI NAND" in page
    assert "It gives no supply voltage, quad enable requirement, test status or SFDP dump." in page
    # Its claims and what its operations and erasers imply are counted apart.
    assert "Capabilities it claims" in page
    assert "Capabilities it implies" in page
    assert "[`READ_1_1_1_FAST`](../opcodes/READ_1_1_1_FAST.md)" in page  # a driver default
    assert "](../issues/source-linux.md)" in page
    # Its entries: linked to their chips and their lines upstream; no
    # column for a value it never gives.
    assert "](../chips/ef4018.md)" in page
    assert "https://github.com/torvalds/linux/blob/" in page
    assert "sf-filterable sf-entries" in page
    assert "  - V min" not in page


def test_mediatek_id_methods_are_labels() -> None:
    # MediaTek's driver tries every entry with the dummy byte and without,
    # so its id methods are the table's labels, and its page says so.
    pages = _pages()
    assert "the driver tries every entry with the dummy byte and without" in pages["mediatek.md"]
    assert "the driver tries every entry" not in pages["linux.md"]


def test_imsprog_page_names_entries_not_lines() -> None:
    db = spiflash.database()
    page = _pages()["imsprog.md"]
    chips = [f for f in db.flashes if Source.IMSPROG in f.sources]
    nor = sum(1 for f in chips if f.type == "nor")
    # The header's counts are chip ids, and say so.
    assert f"{nor:,} SPI NOR ids" in page
    assert f"{len(chips) - nor:,} SPI NAND ids" in page
    # A binary file has no lines: an entry is named by its number, and
    # links to the file.
    (r,) = [r for r in db.records if r.source == "imsprog" and r.name == "W25Q128BV"]
    assert r.url == f"IMSProg_programmer/database/IMSProg.Dat entry {r.line}"
    assert f'"{r.url}")' in page
    assert "IMSProg.Dat#L" not in page


def test_flashrom_page_has_voltages_and_test_status() -> None:
    page = _pages()["flashrom.md"]
    assert "  - V min" in page
    assert "  - Tested" in page
    assert "`TEST_OK_PREW`" in page
    # flashprog is read by the same parser.
    assert "{repo}`tools/spiflash_extract/flashrom.py`" in _pages()["flashprog.md"]


def test_labels_link_to_the_source_pages() -> None:
    html = source_badge_html("u-boot", "../sources/u-boot.html")
    assert html.startswith('<a class="sf-src-link" href="../sources/u-boot.html"')
    assert '<span class="sf-src sf-src-u-boot">' in html
    assert "<a " not in source_badge_html("u-boot")


def test_issue_pages_link_the_source_pages() -> None:
    db = spiflash.database()
    slugs = {id(f): chip_slug(f) for f in db.flashes}
    pages = issue_pages(db, slugs)
    # The summary's column headers are the labels alone (linked to the source
    # pages by the role); the totals link to each source's issues.
    assert "  - {sfsrc}`linux`\n" in pages["index.md"]
    assert "](source-linux.md)" in pages["index.md"]
    assert "](../sources/zephyr.md)" in pages["source-zephyr.md"]
