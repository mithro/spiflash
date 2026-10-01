"""Queries against the shipped database, and the model's merging rules."""

from __future__ import annotations

import itertools
import json
import pickle
import re
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from importlib import resources

import pytest

import spiflash
from spiflash import db as db_module
from spiflash import derive, opcodes, vendors
from spiflash.db import FORMAT, Database, SourceInfo
from spiflash.enums import (
    Bound,
    Feature,
    FlashType,
    FourByteMethod,
    IdFamily,
    IdMethod,
    OperationKind,
    Source,
    TimedEvent,
)
from spiflash.model import (
    Claim,
    EraseBlock,
    Eraser,
    FeatureSource,
    Flash,
    LegacyId,
    Otp,
    Record,
    SfdpDisagreement,
    Voltage,
    name_distance,
    name_matches,
    parse_id,
    parse_tested,
    part_names,
    same_part,
    same_supply_part,
    squash_name,
    strip_continuation,
)
from spiflash.opcodes import OPERATIONS, OpcodeUse
from spiflash.timings import TimingKey, Timings
from test_sfdp import MX25L25635E, W25Q512JV


def rec(**kw: object) -> Record:
    """A record; ``sector_size=n`` gives it a 0xd8 eraser of ``n``-byte
    blocks, which the sector size is derived from."""
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
        "erasers": None,
        "features": [],
        "flags": [],
        "voltage": None,
        "opcodes": [],
        "tested": None,
        "notes": [],
    }
    sector = kw.pop("sector_size", None)
    base.update(kw)
    if isinstance(sector, int):
        size = base["size"]
        assert isinstance(size, int)
        base["erasers"] = [{"opcode": 0xD8, "blocks": [[sector, size // sector]]}]
    return Record.from_json(base)


# --- the shipped data --------------------------------------------------------


def test_every_source_is_present() -> None:
    assert set(spiflash.sources()) == {
        "linux",
        "u-boot",
        "dediprog",
        "rockchip",
        "mediatek",
        "flashrom",
        "flashprog",
        "openocd",
        "openfpgaloader",
        "imsprog",
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
        "rockchip",
        "openocd",
        "openfpgaloader",
        "imsprog",
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


def test_names_rank_by_sources() -> None:
    # Dediprog's W25Q32 and W25Q32JV entries are one source each: W25Q32JV
    # (five sources, flashrom's among them) stays first.
    (w25q32,) = spiflash.lookup("ef4016")
    assert w25q32.name == "W25Q32JV"
    db = Database(
        [
            rec(source="dediprog", name="W25Q32"),
            rec(source="dediprog", name="W25Q32"),
            rec(source="dediprog", name="W25Q32"),
            rec(source="flashrom", name="W25Q32JV"),
            rec(source="linux", name="W25Q32JV"),
        ]
    )
    assert db.flashes[0].names == ("W25Q32JV", "W25Q32")
    # A part name before a pattern (flashrom's W25Q16.V) and before a
    # rebrand's name (Spansion's S25FL016K, which three sources give);
    # neither is dropped.
    (w25q16,) = spiflash.lookup("ef4015")
    assert w25q16.name == "W25Q16JV"
    assert {"W25Q16.V", "S25FL016K"} <= set(w25q16.names)
    # Linux's name for an entry it does not name is no part name.
    (s28hs,) = spiflash.lookup("345b19")
    assert s28hs.name == "S28HS256T"
    # Three sources (Dediprog, Linux, QEMU) call Intel's 25F160S33B8 160S33B.
    (s33,) = spiflash.lookup("898911")
    assert s33.names == ("160S33B", "25F160S33B8")


def test_names_spelled_apart_vote_together() -> None:
    # Dediprog lists CS11G0-, CS11G1- and CS11G2-T0A0AA at 6b 01; MediaTek's
    # CS11G1T0A0AA is the same name without the hyphen, so it votes for
    # the CS11G1, shown as Dediprog, the higher-priority source, writes it.
    (cs11g,) = spiflash.lookup("6b01", flash_type="nand")
    assert cs11g.name == "CS11G1-T0A0AA"
    assert cs11g.size == 256 << 20
    assert "CS11G1T0A0AA" in cs11g.names
    db = Database(
        [
            rec(source="dediprog", name="A-1"),
            rec(source="dediprog", name="B-1"),
            rec(source="mediatek", name="B_1"),
        ]
    )
    assert db.flashes[0].names == ("B-1", "B_1", "A-1")
    # Other spellings do not: a slash or a dot is not a separator.
    db = Database([rec(source="dediprog", name="A-1"), rec(source="mediatek", name="A/1")])
    assert db.flashes[0].name == "A-1"


def test_bank_is_the_most_sources_then_the_higher() -> None:
    # ATXP032 answers seven continuation codes, then 43 (OpenOCD); Dediprog
    # leaves them out. A tie goes to the codes, which no upstream adds.
    (atxp,) = spiflash.lookup("43a700")
    assert atxp.jedec_id == "7f7f7f7f7f7f7f43a700"
    db = Database(
        [
            rec(source="flashrom", id="1c7018"),
            rec(source="linux", id="1c7018"),
            rec(source="dediprog", id="7f1c7018"),
            rec(source="dediprog", id="7f1c7018"),
            rec(source="dediprog", id="7f1c7018"),
        ]
    )
    assert db.flashes[0].bank == 0  # two sources to one, however many records


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
    # The legacy chips first, then the JEDEC chips whose records say their
    # part answers the id too (Dediprog's M25P05A), marked.
    legacy = [f for f in res if f.family == "res1"]
    assert res[: len(legacy)] == legacy
    assert all(f.answers_legacy is None for f in legacy)
    also = res[len(legacy) :]
    assert all(f.family == "jedec" for f in also)
    assert all(f.answers_legacy == LegacyId(IdMethod.RES1, b"\x05") for f in also)
    assert "M25P05A" in [n for f in also for n in f.names]
    assert "M25P05" not in [n for f in spiflash.lookup("05") for n in f.names]
    # Legacy ids make no chip of their own, nor a JEDEC lookup's answer.
    assert all(f.answers_legacy is None for f in spiflash.lookup("202010"))


def test_legacy_ids_lookup() -> None:
    db = spiflash.database()
    (w25q40,) = db.lookup("ef4013", flash_type="nor")
    rems = LegacyId(IdMethod.REMS, bytes.fromhex("ef12"))
    assert w25q40.legacy_ids[rems] == ("dediprog",)
    found = db.lookup("ef12", method="rems")
    assert w25q40.key in [f.key for f in found]
    (marked,) = [f for f in found if f.key == w25q40.key]
    assert marked.answers_legacy == rems
    assert marked.to_json()["answers_legacy"] == "rems:ef12"
    assert {"method": "rems", "id": "ef12", "sources": ["dediprog"]} in marked.to_json()[
        "legacy_ids"
    ]


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
    # flashrom's two 1.8 V S25FS256S entries do not outvote the 3 V
    # S25FL256S both flashrom and flashprog give.
    (s25fl256,) = spiflash.lookup("010219")
    assert s25fl256.voltage == (2700, 3600)
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


def test_nand_ids_merge_across_read_id_methods() -> None:
    # Rockchip reads the id after an address byte, Linux after a dummy byte
    # (W25N01GV) or an address byte (GD5F1GQ4UAYIG): one chip each.
    for chip_id in ("efaa21", "c8f1"):
        (f,) = spiflash.lookup(chip_id, flash_type="nand")
        assert {"linux", "rockchip"} <= set(f.sources), chip_id


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
        # Zephyr's devicetree often names no maker and Rockchip's and
        # MediaTek's tables never do; where no other source's part confirms the id's byte
        # (infer_manufacturer), these chips have none.
        assert bool(f.manufacturer) is (f.key not in NO_MANUFACTURER), f.key
        if f.manufacturer_inferred:
            assert set(f.sources) <= {Source.ROCKCHIP, Source.MEDIATEK, Source.ZEPHYR}, f.key


# Rockchip's clones and makers no other source lists, and a Zephyr board's
# misread MX25L12833F.
NO_MANUFACTURER = {
    "3cd2",  # HSESYHDSW2G
    "52ba13",  # GSS01GSAK1, on Alliance Memory's id
    "52ba23",
    "52ca13",
    "52ca23",
    "666620",  # Zephyr's MX25L12833F
    "8c01",  # XCSP1AAPK, on ESMT's id
    "8ca1",
    "b00c",  # Unim's UM19A
    "b00d",
    "b014",
    "b015",
    "b024",
    "b025",
    "bcb3",  # BWJX08K-2Gb
    "bf21",  # JS28U1GQSCAHG-83
    "eac1",  # SGM7000I-S24W1GH
}


def test_manufacturer_inferred_from_id_and_part_name() -> None:
    (f,) = spiflash.lookup("c952", flash_type="nand")  # Rockchip's HYF2GQ4UAACAE
    assert (f.manufacturer, f.manufacturer_inferred) == ("HeYangTek", True)
    assert f.to_json()["manufacturer_inferred"] is True
    assert all(r.vendor is None for r in f.records)  # the records stay as read
    (w,) = spiflash.lookup("ef4018")
    assert not w.manufacturer_inferred
    # MediaTek's ESMT F50L1G41A shares GigaDevice's c8 21, and names no
    # maker: it does not make an F50 part GigaDevice's, so MediaTek's
    # F50L2G41LB and F50L512M41A are ESMT's, as Linux's F50 parts are.
    for chip_id in ("c80a", "c820"):
        (esmt,) = spiflash.lookup(chip_id, flash_type="nand")
        assert (esmt.manufacturer, esmt.manufacturer_inferred) == ("ESMT", True), chip_id
    db = Database(
        [
            rec_at("c821", "GD5F1GQ5REXXH", vendor="GigaDevice"),
            rec_at("c821", "F50L1G41A", source="mediatek"),
            rec_at("c8017f7f7f", "F50L1G41LB", vendor="ESMT"),
            rec_at("c80a", "F50L2G41LB", source="mediatek"),
        ]
    )
    (f50,) = db.lookup("c80a", flash_type="nand")
    assert f50.manufacturer == "ESMT"


def rec_at(chip_id: str, name: str, *, vendor: str | None = None, source: str = "linux") -> Record:
    return rec(id=chip_id, name=name, vendor=vendor, source=source, type="nand")


def test_parts_told_apart_by_ext_id() -> None:
    # GigaDevice's GD5F1GQ5RE and ESMT's F50L2G41KA both answer c8 41, and
    # only Rockchip gives what follows: c8 for one, 7f for the other.
    (esmt,) = spiflash.lookup("c8417f", flash_type="nand")
    assert esmt.names == ("F50L2G41KA",)
    assert (esmt.manufacturer, esmt.manufacturer_inferred, esmt.size) == ("ESMT", True, 256 << 20)
    assert spiflash.lookup("c8417f7f7f", flash_type="nand")[0].names == ("F50L2G41KA",)
    (gd,) = spiflash.lookup("c841c8", flash_type="nand")
    assert "F50L2G41KA" not in gd.names
    assert {"GD5F1GQ5REXXG", "GD5F1GQ5REYIG"} <= set(gd.names)  # XX: any two
    assert (gd.manufacturer, gd.size) == ("GigaDevice", 128 << 20)
    # The whole id holds both parts, but they are not a disagreement.
    (both,) = spiflash.lookup("c841", flash_type="nand")
    assert both.conflicts == {}
    assert len(both.variants) == 3


def test_with_ext_id_drops_only_another_ext_ids_part() -> None:
    f = Database(
        [
            rec(ext_id="4d00", name="S25FL129P0"),
            rec(ext_id="4d01", name="S25FL127S"),
            rec(ext_id=None, name="S25FL129P"),  # the same part, any variant
            rec(ext_id=None, name="S25FL127S"),  # 4d01's part: not 4d00's
            rec(ext_id=None, name="S25FL032P"),  # no extended id's: kept
        ]
    ).flashes[0]
    assert [r.name for r in f.with_ext_id(bytes.fromhex("4d00")).records] == [
        "S25FL129P0",
        "S25FL129P",
        "S25FL032P",
    ]
    # No extended id agrees: every record without one.
    assert [r.name for r in f.with_ext_id(bytes.fromhex("99")).records] == [
        "S25FL129P",
        "S25FL127S",
        "S25FL032P",
    ]


def test_shipped_ext_id_lookups_keep_the_other_sources() -> None:
    # Linux alone gives the S25FL032P an extended id: the others' S25FL032P,
    # and their supply voltage, stay.
    (f,) = spiflash.lookup("0102154d00")
    assert {"S25FL032P", "S25SL032P"} <= set(f.names)
    assert f.voltage == (2700, 3600)
    assert spiflash.lookup("20ba201000")[0].manufacturer == "Micron"
    assert "N25Q256A" in spiflash.lookup("20ba19104400")[0].names
    # A narrowed chip keeps only its own parts' datasheets.
    (esmt,) = spiflash.lookup("c8417f", flash_type="nand")
    assert esmt.datasheets == ()
    (gd,) = spiflash.lookup("c841c8", flash_type="nand")
    assert any("GD5F1GQ5RE" in d.url for d in gd.datasheets)


def test_narrowed_values_come_from_the_most_specific_records() -> None:
    # The S25FS128S is a 1.8 V part: the records at its own extended id say
    # so, over the no-ext S25FL128S parts' 3 V.
    assert spiflash.lookup("0120184d0081")[0].voltage == (1700, 2000)
    assert spiflash.lookup("0120184d0181")[0].voltage == (1700, 2000)
    # The S25FL256S0 has uniform 256 KiB sectors.
    assert spiflash.lookup("0102194d0080")[0].sector_size == 256 << 10
    # The S70FL01GS is 1 Gbit, whatever Dediprog's one-die S79FS01GS says.
    assert spiflash.lookup("0102214d0080")[0].size == 128 << 20
    # The records stay as broad as before, for display.
    assert "S79FS01GS" in spiflash.lookup("0102214d0080")[0].names


def test_parts_that_differ_by_ext_id() -> None:
    differ = {
        (f.key, attr)
        for f in spiflash.flashes()
        for attr in ("size", "page_size", "sector_size", "voltage")
        if f.by_ext_id(attr)
    }
    assert differ == {
        # The S25FL-S at 4d 00 80: uniform 256 KiB sectors and a 512-byte
        # page; at 4d 01 80: 64 KiB sectors and a 256-byte page.
        ("010219", "page_size"),
        ("010219", "sector_size"),  # 4d 00 xx: 256 KiB sectors; 4d 01 xx: 64 KiB
        ("010220", "page_size"),  # the S25FS512S's 256 B at 4d 00 81
        ("010220", "sector_size"),  # U-Boot's S25FL512S_64K at 4d 01
        ("010220", "voltage"),  # the 1.8 V S25FS512S
        ("012018", "page_size"),
        ("012018", "sector_size"),
        ("012018", "voltage"),  # the 1.8 V S25FS128S at 4d 00 81 and 4d 01 81
        ("c841", "size"),  # the GD5F1GQ5RE and the F50L2G41KA
    }


def test_by_ext_id() -> None:
    (f,) = spiflash.lookup("c841", flash_type="nand")
    assert f.by_ext_id("size") == {bytes.fromhex("7f"): 256 << 20, bytes.fromhex("c8"): 128 << 20}
    assert f.by_ext_id("page_size") == {}
    assert spiflash.lookup("ef4018")[0].by_ext_id("size") == {}


def test_same_part() -> None:
    for a, b in [
        ("ZB35Q01B", "ZB35Q01BYIG"),
        ("GD5F1GQ5REXXG", "GD5F1GQ5REYIG"),
        ("S25FL128S......1", "S25FL128SAGMFI011"),
        ("S25FL128S......0", "S25FL128S_UL"),  # dots over the length they share
    ]:
        assert same_part(a, b), (a, b)
        assert same_part(b, a), (b, a)
    for a, b in [
        ("F50L2G41KA", "GD5F1GQ5REXXG"),
        ("MX25L6433F", "MX25L6435F"),
        ("W25Q64.W", "W25Q64FV"),  # the 1.8 V pattern, not the 3 V part
        ("B.25D80A", "BY25Q80BS"),  # not every Boya part
    ]:
        assert not same_part(a, b), (a, b)
        assert not same_part(b, a), (b, a)


def test_infer_manufacturer_counts_each_chip_once() -> None:
    # Dediprog alone calls Fudan's FM25Q64 Fidelix's: the chip is Fudan's.
    mine = rec_at("a1a1", "FM25S01", source="rockchip")
    fm64 = [
        rec(id="a14017", name="FM25Q64", vendor="Fudan", source="flashrom"),
        rec(id="a14017", name="FM25Q64", vendor="Fudan", source="imsprog"),
        rec(id="a14017", name="FM25Q64", vendor="Fidelix", source="dediprog"),
    ]
    assert Database([mine, *fm64]).lookup("a1a1")[0].manufacturer == "Fudan"
    (fm,) = spiflash.lookup("a14019")
    assert (fm.manufacturer, fm.manufacturer_inferred) == ("Fudan", True)


def test_infer_manufacturer_needs_the_byte_and_one_maker() -> None:
    mine = rec_at("c952", "HYF2GQ4UAACAE", source="rockchip")
    same = rec_at("c921", "HYF1GQ4UDACAE", vendor="HeYangTek")
    db = Database([mine, same])
    assert db.lookup("c952")[0].manufacturer == "HeYangTek"
    # Another maker's byte is not enough without the part name (GSS on 0x52)...
    clone = rec_at("52ba13", "GSS01GSAK1", source="rockchip")
    other = rec_at("522f", "AS5F34G04SND", vendor="Alliance Memory")
    assert Database([clone, other]).lookup("52ba13")[0].manufacturer is None
    # ...and two makers for the name's start is no answer.
    two = rec_at("c9aa", "HYF9", vendor="Someone Else")
    assert Database([mine, same, two]).lookup("c952")[0].manufacturer is None


def test_shipped_folded_ids_are_found_by_either_id() -> None:
    for short, long in [("98e2", "98e240"), ("c226", "c22603"), ("cd71", "cd7171")]:
        (f,) = spiflash.lookup(short, flash_type="nand")
        assert f.id_hex == long
        assert spiflash.lookup(long, flash_type="nand") == [f]
        assert bytes.fromhex(short) in f.ids


def test_nand_ids_fold_into_the_longer_id_of_the_same_part() -> None:
    short = rec_at("c226", "MX35LF2GE4AD", source="rockchip")
    long = rec_at("c22603", "MX35LF2GE4AD", vendor="Macronix")
    db = Database([short, long])
    (f,) = db.flashes
    assert (f.id_hex, f.ids, f.keys) == (
        "c22603",
        (b"\xc2\x26\x03", b"\xc2\x26"),
        ("c22603", "c226"),
    )
    assert f.sources == ("linux", "rockchip")
    # Found by either id, and by the longer one first.
    assert db.lookup("c226") == [f]
    assert db.lookup("c22603") == [f]
    # Ids each starting the next all go into the longest (F50L1G41LB's
    # c8 01, c8 01 7f and c8 01 7f 7f 7f).
    line = Database([rec_at("c801", "P1"), rec_at("c8017f", "P1"), rec_at("c8017f7f7f", "P1")])
    assert [x.id_hex for x in line.flashes] == ["c8017f7f7f"]
    assert len(line.flashes[0].ids) == 3
    # A name with a suffix is the same part (ZB35Q01B, ZB35Q01BYIG).
    suffix = Database([rec_at("5ea1", "ZB35Q01BYIG"), rec_at("5ea1a1", "ZB35Q01B")])
    assert [x.id_hex for x in suffix.flashes] == ["5ea1a1"]
    # The same size, page and block: one part (a different size: see below).
    sized = Database([rec_at("c8a1", "GD5F1G"), rec(id="c8a101", name="GD5F1G", type="nand")])
    assert [x.id_hex for x in sized.flashes] == ["c8a101"]
    # A size the longer id's records do not give at all is no conflict.
    unsized = Database(
        [rec_at("c8a1", "GD5F1G"), rec(id="c8a101", name="GD5F1G", type="nand", size=None)]
    )
    assert [x.id_hex for x in unsized.flashes] == ["c8a101"]


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_nand_folds_end_where_they_end_in_any_order(order: tuple[int, ...]) -> None:
    # ab31 names X1, which ab3101 names too; ab3101 names Z9, as ab310155
    # does: all three end at ab310155, however the records come.
    records = [rec_at("ab31", "X1"), rec_at("ab3101", "X1/Z9"), rec_at("ab310155", "Z9")]
    db = Database([records[i] for i in order])
    assert [f.id_hex for f in db.flashes] == ["ab310155"]
    assert sorted(len(i) for i in db.flashes[0].ids) == [2, 3, 4]
    assert len(db.flashes[0].records) == 3


@pytest.mark.parametrize(
    "records",
    [
        # Two longer ids start with it, neither the start of the other.
        [rec_at("c226", "P1"), rec_at("c22603", "P1"), rec_at("c22604", "P1")],
        # The names differ: two parts.
        [rec_at("c226", "P1"), rec_at("c22603", "P2")],
        # One of its records names another part (Dediprog's M9 at c8 81).
        [rec_at("c881", "GD5F1GM7"), rec_at("c881", "GD5F1GM9"), rec_at("c88101", "GD5F1GM9")],
        # SPI NOR ids never fold.
        [rec(id="c220", name="P1"), rec(id="c22018", name="P1")],
        # Another size is another part ("GD5F1G" and a GD5F1GQ4UAYIG).
        [rec_at("c8a1", "GD5F1G"), rec(id="c8a101", name="GD5F1GQ4UAYIG", type="nand", size=1)],
    ],
)
def test_nand_ids_do_not_fold(records: list[Record]) -> None:
    assert len(Database(records).flashes) == len({r.id for r in records})


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


def test_consensus_counts_sources() -> None:
    # Sources are counted, not records: three of one source are one.
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
    # A source giving two values counts for each; a tie between values goes
    # to the one the higher-priority sources give, then to more records.
    db = Database(
        [
            rec(source="flashrom", page_size=1024),
            rec(source="flashrom", page_size=256),
            rec(source="flashprog", page_size=1024),
            rec(source="u-boot", page_size=256),
        ]
    )
    assert db.flashes[0].page_size == 1024
    db = Database(
        [
            rec(source="linux", page_size=512),
            rec(source="dediprog", page_size=256),
            rec(source="linux", page_size=256),
            rec(source="dediprog", page_size=512),
            rec(source="dediprog", page_size=512),
        ]
    )
    assert db.flashes[0].page_size == 512  # the same sources, but more records


def test_features_union_and_sources() -> None:
    db = Database(
        [
            rec(source="linux", features=["quad_read"]),
            rec(source="flashrom", features=["qpi", "quad_read"]),
        ]
    )
    (f,) = db.flashes
    assert f.features == {"quad_read", "qpi"}
    assert f.feature_sources("qpi") == (
        FeatureSource("flashrom", implied=False, because="claimed"),
    )
    assert [s.source for s in f.feature_sources("quad_read")] == ["flashrom", "linux"]


def test_feature_sources_mark_claimed_and_implied() -> None:
    quad = {"op": "READ_1_1_4", "via": "SPI_NOR_QUAD_READ"}
    db = Database(
        [
            rec(source="linux", opcodes=[quad]),
            rec(source="flashrom", features=["quad_read"], via={"feature:quad_read": "QUAD"}),
            # A claim in any of a source's records wins over an implication.
            rec(source="u-boot", opcodes=[{**quad, "via": "SPI_NOR_QUAD_READ"}]),
            rec(source="u-boot", features=["quad_read"], line=2),
        ]
    )
    (f,) = db.flashes
    assert f.feature_sources("quad_read") == (
        FeatureSource("flashrom", implied=False, because="claimed: QUAD"),
        FeatureSource("linux", implied=True, because="implied by READ_1_1_4 (SPI_NOR_QUAD_READ)"),
        FeatureSource("u-boot", implied=False, because="claimed"),
    )
    doc = f.to_json()["feature_sources"]["quad_read"]
    assert doc[1] == {
        "source": "linux",
        "implied": True,
        "because": "implied by READ_1_1_4 (SPI_NOR_QUAD_READ)",
    }


def test_a_driver_default_implies_nothing() -> None:
    db = Database(
        [
            rec(source="u-boot", opcodes=[{"op": "PP_1_1_4", "via": "default", "assumed": True}]),
            rec(source="linux", opcodes=[{"op": "PP_1_1_4", "via": "SPI_NOR_QUAD_PP"}]),
            rec(source="linux", line=2, opcodes=[{"op": "PP_1_1_4", "via": "x", "assumed": True}]),
        ]
    )
    (f,) = db.flashes
    assert [s.source for s in f.feature_sources("quad_pp")] == ["linux"]
    pp = f.opcodes["PP_1_1_4"]
    # Linux states it for the part, so is not also listed as assuming it.
    assert pp.because == (
        Claim("linux", "SPI_NOR_QUAD_PP"),
        Claim("u-boot", "default", assumed=True),
    )
    assert pp.assumed_by == ("u-boot",)
    assert next(o for o in f.to_json()["opcodes"] if o["op"] == "PP_1_1_4")["assumed_by"] == [
        "u-boot"
    ]


def test_a_shipped_chip_has_claimed_and_implied_sources() -> None:
    (f,) = spiflash.lookup("ef4018")
    lock = {s.source: s for s in f.feature_sources("lock")}
    assert lock["dediprog"] == FeatureSource(
        "dediprog", implied=False, because="claimed: ProtectBlockMask=0x9C"
    )
    quad = {s.source: s for s in f.feature_sources("quad_read")}
    assert quad["linux"] == FeatureSource(
        "linux", implied=True, because="implied by READ_1_1_4 (SPI_NOR_QUAD_READ)"
    )
    erase = {s.source: s for s in f.feature_sources("erase_64k")}
    assert erase["openocd"].because == "implied by eraser 0xd8 (256 x 65536)"
    # U-Boot's quad page program for every SPI_NOR_QUAD_READ part, and its
    # 4-byte form, are its driver's defaults: no chip has U-Boot as a
    # source for quad_pp.
    for chip in spiflash.flashes():
        if "quad_pp" in chip.features:
            assert "u-boot" not in [s.source for s in chip.feature_sources("quad_pp")]


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
    # MediaTek's production driver: after Rockchip's, before the smallest.
    assert Source.ROCKCHIP.priority < Source.MEDIATEK.priority < Source.OPENOCD.priority
    assert Source.MEDIATEK.label == "MediaTek"
    # IMSProg below the curated tables: its format and some values came from
    # closed programmer databases.
    assert Source.OPENFPGALOADER.priority < Source.IMSPROG.priority < Source.QEMU.priority
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
    # An operation only a driver default gives is listed, but not supported:
    # the stacked MT25QL02G erases a die at a time, and has no chip erase.
    (mt,) = spiflash.lookup("20ba22")
    assert mt.supports("DIE_ERASE")
    for op in ("CHIP_ERASE", "CHIP_ERASE_ALT"):
        assert not mt.supports(op)
        assert mt.opcodes[op].assumed_by == mt.opcodes[op].sources == ("qemu",)
    se = f.opcodes["SE"]
    assert se.opcode == 0xD8
    assert se.name == "SE"
    assert se.operation.kind == "erase"
    assert se.sources[0] == "flashrom"  # by source priority
    # OpenOCD's erase_cmd is an eraser, which implies the operation.
    assert Claim("openocd", "eraser: 256 x 65536", implied=True) in se.because
    assert "openocd" in se.implied_by
    doc = f.to_json()["opcodes"]
    assert {
        "op": "SE",
        "opcode": 0xD8,
        "kind": "erase",
        "description": "Erase a sector (usually 64 KiB)",
    }.items() <= next(o for o in doc if o["op"] == "SE").items()


def test_an_opcode_use_takes_its_opcode_from_the_table() -> None:
    for r in spiflash.records():
        for use in r.opcodes:
            assert use.opcode == OPERATIONS[use.op].opcode
            assert use.via, (r.source, r.name, use.op)
    assert OpcodeUse("SE", "erase_cmd").opcode == 0xD8


def test_opcodes_merge_across_records() -> None:
    db = Database(
        [
            rec(source="openocd", opcodes=[{"op": "SE", "via": "erase_cmd"}]),
            rec(
                source="linux",
                opcodes=[{"op": "SE", "via": "default"}, {"op": "RDID", "via": "id"}],
            ),
            rec(source="linux", opcodes=[{"op": "SE", "via": "default"}]),
        ]
    )
    (f,) = db.flashes
    assert list(f.opcodes) == ["RDID", "SE"]
    assert f.opcodes["SE"].because == (Claim("linux", "default"), Claim("openocd", "erase_cmd"))
    assert f.opcodes["SE"].sources == ("linux", "openocd")
    # Every record's rdid implies RDID; Linux states it too, which wins.
    rdid_because = (Claim("linux", "id"), Claim("openocd", "id read (rdid)", implied=True))
    assert f.opcodes["RDID"].because == rdid_because
    assert f.opcodes["RDID"].implied_by == ("openocd",)
    rdid = next(o for o in f.to_json()["opcodes"] if o["op"] == "RDID")
    assert (rdid["sources"], rdid["implied_by"]) == (["linux", "openocd"], ["openocd"])


def test_record_derives_id_and_erase_operations() -> None:
    erasers = [{"opcode": 0x20, "blocks": [[4096, 4096]]}, {"opcode": 0xC7, "blocks": [[1, 1]]}]
    r = rec(erasers=erasers, opcodes=[{"op": "READ_1_1_1", "via": "read"}])
    assert [(u.op, u.via, u.implied) for u in r.opcodes] == [
        ("RDID", "id read (rdid)", True),
        ("READ_1_1_1", "read", False),
        ("BE_4K", "eraser: 4096 x 4096", True),
        ("CHIP_ERASE", "eraser: 1 x 1", True),
    ]
    assert r.opcode_claims == (OpcodeUse("READ_1_1_1", "read"),)
    # Derived from the stored fields, so a replaced field re-derives them.
    r2 = replace(
        r, eraser_claims=(Eraser(0xD8, (EraseBlock(65536, 256),)),), id_method=IdMethod.REMS
    )
    assert [u.op for u in r2.opcodes] == ["REMS", "READ_1_1_1", "SE"]
    # A stated operation comes before the same one implied.
    r3 = rec(erasers=erasers[:1], opcodes=[{"op": "BE_4K", "via": "stated"}])
    assert [(u.op, u.implied) for u in r3.opcode_reasons()["BE_4K"]] == [
        ("BE_4K", False),
        ("BE_4K", True),
    ]
    # SPI NAND reads its id and erases blocks with its own commands.
    block = [{"opcode": 0xD8, "blocks": [[131072, 1024]]}]
    nand = rec(type="nand", id_method="rdid_opcode_dummy", erasers=block)
    assert [(u.op, u.via) for u in nand.opcodes] == [
        ("NAND_RDID_DUMMY", "id read (rdid_opcode_dummy)"),
        ("NAND_BLOCK_ERASE", "eraser: 1024 x 131072"),
    ]
    assert not nand.features


def test_record_stored_fields() -> None:
    r = rec(features=["qpi"], via={"feature:qpi": "QPIEnable"})
    assert r.stored("features") == r.feature_claims == {Feature.QPI}
    assert r.stored("size") == r.size
    assert r.via == {"feature:qpi": "QPIEnable"}
    with pytest.raises(TypeError):
        r.via["x"] = "y"  # type: ignore[index]
    # Hashed and compared on what it stores; pickled with its via.
    assert hash(r) == hash(rec(features=["qpi"], via={"feature:qpi": "other"}))
    assert r != rec(features=["qpi"], via={"feature:qpi": "other"})
    back = pickle.loads(pickle.dumps(r))
    assert (back, back.via, back.opcodes) == (r, r.via, r.opcodes)


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
    # IMSProg's table is a binary file: the link is to the file.
    assert db.link(by_source["imsprog"]) == (
        f"https://github.com/bigbigmdm/IMSProg/blob/{db.sources['imsprog'].commit}"
        "/IMSProg_programmer/database/IMSProg.Dat"
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
    assert plain.parsed_sfdp is None
    assert plain.sfdp_tables == {}
    with_dump = rec(source="openocd", sfdp=W25Q512JV.hex())
    assert with_dump.sfdp == W25Q512JV
    tables = with_dump.parsed_sfdp
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
    assert [(d.source, d.parts, d.sfdp.revision_name) for d in f.sfdp_dumps] == [
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
        if r.sfdp is None and not r.sfdp_tables:
            continue
        tables = r.parsed_sfdp
        assert tables is not None, r.name
        assert tables.partial is (r.sfdp is None), r.name
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


def test_supply_setting_against_the_ranges() -> None:
    # Dediprog's 1.8 V for a part no source gives a range is outside the
    # other part's 2.3 to 3.6 V at the id; its 3.3 V for the 3 V part is
    # not, though another part at the id is a 1.8 V one.
    ranged = rec(source="flashrom", name="P25Q32H", voltage=[2300, 3600])
    low = rec(source="dediprog", name="P25Q32L", supply_mv=1800)
    f = Flash(b"\x85\x60\x16", FlashType.NOR, (ranged, low))
    assert f.supply_outside() == {1800: (low,)}
    assert (f.voltage, f.supply_mv) == (Voltage(2300, 3600), 1800)
    fs = rec(source="flashrom", name="S25FS256S", voltage=[1700, 2000])
    fl = rec(source="flashrom", name="S25FL256S", voltage=[2700, 3600])
    set_fl = rec(source="dediprog", name="S25FL256S", supply_mv=3300)
    set_fs = rec(source="dediprog", name="S25FS256S", supply_mv=1800)
    assert Flash(b"\x01\x02\x19", FlashType.NOR, (fs, fl, set_fl, set_fs)).supply_outside() == {}
    # A record states a range or a setting, not both.
    with pytest.raises(ValueError, match="a supply voltage range and a supply setting"):
        rec(voltage=[2700, 3600], supply_mv=3300)


def test_otp_compared_by_component() -> None:
    sized = rec(source="flashrom", otp={"size": 768})
    regions = rec(source="linux", otp={"size": 768, "regions": 3})
    f = Flash(b"\xef\x60\x16", FlashType.NOR, (sized, regions))
    # A source giving no regions does not vote on them.
    assert f.otp == Otp(768, 3)
    assert f.conflicts == {}
    assert str(f.otp) == "768 B (3 x 256 B)"
    assert Otp(768).compatible(Otp(768, 3))
    assert not Otp(1024).compatible(Otp(768, 3))
    other = rec(source="flashprog", otp={"size": 1024})
    assert "otp.size" in Flash(b"\xef\x60\x16", FlashType.NOR, (sized, other)).conflicts
    with pytest.raises(ValueError, match="not 3 equal regions"):
        Otp(1000, 3)
    # The area implies otp.
    assert "otp" in sized.features


def test_four_byte_modes() -> None:
    stated = rec(source="flashrom", size=32 << 20, four_byte_modes=["wren_en4b", "wrear"])
    assert stated.four_byte_modes == {"wren_en4b", "wrear"}
    assert {"EN4B", "WREAR", "RDEAR"} <= {u.op for u in stated.opcodes}
    assert stated.to_json()["four_byte_modes"] == ["wrear", "wren_en4b"]
    small = rec(source="imsprog", size=1 << 20, four_byte_modes=["en4b"])
    assert str(small.address_bytes) == "3 or 4"
    assert "4byte_addr" in small.features
    always = rec(size=1 << 20, four_byte_modes=["always_4b"])
    assert str(always.address_bytes) == "4"
    f = Flash(b"\xef\x40\x19", FlashType.NOR, (stated, small))
    assert f.four_byte_modes == {"wren_en4b", "wrear", "en4b"}
    assert [s.source for s in f.four_byte_mode_sources("en4b")] == ["imsprog"]
    assert str(f.address_bytes) == "3 or 4"
    # opcodes_4b is the 4-byte operations', and a way out is no way in.
    with pytest.raises(ValueError, match="are not ways in"):
        rec(four_byte_modes=["opcodes_4b"])
    with pytest.raises(ValueError, match="are not ways in"):
        rec(four_byte_modes=["hw_reset"])


@pytest.mark.parametrize(
    ("tested", "status"),
    [
        (None, None),
        ("TEST_OK_PREW", ("ok", "ok", "ok", "ok", "nt")),
        ("TEST_UNTESTED", ("nt", "nt", "nt", "nt", "nt")),
        ("TEST_BAD_PR", ("bad", "bad", "nt", "nt", "nt")),
        # A field the entry leaves out is unknown (C would make it OK).
        ("{ .probe = NA, .read = OK }", ("na", "ok", None, None, None)),
        (
            "{.probe = OK, .read = OK, .erase = NA, .write = NA, .wp = NA}",
            ("ok", "ok", "na", "na", "na"),
        ),
        ("{.probe = OK, .block_protection = DEP}", ("ok", None, None, None, "dep")),
    ],
)
def test_test_status(tested: str | None, status: tuple[str | None, ...] | None) -> None:
    parsed = parse_tested(tested)
    if status is None:
        assert parsed is None
        return
    assert parsed is not None
    assert (parsed.probe, parsed.read, parsed.erase, parsed.write, parsed.wp) == status
    assert rec(tested=tested).test_status == parsed


def test_test_status_errors() -> None:
    with pytest.raises(ValueError, match="not a test status"):
        parse_tested("TEST_SOMETIMES")
    with pytest.raises(ValueError, match="not a test status"):
        parse_tested("{.probe = OK, .smell = OK}")


def test_new_fields_round_trip() -> None:
    r = rec(
        source="dediprog",
        supply_mv=1800,
        otp={"size": 768, "regions": 3},
        legacy_ids=[["res1", "17"], ["rems", "ef17"]],
        four_byte_modes=["en4b"],
    )
    assert r.legacy_ids == (
        LegacyId(IdMethod.RES1, b"\x17"),
        LegacyId(IdMethod.REMS, b"\xef\x17"),
    )
    assert r.legacy_ids[1].key == "rems:ef17"
    again = Record.from_json(r.to_json())
    assert again == r
    assert again.to_json() == r.to_json()
    assert pickle.loads(pickle.dumps(r)) == r


def test_a_supply_suffix_names_another_part() -> None:
    assert same_part("W25X10", "W25X10BL")
    assert not same_supply_part("W25X10", "W25X10BL")
    assert not same_supply_part("P25Q32", "P25Q32U")
    assert same_supply_part("S25FL256S", "S25FL256SXXXXXX1X")  # an order code
    assert same_supply_part("W25X10BV", "W25X10BV")
    # So Dediprog's 2.5 V W25X10BL is outside flashrom's W25X10, another part.
    ranged = rec(source="flashrom", name="W25X10", voltage=[2700, 3600])
    low = rec(source="dediprog", name="W25X10BL", supply_mv=2500)
    assert Flash(b"\xef\x30\x11", FlashType.NOR, (ranged, low)).supply_outside() == {2500: (low,)}


def test_ways_out_have_their_own_labels() -> None:
    assert FourByteMethod.EN4B.label == "EN4B (0xb7)"
    assert FourByteMethod.EN4B.exit_label == "EX4B (0xe9)"
    assert FourByteMethod.WREAR.exit_label == FourByteMethod.WREAR.label


def test_an_otp_area_is_the_otp_reason() -> None:
    r = rec(
        source="flashrom",
        otp={"size": 1024},
        opcodes=[{"op": "PSECR", "via": "OTP: 1024B total; write 0x42"}],
    )
    assert r.feature_reasons()[Feature.OTP] == "implied by its OTP area, 1 KiB"


# --- times (phase 7) -------------------------------------------------------------


def test_timing_consensus_per_bound() -> None:
    f = Flash(
        b"\xef\x40\x18",
        FlashType.NOR,
        (
            rec(source="dediprog", timings={"chip_erase": {"unspecified": 200 * 10**9}}),
            rec(source="zephyr", timings={"dpd_exit": {"maximum": 35_000}}),
            rec(source="flashrom", timings={"dpd_exit": {"maximum": 35_000}}),
            rec(source="qemu", timings={"dpd_exit": {"maximum": 3_000}}),
        ),
    )
    assert f.timing("chip_erase", "unspecified") == 200 * 10**9
    # An unspecified time is no maximum or typical.
    assert f.timing("chip_erase", "maximum") is None
    assert f.timing(TimedEvent.DPD_EXIT, Bound.MAXIMUM) == 35_000
    key = TimingKey(TimedEvent.DPD_EXIT)
    assert list(f.timings[key, Bound.MAXIMUM]) == [3_000, 35_000]
    assert f.value("timings.dpd_exit.maximum") == 35_000
    assert "timings.dpd_exit.maximum" in f.conflicts
    assert f.to_json()["timings"]["dpd_exit"]["maximum"]["value"] == 35_000


def test_one_sources_parts_sharing_an_id_do_not_conflict() -> None:
    def flash(*given: tuple[str, str, int]) -> Flash:
        recs = tuple(
            rec(source=s, name=n, line=i, timings={"chip_erase": {"unspecified": t * 10**9}})
            for i, (s, n, t) in enumerate(given)
        )
        return Flash(b"\xef\x40\x18", FlashType.NOR, recs)

    attr = "timings.chip_erase.unspecified"
    # Dediprog's W25Q128BV, FV and JV: three parts, each its own time.
    parts = flash(("dediprog", "W25Q128BV", 40), ("dediprog", "W25Q128FV", 200))
    assert attr not in parts.conflicts
    # One part twice (an ordering code's tail is the same part), or two
    # sources: a disagreement.
    assert attr in flash(("dediprog", "W25Q128JV", 50), ("dediprog", "W25Q128JV-IQ", 200)).conflicts
    assert attr in flash(("dediprog", "W25Q128BV", 40), ("flashrom", "W25Q128FV", 200)).conflicts
    (shipped,) = spiflash.lookup("ef4018")
    assert attr not in shipped.conflicts


def test_times_are_compared_at_sfdp_resolution() -> None:
    def flash(*ns: int) -> Flash:
        recs = tuple(
            rec(source=s, timings={"dpd_exit": {"maximum": n}})
            for s, n in zip(("zephyr", "flashrom"), ns, strict=True)
        )
        return Flash(b"\xef\x40\x18", FlashType.NOR, recs)

    # 35 µs is 40 µs (5 x 8 µs) in DW14's units: they agree; 48 µs does not.
    assert derive.on_sfdp_grid(TimedEvent.DPD_EXIT, Bound.MAXIMUM, 35_000) == 40_000
    assert "timings.dpd_exit.maximum" not in flash(35_000, 40_000).conflicts
    assert "timings.dpd_exit.maximum" in flash(35_000, 48_000).conflicts
    # A maximum through a multiplier is compared exactly.
    assert derive.on_sfdp_grid(TimedEvent.CHIP_ERASE, Bound.MAXIMUM, 10**9) is None


def test_timing_order() -> None:
    f = Flash(
        b"\xef\x40\x18",
        FlashType.NOR,
        (
            rec(source="zephyr", timings={"dpd_exit": {"maximum": 3_000}}),
            rec(source="qemu", sfdp=W25Q512JV.hex()),
        ),
    )
    assert f.timing_order() == []
    g = Flash(
        b"\xef\x40\x20",
        FlashType.NOR,
        (
            rec(source="dediprog", timings={"chip_erase": {"unspecified": 10**9}}),
            rec(source="qemu", sfdp=W25Q512JV.hex()),
        ),
    )
    # Dediprog's 1 s is below the table's 192 s typical, but not ordered.
    assert g.timing_order() == []
    h = Flash(
        b"\xef\x40\x18",
        FlashType.NOR,
        (
            rec(source="zephyr", timings={"page_program": {"maximum": 100_000}}),
            rec(source="qemu", sfdp=W25Q512JV.hex()),
        ),
    )
    key = TimingKey(TimedEvent.PAGE_PROGRAM)
    assert h.timing_order() == [(key, Bound.TYPICAL, 704_000, Bound.MAXIMUM, 100_000)]


def test_a_records_times_are_its_claims_over_its_tables() -> None:
    r = rec(
        source="qemu", size=None, sfdp=W25Q512JV.hex(), timings={"dpd_exit": {"maximum": 2_500}}
    )
    assert r.timings.get("dpd_exit", "maximum") == 2_500
    assert r.timings.get("chip_erase", "typical") == 192 * 10**9
    # DW10's multiplier, 14, for the chip erase's maximum (CHIP_ERASE_MULTIPLIER).
    assert r.timings.get("chip_erase", "maximum") == 14 * 192 * 10**9
    assert r.timings.get("block_erase", "maximum", 0x20) == 14 * 64 * 10**6
    assert r.timing_claims == Timings.from_json({"dpd_exit": {"maximum": 2_500}})
    # 2.5 µs is 2.56 µs on DW14's grid, not 3 µs: a disagreement.
    assert r.sfdp_disagreements() == (SfdpDisagreement("timings.dpd_exit.maximum", 2_500, 3_000),)
    assert r.given("timings.page_program.typical") == 704_000
    assert pickle.loads(pickle.dumps(r)).timings == r.timings


def test_a_bound_its_event_cannot_have_is_refused() -> None:
    with pytest.raises(ValueError, match="none this event has"):
        rec(timings={"dpd_exit": {"typical": 1000}})
    with pytest.raises(ValueError, match="whole nanoseconds"):
        rec(timings={"dpd_exit": {"maximum": 0}})
    with pytest.raises(ValueError, match="only, and always, for a block erase"):
        rec(timings={"chip_erase:0x20": {"maximum": 1000}})
    assert str(TimingKey.parse("block_erase:0x20")) == "block_erase:0x20"
