"""Each fact is stored once: checks over the shipped records.json, read raw.

A value a record's other stored fields imply is derived at load
(:mod:`spiflash.derive`), so the data must not hold it too. Each phase of
the data model adds its rules here."""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from spiflash import derive
from spiflash.derive import ERASE_BY_OPCODE, ID_OPERATION
from spiflash.enums import Feature, IdMethod
from spiflash.model import Record
from spiflash_extract import record


def _records() -> list[dict[str, Any]]:
    text = resources.files("spiflash").joinpath("data", "records.json").read_text()
    records: list[dict[str, Any]] = json.loads(text)["records"]
    return records


RECORDS = _records()


def _nor() -> list[dict[str, Any]]:
    return [r for r in RECORDS if r["type"] == "nor"]


def _where(r: dict[str, Any]) -> str:
    return f"{r['source']} {r['file']}:{r['line']} {r['name']}"


def test_no_operation_stores_its_opcode() -> None:
    assert not [_where(r) for r in RECORDS if any("opcode" in o for o in r["opcodes"])]


def test_no_erase_operation_an_eraser_gives() -> None:
    def stored(r: dict[str, Any]) -> set[str]:
        given = {ERASE_BY_OPCODE[e["opcode"]] for e in r["erasers"] or () if e["opcode"]}
        return given & {o["op"] for o in r["opcodes"]}

    assert not [(_where(r), stored(r)) for r in _nor() if stored(r)]


def test_no_operation_the_record_derives() -> None:
    # As make() decides it: a stored use is a derived one when its operation
    # is, and it gives no dummy clocks of its own or the same ones.
    def stored(r: dict[str, Any]) -> set[str]:
        derived = derive.opcodes(Record.from_json(r))
        return {
            o["op"]
            for o in r["opcodes"]
            for u in derived
            if o["op"] == u.op and o.get("dummy_clocks") in (None, u.dummy_clocks)
        }

    assert not [(_where(r), stored(r)) for r in RECORDS if stored(r)]


def test_the_id_read_is_derived_unless_the_entry_names_its_command() -> None:
    def stored(r: dict[str, Any]) -> bool:
        op = ID_OPERATION.get(IdMethod(r["id_method"])) if r["id_method"] else None
        return "id_method" not in r["via"] and op in {o["op"] for o in r["opcodes"]}

    assert not [_where(r) for r in _nor() if stored(r)]


def _vias(r: dict[str, Any]) -> list[str]:
    return [*r["via"].values(), *(o["via"] for o in r["opcodes"])]


def test_flags_are_residue() -> None:
    def held(r: dict[str, Any]) -> set[str]:
        return {f for f in r["flags"] if any(record.holds(v, f) for v in _vias(r))}

    assert not [(_where(r), held(r)) for r in RECORDS if held(r)]


def test_via_keys_follow_the_scheme() -> None:
    for r in RECORDS:
        record.check_via(r)
        assert list(r["via"]) == sorted(r["via"]), _where(r)
        assert all(r["via"].values()), _where(r)


def test_each_token_is_stored_once() -> None:
    def twice(r: dict[str, Any]) -> list[str]:
        found = [t for v in r["via"].values() for t in record.tokens(v)]
        return [t for t in set(found) if found.count(t) > 1]

    def held_by_op(r: dict[str, Any]) -> list[str]:
        claims = [v for k, v in r["via"].items() if k.startswith("feature:")]
        ops = [o["via"] for o in r["opcodes"]]
        return [t for v in claims for t in record.tokens(v) if any(record.holds(o, t) for o in ops)]

    def noted(r: dict[str, Any]) -> set[str]:
        return set(r["notes"]) & {t for v in r["via"].values() for t in record.tokens(v)}

    assert not [(_where(r), twice(r)) for r in RECORDS if twice(r)]
    assert not [(_where(r), held_by_op(r)) for r in RECORDS if held_by_op(r)]
    assert not [(_where(r), noted(r)) for r in RECORDS if noted(r)]


@pytest.mark.parametrize(
    ("source", "pattern"),
    [
        ("mediatek", r"\d+ B OOB per page; .*"),  # the flags hold the geometry
        ("flashrom", r"\s*[Ss]upports SFDP\.?\s*"),  # RDSFDP's via holds it
        ("flashprog", r"\s*[Ss]upports SFDP\.?\s*"),
    ],
)
def test_no_note_a_field_holds(source: str, pattern: str) -> None:
    rx = re.compile(pattern)
    found = [n for r in RECORDS if r["source"] == source for n in r["notes"] if rx.fullmatch(n)]
    assert not found


