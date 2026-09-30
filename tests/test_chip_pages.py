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
