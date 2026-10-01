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

import spiflash
from spiflash import derive
from spiflash.derive import ERASE_BY_OPCODE, ID_OPERATION
from spiflash.enums import ENTER_METHODS, Bound, Feature, IdMethod, OperationKind
from spiflash.model import TIMING_COMPONENTS, EraseBlock, Record
from spiflash.opcodes import OPERATIONS
from spiflash.registers import Register, RegisterBit
from spiflash.timings import BOUNDS, TimingKey, Timings, component_name, parse_component
from spiflash_extract import record
from spiflash_extract.flashrom import otp as flashrom_otp


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
    # The SPI NAND core commands each driver sends every part.
    ("linux", "NAND_PAGE_READ", "every part (SPINAND_PAGE_READ_1S_1S_0_OP)"),
    ("linux", "NAND_PROGRAM_EXECUTE", "every part (SPINAND_PROG_EXEC_1S_1S_0_OP)"),
    ("linux", "NAND_GET_FEATURE", "every part (SPINAND_GET_FEATURE_1S_1S_1S_OP)"),
    ("linux", "NAND_SET_FEATURE", "every part (SPINAND_SET_FEATURE_1S_1S_1S_OP)"),
    ("mediatek", "NAND_PAGE_READ", "every part (SNAND_CMD_READ_TO_CACHE)"),
    ("mediatek", "NAND_PROGRAM_EXECUTE", "every part (SNAND_CMD_PROGRAM_EXECUTE)"),
    ("mediatek", "NAND_GET_FEATURE", "every part (SNAND_CMD_GET_FEATURE)"),
    ("mediatek", "NAND_SET_FEATURE", "every part (SNAND_CMD_SET_FEATURE)"),
    # The one-line read and load every SNAND_IO_CAP table has.
    ("mediatek", "NAND_READ_CACHE_1_1_1_FAST", "every snand_cap_read_from_cache* table"),
    ("mediatek", "NAND_PROGRAM_LOAD_1_1_1", "every snand_cap_program_load* table"),
    ("rockchip", "NAND_READ_CACHE_1_1_1", "every part: page_read_cmd = 0x03"),
    ("rockchip", "NAND_PROGRAM_LOAD_1_1_1", "every part: page_prog_cmd = 0x02"),
    ("rockchip", "NAND_PAGE_READ", "every part (sfc_nand_read)"),
    ("rockchip", "NAND_PROGRAM_EXECUTE", "every part (sfc_nand_prog_page_raw)"),
    ("rockchip", "NAND_GET_FEATURE", "every part (sfc_nand_read_feature)"),
    ("rockchip", "NAND_SET_FEATURE", "every part (sfc_nand_write_feature)"),
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
            if field.startswith("timings."):
                key, bound = parse_component(field.removeprefix("timings."))
                assert d["timings"][str(key)][str(bound)] == stored, _where(d)
            elif field != "erasers":
                assert d[field] == stored, _where(d)
    # One board copies another part's table (16 MiB, with DTR, for a 2 MiB
    # P25Q16H). Two boards' page-size is their driver's setting, kept as a
    # flag (spiflash_extract.zephyr.PAGE_SIZE_IS_THE_DRIVERS). frdm_mcxe247's
    # W25Q64 carries the MX25R6435F's table (QE at SR1 bit 6), and its own
    # quad-enable-requirements, Winbond's S2B1v1. nrf7002dk's MX25R6435F
    # gives t-exit-dpd 5 µs; its BFPT's DW14 40 µs (the datasheet's tRDP is
    # 35 µs, or 45 µs in high-performance mode).
    assert sorted(found) == [
        ("MX25R6435F", "timings.dpd_exit.maximum"),
        ("P25Q16H", "size"),
        ("W25Q64", "quad_enable_requirement"),
    ]


def test_no_sfdp_residue() -> None:
    zephyr = [f for r in RECORDS if r["source"] == "zephyr" for f in r["flags"]]
    assert not [f for f in zephyr if f.startswith("sfdp-")]
    notes = [n for r in RECORDS if r["source"] in ("qemu", "zephyr") for n in r["notes"]]
    assert not [n for n in notes if n.startswith(("SFDP:", "sfdp-bfp gives"))]
    # One decoder: the extractors have none of their own.
    assert not (Path(record.__file__).parent / "sfdp.py").exists()


