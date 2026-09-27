"""Queries against the shipped database, and the model's merging rules."""

from __future__ import annotations

import json
from importlib import resources

import pytest

import spiflash
from spiflash import db as db_module
from spiflash import opcodes, vendors
from spiflash.db import FORMAT, Database
from spiflash.model import Flash, Record, name_matches, parse_id, part_names, strip_continuation
from spiflash.opcodes import OPERATIONS


def rec(**kw: object) -> Record:
    base: dict[str, object] = {
        "source": "linux",
        "file": "f.c",
        "line": 1,
        "type": "nor",
        "vendor": "winbond",
        "name": "w25q128",
        "id": "ef4018",
        "ext_id": None,
        "id_method": "rdid",
        "size": 16 << 20,
        "page_size": 256,
        "sector_size": 65536,
        "erasers": None,
        "features": [],
        "flags": [],
        "voltage": None,
        "opcodes": [],
        "tested": None,
        "notes": [],
    }
    base.update(kw)
    return Record.from_json(base)


# --- the shipped data --------------------------------------------------------


def test_every_source_is_present() -> None:
    assert set(spiflash.sources()) == {
        "linux",
        "u-boot",
        "flashrom",
        "flashprog",
        "openocd",
        "openfpgaloader",
        "jep106",
    }
    for name, s in spiflash.sources().items():
        assert len(s["commit"]) == 40, name
        assert s["records"] > 0, name
    by_source: dict[str, int] = {}
    for r in spiflash.records():
        by_source[r.source] = by_source.get(r.source, 0) + 1
    for name, n in by_source.items():
        assert spiflash.sources()[name]["records"] == n


def test_data_files_are_one_record_per_line() -> None:
    text = resources.files("spiflash").joinpath("data", "records.json").read_text()
    lines = text.splitlines()
    assert lines[0] == f'{{"format": {FORMAT}, "records": ['
    assert lines[-1] == "]}"
    assert len(lines) - 2 == len(spiflash.records())
    json.loads(text)


def test_w25q128() -> None:
    (f,) = spiflash.lookup("ef4018")
    assert f.manufacturer == "Winbond"
    assert {"W25Q128FV", "W25Q128JV"} <= set(f.names)
    assert f.size == 16 << 20
    assert f.page_size == 256
    assert f.sector_size == 64 << 10
    assert f.voltage == (2700, 3600)
    assert {"erase_4k", "quad_read"} <= f.features
    assert set(f.sources) == {
        "flashrom",
        "flashprog",
        "linux",
        "u-boot",
        "openocd",
        "openfpgaloader",
    }
    assert f.type == "nor"
    assert f.family == "jedec"
    assert f.jedec_id == "ef4018"
    assert f.manufacturer_id == 0xEF
    assert spiflash.jep106(f.manufacturer_id) == "NEXCOM"  # Winbond's JEP106 entry


@pytest.mark.parametrize(
    "query",
    ["ef4018", "0xEF4018", "ef 40 18", "EF:40:18", b"\xef\x40\x18", 0xEF4018, [0xEF, 0x40, 0x18]],
)
def test_lookup_accepts_every_spelling(query: str | bytes | int | list[int]) -> None:
    assert [f.id_hex for f in spiflash.lookup(query)] == ["ef4018"]


def test_continuation_codes_are_optional() -> None:
    # Eon is in JEP106 bank 2 (7f 1c) but its chips mostly answer 1c alone.
    plain = spiflash.lookup("1c7018")
    prefixed = spiflash.lookup("7f1c7018")
    assert [f.id for f in plain] == [f.id for f in prefixed]
    assert plain
    assert plain[0].manufacturer == "Eon"
    assert spiflash.jep106(0x1C, bank=1) == "Eon Silicon Devices"


def test_extended_id_narrows_variants() -> None:
    (everything,) = spiflash.lookup("012018")
    (s1,) = spiflash.lookup("01 20 18 4d 01 80")
    names = {r.name for r in s1.records}
    assert "s25fl128s1" in names
    assert "s25fl128s0" not in names  # ext 4d0080: a different variant
    assert len(s1.records) < len(everything.records)
    # Records with no extended id cover every variant, so they stay.
    assert any(r.ext_id is None for r in s1.records)


def test_only_the_longest_id_of_each_type() -> None:
    found = spiflash.lookup("c22018")
    assert [(f.type, f.id_hex) for f in found] == [("nor", "c22018"), ("nand", "c220")]
    assert "MX25L12835F" in found[0].names
    # Linux's one-byte Macronix catch-all is still there for an unknown part.
    assert [f.id_hex for f in spiflash.lookup("c2ffff", flash_type="nor")] == ["c2"]


def test_openocd_names_get_their_prefix_back() -> None:
    (f,) = spiflash.lookup("c22018", flash_type="nor")
    assert "25L12845" not in f.names


def test_unknown_id() -> None:
    assert spiflash.lookup("123456") == []


def test_legacy_ids_are_separate() -> None:
    res = spiflash.lookup("05", method="res1")
    assert res
    assert all(f.family == "res1" for f in res)
    assert "M25P05" not in [n for f in spiflash.lookup("05") for n in f.names]


