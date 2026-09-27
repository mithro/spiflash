"""The opcode pages: their timing diagrams, and what the pages say."""

from __future__ import annotations

import pytest

from opcode_pages import explain, phases, related
from opcode_timing import MAX_CLOCKS, diagram
from spiflash.opcodes import OPERATIONS


def rows(op_name: str) -> dict[str, dict[str, object]]:
    return {row["name"]: row for row in diagram(OPERATIONS[op_name])["signal"]}


def test_command_bits_are_the_opcode() -> None:
    # 0x6b = 0110 1011, most significant bit first, on IO0, after the idle cell.
    io0 = rows("READ_1_1_4")["IO0 (SI)"]["wave"]
    levels = []
    for w in str(io0)[1:9]:
        levels.append(levels[-1] if w == "." else w)
    assert "".join(levels) == "01101011"


def test_single_line_read_answers_on_so() -> None:
    signals = rows("READ_1_1_1")
    assert "D7" in signals["IO1 (SO)"]["data"]
    assert "D7" not in signals["IO0 (SI)"].get("data", [])
    # A write goes the other way: the host sends on SI.
    assert "D7" in rows("PP_1_1_1")["IO0 (SI)"]["data"]


def test_quad_lines_carry_the_right_bits() -> None:
    signals = rows("READ_1_4_4")
    # The address's top bit goes out on the highest line first.
    assert signals["IO3 (HOLD#)"]["data"][0] == "A23"
    assert signals["IO0 (SI)"]["data"][0] == "A20"
    # Data: D7 on IO3, D4 on IO0 in the first clock.
    assert signals["IO3 (HOLD#)"]["data"][-2] == "D7"
    assert signals["IO0 (SI)"]["data"][-2] == "D4"


def test_long_phases_are_shortened_but_commands_never() -> None:
    doc = diagram(OPERATIONS["READ_1_1_1_4B"])  # 32 address clocks
    clock = next(s for s in doc["signal"] if s["name"] == "SCLK")["wave"]
    assert "|" in clock
    assert len(clock) < 8 + 32
    # A 24-clock dummy (RES) is shortened too.
    assert len(rows("RES")["SCLK"]["wave"]) < 8 + 24 + 8
    assert MAX_CLOCKS < 8


def test_every_operation_has_a_diagram() -> None:
    for op in OPERATIONS.values():
        doc = diagram(op)
        waves = [s["wave"] for s in doc["signal"]]
        assert len({len(w) for w in waves}) == 1, op.name  # every row the same length


def test_octal_dtr_is_a_bus_with_a_strobe() -> None:
    signals = rows("READ_8D_8D_8D")
    assert set(signals) == {"phase", "CS#", "SCLK", "IO[7:0]", "DS"}
    assert "ext" in signals["IO[7:0]"]["data"]
    assert "h" in str(signals["DS"]["wave"])  # the strobe runs with the data


@pytest.mark.parametrize(
    ("name", "phrase"),
    [
        ("READ_1_4_4", "6 dummy clocks"),
        ("PP_1_1_1", "Write Enable (0x06)"),
        ("BP", "one byte per command"),
        ("SE", "block containing a 3-byte address"),
        ("CHIP_ERASE", "whole chip"),
        ("RDID", "JEP106"),
        ("READ_1_8_8", "depends on the part"),
    ],
)
def test_explanations(name: str, phrase: str) -> None:
    assert phrase in explain(OPERATIONS[name])


def test_phases_table() -> None:
    table = {row[0]: row for row in phases(OPERATIONS["READ_1_4_4"])}
    assert table["Command"][1:3] == ["1", "8"]
    assert table["Address"][1:3] == ["4", "6"]
    assert table["Dummy"][2] == "6"
    assert table["Data"][2] == "2 per byte"
    assert "Data" not in {row[0] for row in phases(OPERATIONS["CHIP_ERASE"])}


def test_related() -> None:
    names = {o.name for o in related(OPERATIONS["READ_1_1_1_FAST"])}
    assert {"READ_8D_8D_8D", "READ_1_1_1_FAST_4B"} <= names
    assert {o.name for o in related(OPERATIONS["SE_4B"])} == {"SE"}
