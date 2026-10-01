"""SFDP tables to and from the database (encode, to_entry) and compared
(diff), on QEMU's shipped dumps, Zephyr's copied tables and hand-built ones."""

from __future__ import annotations

from typing import Any

import pytest

import spiflash
from spiflash import derive, sfdp_tools
from spiflash.enums import Feature
from spiflash.model import Flash, Record
from spiflash.opcodes import OPERATIONS
from spiflash.sfdp import AddressBytes, Sfdp, from_tables, parse
from spiflash.sfdp_tools import ENCODE_LOSSES, diff, encode, to_entry
from test_db import rec
from test_sfdp import MX25L25635E, W25Q512JV

#: Who a dump read as an entry is (to_entry leaves it to the caller).
IDENTITY = {"source": "qemu", "file": "x.c", "line": 1, "name": "x"}

#: What main's own sfdp-bfp decoder (spiflash_extract.sfdp, now gone) gave
#: each Zephyr node, with the node's size and page size where it states
#: one: (size, page size, [(erase opcode, block)], reads).
ZEPHYR_BFPTS: dict[tuple[str, int], tuple[int, int | None, list[tuple[int, int]], list[str]]] = {
    ("boards/adi/apard32690/apard32690_max32690_m4.dts", 303): (
        8 << 20,
        4096,
        [(0x20, 4096), (0x52, 32768), (0xD8, 65536)],
        ["READ_1_1_2", "READ_1_1_4", "READ_1_2_2", "READ_1_4_4"],
    ),
    ("boards/andestech/adp_xc7k_ae350/adp_xc7k_ae350_common.dtsi", 184): (
        2 << 20,
        None,
        [(0x20, 4096), (0x52, 32768), (0xD8, 65536)],
        ["READ_1_1_2", "READ_1_1_4", "READ_1_2_2", "READ_1_4_4"],
    ),
    ("boards/arduino/portenta_h7/arduino_portenta_h7-common.dtsi", 196): (
        16 << 20,
        256,
        [(0x20, 4096), (0x52, 32768), (0xD8, 65536)],
        ["READ_1_1_2", "READ_1_1_4", "READ_1_2_2", "READ_1_4_4"],
    ),
    ("boards/coredevices/p2d/p2d.dts", 205): (
        32 << 20,
        256,
        [(0x20, 4096), (0x52, 32768), (0xD8, 65536)],
        ["READ_1_1_2", "READ_1_1_4", "READ_1_2_2", "READ_1_4_4"],
    ),
    ("boards/nordic/nrf54h20dk/nrf54h20dk_nrf54h20-common.dtsi", 24): (
        8 << 20,
        256,
        [(0x20, 4096), (0xD8, 65536)],
        [],
    ),
    ("boards/nordic/nrf9161dk/nrf9161dk_nrf9161_common_0_7_0.dtsi", 11): (
        32 << 20,
        256,
        [(0x20, 4096), (0x52, 32768), (0xD8, 65536)],
        ["READ_1_1_4", "READ_1_4_4"],
    ),
    ("boards/nxp/frdm_mcxe247/frdm_mcxe247.dts", 297): (
        8 << 20,
        128,
        [(0x20, 4096), (0x52, 32768), (0xD8, 65536)],
        ["READ_1_1_2", "READ_1_1_4", "READ_1_2_2", "READ_1_4_4"],
    ),
    ("boards/particle/argon/dts/mesh_feather.dtsi", 174): (
        4 << 20,
        None,
        [(0x20, 4096), (0x52, 32768), (0xD8, 65536)],
        ["READ_1_1_2", "READ_1_1_4", "READ_1_2_2", "READ_1_4_4"],
    ),
    ("boards/seeed/wio_tracker_l1/wio_tracker_l1.dts", 219): (
        2 << 20,
        None,
        [(0x20, 4096), (0x52, 32768), (0x81, 256), (0xD8, 65536)],
        ["READ_1_1_2", "READ_1_1_4", "READ_1_2_2", "READ_1_4_4"],
    ),
    ("boards/shields/x_nucleo_pgeez1/x_nucleo_pgeez1.overlay", 23): (
        4 << 20,
        512,
        [(0x20, 4096), (0xD8, 65536), (0xDB, 512)],
        ["READ_1_1_2", "READ_1_1_4"],
    ),
}


