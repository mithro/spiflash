"""What a chip page says of a folded id, an inferred maker and parts that an
extended id tells apart."""

from __future__ import annotations

from pathlib import Path

import spiflash
from page_markup import chip_slug
from spiflash_pages import chip_page


def page(chip_id: str) -> str:
    db = spiflash.database()
    (f,) = db.lookup(chip_id, flash_type="nand")
    f = next(g for g in db.flashes if g.key == f.key and g.type == f.type)
    slugs = {id(g): chip_slug(g) for g in db.flashes}
    return chip_page(db, f, "vendor", slugs, [])


def test_a_folded_chip_says_which_id_each_source_gives() -> None:
    text = page("c22603")
    assert "Also matched on its first bytes" in text
    assert "  - Id\n" in text
    assert "{sfid}`c2 26`" in text  # Rockchip's row
    assert "  - Id\n" not in page("efaa21")  # not folded: no column


def test_an_inferred_maker_is_marked_in_the_header() -> None:
    text = page("c952")
    header = next(line for line in text.splitlines() if line.startswith("{bdg-link"))
    assert header.startswith("{bdg-link-primary}`HeYangTek (inferred) <")
    assert "(inferred from the id and part name)" in text


def test_parts_an_extended_id_tells_apart_have_their_own_maker() -> None:
    text = page("c841")
    ext = text[text.index("## Extended ids") :]
    assert "ESMT (inferred)" in ext
    assert "GigaDevice" in ext


def test_the_own_source_ring_does_not_follow_the_text() -> None:
    # Rockchip's label has dark text: its ring, around the page's own
    # source, stays the one every label has.
    css = (Path(__file__).resolve().parent.parent / "docs/_static/spiflash.css").read_text()
    (mine,) = [line for line in css.splitlines() if line.startswith(".sf-src-mine {")]
    assert "0 0 0 4px var(--sf-ring)" in mine
    assert "currentColor" not in mine
    assert "--sf-ring: #fff;" in css


def test_a_card_says_when_parts_differ() -> None:
    text = page("c841")
    card = text[text.index("{grid-item-card} Capacity") : text.index("{grid-item-card} Page")]
    assert "[Differs by part](#extended-ids)" in card
    assert "Differs by part" not in page("c952")


def test_the_supply_card_says_when_parts_differ() -> None:
    db = spiflash.database()
    f = next(g for g in db.flashes if g.key == "010220")
    text = chip_page(db, f, "vendor", {id(g): chip_slug(g) for g in db.flashes}, [])
    card = text[text.index("{grid-item-card} Supply") :]
    assert card.split(":::")[0].count("[Differs by part](#extended-ids)") == 1


def test_a_card_note_is_small_and_the_value_big() -> None:
    css = (Path(__file__).resolve().parent.parent / "docs/_static/spiflash.css").read_text()
    assert ".sf-card .sd-card-body p:first-of-type {" in css
    assert ".sf-card .sd-card-body p + p {" in css
    assert ".sf-card .sd-card-body p:last-child" not in css


def nor_page(key: str) -> str:
    db = spiflash.database()
    f = next(g for g in db.flashes if g.key == key and g.type == "nor")
    return chip_page(db, f, "vendor", {id(g): chip_slug(g) for g in db.flashes}, [])


def test_tables_a_source_copies_come_after_the_whole_dumps() -> None:
    # The MX25R6435F: Zephyr's boards copy its BFPT alone, in two versions.
    text = nor_page("c22817")
    sfdp = text[text.index("## SFDP") :]
    assert "copied without the SFDP header (sfdp-bfp)" in sfdp
    assert "`spiflash sfdp-diff c22817 c22817#2`" in sfdp
    # The W25Q512JV: QEMU's whole dump.
    sfdp = nor_page("ef4020")
    assert "Dump of" in sfdp[sfdp.index("## SFDP") :]
    # The sizes are markup of their own, not escaped text.
    assert "  - {sfnum}`64 MiB`, {sfnum}`256 B` pages, 3 or 4-byte addresses" in sfdp


def test_a_value_from_a_records_own_tables_is_marked() -> None:
    text = nor_page("ef4020")
    sources = text[text.index("## What each source says") :]
    qemu = next(line for line in sources.split("* - ") if line.startswith("{sfsrc}`qemu`"))
    assert qemu.count("*(SFDP)*") == 3  # size, page and sector
    layouts = text[text.index("## Erase layouts") : text.index("## What each source says")]
    assert "*(SFDP)*" in layouts
    # QEMU's ER_4K states the 4 KiB eraser its dump gives: its provenance.
    assert "[`BE_4K`](../opcodes/BE_4K.md) *(SFDP)*, from `ER_4K`" in layouts