def test_the_data_is_the_stored_fields() -> None:
    """Every record holds its stored fields, all of them, and nothing else."""
    for d in RECORDS:
        assert Record.from_json(d).to_json() == d, _where(d)
        assert list(Record.from_json(d).to_json()) == list(record.KEYS)


# --- capabilities and the sector size are derived ----------------------------


def test_no_claim_the_record_implies() -> None:
    def twice(r: dict[str, Any]) -> set[str]:
        return set(r["features"]) & derive.features(Record.from_json(r))

    assert not [(_where(r), twice(r)) for r in RECORDS if twice(r)]


def test_no_stored_sector_size() -> None:
    assert not [_where(r) for r in RECORDS if "sector_size" in r]


_EVERY_PART = "m25p80 decodes it for every part"

#: Every driver default the data holds, as (source, operation, via): each a
#: default its upstream's driver sends to every part (or every part of a
#: class), whatever the entry says, or the 4-byte form of one. An operation
#: is not marked assumed except here, so a per-part one cannot be silently.
DEFAULTS = {
    ("linux", "READ_1_1_1", "default (spi_nor_init_default_params)"),
    ("linux", "READ_1_1_1_FAST", "default (spi_nor_init_default_params), m25p,fast-read"),
    ("linux", "PP_1_1_1", "default (spi_nor_init_default_params)"),
    ("linux", "CHIP_ERASE", "default (spi_nor_erase)"),
    ("linux", "READ_1_1_1_4B", "SPI_NOR_4B_OPCODES"),
    ("linux", "READ_1_1_1_FAST_4B", "SPI_NOR_4B_OPCODES"),
    ("linux", "PP_1_1_1_4B", "SPI_NOR_4B_OPCODES"),
    ("linux", "SE_4B", "SPI_NOR_4B_OPCODES"),  # of the default 64 KiB sector
    ("u-boot", "READ_1_1_1", "default (spi_nor_init_params)"),
    ("u-boot", "READ_1_1_1_FAST", "default unless SPI_NOR_NO_FR"),
    ("u-boot", "PP_1_1_1", "default (spi_nor_init_params)"),
    ("u-boot", "CHIP_ERASE", "default unless NO_CHIP_ERASE"),
    ("u-boot", "PP_1_1_4", "SPI_NOR_QUAD_READ: default (spi_nor_init_params)"),
    ("u-boot", "READ_1_1_1_4B", "SPI_NOR_4B_OPCODES"),
    ("u-boot", "READ_1_1_1_FAST_4B", "SPI_NOR_4B_OPCODES"),
    ("u-boot", "PP_1_1_1_4B", "SPI_NOR_4B_OPCODES"),
    ("u-boot", "PP_1_1_4_4B", "SPI_NOR_4B_OPCODES"),
    ("qemu", "READ_1_1_1", _EVERY_PART),
    ("qemu", "READ_1_1_1_FAST", _EVERY_PART),
    ("qemu", "PP_1_1_1", _EVERY_PART),
    ("qemu", "CHIP_ERASE", f"BULK_ERASE: {_EVERY_PART}"),
    ("qemu", "CHIP_ERASE_ALT", f"BULK_ERASE_60: {_EVERY_PART}"),
    ("openfpgaloader", "READ_1_1_1", "every read"),
    ("openfpgaloader", "PP_1_1_1", "every write"),
    ("openfpgaloader", "READ_1_1_1_4B", "every read above 16 MiB"),
    ("openfpgaloader", "PP_1_1_1_4B", "every write above 16 MiB"),
    ("openfpgaloader", "BE_4K_4B", "subsector_erase = true, above 16 MiB"),
    ("openfpgaloader", "SE_4B", "sector_erase = true, above 16 MiB"),
    ("imsprog", "READ_1_1_1", "every read"),
    ("imsprog", "PP_1_1_1", "every write, in 256-byte pages"),
    ("imsprog", "SE", "every erase, at every 64 KiB"),
}


def test_only_the_known_driver_defaults_are_assumed() -> None:
    found = {
        (r["source"], o["op"], o["via"]) for r in RECORDS for o in r["opcodes"] if "assumed" in o
    }
    assert found == DEFAULTS
    assert all(o["assumed"] is True for r in RECORDS for o in r["opcodes"] if "assumed" in o)


def test_no_quad_program_from_u_boots_default() -> None:
    # U-Boot adds PP_1_1_4 (and its 4-byte form) to every SPI_NOR_QUAD_READ
    # part: its driver's default, not the part's.
    uboot = [Record.from_json(r) for r in RECORDS if r["source"] == "u-boot"]
    assert not [r.name for r in uboot if Feature.QUAD_PP in r.features]