def records() -> dict[tuple[str, int], Record]:
    return {(r.file, r.line): r for r in spiflash.records() if r.source == "zephyr"}


def test_zephyrs_tables_give_what_the_old_decoder_did() -> None:
    by_place = records()
    for place, (size, page, erasers, reads) in ZEPHYR_BFPTS.items():
        r = by_place[place]
        assert r.sfdp_tables, place
        assert (r.size, r.page_size) == (size, page), place
        assert (
            sorted(
                (e.opcode, e.blocks[0].size)
                for e in r.erasers
                if e.opcode not in (0x21, 0x5C, 0xDC)
            )
            == erasers
        ), place
        assert set(reads) <= {u.op for u in r.opcodes}, place


def test_from_tables() -> None:
    # JESD216's other density form (2^N bits), 4-byte addresses only, DTR,
    # the 2-2-2 and 4-4-4 reads, and no erase types but DWORD 1's 4 KiB one:
    # the case main's own decoder was tested on.
    dw1 = 0xE5 | 0x20 << 8 | 1 << 16 | 2 << 17 | 1 << 19
    dws = [dw1, 1 << 31 | 28, 0, 0x3B00, 0x11, 0xBB << 24, 0xEB << 24, 0, 0]
    s = from_tables({0xFF00: b"".join(d.to_bytes(4, "little") for d in dws)})
    assert s.partial
    assert (s.data, s.major, s.minor, s.access_protocol) == (b"", None, None, None)
    assert (s.revision, s.revision_name) == ("unknown", "unknown revision")
    (h,) = s.headers
    assert (h.synthetic, h.major, h.length, h.revision) == (True, None, 9, "?")
    assert s.size == 32 << 20
    assert s.bfpt is not None
    assert (s.address_bytes, s.bfpt.dtr) == (AddressBytes.FOUR, True)
    assert {p: r.opcode for p, r in s.reads.items()} == {
        "1-1-2": 0x3B,
        "2-2-2": 0xBB,
        "4-4-4": 0xEB,
    }
    facts = s.facts()
    assert [e.to_json() for e in facts.erasers] == [{"opcode": 0x20, "blocks": [[4096, 8192]]}]
    assert (facts.page_size, s.bfpt.quad_enable) == (None, None)
    assert {"READ_1_1_2", "READ_4_4_4", "BE_4K", "RDSFDP", "READ_1_1_1"} <= {
        u.op for u in facts.opcodes
    }
    assert {"4byte_addr", "qpi", "dual_read", "quad_read", "erase_4k"} <= s.features()
    assert "SFDP parameter table without the SFDP header: BFPT" in s.describe()
    assert s.to_json()["partial"] is True
    # A table that is not whole dwords is read as far as it goes.
    short = from_tables({0xFF00: bytes(37)})
    assert any("not whole dwords" in w for w in short.warnings)
    # A whole dump decodes as its tables do.
    whole = parse(MX25L25635E)
    bfpt = whole.table(0xFF00)
    assert bfpt is not None
    assert from_tables({0xFF00: bfpt}).facts() == whole.facts()


