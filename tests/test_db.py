"""Queries against the shipped database, and the model's merging rules."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from importlib import resources

import pytest

import spiflash
from spiflash import db as db_module
from spiflash import opcodes, vendors
from spiflash.db import FORMAT, Database, SourceInfo
from spiflash.enums import Feature, FlashType, IdFamily, IdMethod, OperationKind, Source
from spiflash.model import (
    EraseBlock,
    Eraser,
    Flash,
    Record,
    Voltage,
    name_distance,
    name_matches,
    parse_id,
    part_names,
    squash_name,
    strip_continuation,
)
from spiflash.opcodes import OPERATIONS
from test_sfdp import MX25L25635E, W25Q512JV


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
        "dediprog",
        "flashrom",
        "flashprog",
        "openocd",
        "openfpgaloader",
        "qemu",
        "zephyr",
        "jep106",
    }
    for name, s in spiflash.sources().items():
        assert len(s.commit) == 40, name
        assert s.records > 0, name
        assert s.date.year >= 2026, name
    by_source = Counter(r.source for r in spiflash.records())
    for name, n in by_source.items():
        assert spiflash.sources()[name].records == n


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
        "dediprog",
        "openocd",
        "openfpgaloader",
        "zephyr",
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
    assert "S25FL128S1" in names
    assert "S25FL128S0" not in names  # ext 4d0080: a different variant
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


def test_dediprog_does_not_outvote() -> None:
    # Dediprog's DataFlash entries give no page size, and its sector sizes
    # (one vote, however many entries) lose to the reviewed tables'.
    (at45,) = spiflash.lookup("1f2800")
    assert at45.page_size == 1024
    (en,) = spiflash.lookup("1c2010")
    assert en.sector_size == 32 << 10
    (s25,) = spiflash.lookup("014014")
    assert s25.size == 1 << 20  # Dediprog's S25FL208K says 2 MiB
    # One flashrom entry each for 2 and 4 MiB, and two of Dediprog's three
    # for 4 MiB (the density byte, 0x16, is 4 MiB).
    (w77,) = spiflash.lookup("ef8a16")
    assert w77.size == 4 << 20


def test_nand() -> None:
    found = spiflash.lookup("efaa21", flash_type="nand")
    assert found
    assert found[0].type == "nand"
    assert "W25N01GV" in found[0].names
    # Dediprog's id, read after a dummy byte, is the same chip as Linux's.
    assert {"linux", "dediprog"} <= set(found[0].sources)
    assert spiflash.lookup("efaa21", flash_type="nor") == []


def test_find() -> None:
    assert "ef4018" in [f.id_hex for f in spiflash.find("w25q128jv")]
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
        # Zephyr's devicetree often names no maker, and the id's first byte
        # is not taken as naming one; every other source names one.
        assert f.manufacturer or set(f.sources) == {Source.ZEPHYR}, f.id_hex


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


def test_consensus_is_one_vote_per_source() -> None:
    # A source listing a part three times is one vote, for what it says most.
    db = Database(
        [
            rec(source="dediprog", sector_size=64 << 10),
            rec(source="dediprog", sector_size=64 << 10),
            rec(source="dediprog", sector_size=64 << 10),
            rec(source="dediprog", sector_size=32 << 10),
            rec(source="flashrom", sector_size=32 << 10),
            rec(source="flashprog", sector_size=32 << 10),
        ]
    )
    assert db.flashes[0].sector_size == 32 << 10
    # A source giving two values equally often votes for both; a tie between
    # values goes to the one the higher-priority sources give.
    db = Database(
        [
            rec(source="flashrom", page_size=1024),
            rec(source="flashrom", page_size=256),
            rec(source="flashprog", page_size=1024),
            rec(source="u-boot", page_size=256),
        ]
    )
    assert db.flashes[0].page_size == 1024


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


def test_unknown_source_is_rejected() -> None:
    with pytest.raises(ValueError, match="someone-else"):
        rec(source="someone-else")


def test_sources_are_in_priority_order() -> None:
    assert [s.priority for s in Source] == list(range(len(Source)))
    assert Source.FLASHROM.priority < Source.LINUX.priority < Source.OPENFPGALOADER.priority
    # Dediprog's own table: after the reviewed ones, before the smallest.
    assert Source.UBOOT.priority < Source.DEDIPROG.priority < Source.OPENOCD.priority
    assert Source.DEDIPROG.label == "Dediprog"
    # QEMU, then Zephyr last: board descriptions, not a curated table of parts.
    assert list(Source)[-2:] == [Source.QEMU, Source.ZEPHYR]
    assert Source.QEMU.label == "QEMU"
    assert Source.UBOOT.label == "U-Boot"
    assert Source("u-boot") is Source.UBOOT


def test_enums_are_their_strings() -> None:
    assert FlashType.NOR == "nor"
    assert Feature.FOUR_BYTE_ADDR == "4byte_addr"
    assert Feature.QPI.description == "supports QPI (4-4-4) mode"
    assert IdMethod.RDID_OPCODE_DUMMY.family is IdFamily.JEDEC
    assert IdMethod.RES1.family is IdFamily.RES1
    assert FlashType.NAND.label == "SPI NAND"


def test_typed_record_fields() -> None:
    (f,) = spiflash.lookup("ef4018")
    flashrom = next(r for r in f.records if r.source is Source.FLASHROM)
    assert flashrom.type is FlashType.NOR
    assert flashrom.id_method is IdMethod.RDID
    assert Feature.ERASE_4K in flashrom.features
    assert flashrom.voltage == Voltage(2700, 3600)
    assert flashrom.voltage.maximum_mv == 3600
    eraser = flashrom.erasers[0]
    assert eraser == Eraser(0x20, (EraseBlock(4096, 4096),))
    assert f.family is IdFamily.JEDEC


def test_lookup_takes_enums_or_strings() -> None:
    assert spiflash.lookup("efaa21", flash_type=FlashType.NAND) == spiflash.lookup(
        "efaa21", flash_type="nand"
    )
    assert spiflash.lookup("05", method=IdFamily.RES1) == spiflash.lookup("05", method="res1")
    with pytest.raises(ValueError, match="flash"):
        spiflash.lookup("ef4018", flash_type="flash")


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
        ("S25FL032(A/P)", ("S25FL032A", "S25FL032P")),
        ("EN25Q32(/A/B)", ("EN25Q32", "EN25Q32A", "EN25Q32B")),
        ("MX25L4005(A/C)/MX25L4006E", ("MX25L4005A", "MX25L4005C", "MX25L4006E")),
        ("MX25U3235(E/F)", ("MX25U3235E", "MX25U3235F")),
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
    assert {op.kind for op in opcodes.OPERATIONS.values()} == set(OperationKind)


def test_link_to_the_upstream_line() -> None:
    db = spiflash.database()
    (f,) = db.lookup("ef4018")
    by_source = {r.source: r for r in f.records}
    linux = by_source["linux"]
    commit = db.sources["linux"].commit
    assert db.link(linux) == (
        f"https://github.com/torvalds/linux/blob/{commit}/{linux.file}#L{linux.line}"
    )
    # flashprog lives on Gerrit; links go to its GitHub mirror.
    assert db.link(by_source["flashprog"]).startswith(
        "https://github.com/SourceArcade/flashprog/blob/"
    )
    assert Database([rec()]).link(rec()) is None  # no sources known
    elsewhere = SourceInfo(
        url="https://example.org/x",
        browse="https://example.org/x",
        branch="main",
        commit="c",
        date=datetime(2026, 9, 1, tzinfo=UTC),
        paths=("a.c",),
        license="MIT",
        records=1,
    )
    assert Database([rec()], sources={"linux": elsewhere}).link(rec()) is None


# --- SFDP dumps on records ---------------------------------------------------


def test_record_sfdp_dump() -> None:
    plain = rec()
    assert plain.sfdp is None
    assert plain.sfdp_tables() is None
    with_dump = rec(source="openocd", sfdp=W25Q512JV.hex())
    assert with_dump.sfdp == W25Q512JV
    tables = with_dump.sfdp_tables()
    assert tables is not None
    assert tables.size == 64 << 20
    other = rec(source="openfpgaloader", name="w25q128jv", sfdp=MX25L25635E.hex())
    same = rec(source="qemu", name="w25q128fv", sfdp=W25Q512JV.hex())
    db = Database([plain, with_dump, other, same])
    (f,) = db.flashes
    assert f.sfdp is not None
    assert f.sfdp.revision_name == "JESD216B"  # OpenOCD outranks openFPGALoader
    assert f.sfdp_source == "openocd"
    # Parts sharing an id can carry different dumps: each is kept, with its parts.
    assert [(d.source, d.parts, d.tables.revision_name) for d in f.sfdp_dumps] == [
        ("openocd", ("W25Q128", "W25Q128FV"), "JESD216B"),
        ("openfpgaloader", ("W25Q128JV",), "JESD216"),
    ]
    assert [r.source for r in f.sfdp_dumps[0].records] == ["openocd", "qemu"]
    doc = f.to_json()
    assert [(d["source"], d["parts"], d["size"]) for d in doc["sfdp"]] == [
        ("openocd", ["W25Q128", "W25Q128FV"], 64 << 20),
        ("openfpgaloader", ["W25Q128JV"], 32 << 20),
    ]
    json.dumps(doc)
    (none,) = Database([plain]).flashes
    assert none.sfdp is None
    assert none.sfdp_source is None
    assert none.sfdp_dumps == ()
    assert none.to_json()["sfdp"] == []


def test_every_shipped_sfdp_dump_decodes() -> None:
    for r in spiflash.records():
        if r.sfdp is None:
            continue
        tables = r.sfdp_tables()
        assert tables is not None, r.name
        assert tables.bfpt is not None, r.name
        assert tables.features() <= r.features, r.name
        assert "sfdp" in r.features


# --- searching part names ----------------------------------------------------


def test_find_regex() -> None:
    found = spiflash.find_regex("^W25Q(64|128)J[VW]$")
    assert {"ef4017", "ef4018", "ef6018", "ef8018"} <= {f.key for f in found}
    assert all(any(n.startswith(("W25Q64J", "W25Q128J")) for n in f.names) for f in found)
    # Unanchored and case-blind, like grep -i; the names are as written.
    assert spiflash.find_regex("25q128jv") == spiflash.find_regex("W25Q128JV")
    assert "ef4018" in [f.key for f in spiflash.find_regex(r"^W25Q128\.V$")]
    assert spiflash.find_regex("^NOT-A-PART$") == []


def test_find_regex_compiled_keeps_its_flags() -> None:
    db = spiflash.database()
    assert db.find_regex(re.compile("w25q128jv")) == []
    assert db.find_regex(re.compile("W25Q128JV"))


def test_find_regex_error() -> None:
    with pytest.raises(ValueError, match=r"not a regular expression: '\(' \(missing \)"):
        spiflash.find_regex("(")


@pytest.mark.parametrize(
    ("pattern", "chip"),
    [
        ("W25Q128*", "ef4018"),
        ("w25q128?v", "ef4018"),
        ("MX25?12835F", "c22018"),
        ("S25FL*S", "010220"),
        ("W25N01G[VW]", "efaa21"),  # SPI NAND
        ("SST25VF040B.REMS", "rems:bf8d"),  # a legacy id
        ("W25Q 128 JV", "ef4018"),
    ],
)
def test_find_glob(pattern: str, chip: str) -> None:
    assert chip in [f.key for f in spiflash.find_glob(pattern)]


def test_find_glob_covers_the_whole_name() -> None:
    assert spiflash.find_glob("25Q128JV") == []
    assert len(spiflash.find_glob("W25Q128")) < len(spiflash.find_glob("W25Q128*"))
    for f in spiflash.find_glob("W25Q[!0-9]*"):
        assert any(n[4].isalpha() for n in f.names if n.startswith("W25Q"))
    assert spiflash.find_glob("") == []
    assert spiflash.find_glob("[") == []  # a lone [ is itself


def test_find_nearest_order_code() -> None:
    near = spiflash.find_nearest("W25Q128JVSIQ", 4)
    assert [(m.flash.key, m.name, m.score) for m in near[:2]] == [
        ("ef4018", "W25Q128JV", 3),
        ("ef7018", "W25Q128JV", 3),
    ]
    assert near[0].reason == "the query adds SIQ"
    assert [m.score for m in near] == sorted(m.score for m in near)
    # flashrom's wildcards cover the whole of an order code.
    (m,) = spiflash.find_nearest("S25FL128SAGMFI001", 1)
    assert (m.name, m.flash.key, m.score, m.reason) == (
        "S25FL128S......0",
        "012018",
        1,
        "the query adds 1",
    )


def test_find_nearest_typos_and_separators() -> None:
    (m,) = spiflash.find_nearest("W25Q182JV", 1)  # two digits swapped: one edit
    assert (m.name, m.score, m.reason) == ("W25Q128JV", 4, "differs after W25Q1")
    (m,) = spiflash.find_nearest("mx25l6406e", 1)
    assert (m.name, m.flash.key, m.score, m.reason) == ("MX25L6406E", "c22017", 0, "the same part")
    (m,) = spiflash.find_nearest("W25Q16JV IM", 1)  # the database has W25Q16JV-IM
    assert (m.name, m.score) == ("W25Q16JV-IM", 0)
    (m,) = spiflash.find_nearest("W25N01GVZEIG", 1)  # SPI NAND
    assert (m.name, m.flash.key, m.flash.type) == ("W25N01GV", "efaa21", "nand")
    (m,) = spiflash.find_nearest("GD25Q6", 1)
    assert (m.name, m.reason) == ("GD25Q64", "the name adds 4")
    assert spiflash.find_nearest("25Q64", 1)[0].reason == "differs from the first character"


def test_find_nearest_one_per_chip() -> None:
    near = spiflash.find_nearest("W25Q128JV", 50)
    keys = [m.flash.key for m in near]
    assert len(keys) == len(set(keys)) == 50
    # Equal scores: the longer shared start, then a name without wildcards.
    assert near[0].name == "W25Q128JV"


def test_find_nearest_edge_cases() -> None:
    assert spiflash.find_nearest("") == []
    assert spiflash.find_nearest("--") == []
    assert len(spiflash.find_nearest("W25Q")) == 10
    with pytest.raises(ValueError, match="count must be at least 1, not 0"):
        spiflash.find_nearest("W25Q", 0)


@pytest.mark.parametrize(
    ("query", "name", "cost", "common"),
    [
        ("W25Q128JV", "W25Q128JV", 0, 9),
        ("w25q128-jv", "W25Q128JV", 0, 9),
        ("W25Q128JV", "W25Q128.V", 0, 9),
        ("W25Q128JVSIQ", "W25Q128JV", 3, 9),
        ("W25Q128JVSIQ", "W25Q128", 5, 7),
        ("W25Q128JVSIQ", "W25Q128JW", 7, 8),
        ("W25Q128JVSIQ", "W25Q128FV", 7, 7),
        ("W25Q128", "W25Q128JV", 2, 7),
        ("W25Q182JV", "W25Q128JV", 4, 5),
        ("X25Q128JV", "W25Q128JV", 4, 0),
        ("", "W25Q", 4, 0),
    ],
)
def test_name_distance(query: str, name: str, cost: int, common: int) -> None:
    assert name_distance(query, name) == (cost, common)


def test_squash_name() -> None:
    assert squash_name("w25q16jv-im") == "W25Q16JVIM"
    assert squash_name("N25Q128_3V / x") == "N25Q1283VX"
    assert squash_name("W25Q128.V") == "W25Q128V"
    assert squash_name("W25Q128.V", wildcards=True) == "W25Q128.V"


def test_name_match_to_json() -> None:
    (m,) = spiflash.find_nearest("W25Q128JVSIQ", 1)
    doc = m.to_json()
    assert (doc["name"], doc["score"], doc["reason"]) == ("W25Q128JV", 3, "the query adds SIQ")
    assert doc["chip"]["jedec_id"] == "ef4018"
