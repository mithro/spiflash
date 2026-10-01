"""Each fact is stored once: checks over the shipped records.json, read raw.

A value a record's other stored fields imply is derived at load
(:mod:`spiflash.derive`), so the data must not hold it too. Each phase of
the data model adds its rules here."""

from __future__ import annotations

import json
import re
from importlib import resources
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
    def stored(r: dict[str, Any]) -> set[str]:
        derived = {u.op for u in derive.opcodes(Record.from_json(r))}
        return derived & {o["op"] for o in r["opcodes"]}

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


def test_a_driver_default_implies_nothing() -> None:
    def without_defaults(r: dict[str, Any]) -> dict[str, Any]:
        return {**r, "opcodes": [o for o in r["opcodes"] if not o.get("assumed")]}

    def differs(r: dict[str, Any]) -> bool:
        before = derive.features(Record.from_json(r))
        return before != derive.features(Record.from_json(without_defaults(r)))

    assert not [_where(r) for r in RECORDS if differs(r)]
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


def test_no_eraser_for_a_part_that_needs_no_erase() -> None:
    # No erase command, that is: flashrom's M95 EEPROMs have its erase
    # routine (spi_block_erase_emulation), which writes the bytes.
    def erases(r: dict[str, Any]) -> bool:
        return any(e["opcode"] is not None for e in r["erasers"] or ())

    assert not [_where(r) for r in RECORDS if "no_erase" in r["features"] and erases(r)]


def test_each_eraser_gives_its_operation_and_the_sector_is_an_eraser_block() -> None:
    def disagree(r: dict[str, Any]) -> list[str]:
        loaded = Record.from_json(r)
        ops = {u.op for u in loaded.opcodes}
        out = [
            f"0x{e.opcode:02x}"
            for e in loaded.erasers
            if r["type"] == "nor" and e.opcode is not None and ERASE_BY_OPCODE[e.opcode] not in ops
        ]
        blocks = {b.size for e in loaded.erasers for b in e.blocks}
        if loaded.sector_size is not None and loaded.sector_size not in blocks:
            out.append(f"sector {loaded.sector_size}")
        return out

    assert not [(_where(r), disagree(r)) for r in RECORDS if disagree(r)]