def test_dw1s_4k_erase_needs_dw1s_uniform_bits() -> None:
    # No erase types; DW1[15:8] is 0x20 in both, but DW1[1:0] 11 says the
    # part has no uniform 4 KiB erase.
    def tables(bits: int) -> Sfdp:
        dws = [0xFFF32000 | 0xE4 | bits, (16 << 23) - 1, 0, 0, 0, 0, 0, 0, 0]
        return from_tables({0xFF00: b"".join(d.to_bytes(4, "little") for d in dws)})

    assert [e.opcode for e in tables(0b01).facts().erasers] == [0x20]
    assert "BE_4K" in {u.op for u in tables(0b01).facts().opcodes}
    assert tables(0b11).facts().erasers == ()
    assert "BE_4K" not in {u.op for u in tables(0b11).facts().opcodes}
    assert "erase_4k" not in tables(0b11).features()


def test_facts_carry_each_reads_dummy_clocks() -> None:
    facts = parse(W25Q512JV).facts()
    clocks = {u.op: u.dummy_clocks for u in facts.opcodes}
    assert clocks["READ_1_4_4"] == 6
    assert clocks["READ_4_4_4"] == 2
    assert clocks["READ_1_1_1_FAST_4B"] == 8
    assert clocks["SE"] is None  # not a read
    assert all(u.implied and not u.assumed for u in facts.opcodes)
    # JESD216's read 0x03 is the part's own; nothing says fast read 0x0b.
    assert "READ_1_1_1" in clocks
    assert "READ_1_1_1_FAST" not in clocks


def test_a_records_features_are_derived_once() -> None:
    # Sfdp.features() is the record's rules on the tables alone.
    for r in spiflash.records():
        if r.parsed_sfdp is not None:
            assert r.parsed_sfdp.features() <= r.features, r.name
            assert derive.sfdp_features(r.parsed_sfdp) == r.parsed_sfdp.features()


# --- diff ---------------------------------------------------------------------


def test_diff_of_a_table_with_itself_is_empty() -> None:
    for r in spiflash.records():
        if r.parsed_sfdp is not None:
            d = diff(r.parsed_sfdp, r.parsed_sfdp)
            assert not d, r.name
            assert d.describe() == "no differences"


def chip(key: str) -> Flash:
    return next(f for f in spiflash.flashes() if f.key == key and f.type == "nor")


def test_diff_names_the_known_differences() -> None:
    # QEMU's MX25L25635E and F dumps, both c22019: the F adds the 4-4-4
    # read, and its vendor table differs.
    e, f = (d.sfdp for d in chip("c22019").sfdp_dumps)
    d = diff(e, f)
    assert bool(d)
    assert [(x.path, x.a, x.b) for x in d.fields] == [
        ("reads.4-4-4", None, "0xeb, 2 mode + 4 wait clocks"),
    ]
    assert [(x.name, x.index) for x in d.dwords] == [
        ("BFPT", 5),
        ("BFPT", 7),
        ("vendor table (0xc2)", 2),
        ("vendor table (0xc2)", 3),
    ]
    assert d.tables == ()
    text = d.describe("E", "F")
    assert "reads.4-4-4: none in E, 0xeb, 2 mode + 4 wait clocks in F" in text
    assert "BFPT DW7: 0xff00ffff in E, 0xeb44ffff in F" in text
    assert d.to_json()["differ"] is True
    # Tables one has and the other has not.
    d = diff(parse(MX25L25635E), parse(W25Q512JV))
    assert {(t.name, t.a, t.b) for t in d.tables} >= {
        ("4BAIT", None, "1.0, 2 dwords"),
        ("vendor table (0xc2)", "1.0, 4 dwords", None),
    }


def test_diff_marks_the_mode_and_wait_split_as_expected() -> None:
    s = parse(W25Q512JV)
    d = diff(s, encode(Record.from_json(to_entry(s) | IDENTITY), assume=True).sfdp)
    split = {x.path: x.expected for x in d.fields if x.path.startswith("reads.")}
    assert split == {
        "reads.1-2-2": "the same 4 dummy clocks, split differently",
        "reads.1-4-4": "the same 6 dummy clocks, split differently",
        "reads.4-4-4": "the same 2 dummy clocks, split differently",
    }
    assert all(x.path not in split for x in d.unexpected)
    assert "(expected: the same 6 dummy clocks, split differently)" in d.describe()