# --- register bits: the quad enable bit, its requirement, protection ---------


def test_a_quad_enable_bit_or_a_requirement_not_both() -> None:
    both = [r for r in RECORDS if r["quad_enable"] is not None and r["quad_enable_requirement"]]
    assert not [_where(r) for r in both]


def test_no_lock_or_quad_read_claim_the_registers_imply() -> None:
    def twice(d: dict[str, Any]) -> set[str]:
        r = Record.from_json(d)
        out = set()
        if "lock" in d["features"] and r.protection is not None and r.protection.blocks:
            out.add("lock")
        if "quad_read" in d["features"] and isinstance(r.quad_enable, RegisterBit):
            out.add("quad_read")
        return out

    assert not [(_where(d), twice(d)) for d in RECORDS if twice(d)]


def test_no_two_roles_on_one_bit() -> None:
    # Protection refuses two of its roles on one bit; nor is the quad
    # enable bit one of them (Linux's GD25Q256 entry would have had QE and
    # TB both at SR1 bit 6, had it taken the GD25Q256C's QE).
    shared = [(_where(d), Record.from_json(d).shared_bits()) for d in RECORDS]
    assert not [(where, bits) for where, bits in shared if bits]


def test_no_requirement_operation_stored() -> None:
    def stored(d: dict[str, Any]) -> set[str]:
        qer = Record.from_json(d).quad_enable_requirement
        ops = set(derive.REQUIREMENT_OPERATIONS.get(qer, ())) if qer else set()
        return ops & {o["op"] for o in d["opcodes"]}

    assert not [(_where(d), stored(d)) for d in RECORDS if stored(d)]


#: Where each source's quad enable bits come from, as (source, the start of
#: their via): the entry's own statement, never a driver's default for
#: every part (Linux's core SR2 bit 1, its makers' default_init).
#: flashprog's are all its .reg_bits' .qe, so need no via.
QUAD_ENABLE_FROM = {
    ("flashprog", None),
    ("dediprog", "QEbitAddr="),
    ("rockchip", "QE_bits="),
    ("rockchip", "has_qe_bits="),
    ("openfpgaloader", "quad_register="),
    ("linux", ".fixups = &"),
    ("linux", "SPINAND_HAS_QE_BIT"),
}


def test_only_the_known_sources_give_a_quad_enable_bit() -> None:
    def source(d: dict[str, Any]) -> tuple[str, str | None]:
        via = d["via"].get("quad_enable")
        starts = [s for src, s in QUAD_ENABLE_FROM if src == d["source"] and s]
        return d["source"], next((s for s in starts if via and via.startswith(s)), via)

    found = {source(d) for d in RECORDS if d["quad_enable"] is not None}
    assert found == QUAD_ENABLE_FROM


def test_no_template_quad_enable() -> None:
    # Dediprog's 0x200 (its template's SR2 bit 1) and 0 say nothing of the
    # part, and 0x80 is SR1 bit 7, the status register protect bit.
    vias = [d["via"].get("quad_enable", "") for d in RECORDS if d["source"] == "dediprog"]
    masks = {int(v.removeprefix("QEbitAddr="), 16) for v in vias if v}
    assert masks
    assert not masks & {0, 0x200, 0x80}


def test_no_protection_layout_from_a_mask_or_another_scheme() -> None:
    # Dediprog's ProtectBlockMask is the bits its programmer clears, not a
    # layout; Linux's entries whose fixups replace the status register
    # locking, and U-Boot's SST26 parts (a block protection register), have
    # none either. Their lock stays a claim.
    def other(d: dict[str, Any]) -> bool:
        replaced = any("replace the status register locking" in n for n in d["notes"])
        sst26 = any("SPI_NOR_HAS_SST26LOCK" in o["via"] for o in d["opcodes"])
        return d["source"] == "dediprog" or replaced or sst26

    assert not [_where(d) for d in RECORDS if other(d) and d["protection"]]
    claims = [d for d in RECORDS if other(d) and d["source"] != "dediprog"]
    assert len(claims) == 15  # Linux 11, U-Boot 4
    assert all("lock" in d["features"] for d in claims)