def test_nand() -> None:
    found = spiflash.lookup("efaa21", flash_type="nand")
    assert found
    assert found[0].type == "nand"
    assert "W25N01GV" in found[0].names
    assert spiflash.lookup("efaa21", flash_type="nor") == []


def test_find() -> None:
    found = spiflash.find("w25q128jv")
    assert [f.id_hex for f in found][:2] == ["ef4018", "ef7018"] or "ef4018" in [
        f.id_hex for f in found
    ]
    # A family prefix finds the parts; a full order code finds the family.
    assert "ef4018" in [f.id_hex for f in spiflash.find("W25Q128")]
    assert "ef4018" in [f.id_hex for f in spiflash.find("W25Q128JVSIQ")]
    assert spiflash.find("") == []
    assert spiflash.find("NOT-A-PART-XYZ") == []


def test_by_manufacturer_uses_any_spelling() -> None:
    db = spiflash.database()
    a = db.by_manufacturer("win")
    b = db.by_manufacturer("Winbond")
    assert a == b
    assert len(a) > 20


def test_every_jedec_flash_has_a_name_and_manufacturer() -> None:
    for f in spiflash.flashes():
        assert f.names, f.id_hex
        assert f.manufacturer, f.id_hex


def test_vendor_spellings_all_canonical() -> None:
    canon = set(vendors.known().values())
    spellings = {r.vendor for r in spiflash.records() if r.vendor}
    unmapped = {v for v in spellings if v.lower() not in vendors.known()}
    # Every spelling an upstream uses is mapped, so a new one fails here.
    assert not unmapped, unmapped
    assert vendors.canonical(None) is None
    assert vendors.canonical("Someone New") == "Someone New"
    assert "Winbond" in canon


def test_to_json_round_trips() -> None:
    (f,) = spiflash.lookup("012018")
    doc = json.loads(json.dumps(f.to_json()))
    assert doc["id"] == "012018"
    assert doc["manufacturer"] == "Spansion"
    assert doc["conflicts"]  # the S25FL12x family disagrees on page and sector size
    assert {r["source"] for r in doc["records"]} >= {"linux", "flashrom"}


# --- the merging rules, on hand-made records ---------------------------------


def test_consensus_prefers_majority_then_priority() -> None:
    db = Database(
        [
            rec(source="openocd", size=1),
            rec(source="linux", size=2),
            rec(source="u-boot", size=2),
            rec(source="flashrom", size=None, page_size=512),
            rec(source="linux", size=None, page_size=256),
        ]
    )
    (f,) = db.flashes
    assert f.size == 2
    # page_size: 256 from three sources (openocd, linux, u-boot), 512 from one.
    assert f.page_size == 256
    assert f.values("page_size") == {256: ("linux", "u-boot", "openocd"), 512: ("flashrom",)}
    assert set(f.conflicts) == {"size", "page_size"}
    tie = Database([rec(source="openocd", size=1), rec(source="flashrom", size=2)])
    assert tie.flashes[0].size == 2  # flashrom outranks openocd


def test_features_union_and_sources() -> None:
    db = Database(
        [
            rec(source="linux", features=["quad_read"]),
            rec(source="flashrom", features=["qpi", "quad_read"]),
        ]
    )
    (f,) = db.flashes
    assert f.features == {"quad_read", "qpi"}
    assert f.feature_sources("qpi") == ("flashrom",)
    assert f.feature_sources("quad_read") == ("flashrom", "linux")


def test_records_without_an_id_are_kept_but_not_grouped() -> None:
    db = Database([rec(id=None, id_method=None), rec()])
    assert len(db.records) == 2
    assert len(db.flashes) == 1


def test_unknown_source_sorts_last() -> None:
    db = Database([rec(source="someone-else", size=1), rec(source="openocd", size=2)])
    assert db.flashes[0].size == 2
    assert db.flashes[0].sources == ("openocd", "someone-else")


def test_record_properties() -> None:
    r = rec(vendor="mac", id="c22018", file="x.c", line=7)
    assert r.manufacturer == "Macronix"
    assert r.id_hex == "c22018"
    assert r.url == "x.c:7"
    assert r.is_jedec
    assert not rec(id_method="rems").is_jedec
    assert rec(id=None).id_hex is None


def test_flash_with_ext_id_keeps_all_when_none_match() -> None:
    f = Flash(b"\x01\x20\x18", "nor", (rec(ext_id="4d00"),))
    assert f.with_ext_id(b"\x99").records == f.records


# --- helpers -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "parts"),
    [
        ("w25q128fv/jv", ("W25Q128FV", "W25Q128JV")),
        ("w25q32fv/w25q32jv", ("W25Q32FV", "W25Q32JV")),
        ("S25FL064P / EPCS64", ("S25FL064P", "EPCS64")),
        ("S25FL128S_UL Uniform 128 kB Sectors", ("S25FL128S_UL",)),
        ("KB9012 (EDI)", ("KB9012",)),
        ("W25Q128.V", ("W25Q128.V",)),
        ("mx25l3205/mx25l3206e", ("MX25L3205", "MX25L3206E")),
        ("a//b", ("A", "B")),
    ],
)
def test_part_names(name: str, parts: tuple[str, ...]) -> None:
    assert part_names(name) == parts


