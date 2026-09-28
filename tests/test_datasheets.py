"""Datasheets: the shipped list, how a chip gets its own, and the import."""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING

import import_datasheets
import spiflash
from spiflash import Database, Datasheet
from spiflash.cli import describe
from test_db import rec

if TYPE_CHECKING:
    from pathlib import Path


def sheet(**kw: object) -> Datasheet:
    base: dict[str, object] = {
        "url": "https://example.com/a.pdf",
        "title": "A",
        "official": True,
        "parts": ["W25Q128JV"],
        "ids": ["ef4018"],
    }
    base.update(kw)
    return Datasheet.from_json(base)


def test_shipped_datasheets_all_belong_to_a_chip() -> None:
    db = spiflash.database()
    assert db.datasheets
    keys = {f.key for f in db.flashes}
    for d in db.datasheets:
        assert d.url.startswith(("http://", "https://")), d.url
        assert d.title
        assert set(d.ids) <= keys, d.url
        assert set(d.confirmed) <= set(d.ids), d.url
        assert d.sha256 is not None
        assert len(d.sha256) == 64
    # Each is on every chip it covers, and only there.
    attached = sum(len(f.datasheets) for f in db.flashes)
    assert attached == sum(len(d.ids) for d in db.datasheets)


def test_urls_are_unique() -> None:
    urls = [d.url for d in spiflash.database().datasheets]
    assert len(urls) == len(set(urls))


def test_from_json() -> None:
    d = sheet(date="2021-03-10", revision="H", confirmed=["ef4018"], also_at=["https://m/a.pdf"])
    assert d.date == datetime.date(2021, 3, 10)
    assert d.revision == "H"
    assert d.confirmed == ("ef4018",)
    assert d.also_at == ("https://m/a.pdf",)
    assert sheet().date is None
    assert sheet().confirmed == ()


def test_best_first() -> None:
    old = sheet(url="https://v/old.pdf", date="2010-01-01")
    new = sheet(url="https://v/new.pdf", date="2020-01-01")
    copy = sheet(url="https://c/new.pdf", date="2024-01-01", official=False)
    shown = sheet(url="https://c/id.pdf", official=False, confirmed=["ef4018"])
    db = Database([rec()], datasheets=[old, copy, new, shown])
    (f,) = db.flashes
    # The id in the document beats who publishes it, which beats age.
    assert [d.url for d in f.datasheets] == [shown.url, new.url, old.url, copy.url]


def test_datasheets_follow_the_key() -> None:
    legacy = rec(id="bf48", id_method="rems", name="sst25vf512")
    db = Database(
        [rec(), legacy],
        datasheets=[sheet(ids=["rems:bf48"]), sheet(url="https://x/other.pdf", ids=["c22018"])],
    )
    by = {f.key: f for f in db.flashes}
    assert set(by) == {"ef4018", "rems:bf48"}
    assert by["ef4018"].datasheets == ()
    assert len(by["rems:bf48"].datasheets) == 1


def test_ext_id_keeps_datasheets() -> None:
    db = Database([rec()], datasheets=[sheet()])
    (f,) = db.flashes
    assert f.with_ext_id(b"\x00").datasheets == f.datasheets


def test_json_and_cli() -> None:
    db = Database([rec()], datasheets=[sheet(confirmed=["ef4018"], date="2021-03-10")])
    (f,) = db.flashes
    (d,) = f.to_json()["datasheets"]
    assert d == {
        "url": "https://example.com/a.pdf",
        "title": "A",
        "revision": None,
        "date": "2021-03-10",
        "official": True,
        "id_confirmed": True,
    }
    assert "    datasheet: https://example.com/a.pdf" in describe(f).splitlines()


def test_import(tmp_path: Path) -> None:
    line = {
        "path": "winbond/W25Q128JV.pdf",
        "sha256": "0" * 64,
        "bytes": 1,
        "url": "https://www.winbond.com/W25Q128JV.pdf",
        "official": True,
        "also_at": [],
        "title": "W25Q128JV",
        "revision": "H",
        "document_date": "2021-03-10",
        "parts": ["W25Q128JV"],
        "ids": ["ef4018", "ef7018"],
        "id_confirmed": {"ef4018": "EFh 40h 18h", "ef7018": None},
        "retrieved": "2026-09-28",
        "found_via": "vendor-site",
    }
    assert import_datasheets.entry(line) == {
        "url": "https://www.winbond.com/W25Q128JV.pdf",
        "title": "W25Q128JV",
        "revision": "H",
        "date": "2021-03-10",
        "official": True,
        "also_at": [],
        "parts": ["W25Q128JV"],
        "ids": ["ef4018", "ef7018"],
        "confirmed": ["ef4018"],
        "sha256": "0" * 64,
    }
    # An id the database does not have is refused, and nothing is written.
    bad = tmp_path / "datasheets.jsonl"
    bad.write_text(json.dumps({**line, "ids": ["000000"]}) + "\n")
    assert import_datasheets.main([str(bad)]) == 1