def test_no_tb_from_a_drivers_constant() -> None:
    # U-Boot tests SR_TB, bit 5, on every SPI_NOR_HAS_TB part, and QEMU's
    # model puts every HAS_SR_TB part's TB there: the bit is theirs, not
    # the part's, so neither gives a TB. The W25Q512JV's is bit 6.
    tb = [
        d for d in RECORDS if d["source"] in ("u-boot", "qemu") and "tb" in (d["protection"] or {})
    ]
    assert not [_where(d) for d in tb]
    (chip,) = spiflash.lookup("ef4020")
    assert chip.protection is not None
    assert chip.protection.tb is None or chip.protection.tb.bit != 5


# --- SPI NAND geometry, dies, die select, dummy clocks ------------------------


def test_no_spi_nand_geometry_on_spi_nor() -> None:
    def given(r: dict[str, Any]) -> list[str]:
        return [f for f in record.NAND_ONLY if r[f] is not None]

    assert not [(_where(r), given(r)) for r in _nor() if given(r)]


def test_each_operation_is_of_the_records_kind_of_flash() -> None:
    def other(r: dict[str, Any]) -> list[str]:
        return [o["op"] for o in r["opcodes"] if OPERATIONS[o["op"]].flash_type != r["type"]]

    assert not [(_where(r), other(r)) for r in RECORDS if other(r)]
    # And as each loads, derived ones too: no SPI NOR id read or erase on
    # a SPI NAND part.
    for d in RECORDS:
        loaded = Record.from_json(d)
        assert {OPERATIONS[u.op].flash_type for u in loaded.opcodes} <= {loaded.type}, _where(d)