# --- to_entry -----------------------------------------------------------------


def test_to_entry_is_what_a_record_carrying_the_tables_derives() -> None:
    for r in spiflash.records():
        s = r.parsed_sfdp
        if s is None:
            continue
        entry = to_entry(s)
        assert list(entry) == list(r.to_json()), r.name
        assert entry["sfdp"] is None
        read = Record.from_json(entry | IDENTITY)
        carrying = Record.from_json(
            {
                **entry,
                **IDENTITY,
                "features": [],
                "via": {},
                "size": None,
                "page_size": None,
                "erasers": None,
                "opcodes": [],
                "sfdp": r.sfdp.hex() if r.sfdp else None,
                "sfdp_tables": {f"{k:04x}": v.hex() for k, v in r.sfdp_tables.items()},
            }
        )
        assert (read.size, read.page_size) == (carrying.size, carrying.page_size), r.name
        assert set(read.erasers) == set(carrying.erasers), r.name
        assert read.features == carrying.features, r.name
        assert {(u.op, u.dummy_clocks) for u in read.opcodes} == {
            (u.op, u.dummy_clocks) for u in carrying.opcodes
        }, r.name


def test_to_entry_claims_what_only_sfdp_says() -> None:
    # A JESD216 (1.0) table of a 16 MiB part that takes 3 or 4 address
    # bytes: nothing else in the entry implies 4byte_addr.
    dws = [
        0xFFF320E5 | 1 << 17,
        (16 << 23) - 1,
        0x6B08EB44,
        0xBB043B08,
        0xFFFFFFEE,
        0xFF00FFFF,
        0xFF00FFFF,
        0x520F200C,
        0xFF00D810,
    ]
    s = from_tables({0xFF00: b"".join(d.to_bytes(4, "little") for d in dws)})
    entry = to_entry(s)
    assert entry["features"] == ["4byte_addr"]
    assert entry["via"] == {
        "feature:4byte_addr": "its SFDP tables (BFPT DW1: 3 or 4 address bytes)"
    }
    assert Feature.FOUR_BYTE_ADDR in Record.from_json(entry | IDENTITY).features
    # The erases the erasers give are not stored twice.
    assert "BE_4K" not in [o["op"] for o in entry["opcodes"]]
    dummy = {o["op"]: o.get("dummy_clocks") for o in entry["opcodes"]}
    assert dummy["READ_1_4_4"] == 6


# --- encode -------------------------------------------------------------------


def nor_chips() -> list[Flash]:
    return [f for f in spiflash.flashes() if f.type == "nor" and f.size]


def encodable_page(f: Flash) -> int:
    """The page size DW11 can give: a power of two, else 256 (assumed)."""
    page = f.page_size
    return page if page and not page & (page - 1) else 256


def per_part_ops(f: Flash) -> set[str]:
    return {name for name, o in f.opcodes.items() if any(not c.assumed for c in o.because)}


def test_encode_round_trips_each_field() -> None:
    for f in nor_chips():
        for assume in (False, True):
            out = encode(f, assume=assume)
            s = out.sfdp
            assert s.warnings == () or all("4BAIT claims" in w for w in s.warnings), f.key
            assert s.size == f.size, f.key
            if assume:
                assert out.revision == (1, 6)
                assert s.page_size == encodable_page(f), f.key
            else:
                assert out.revision == (1, 0)
                assert s.page_size is None
            # Never invented: every operation it gives is one some source
            # gives the part, or the read and RDSFDP JESD216 guarantees.
            given = {o.name for o in s.operations() if o.name}
            assert given - per_part_ops(f) <= {"READ_1_1_1", "RDSFDP"}, f.key
            # Each BFPT read's dummy clocks are the part's, or the usual.
            for o in s.operations():
                if o.via.startswith("BFPT") and o.name and o.name.startswith("READ"):
                    used = [u.dummy_clocks for r in f.records for u in r.opcodes if u.op == o.name]
                    known = next(
                        (c for c in used if c is not None), OPERATIONS[o.name].dummy_clocks
                    )
                    assert o.dummy_clocks == known, (f.key, o.name)


