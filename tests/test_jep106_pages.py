"""The JEP106 pages, and the table helpers that align numbers."""

from __future__ import annotations

import pytest

import spiflash
from jep106_pages import chips_by_code, code_slug, code_text, generate_all
from page_markup import EM_DASH, EN_DASH, TIMES, common_unit, count, more, size_text, volt
from spiflash_pages import NumberRole


def test_codes() -> None:
    assert code_slug((0, 0xEF)) == "bank1-ef"
    assert code_text((2, 0x9D)) == "7f 7f 9d"


def test_pages_only_for_codes_chips_use() -> None:
    db = spiflash.database()
    slugs = {id(f): f.key for f in db.flashes}
    used = chips_by_code(db)
    pages = generate_all(db, slugs)
    assert set(pages) == {"index.md", *(f"{code_slug(c)}.md" for c in used)}
    assert len(used) < len(db.manufacturers)
    winbond = pages["bank1-ef.md"]
    # JEP106 gives 0xef to NEXCOM; the sources call these chips Winbond.
    assert winbond.startswith("# NEXCOM")
    assert "[Winbond](../vendors/winbond.md)" in winbond
    assert "](../chips/ef4018.md)" in winbond
    # The index lists every name, with its count; only the used ones link.
    index = pages["index.md"]
    assert index.count("\n* - ") == len(db.manufacturers) + 1
    assert "[NEXCOM](bank1-ef.md)" in index
    assert "sf-hide-zero" in index


@pytest.mark.parametrize(
    ("text", "parts"),
    [
        ("16 MiB", {"a": "16", "unit": "MiB"}),
        ("2.7 V", {"a": "2", "af": "7", "unit": "V"}),
        (f"2.7{EN_DASH}3.6 V", {"a": "2", "af": "7", "b": "3", "bf": "6", "unit": "V"}),
        (f"512 {TIMES} 64 KiB", {"count": "512", "a": "64", "unit": "KiB"}),
        ("1,234", {"a": "1,234"}),
        (f"{EM_DASH} V", {"a": EM_DASH, "unit": "V"}),
    ],
)
def test_number_parts(text: str, parts: dict[str, str]) -> None:
    m = NumberRole.PARTS.match(text)
    assert m
    assert {k: v for k, v in m.groupdict().items() if v is not None} == parts


def test_number_helpers() -> None:
    assert size_text(1 << 24) == "{sfnum}`16 MiB`"
    assert size_text(None) == f"{{sfnum}}`{EM_DASH} B`"
    assert volt(2700) == "{sfnum}`2.7 V`"
    assert count(1234) == "{sfnum}`1,234`"
    assert more(["A", "B"], []) == "A, B"
    assert more(["A", "B"], ["C", "D"]) == "A, B, {sfmore}`C, D`"


def test_sizes_in_a_common_unit() -> None:
    assert common_unit([256, 4096]) == 1
    assert common_unit([16 << 20, 32 << 20, None]) == 1 << 20
    assert common_unit([64 << 10, 256 << 10]) == 1 << 10
    assert common_unit([None]) == 1 << 30
    assert size_text(4096, 1) == "{sfnum}`4,096 B`"
    assert size_text(4 << 20, 1 << 10) == "{sfnum}`4,096 KiB`"