def test_no_spi_nand_eraser_implies_a_nor_erase() -> None:
    erase = {Feature.ERASE_4K, Feature.ERASE_32K, Feature.ERASE_64K}

    def found(r: dict[str, Any]) -> bool:
        loaded = Record.from_json(r)
        ops = {u.op for u in loaded.opcodes}
        return bool(loaded.features & erase) or bool(ops & set(ERASE_BY_OPCODE.values()))

    assert not [_where(r) for r in RECORDS if r["type"] == "nand" and found(r)]


def test_a_spi_nand_eraser_is_its_block_erase() -> None:
    def other(r: dict[str, Any]) -> list[int | None]:
        return [e["opcode"] for e in r["erasers"] or () if e["opcode"] != 0xD8]

    assert not [(_where(r), other(r)) for r in RECORDS if r["type"] == "nand" and other(r)]


def test_no_eraser_for_a_part_that_needs_no_erase() -> None:
    # No erase command, that is: flashrom's M95 EEPROMs have its erase
    # routine (spi_block_erase_emulation), which writes the bytes.
    def erases(r: dict[str, Any]) -> bool:
        return any(e["opcode"] is not None for e in r["erasers"] or ())

    assert not [_where(r) for r in RECORDS if "no_erase" in r["features"] and erases(r)]


# --- what a record's SFDP tables say is derived -------------------------------


def _with_sfdp() -> list[dict[str, Any]]:
    return [r for r in RECORDS if r["sfdp"] or r["sfdp_tables"]]


def test_sfdp_is_a_dump_or_tables_not_both() -> None:
    assert not [_where(r) for r in RECORDS if r["sfdp"] and r["sfdp_tables"]]
    assert {r["source"] for r in _with_sfdp()} == {"qemu", "zephyr"}


def test_the_raw_tables_round_trip() -> None:
    # The tables are the stored fact: they load, decode, and are written
    # back byte for byte.
    for d in _with_sfdp():
        r = Record.from_json(d)
        parsed = r.parsed_sfdp
        assert parsed is not None, _where(d)
        assert parsed.bfpt is not None, _where(d)
        for table_id, raw in r.sfdp_tables.items():
            assert parsed.table(table_id) == raw, _where(d)
        assert r.to_json()["sfdp_tables"] == d["sfdp_tables"]


def test_no_stored_value_its_tables_give() -> None:
    """A size, page size, eraser, operation or capability claim a record's
    own SFDP tables give is derived, so not stored; one that differs is a
    disagreement (Record.sfdp_disagreements). A stored operation is the
    tables' when it states no dummy clocks, or the same ones."""

    def twice(d: dict[str, Any]) -> list[str]:
        r = Record.from_json(d)
        assert r.sfdp_facts is not None
        assert r.parsed_sfdp is not None
        facts = r.sfdp_facts
        out = [n for n in ("size", "page_size") if d[n] is not None and d[n] == getattr(facts, n)]
        out += [f"eraser 0x{e.opcode:02x}" for e in r.eraser_claims if e in r.sfdp_erasers]
        given = {u.op: u.dummy_clocks for u in facts.opcodes}
        out += [
            o["op"]
            for o in d["opcodes"]
            if o["op"] in given and o.get("dummy_clocks") in (None, given[o["op"]])
        ]
        out += [f for f in d["features"] if f in derive.sfdp_features(r.parsed_sfdp)]
        return out

    assert not [(_where(d), twice(d)) for d in _with_sfdp() if twice(d)]


def test_a_disagreement_is_the_stored_value() -> None:
    found = []
    for d in _with_sfdp():
        for field, stored, said in Record.from_json(d).sfdp_disagreements():
            assert stored != said
            found.append((d["name"], field))
            if field != "erasers":
                assert d[field] == stored, _where(d)
    # One board copies another part's table (16 MiB, with DTR, for a 2 MiB
    # P25Q16H). Two boards' page-size is their driver's setting, kept as a
    # flag (spiflash_extract.zephyr.PAGE_SIZE_IS_THE_DRIVERS).
    assert sorted(found) == [("P25Q16H", "size")]


def test_no_sfdp_residue() -> None:
    zephyr = [f for r in RECORDS if r["source"] == "zephyr" for f in r["flags"]]
    assert not [f for f in zephyr if f.startswith("sfdp-")]
    notes = [n for r in RECORDS if r["source"] in ("qemu", "zephyr") for n in r["notes"]]
    assert not [n for n in notes if n.startswith(("SFDP:", "sfdp-bfp gives"))]
    # One decoder: the extractors have none of their own.
    assert not (Path(record.__file__).parent / "sfdp.py").exists()