def test_entry_to_sfdp_to_entry_gives_back_what_encode_wrote() -> None:
    for f in nor_chips():
        out = encode(f, assume=True)
        back = Record.from_json(to_entry(out.sfdp) | IDENTITY)
        assert back.size == f.size
        assert back.page_size == encodable_page(f)
        # Its erasers are the sources', a 4-byte one where they give the op.
        given = {(e.opcode, e.blocks[0].size) for r in f.records for e in r.erasers}
        four_byte = {e.opcode: e for e in back.erasers if e.opcode in (0x21, 0x5C, 0xDC)}
        three = {(e.opcode, e.blocks[0].size) for e in back.erasers} - {
            (op, e.blocks[0].size) for op, e in four_byte.items()
        }
        assert three <= given, f.key
        assert {derive.ERASE_BY_OPCODE[op] for op in four_byte} <= per_part_ops(f), f.key
        written = {o.name for o in out.sfdp.operations() if o.name}
        assert written <= {u.op for u in back.opcodes}, f.key


def test_sfdp_to_entry_to_sfdp_loses_only_what_is_documented() -> None:
    reads_without_an_op = {"2-2-2", "1-1-8", "1-8-8", "8D-8D-8D"}
    for r in spiflash.records():
        s = r.parsed_sfdp
        if s is None:
            continue
        back = encode(Record.from_json(to_entry(s) | IDENTITY), assume=True).sfdp
        d = diff(s, back)
        for x in d.fields:
            head, _, rest = x.path.partition(".")
            assert head in ENCODE_LOSSES, (r.name, x)
            if head == "reads":
                assert x.expected or rest in reads_without_an_op, (r.name, x)
        # The erase types are the same, smallest first, without their times.
        kept = {(e.size, e.opcode, e.opcode_4b) for e in back.erase_types}
        assert kept == {(e.size, e.opcode, e.opcode_4b) for e in s.erase_types}, r.name
        assert {t.name for t in d.tables} <= {
            "4BAIT",
            "vendor table (0x9d)",
            "vendor table (0xc2)",
            "vendor table (0x20)",
            "vendor table (0xef)",
            "xSPI profile 1.0",
            "sector map",
            "SCCR map",
            "BFPT",
        }, (r.name, d.tables)


def test_encode_never_invents() -> None:
    # A part with no page size: 1.0 without assume, and missing says why.
    r = rec(page_size=None, erasers=[{"opcode": 0x20, "blocks": [[4096, 4096]]}])
    out = encode(r)
    assert out.revision == (1, 0)
    assert "DW11: the page size" in out.missing
    assert any(line.startswith("DW1 bit 2:") for line in out.assumed)
    s = out.sfdp
    assert (s.revision, s.size, s.page_size) == ("1.0", 16 << 20, None)
    assert [(e.opcode, e.size) for e in s.erase_types] == [(0x20, 4096)]
    assert s.reads == {}  # no read the record gives
    # With assume, the later dwords are JESD216's "not supported", listed.
    out = encode(r, assume=True)
    assert out.revision == (1, 6)
    assert "DW15: the quad enable requirement, written as 0, no QE bit" in out.assumed
    assert out.missing == ()
    assert out.sfdp.bfpt is not None
    assert out.sfdp.bfpt.suspend_resume is False
    assert out.sfdp.bfpt.enter_deep_power_down is None
    # A read the record gives without dummy clocks takes the usual number,
    # and says so; one with none known is left out.
    q = rec(opcodes=[{"op": "READ_1_1_4", "via": "x"}, {"op": "READ_4_4_4", "via": "y"}])
    out = encode(q)
    assert "READ_1_1_4: 8 dummy clocks, the operation's usual number" in out.assumed
    assert "READ_4_4_4: no dummy clocks known, so not in the BFPT" in out.missing
    assert set(out.sfdp.reads) == {"1-1-4"}
    # A driver default is not the part's.
    d = rec(opcodes=[{"op": "READ_1_1_4", "via": "every part", "assumed": True}])
    assert encode(d).sfdp.reads == {}