def test_no_die_erase_layout_is_stored() -> None:
    die_erases = {OPERATIONS[op].opcode for op in derive.DIE_ERASES}

    def stored(r: dict[str, Any]) -> bool:
        return any(e["opcode"] in die_erases for e in r["erasers"] or ())

    assert not [_where(r) for r in RECORDS if stored(r)]
    # Every record with dies, a size and a die erase has the layout, derived.
    found = 0
    for d in RECORDS:
        r = Record.from_json(d)
        ops = {u.op for u in r.opcode_claims} & derive.DIE_ERASES
        if r.dies and r.dies > 1 and r.size and ops:
            layouts = {(e.opcode, e.blocks) for e in r.erasers if e.opcode in die_erases}
            want = {(OPERATIONS[op].opcode, (EraseBlock(r.size // r.dies, r.dies),)) for op in ops}
            assert layouts == want, _where(d)
            found += 1
    assert found


def test_a_die_is_selected_one_way() -> None:
    def both(r: dict[str, Any]) -> bool:
        ops = {o["op"] for o in r["opcodes"]}
        return r["die_select_bit"] is not None and bool(ops & record.DIE_SELECTS)

    assert not [_where(r) for r in RECORDS if both(r)]
    # Only a part of more than one die selects one.
    selects = [
        r
        for r in RECORDS
        if r["die_select_bit"] or record.DIE_SELECTS & {o["op"] for o in r["opcodes"]}
    ]
    assert selects
    assert all((Record.from_json(r).dies or 0) > 1 for r in selects)


#: Where each source's dies come from, as (source, the start of their via):
#: ``None`` for a source filling them from the same place in every entry
#: (Linux's NAND_MEMORG, MediaTek's SNAND_MEMORG).
DIES_FROM = {
    ("flashrom", "spi_block_erase_c4"),
    ("flashprog", "spi_block_erase_c4"),
    ("dediprog", "DieSizeInKByte="),
    ("qemu", "die_cnt="),
    ("linux", None),
    ("linux", "mt25q01_fixups:"),
    ("linux", "mt25q02_fixups:"),
    ("linux", "n25q00_fixups:"),
    ("linux", "mt35_two_die_fixups:"),
    ("linux", "s25fs256t_fixups:"),
    ("linux", "s25hx_t_fixups:"),
    ("linux", "s28hx_t_fixups:"),
    ("linux", "winbond_nor_multi_die_fixups:"),
    ("mediatek", None),
}


def test_only_the_known_sources_give_dies() -> None:
    def source(d: dict[str, Any]) -> tuple[str, str | None]:
        via = d["via"].get("dies")
        starts = [s for src, s in DIES_FROM if src == d["source"] and s]
        return d["source"], next((s for s in starts if via and via.startswith(s)), via)

    found = {source(d) for d in RECORDS if d["dies"] is not None}
    assert found == DIES_FROM
    # A NAND_MEMORG or SNAND_MEMORG states the dies of every part; a SPI
    # NOR source only of those with more than one, but Linux's S25FS256T,
    # which its fixups give one.
    one = [d for d in RECORDS if d["dies"] == 1 and d["type"] == "nor"]
    assert [d["name"] for d in one] == ["S25FS256T"]


def test_dummy_clocks_only_where_a_source_states_them() -> None:
    # Linux's SPI NAND op variants (dummy bytes) and MediaTek's SNAND_OPs
    # (dummy clocks) state them; no other source's entry does, and an SFDP
    # read's are derived from its tables.
    given = {(d["source"], d["type"]) for d in RECORDS for o in d["opcodes"] if "dummy_clocks" in o}
    assert given == {("linux", "nand"), ("mediatek", "nand")}
    reads = {
        OPERATIONS[o["op"]].kind
        for d in RECORDS
        for o in d["opcodes"]
        if "dummy_clocks" in o and d["source"] == "linux"
    }
    assert reads == {OperationKind.READ}  # Linux's loads have no dummy phase


@pytest.mark.parametrize(
    ("source", "pattern"),
    [
        # Each a field now, or the operations' via.
        ("mediatek", r"(sparesize|planes_per_die|ndies|select_die|read_from_cache|program_load)="),
        ("rockchip", r"max_ecc_bits=.*"),
        ("rockchip", r"FEA_4BIT_(READ|PROG)"),
        ("qemu", r"die_cnt=.*"),
    ],
)
def test_no_flag_a_field_holds(source: str, pattern: str) -> None:
    rx = re.compile(pattern)
    found = [f for r in RECORDS if r["source"] == source for f in r["flags"] if rx.match(f)]
    assert not found


def test_no_geometry_note() -> None:
    notes = [n for r in RECORDS if r["type"] == "nand" for n in r["notes"]]
    assert not [n for n in notes if re.search(r"plane\(s\) of|bit\(s\) per cell|B OOB", n)]


def test_no_quad_enable_from_the_spi_nand_cores_default() -> None:
    # Linux's SPI NAND core clears the QE bit on every part without
    # SPINAND_HAS_QE_BIT: the core's default, which says nothing of the
    # part (the XT26G01D has a QE bit quad reads need, and no flag).
    linux = [d for d in RECORDS if d["source"] == "linux" and d["type"] == "nand"]
    assert not [_where(d) for d in linux if d["quad_enable"] == "none"]
    (xtx,) = spiflash.lookup("0b31")
    assert xtx.quad_enable == RegisterBit(Register.NAND_CONFIG, 0)


def test_oob_size_is_the_parameter_pages() -> None:
    # Linux's oobsize for these is another view of the spare area (with the
    # on-die ECC on, or with the parity area): a note, not the field.
    for part, chip, spare in (
        ("MX35LF2GE4AD", "c22603", 128),
        ("MX35LF4GE4AD", "c23703", 256),
        ("W25N01KV", "efae21", 64),
    ):
        (d,) = [d for d in RECORDS if d["source"] == "linux" and d["name"] == part]
        assert d["oob_size"] is None
        assert any("parameter page" in n for n in d["notes"])
        (f,) = spiflash.lookup(chip, flash_type="nand")
        assert f.oob_size == spare
        assert "oob_size" not in f.conflicts


def test_operations_are_told_apart_by_their_shape() -> None:
    # 0xc2 is NAND_DIE_SELECT and NAND_PROGRAM_LOAD_1_8_8, 0x13 a SPI NOR
    # read and SPI NAND's page read, 0x9f three SPI NAND read-ids: an
    # operation is its opcode and its shape.
    def shape(op: str) -> tuple[object, ...]:
        o = OPERATIONS[op]
        return (
            o.flash_type,
            o.opcode,
            o.protocol,
            o.address_bytes,
            o.dummy_clocks,
            o.data,
            o.data_bytes,
        )

    shapes = [shape(op) for op in OPERATIONS]
    assert len(set(shapes)) == len(shapes)
    assert shape("NAND_DIE_SELECT") != shape("NAND_PROGRAM_LOAD_1_8_8")
    assert shape("RUID") != shape("READ_OTP")  # both 0x4b


# --- 4-byte addressing, supply, OTP, legacy ids (phase 6) ----------------------


def test_four_byte_modes_are_ways_in() -> None:
    # Never opcodes_4b (the _4B operations say it) nor a way out.
    stored = {m for d in RECORDS for m in d["four_byte_modes"]}
    assert stored <= {str(m) for m in ENTER_METHODS}
    assert "opcodes_4b" not in stored


def test_no_operation_a_way_in_gives() -> None:
    # EN4B, WREAR, RDEAR, BRWR and BRRD are derived from the ways in; a
    # source stating one beside its way in states it once.
    def twice(d: dict[str, Any]) -> set[str]:
        modes = Record.from_json(d).four_byte_modes
        given = {op for m in modes for op in derive.FOUR_BYTE_MODE_OPERATIONS[m]}
        return given & {o["op"] for o in d["opcodes"]}

    assert not [(_where(d), twice(d)) for d in RECORDS if twice(d)]


#: Where each source's ways into 4-byte mode come from, as (source, the
#: start of their via): each a per-entry field or flag, never a driver's
#: default for every part (Linux's and U-Boot's per-maker set_4byte
#: functions are not taken).
FOUR_BYTE_FROM = {
    ("flashrom", "FEATURE_4BA_"),
    ("flashprog", "FEATURE_4BA_"),
    ("imsprog", "addr4bit="),
    ("rockchip", "FEA_4BYTE_ADDR_MODE"),
    ("zephyr", "enter-4byte-command="),
}


def test_only_the_known_sources_give_ways_into_4_byte_mode() -> None:
    def source(d: dict[str, Any], mode: str) -> tuple[str, str]:
        via = d["via"].get(f"four_byte_modes:{mode}") or d["via"]["four_byte_modes"]
        starts = [s for src, s in FOUR_BYTE_FROM if src == d["source"]]
        return d["source"], next((s for s in starts if via.startswith(s)), via)

    found = {source(d, m) for d in RECORDS for m in d["four_byte_modes"]}
    assert found == FOUR_BYTE_FROM


def test_zephyr_enter_4byte_addr_is_a_dw16_byte() -> None:
    # p2d.dts gives the GD25LE255E <0xb7>, EN4B's opcode: not read; its
    # BFPT's DW16 gives its way in.
    (d,) = [d for d in RECORDS if d["source"] == "zephyr" and d["name"] == "GD25LE255E"]
    assert d["four_byte_modes"] == []
    assert "enter-4byte-addr=0xb7" in d["flags"]
    assert any(n.startswith("enter-4byte-addr=0xb7 not read") for n in d["notes"])
    assert Record.from_json(d).four_byte_modes == {"en4b"}


def test_a_supply_range_or_a_setting_not_both() -> None:
    assert not [_where(d) for d in RECORDS if d["voltage"] and d["supply_mv"]]
    # A setting only where a programmer's table gives one, for every entry.
    given = {d["source"] for d in RECORDS if d["supply_mv"] is not None}
    assert given == {"dediprog", "imsprog"}
    assert all(d["supply_mv"] for d in RECORDS if d["source"] in given)


def test_no_otp_claim_an_area_or_operation_gives() -> None:
    assert not [
        _where(d)
        for d in RECORDS
        if "otp" in d["features"] and "otp" in derive.features(Record.from_json(d))
    ]
    # flashrom's OTP comments about the whole entry are its area, not notes.
    parsed = [
        n
        for d in RECORDS
        if d["source"] in ("flashrom", "flashprog")
        for n in d["notes"]
        if flashrom_otp(n) is not None
    ]
    assert not parsed


def test_no_legacy_id_the_records_own() -> None:
    for d in RECORDS:
        for method, ident in d["legacy_ids"]:
            assert IdMethod(method).family.value != "jedec", _where(d)
            assert ident != d["id"], _where(d)
    assert {d["source"] for d in RECORDS if d["legacy_ids"]} == {"dediprog"}


@pytest.mark.parametrize(
    ("source", "pattern"),
    [
        # Each a field now (supply_mv, legacy_ids), or a template.
        ("dediprog", r"(Voltage|AlternativeID)="),
        ("imsprog", r"(chipVCC|pageSize|blockSize)="),
        ("flashrom", r"FEATURE_4BA_(ENTER|ENTER_WREN|ENTER_EAR7|EAR_C5C8|EAR_1716)$"),
        ("flashprog", r"FEATURE_4BA_(ENTER|ENTER_WREN|ENTER_EAR7|EAR_C5C8|EAR_1716)$"),
        ("rockchip", r"FEA_4BYTE_ADDR_MODE$"),
    ],
)
def test_no_flag_a_phase_6_field_holds(source: str, pattern: str) -> None:
    rx = re.compile(pattern)
    found = [f for r in RECORDS if r["source"] == source for f in r["flags"] if rx.match(f)]
    assert not found


def test_no_security_register_commands_on_a_function_register_part() -> None:
    # On ISSI's parts 0x48 and 0x42 read and write the function register,
    # so a chip with a bit there has no RSECR or PSECR (flashrom's IS25LP
    # and IS25WP OTP comments name them wrongly).
    def function_bits(f: spiflash.Flash) -> bool:
        bits = [b for r in f.records if r.protection for b in r.protection.roles().values()]
        bits += [r.quad_enable for r in f.records if isinstance(r.quad_enable, RegisterBit)]
        return any(b.register is Register.FUNCTION for b in bits)

    chips = [f for f in spiflash.flashes() if function_bits(f)]
    assert chips
    assert not [f.key for f in chips if {"RSECR", "PSECR"} & set(f.opcodes)]
    issi = [d for d in RECORDS if d["source"] == "flashrom" and d["name"] == "IS25LP128"]
    (d,) = issi
    assert d["otp"] == {"size": 1024}
    assert any("function register" in n for n in d["notes"])


def test_otp_sizes_the_comments_get_wrong_are_the_datasheets() -> None:
    for name in ("S25FL132K", "W25Q40.V"):
        for d in [d for d in RECORDS if d["name"] == name and d["otp"]]:
            assert d["otp"]["size"] == 768, _where(d)
            assert any(n.startswith("OTP area 768 B") for n in d["notes"]), _where(d)


# --- times and the maximum clock (phase 7) -------------------------------------


def _times(d: dict[str, Any]) -> list[tuple[str, str, int]]:
    return [
        (key, bound, ns) for key, bounds in d["timings"].items() for bound, ns in bounds.items()
    ]


def test_timing_keys_and_bounds_are_known() -> None:
    for d in RECORDS:
        for key, bound, ns in _times(d):
            parsed = TimingKey.parse(key)
            assert Bound(bound) in BOUNDS[parsed.event], _where(d)
            assert isinstance(ns, int), _where(d)
            assert ns > 0, _where(d)
            # Every time a record gives is one the sources are compared on.
            assert component_name(parsed, Bound(bound)) in TIMING_COMPONENTS, _where(d)
        r = Record.from_json(d)
        tables = r.sfdp_facts.timings if r.sfdp_facts else Timings()
        assert r.timings == Timings.from_json(d["timings"]).over(tables), _where(d)
        for key, bound in r.timings:
            assert component_name(key, bound) in TIMING_COMPONENTS, _where(d)


def test_timing_bounds_in_order() -> None:
    for d in RECORDS:
        assert not Timings.from_json(d["timings"]).disorder(), _where(d)
        # And with what its tables give.
        assert not Record.from_json(d).timings.disorder(), _where(d)


#: Who gives a time, as (source, event, bound): each a per-part statement
#: of the entry, never a driver's wait for every part.
TIMINGS_FROM = {
    ("dediprog", "chip_erase", "unspecified"),
    ("zephyr", "dpd_enter", "maximum"),
    ("zephyr", "dpd_exit", "maximum"),
    ("zephyr", "dpd_min_time", "minimum"),
    ("zephyr", "dpd_wake_pulse", "minimum"),
    ("zephyr", "reset_pulse", "minimum"),
    ("zephyr", "reset_recovery", "maximum"),
}


def test_only_the_known_sources_give_timings() -> None:
    found = {(d["source"], key, bound) for d in RECORDS for key, bound, _ in _times(d)}
    assert found == TIMINGS_FROM


def test_dediprogs_chip_erase_is_unspecified() -> None:
    times = [(d, ns) for d in RECORDS if d["source"] == "dediprog" for _, _, ns in _times(d)]
    assert len(times) == 899
    for d, ns in times:
        assert ns % 10**9 == 0, _where(d)
        assert d["via"]["timings.chip_erase"] == f"ChipEraseTime={ns // 10**9}", _where(d)


def test_no_stored_timing_its_tables_give() -> None:
    def twice(d: dict[str, Any]) -> list[str]:
        facts = Record.from_json(d).sfdp_facts
        assert facts is not None
        return [
            f"{k}.{b}"
            for k, b, ns in _times(d)
            if facts.timings.values.get((TimingKey.parse(k), Bound(b))) == ns
        ]

    assert not [(_where(d), twice(d)) for d in _with_sfdp() if twice(d)]


def test_no_timing_from_a_driver_default() -> None:
    def default(via: str) -> bool:
        # IMSProg's delay= (a bus-speed factor), not the AT45's dpd-delays.
        words = ("Timeout", "probe_timing", "Clock", "spi-max-frequency", "MAX_FREQ")
        return any(w in via for w in words) or re.search(r"(?<![\w-])delay=", via) is not None

    vias = [(d, v) for d in RECORDS for k, v in d["via"].items() if k.startswith("timings")]
    assert vias
    assert not [_where(d) for d, v in vias if default(v)]


def test_no_timing_residue() -> None:
    tokens = ("has-dpd", "dpd-wakeup-sequence", "t-enter-dpd", "t-exit-dpd", "t-reset-")
    tokens += ("ChipEraseTime", "enter-dpd-delay", "exit-dpd-delay")
    assert not [(_where(d), f) for d in RECORDS for f in d["flags"] if f.startswith(tokens)]
    assert not [(_where(d), n) for d in RECORDS for n in d["notes"] if n.startswith(tokens)]


def test_dpd_release_is_not_a_signature_read() -> None:
    # RDPD is 0xab alone; RES, the id read on the same opcode, is never
    # what has-dpd gives. A part waking by a chip select pulse (Zephyr's
    # dpd-wakeup-sequence) states no RDPD.
    for d in RECORDS:
        ops = {o["op"]: o["via"] for o in d["opcodes"]}
        if "RDPD" in ops and "RES" in ops:
            assert ops["RES"] != ops["RDPD"], _where(d)
        if d["via"].get("timings", "").startswith("dpd-wakeup-sequence"):
            assert "RDPD" not in ops, _where(d)
    stated = {d["source"] for d in RECORDS for o in d["opcodes"] if o["op"] in ("DP", "RDPD")}
    assert stated == {"zephyr"}
    assert OPERATIONS["RDPD"].opcode == OPERATIONS["RES"].opcode == 0xAB
    assert OPERATIONS["RDPD"].protocol != OPERATIONS["RES"].protocol


def test_every_stored_timing_has_its_token() -> None:
    for d in RECORDS:
        for key, _, _ in _times(d):
            assert f"timings.{key}" in d["via"] or "timings" in d["via"], _where(d)


def test_a_clock_only_where_dediprog_gives_one() -> None:
    clocked = [d for d in RECORDS if d["max_clock_hz"] is not None]
    assert {d["source"] for d in clocked} == {"dediprog"}
    for d in clocked:
        assert d["max_clock_hz"] % 10**6 == 0, _where(d)
        via = d["via"]["max_clock_hz"]
        assert re.fullmatch(r"(Clock|clock|CLOCK)=\d+ ?MHz", via, re.IGNORECASE), _where(d)