def test_name_matches() -> None:
    assert name_matches("W25Q128.V", "w25q128jv")
    assert name_matches("S25FL128S......0", "S25FL128SAGMFI00")
    assert not name_matches("S25FL128S......0", "S25FL128SAGMFI001")
    assert name_matches("S25FL128S......0", "S25FL128SAGMFI001", prefix=True)
    assert not name_matches("W25Q128.V", "W25Q128JVS")
    assert name_matches("W25Q128.V", "W25Q128JVSIQ", prefix=True)


def test_find_order_code_through_wildcards() -> None:
    assert "012018" in [f.id_hex for f in spiflash.find("S25FL128SAGMFI001")]


def test_parse_id_errors() -> None:
    for bad in ("", "abc", "zz", "0x"):
        with pytest.raises(ValueError, match="not a hex id"):
            parse_id(bad)
    with pytest.raises(ValueError, match="negative"):
        parse_id(-1)
    assert parse_id(0) == b"\x00"
    assert parse_id(bytearray(b"\x01")) == b"\x01"


def test_strip_continuation() -> None:
    assert strip_continuation(b"\x7f\x7f\x9d\x60") == (2, b"\x9d\x60")
    assert strip_continuation(b"\x7f") == (0, b"\x7f")


def test_bad_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_module.json, "loads", lambda _text: {"format": 99})
    with pytest.raises(ValueError, match="unsupported format"):
        db_module.Database.load()


# --- opcodes -----------------------------------------------------------------


def test_opcodes_of_a_shipped_chip() -> None:
    (f,) = spiflash.lookup("ef4018")
    names = list(f.opcodes)
    assert names[0] == "RDID"  # id first, then read, program, erase, ...
    for op in ("READ_1_1_1", "READ_1_1_4", "PP_1_1_1", "BE_4K", "SE", "CHIP_ERASE"):
        assert f.supports(op), op
    assert not f.supports("READ_1_1_8")
    se = f.opcodes["SE"]
    assert se.opcode == 0xD8
    assert se.name == "SE"
    assert se.operation.kind == "erase"
    assert se.sources[0] == "flashrom"  # by source priority
    assert ("openocd", "erase_cmd") in se.because
    doc = f.to_json()["opcodes"]
    assert {
        "op": "SE",
        "opcode": 0xD8,
        "kind": "erase",
        "description": "Erase a sector (usually 64 KiB)",
    }.items() <= next(o for o in doc if o["op"] == "SE").items()


def test_every_record_opcode_matches_the_table() -> None:
    for r in spiflash.records():
        for use in r.opcodes:
            assert OPERATIONS[use.op].opcode == use.opcode, (r.source, r.name, use)
            assert use.via, (r.source, r.name, use.op)


def test_opcodes_merge_across_records() -> None:
    db = Database(
        [
            rec(source="openocd", opcodes=[{"op": "SE", "opcode": 0xD8, "via": "erase_cmd"}]),
            rec(
                source="linux",
                opcodes=[
                    {"op": "SE", "opcode": 0xD8, "via": "default"},
                    {"op": "RDID", "opcode": 0x9F, "via": "id"},
                ],
            ),
            rec(source="linux", opcodes=[{"op": "SE", "opcode": 0xD8, "via": "default"}]),
        ]
    )
    (f,) = db.flashes
    assert list(f.opcodes) == ["RDID", "SE"]
    assert f.opcodes["SE"].because == (("linux", "default"), ("openocd", "erase_cmd"))
    assert f.opcodes["SE"].sources == ("linux", "openocd")


def test_operations_table() -> None:
    assert opcodes.get("READ_1_1_4").opcode == 0x6B
    with pytest.raises(KeyError):
        opcodes.get("NOPE")
    order = sorted(["SE", "READ_1_1_1", "RDID", "EN4B", "PP_1_1_1", "WRSR"], key=opcodes.sort_key)
    assert order == ["RDID", "READ_1_1_1", "PP_1_1_1", "SE", "WRSR", "EN4B"]
    assert {op.kind for op in opcodes.OPERATIONS.values()} == set(opcodes.KINDS)


def test_link_to_the_upstream_line() -> None:
    db = spiflash.database()
    (f,) = db.lookup("ef4018")
    by_source = {r.source: r for r in f.records}
    linux = by_source["linux"]
    commit = db.sources["linux"]["commit"]
    assert db.link(linux) == (
        f"https://github.com/torvalds/linux/blob/{commit}/{linux.file}#L{linux.line}"
    )
    # flashprog lives on Gerrit; links go to its GitHub mirror.
    assert db.link(by_source["flashprog"]).startswith(
        "https://github.com/SourceArcade/flashprog/blob/"
    )
    assert Database([rec(source="nowhere")]).link(rec(source="nowhere")) is None
    other = Database([rec()], sources={"linux": {"url": "https://example.org/x", "commit": "c"}})
    assert other.link(rec()) is None