def test_encode_4byte_instructions() -> None:
    ops = [
        {"op": op, "via": "x"}
        for op in ("READ_1_1_4", "READ_1_1_4_4B", "READ_1_4_4_4B", "PP_1_1_1_4B", "SE_4B")
    ]
    r = rec(size=32 << 20, opcodes=ops, erasers=[{"opcode": 0xD8, "blocks": [[65536, 512]]}])
    out = encode(r)
    s = out.sfdp
    assert s.four_byte is not None
    assert {i.description for i in s.four_byte.instructions} == {
        "fast read 1-1-4",
        "page program",
    }
    assert s.four_byte.erase_opcodes == (0xDC, None, None, None)
    assert "READ_1_4_4_4B: the 4BAIT lists a 4-byte read only with its 3-byte form" in out.missing
    assert s.address_bytes is AddressBytes.THREE_OR_FOUR


def test_encode_qpi_sequences_land_in_their_dw15_bits() -> None:
    # DW15[8:4] are the 4-4-4 enable sequences (bit 5: 0x38, bit 6: 0x35),
    # DW15[3:0] the disable ones; bit 9 is 0-4-4 mode, which encode never
    # claims.
    ops = [{"op": op, "via": "x"} for op in ("EQPI_38", "RSTQIO_FF")]
    s = encode(rec(opcodes=ops), assume=True).sfdp
    assert s.bfpt is not None
    assert s.bfpt.qpi_enable == ("0x38",)
    assert s.bfpt.qpi_disable == ("0xff",)
    assert s.bfpt.mode_0_4_4 is False
    s = encode(rec(opcodes=[{"op": "EQPI_35", "via": "x"}]), assume=True).sfdp
    assert s.bfpt is not None
    assert s.bfpt.qpi_enable == ("0x35",)
    # The shipped PY25Q64HA, whose sources give 0x38: the bits it was
    # once written to (9, 10) would read as 0-4-4 mode.
    dw15 = encode(chip("852017"), assume=True).sfdp.bfpt
    assert dw15 is not None
    assert (dw15.qpi_enable, dw15.qpi_disable, dw15.mode_0_4_4) == (("0x38",), ("0xff",), False)
    assert dw15.dwords[14] == 0xFF000021
    # diff compares 0-4-4 mode.
    with_044 = parse(W25Q512JV)
    assert with_044.bfpt is not None
    assert with_044.bfpt.mode_0_4_4 is True
    bare = encode(rec(), assume=True).sfdp
    assert "mode_0_4_4" in {f.path for f in diff(with_044, bare).fields}


def test_encode_refuses() -> None:
    with pytest.raises(ValueError, match=r"SFDP 1\.0, 1\.5, 1\.6, not 1\.7"):
        encode(rec(), revision=(1, 7))
    with pytest.raises(ValueError, match="no size"):
        encode(rec(size=None))
    with pytest.raises(ValueError, match="SPI NOR"):
        encode(rec(type="nand", id_method="rdid_opcode_dummy"))


def test_encoded_json() -> None:
    out = encode(rec())
    doc: dict[str, Any] = out.to_json()
    assert doc["revision"] == "1.0"
    assert bytes.fromhex(doc["data"]) == out.data
    assert isinstance(out.sfdp, Sfdp)
    assert sfdp_tools.REVISIONS == ((1, 0), (1, 5), (1, 6))
