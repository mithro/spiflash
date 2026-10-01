"""Rockchip: the rkflash driver of Rockchip's U-Boot,
:upstream:`rockchip:drivers/rkflash/sfc_nor.c` (SPI NOR) and
:upstream:`rockchip:drivers/rkflash/sfc_nand.c` (SPI NAND).

Both tables are positional ``struct`` initialisers, the part name in a
comment above each::

    /* GD25Q127C and GD25Q128C/E */
    { 0xc84018, 128, 8, 0x03, 0x02, 0x6B, 0x32, 0x20, 0xD8, 0x0C, 15, 9, 0 },

    /* W25N01GV */
    { 0xEF, 0xAA, 0x21, 4, 0x40, 1, 1024, 0x4C, 18, 0x1, 0,
      { 0x04, 0x14, 0x24, 0xFF }, &sfc_nand_get_ecc_status1 },

``struct flash_info`` (:upstream:`rockchip:drivers/rkflash/sfc_nor.h`) is the
id, the block and sector sizes in 512-byte sectors, the single-line read and
program opcodes, the quad ones, the 4 KiB and block erase opcodes, the
``FEA_*`` feature bits, the size as a power of two of sectors, and the quad
enable bit. ``struct nand_info`` (:upstream:`rockchip:drivers/rkflash/sfc_nand.h`)
is three id bytes, sectors per page, pages per block, planes and blocks per
plane, the feature bits, the size, the ECC strength, whether the part has a
quad enable bit, where the FTL's metadata goes in the spare area, and the
function decoding the ECC status. What each means is what the driver does
with it: ``snor_parse_flash_table()`` and ``snor_write()`` in sfc_nor.c,
``sfc_nand_get_info()`` and ``sfc_nand_init()`` in sfc_nand.c.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from spiflash import derive
from spiflash.model import part_names

from . import cparse
from .ops import Opcodes
from .record import Record, feature_via, make

if TYPE_CHECKING:
    from pathlib import Path

NOR = "drivers/rkflash/sfc_nor.c"
NOR_H = "drivers/rkflash/sfc_nor.h"
NAND = "drivers/rkflash/sfc_nand.c"
NAND_H = "drivers/rkflash/sfc_nand.h"
SFC_H = "drivers/rkflash/sfc.h"  # MID_MACRONIX

SECTOR = 512  # the unit of every size in both tables

# The operation each opcode is, in each slot of struct flash_info.
_READ = {
    0x03: "READ_1_1_1",
    0x0B: "READ_1_1_1_FAST",
    0x13: "READ_1_1_1_4B",
    0x0C: "READ_1_1_1_FAST_4B",
}
_PROG = {0x02: "PP_1_1_1", 0x12: "PP_1_1_1_4B"}
_READ_4 = {0x6B: "READ_1_1_4", 0x6C: "READ_1_1_4_4B"}
_PROG_4 = {0x32: "PP_1_1_4", 0x34: "PP_1_1_4_4B"}
# Sent with the address on four lines too, on Macronix parts only.
_PROG_4_MACRONIX = {0x38: "PP_1_4_4", 0x3E: "PP_1_4_4_4B"}
_SECTOR_ERASE = {0x20: "BE_4K", 0x21: "BE_4K_4B"}
_BLOCK_ERASE = {0xD8: "SE", 0xDC: "SE_4B"}

# The function snor_parse_flash_table() writes the status registers with,
# for each value of the FEA_READ_STATUE_MASK bits of the feature.
_WRITE_STATUS = ("snor_write_status", "snor_write_status1", "snor_write_status2")

# The one name comment written as a pattern, and what is taken from it: the
# XM25QU256B answers 20 70 19 (Dediprog), not this entry's 20 60 19.
_NAMES = {
    "XM25QH(QU)256B": (
        "XM25QH256B",
        "the comment also names the XM25QU256B, which answers another id",
    )
}

# A part number: a capital, then at least one digit.
_PART = re.compile(r"[A-Z][\w/-]*\d[\w/-]*")


def _name(comment: str) -> tuple[str | None, list[str]]:
    """The part names in an entry's comment, as one ``/``-separated name,
    and the rest of the comment. A comment can name several parts
    (``"MT29F2G01ABA, XT26G02E, F50L2G41XA"``, ``"GD25Q127C and
    GD25Q128C/E"``) and go on in prose (``"GD5F4GQ6RExxG 1*4096"``)."""
    words = comment.replace(",", " ").split()
    parts: list[str] = []
    notes = []
    while words and (words[0] in _NAMES or words[0] == "and" or _PART.fullmatch(words[0])):
        word = words.pop(0)
        if word in _NAMES:
            word, note = _NAMES[word]
            notes.append(note)
        if word != "and":
            parts += part_names(word)
    rest = " ".join(words)
    return "/".join(parts) or None, [rest, *notes] if rest else notes


def _entries(
    raw: str, rel: str, struct: str, table: str
) -> list[tuple[int, list[str], str, list[str]]]:
    """Each entry of ``struct <struct> <table>[]``: its line, its fields,
    its part name and any further notes."""
    text = cparse.strip_comments(raw)
    body = cparse.array_body(text, rf"struct\s+{struct}\s+{table}\s*\[\s*\]")
    if body is None:
        msg = f"{rel}: no {table}[]"
        raise ValueError(msg)
    out = []
    end = body.offset
    for entry in cparse.braced_items(body.body, body.offset):
        line = cparse.line_of(raw, entry.offset)
        found = cparse.comments(raw[end : entry.offset - 1])
        end = entry.offset + len(entry.body) + 1
        if len(found) != 1:
            msg = f"{rel}:{line}: {len(found)} comments before the entry, not 1"
            raise ValueError(msg)
        name, notes = _name(found[0])
        if name is None:
            msg = f"{rel}:{line}: no part name in {found[0]!r}"
            raise ValueError(msg)
        out.append((line, cparse.split_top(entry.body), name, notes))
    return out


def _op(slot: str, table: dict[int, str], opcode: int) -> str:
    """The operation ``opcode`` is in ``slot``."""
    if opcode not in table:
        msg = f"unknown {slot} 0x{opcode:02x}"
        raise ValueError(msg)
    return table[opcode]


def _bits(value: int, allowed: int, symbols: dict[str, str | int]) -> list[str]:
    """The ``FEA_*`` bits set in ``value``, of the ``allowed`` ones the
    driver reads."""
    if value & ~allowed:
        msg = f"unknown feature bits 0x{value & ~allowed:02x}"
        raise ValueError(msg)
    return cparse.bit_names(str(value), symbols, "FEA_")


def _values(fields: list[str], count: int) -> list[int]:
    if len(fields) != count:
        msg = f"{len(fields)} fields, not {count}"
        raise ValueError(msg)
    return [cparse.evaluate(f) for f in fields]


def extract_nor(root: Path) -> list[Record]:
    raw = (root / NOR).read_text()
    symbols: dict[str, str | int] = {
        **cparse.defines(cparse.strip_comments((root / SFC_H).read_text())),
        **cparse.defines(cparse.strip_comments((root / NOR_H).read_text())),
    }
    records = []
    first: dict[str, int] = {}
    for line, fields, name, notes in _entries(raw, NOR, "flash_info", "spi_flash_tbl"):
        try:
            rec = _nor_record(_values(fields, 13), symbols)
        except ValueError as e:
            msg = f"{NOR}:{line}: {e}"
            raise ValueError(msg) from e
        # snor_get_flash_info() takes the first entry with the id.
        unused = [_unused(first[rec["id"]])] if rec["id"] in first else []
        first.setdefault(rec["id"], line)
        rec["notes"] = [*notes, *unused, *rec["notes"]]
        records.append(make("rockchip", NOR, line, name, **rec))
    return records


def _unused(line: int) -> str:
    return f"never used: the driver matches the entry on line {line} first"


def _nor_record(values: list[int], symbols: dict[str, str | int]) -> dict[str, Any]:
    """The record fields of one ``struct flash_info``."""
    chip_id, block, sector, read, prog, read_4, prog_4, sec_erase, blk_erase = values[:9]
    feature, density, qe_bits, reserved = values[9:]
    # snor_write() erases 8 sectors (4 KiB) with sector_erase_cmd.
    if sector != 8 or reserved:
        msg = f"sector_size {sector}, reserved2 {reserved}"
        raise ValueError(msg)
    mask = cparse.evaluate("FEA_READ_STATUE_MASK", symbols)
    if feature & mask >= len(_WRITE_STATUS):
        msg = f"no status register write for feature 0x{feature:02x}"
        raise ValueError(msg)
    allowed = cparse.evaluate(
        "FEA_4BIT_READ | FEA_4BIT_PROG | FEA_4BYTE_ADDR | FEA_4BYTE_ADDR_MODE", symbols
    )
    bits = _bits(feature & ~mask, allowed, symbols)

    ops = Opcodes(symbols)
    ops.add("RDID", "id match (snor_get_flash_info)", "CMD_READ_JEDECID")
    ops.add(_op("read_cmd", _READ, read), "read_cmd", value=read)
    ops.add(_op("prog_cmd", _PROG, prog), "prog_cmd", value=prog)
    quad_read = _op("read_cmd_4", _READ_4, read_4)
    if "FEA_4BIT_READ" in bits:
        ops.add(quad_read, "read_cmd_4 (FEA_4BIT_READ)", value=read_4)
    quad_prog = _op("prog_cmd_4", {**_PROG_4, **_PROG_4_MACRONIX}, prog_4)
    notes = []
    if "FEA_4BIT_PROG" in bits:
        if prog_4 in _PROG_4 or chip_id >> 16 == cparse.evaluate("MID_MACRONIX", symbols):
            ops.add(quad_prog, "prog_cmd_4 (FEA_4BIT_PROG)", value=prog_4)
        else:
            notes.append(
                f"prog_cmd_4 0x{prog_4:02x} left out: the driver sends it with the address "
                "on four lines on Macronix parts only, and on one line here"
            )
    ops.add(_op("sector_erase_cmd", _SECTOR_ERASE, sec_erase), "sector_erase_cmd", value=sec_erase)
    ops.add(_op("block_erase_cmd", _BLOCK_ERASE, blk_erase), "block_erase_cmd", value=blk_erase)
    if "FEA_4BYTE_ADDR_MODE" in bits:
        ops.add("EN4B", "FEA_4BYTE_ADDR_MODE", "CMD_ENTER_4BYTE_MODE")

    quad_enable, via, notes_qe = _nor_quad_enable(chip_id, qe_bits, feature & mask, bits, ops)
    notes += notes_qe
    size = SECTOR << density
    claims = [
        (feat, bit)
        for bit, feat in (
            ("FEA_4BIT_READ", "quad_read"),
            ("FEA_4BIT_PROG", "quad_pp"),
            ("FEA_4BYTE_ADDR", "4byte_addr"),
        )
        if bit in bits
    ]
    return {
        "id": f"{chip_id:06x}",
        "size": size,
        "page_size": cparse.evaluate("NOR_PAGE_SIZE", symbols),
        "erasers": [
            {"opcode": sec_erase, "blocks": [[4096, size // 4096]]},
            {"opcode": blk_erase, "blocks": [[block * SECTOR, size // (block * SECTOR)]]},
        ],
        "features": {feat for feat, _ in claims},
        "flags": [*bits, f"write_status={_WRITE_STATUS[feature & mask]}", f"QE_bits={qe_bits}"],
        "via": feature_via(claims) | via,
        "quad_enable": quad_enable,
        "opcodes": ops.to_json(),
        "notes": notes,
    }


# The status registers snor_read_status() reads (0x05, 0x35, 0x15), by
# index: what QE_bits >> 3 is.
_STATUS_REGISTERS = ("sr1", "sr2", "sr3")

# The sfc_nor.h name of each status register operation's opcode.
_STATUS_COMMANDS = {
    "RDSR2": "CMD_READ_STATUS2",
    "RDSR3": "CMD_READ_STATUS3",
    "WRSR2": "CMD_WRITE_STATUS2",
    "WRSR3": "CMD_WRITE_STATUS3",
    "WRSR_16": "CMD_WRITE_STATUS",
}

#: The entries whose QE bit snor_flash_info_adjust() changes: GD25Q256C
#: (SFDP 1.0) has it at SR1 bit 6, the table's; the D and E (a BFPT of
#: minor revision 6) at SR2 bit 1. Linux's gd25q256_post_bfpt() splits
#: them alike.
_QE_BY_REVISION = {
    0xC84019: (
        "no quad enable bit: snor_flash_info_adjust() makes QE_bits 9 (SR2 bit 1) "
        "where the BFPT's minor revision is 6 (the GD25Q256D and E); the table's 6 "
        "(SR1 bit 6) is the GD25Q256C's"
    )
}


def _nor_quad_enable(
    chip_id: int, qe_bits: int, write_status: int, bits: list[str], ops: Opcodes
) -> tuple[str | dict[str, object] | None, dict[str, str], list[str]]:
    """The quad enable bit of a ``struct flash_info`` (its ``via``, and any
    note), and the operations setting it, added to ``ops``.

    ``QE_bits`` is register ``n >> 3`` of those snor_read_status() reads
    (0x05, 0x35, 0x15), bit ``n & 7``; 0 is no bit: the driver reads with
    four lines (``FEA_4BIT_READ``) without setting one (snor_init). To
    set it, snor_enable_QE() reads its register, then the entry's
    ``write_status`` function writes it: ``snor_write_status`` with the
    register's own command (0x01, 0x31 or 0x11, one byte);
    ``snor_write_status1`` with a 2-byte 0x01 (SR1, SR2), reading SR2;
    ``snor_write_status2`` (Macronix) with a 2-byte 0x01 (SR1, then the
    configuration register, read with 0x15)."""
    if chip_id in _QE_BY_REVISION:
        return None, {}, [_QE_BY_REVISION[chip_id]]
    via = {"quad_enable": f"QE_bits={qe_bits}"}
    if not qe_bits:
        return ("none", via, []) if "FEA_4BIT_READ" in bits else (None, {}, [])
    reg = qe_bits >> 3
    if reg >= len(_STATUS_REGISTERS):
        msg = f"QE_bits {qe_bits}: no status register {reg + 1}"
        raise ValueError(msg)
    if "FEA_4BIT_READ" in bits:  # snor_enable_QE() is called for it only
        writer = f"write_status={_WRITE_STATUS[write_status]}"
        sent: tuple[str | None, ...] = ((None, "RDSR2", "RDSR3")[reg],)
        if write_status == 0:
            sent += ((None, "WRSR2", "WRSR3")[reg],)
        elif write_status == 1:
            sent += ("RDSR2", "WRSR_16")
        else:
            sent += ("RDSR3" if reg == 0 else None, "WRSR_16")
        for op in sent:
            if op is not None:
                ops.add(op, writer, _STATUS_COMMANDS[op])
    return {"register": _STATUS_REGISTERS[reg], "bit": qe_bits & 7}, via, []


def extract_nand(root: Path) -> list[Record]:
    raw = (root / NAND).read_text()
    symbols: dict[str, str | int] = dict(
        cparse.defines(cparse.strip_comments((root / NAND_H).read_text()))
    )
    records = []
    seen: list[tuple[int, int, int, int]] = []
    for line, fields, name, notes in _entries(raw, NAND, "nand_info", "spi_nand_tbl"):
        try:
            rec, key = _nand_record(fields, symbols)
        except ValueError as e:
            msg = f"{NAND}:{line}: {e}"
            raise ValueError(msg) from e
        # sfc_nand_get_info() takes the first entry that fits, comparing the
        # third id byte only where the entry's is not 0.
        id0, id1, id2 = key
        unused = [_unused(n) for a, b, c, n in seen if (a, b) == (id0, id1) and c in (0, id2)][:1]
        seen.append((*key, line))
        rec["notes"] = [*notes, *unused]
        records.append(make("rockchip", NAND, line, name, **rec))
    return records


#: The commands sfc_nand.c sends every part, whatever its entry says: its
#: defaults (the block erase is the eraser's). Reading from cache with
#: 0x03 and loading with 0x02 are what sfc_nand_init() sets up before
#: looking at the feature bits.
NAND_DEFAULTS = {
    "NAND_READ_CACHE_1_1_1": "every part: page_read_cmd = 0x03",
    "NAND_PROGRAM_LOAD_1_1_1": "every part: page_prog_cmd = 0x02",
    "NAND_PAGE_READ": "every part (sfc_nand_read)",
    "NAND_PROGRAM_EXECUTE": "every part (sfc_nand_prog_page_raw)",
    "NAND_GET_FEATURE": "every part (sfc_nand_read_feature)",
    "NAND_SET_FEATURE": "every part (sfc_nand_write_feature)",
}


def _nand_record(
    fields: list[str], symbols: dict[str, str | int]
) -> tuple[dict[str, Any], tuple[int, int, int]]:
    """The record fields of one ``struct nand_info``, and its id bytes."""
    if len(fields) != 13:
        msg = f"{len(fields)} fields, not 13"
        raise ValueError(msg)
    id0, id1, id2, spp, ppb, planes, blocks, feature, density, ecc, qe = (
        cparse.evaluate(f) for f in fields[:11]
    )
    meta, decoder = " ".join(fields[11].split()), fields[12].lstrip("&").strip()
    if not re.fullmatch(r"sfc_nand_get_ecc_status\d+", decoder):
        msg = f"unknown ECC status decoder {decoder!r}"
        raise ValueError(msg)
    # The page buffer holds 8 sectors; one plane bit selects between 2 planes.
    if spp not in (4, 8) or planes not in (1, 2):
        msg = f"{spp} sectors per page, {planes} planes"
        raise ValueError(msg)
    page = spp * SECTOR
    size = page * ppb * planes * blocks
    if SECTOR << density != size:
        msg = f"density {density}, but the geometry is {size} bytes"
        raise ValueError(msg)
    allowed = cparse.evaluate("FEA_4BIT_READ | FEA_4BIT_PROG | FEA_SOFT_QOP_BIT", symbols)
    bits = _bits(feature, allowed, symbols)
    # sfc_nand_init() reads from cache with 0x6b for FEA_4BIT_READ, and
    # loads with 0x32 for FEA_4BIT_PROG (when it reads on four lines).
    ops = Opcodes()
    for op, bit, cmd in (
        ("NAND_READ_CACHE_1_1_4", "FEA_4BIT_READ", "page_read_cmd = 0x6b"),
        ("NAND_PROGRAM_LOAD_1_1_4", "FEA_4BIT_PROG", "page_prog_cmd = 0x32"),
    ):
        if bit in bits:
            ops.add(op, f"{bit}: {cmd}")
    for op, via in NAND_DEFAULTS.items():
        ops.add(op, via, assumed=True)
    # sfc_nand_read_id() sends 0x9f and an address byte. An id2 of 0 is not
    # compared, so not part of the id. One that repeats the manufacturer byte
    # or is 0x7f is not a device byte either, but what follows one: the
    # GD5F1GQ5REYIG answers c8 41 (then c8 again, DS-00889 Table 8-1) and
    # the F50L2G41KA c8 41 7f, and the entries tell them apart by it.
    device = [id0, id1, id2] if id2 and id2 not in (id0, 0x7F) else [id0, id1]
    # has_qe_bits: sfc_nand_enable_QE() sets bit 0 of feature 0xb0 before
    # quad reads (FEA_4BIT_READ); without it, sfc_nand_init() reads with
    # four lines setting nothing, as NOR's QE_bits 0 does.
    quad_enable: str | dict[str, object] | None = None
    if qe:
        quad_enable = {"register": "nand-b0", "bit": 0}
    elif "FEA_4BIT_READ" in bits:
        quad_enable = "none"
    return {
        "type": "nand",
        "id": bytes(device).hex(),
        "ext_id": bytes([id2]).hex() if len(device) == 2 and id2 else None,
        "id_method": "rdid_opcode_addr",
        "size": size,
        "page_size": page,
        "erasers": [derive.block_eraser(0xD8, page * ppb, size).to_json()],
        "planes": planes,
        # max_ecc_bits, which the driver passes to its FTL with no step.
        "ecc": {"strength_bits": ecc},
        "via": {"quad_enable": f"has_qe_bits={qe}"} if quad_enable else {},
        "quad_enable": quad_enable,
        "opcodes": ops.to_json(),
        "flags": [*bits, f"has_qe_bits={qe}", f"meta={meta}", f"ecc_status={decoder}"],
    }, (id0, id1, id2)


def extract(root: Path) -> list[Record]:
    return extract_nor(root) + extract_nand(root)
