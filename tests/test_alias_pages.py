"""The part-name pages: chips/<PART>.html for every part name."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import spiflash
from alias_pages import aliases, choice_page, generate_all, redirect_page, write
from check_links import bare_urls
from page_markup import chip_slug, source_badge_html
from spiflash import Database
from test_db import rec

if TYPE_CHECKING:
    from pathlib import Path


def _taken(db: Database) -> set[str]:
    return {"index"} | {chip_slug(f) for f in db.flashes}


def test_shipped_names_redirect_to_their_chip() -> None:
    db = spiflash.database()
    pages = generate_all(aliases(db.flashes, _taken(db)))
    for name in ("S25FL016A", "S25SL016A", "S25FL016"):
        page = pages[f"{name}.html"]
        assert 'content="0; url=010214.html"' in page
        assert '<link rel="canonical" href="010214.html">' in page
        assert 'location.replace("010214.html" + location.hash)' in page
        assert '<a href="010214.html">' in page
        assert '<meta name="robots" content="noindex">' in page


def test_shipped_shared_name_gets_a_choice() -> None:
    db = spiflash.database()
    found = aliases(db.flashes, _taken(db))
    assert sorted(chip_slug(f) for f in found.names["MT25QL01G"]) == ["20ba21", "21ba20"]
    page = generate_all(found)["MT25QL01G.html"]
    assert "http-equiv" not in page
    assert '<a href="20ba21.html">' in page
    assert '<a href="21ba20.html">' in page


def test_shipped_pages_need_no_more_links() -> None:
    # docs/check_links.py scans these pages too.
    db = spiflash.database()
    for text in generate_all(aliases(db.flashes, _taken(db))).values():
        assert bare_urls(text) == []


def test_one_chip_one_redirect() -> None:
    db = Database([rec(), rec(source="flashrom", name="W25Q128FV")])
    found = aliases(db.flashes, _taken(db))
    assert set(found.names) == {"W25Q128", "W25Q128FV"}
    assert generate_all(found)["W25Q128FV.html"] == redirect_page("W25Q128FV", "ef4018.html")


def test_shared_name_lists_each_chip_and_who_says_so() -> None:
    db = Database(
        [
            rec(),
            rec(source="flashrom", name="W25Q128JV", id="ef4018"),
            rec(source="u-boot", name="w25q128jv", id="ef7018"),
            rec(source="flashrom", name="W25Q128JV", id="ef7018", line=2),
        ]
    )
    found = aliases(db.flashes, _taken(db))
    chips = found.names["W25Q128JV"]
    assert [chip_slug(f) for f in chips] == ["ef4018", "ef7018"]
    page = generate_all(found)["W25Q128JV.html"]
    assert page == choice_page("W25Q128JV", chips)
    assert "<code>ef 40 18</code>" in page
    assert "<code>ef 70 18</code>" in page
    assert "Winbond" in page
    rows = [line for line in page.splitlines() if line.startswith("<tr><td>")]
    # The sources in their order of trust, as the site's labels.
    flashrom, uboot = source_badge_html("flashrom"), source_badge_html("u-boot")
    assert "U-Boot" in uboot
    assert [row.rsplit("<td>", 1)[1] for row in rows] == [
        f"{flashrom}</td></tr>",
        f"{flashrom} {uboot}</td></tr>",
    ]


def test_a_legacy_id_counts_as_another_chip() -> None:
    db = Database([rec(name="sst25vf512"), rec(name="sst25vf512", id="bf48", id_method="rems")])
    (chips,) = aliases(db.flashes, _taken(db)).names.values()
    page = choice_page("SST25VF512", chips)
    assert '<a href="rems-bf48.html">' in page
    assert "bf 48 (REMS)" in page


def test_names_that_cannot_be_pages_are_skipped() -> None:
    db = Database(
        [
            rec(source="flashrom", name="W25Q128.V"),
            rec(name="w25q+x", id="ef4019"),
            rec(name="ef4018", id="ef4017"),
            rec(name="index", id="ef4016"),
        ]
    )
    found = aliases(db.flashes, _taken(db))
    assert found.skipped == {
        "W25Q128.V": "wildcard",
        "W25Q+X": "unsafe",
        "EF4018": "taken",
        "INDEX": "taken",
    }
    assert found.names == {}


def test_write_keeps_real_pages_and_drops_stale_aliases(tmp_path: Path) -> None:
    real = tmp_path / "ef4018.html"
    real.write_text("<html>the chip</html>")
    write(tmp_path, {"OLD.html": redirect_page("OLD", "ef4018.html")})
    write(tmp_path, {"NEW.html": redirect_page("NEW", "ef4018.html")})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["NEW.html", "ef4018.html"]
    with pytest.raises(FileExistsError):
        write(tmp_path, {"ef4018.html": redirect_page("EF4018", "ef4017.html")})
    assert real.read_text() == "<html>the chip</html>"


def test_long_source_names_take_two_lines() -> None:
    html = source_badge_html("openfpgaloader")
    assert "sf-src-split" in html
    assert "<span>openFPGA</span><span>Loader</span>" in html
    assert "sf-src-split" not in source_badge_html("linux")
