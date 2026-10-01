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

from spiflash.derive import ERASE_BY_OPCODE, ID_OPERATION
from spiflash.enums import IdMethod
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


def test_no_id_operation_the_id_method_gives() -> None:
    def stored(r: dict[str, Any]) -> bool:
        op = ID_OPERATION.get(IdMethod(r["id_method"])) if r["id_method"] else None
        return op in {o["op"] for o in r["opcodes"]}

    assert not [_where(r) for r in _nor() if stored(r)]


def test_flags_are_residue() -> None:
    def held(r: dict[str, Any]) -> set[str]:
        vias = [*r["via"].values(), *(o["via"] for o in r["opcodes"])]
        return set(r["flags"]) & {t for v in vias for t in record.tokens(v)}

    assert not [(_where(r), held(r)) for r in RECORDS if held(r)]


def test_via_keys_follow_the_scheme() -> None:
    for r in RECORDS:
        record.check_via(r["via"], r["features"])
        assert list(r["via"]) == sorted(r["via"]), _where(r)
        assert all(r["via"].values()), _where(r)


@pytest.mark.parametrize(
    ("source", "pattern"),
    [
        ("mediatek", r"\d+ B OOB per page; .*"),  # the flags hold the geometry
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
