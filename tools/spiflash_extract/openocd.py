"""OpenOCD: ``src/flash/nor/spi.c`` (the shared SPI flash table) and
``src/helper/jep106.inc`` (the JEP106 manufacturer list).

``FLASH_ID(name, read_cmd, qread_cmd, pprog_cmd, erase_cmd, chip_erase_cmd,
device_id, pagesize, sectorsize, size)`` and, for FRAM, ``FRAM_ID(name,
read_cmd, qread_cmd, pprog_cmd, device_id, size)``. ``device_id`` holds the
RDID bytes little-endian, ``0xNNZZYYXX``: XX the manufacturer, YY the memory
type, ZZ the capacity, and NN the number of 0x7f continuation codes before XX.
Names are ``"<vendor abbreviation> <part>"`` (``"win w25q128fv/jv"``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from . import cparse
from .ops import ERASE_BY_OPCODE, Opcodes
from .record import Record, make

if TYPE_CHECKING:
    from pathlib import Path

SPI_C = "src/flash/nor/spi.c"
SPI_H = "src/flash/nor/spi.h"  # SPIFLASH_READ_ID

# OpenOCD leaves the family prefix off some part numbers ("mac 25l12845" is
# the MX25L12845, "adesto xp032" the ATXP032). LiteSPI's generator patched
# OpenOCD's table to put them back; this does the same to the names.
_PART_PREFIX = {"mac": "mx", "atmel": "at", "adesto": "at"}


def part_name(vendor: str, name: str) -> str:
    prefix = _PART_PREFIX.get(vendor)
    if prefix and not name.lower().startswith(prefix):
        return prefix + name
    return name
JEP106 = "src/helper/jep106.inc"


def device_id_hex(device_id: int) -> str:
    """The RDID bytes, as the chip sends them, of an OpenOCD ``device_id``."""
    cont = (device_id >> 24) & 0xFF
    b = [device_id & 0xFF, (device_id >> 8) & 0xFF, (device_id >> 16) & 0xFF]
    return "7f" * cont + "".join(f"{x:02x}" for x in b)


def extract(root: Path) -> list[Record]:
    raw = (root / SPI_C).read_text()
    symbols: dict[str, str | int] = dict(
        cparse.defines(cparse.strip_comments((root / SPI_H).read_text()))
    )
    text = cparse.drop_preprocessor(cparse.strip_comments(raw))
    records = []
    for m in re.finditer(r"\b(FLASH_ID|FRAM_ID)\s*\(", text):
        start = m.end() - 1
        end = cparse.matching(text, start)
        args = cparse.split_top(text[start + 1 : end])
        if args[0] == "NULL":
            continue  # the table's terminator
        full = cparse.c_string(args[0])
        vendor, _, name = full.partition(" ")
        name = part_name(vendor, name)
        nums = [cparse.evaluate(a) for a in args[1:]]
        eol = raw.find("\n", end)
        notes = cparse.comments(raw[end:eol])
        ops = Opcodes(symbols)
        ops.add("RDID", "probe (SPIFLASH_READ_ID)", "SPIFLASH_READ_ID")
        if m.group(1) == "FLASH_ID":
            read, qread, pp, erase, chip_erase, dev, page, sector, size = nums
            features = set()
            if size > 16 * 1024 * 1024:
                features.add("4byte_addr")
            erase_feature = {4096: "erase_4k", 32768: "erase_32k", 65536: "erase_64k"}
            if sector in erase_feature:
                features.add(erase_feature[sector])
            _field(ops, notes, "read_cmd", read, _READ)
            _field(ops, notes, "qread_cmd", qread, _QREAD)
            _field(ops, notes, "pprog_cmd", pp, _PROGRAM)
            _field(ops, notes, "erase_cmd", erase, ERASE_BY_OPCODE)
            _field(ops, notes, "chip_erase_cmd", chip_erase, ERASE_BY_OPCODE)
            if qread in _QUAD:
                features.add("quad_read")
            erasers = [{"opcode": erase, "blocks": [[sector, size // sector]]}] if sector else []
            if chip_erase:
                erasers.append({"opcode": chip_erase, "blocks": [[size, 1]]})
        else:
            read, qread, pp, dev, size = nums
            features = {"no_erase"}
            page = sector = 0
            _field(ops, notes, "read_cmd", read, _READ)
            _field(ops, notes, "qread_cmd", qread, _QREAD)
            _field(ops, notes, "pprog_cmd", pp, _PROGRAM)
            erasers = []
            notes.append("FRAM")
        opcodes = ops.to_json()
        records.append(
            make(
                "openocd",
                SPI_C,
                cparse.line_of(raw, m.start()),
                name,
                vendor=vendor,
                id=device_id_hex(dev),
                size=size,
                page_size=page or None,
                sector_size=sector or None,
                erasers=erasers or None,
                features=sorted(features),
                opcodes=opcodes,
                notes=notes,
            )
        )
    return records


_READ = {0x03: "READ_1_1_1", 0x13: "READ_1_1_1_4B", 0x0B: "READ_1_1_1_FAST"}
# qread_cmd: the fastest read the part has, which is not always a quad one.
_QREAD = {
    0x0B: "READ_1_1_1_FAST",
    0x0C: "READ_1_1_1_FAST_4B",
    0x3B: "READ_1_1_2",
    0xBB: "READ_1_2_2",
    0x6B: "READ_1_1_4",
    0xEB: "READ_1_4_4",
    0x6C: "READ_1_1_4_4B",
    0xEC: "READ_1_4_4_4B",
}
_QUAD = {0x6B, 0xEB, 0x6C, 0xEC}
_PROGRAM = {0x02: "PP_1_1_1", 0x12: "PP_1_1_1_4B"}


def _field(ops: Opcodes, notes: list[str], field: str, value: int, table: dict[int, str]) -> None:
    """Name the opcode in one of OpenOCD's fields. 0 means the part has
    none; a byte this table does not know is kept as a note, not guessed."""
    if not value:
        return
    op = table.get(value)
    if op is None:
        notes.append(f"OpenOCD's {field} is 0x{value:02x}, which is not a known operation")
        return
    ops.add(op, field, value=value)


def manufacturers(root: Path) -> list[dict[str, object]]:
    """The JEP106 list: ``[{"bank": 0, "id": 0x01, "name": "AMD"}, ...]``.

    ``bank`` counts from 0 (the number of 0x7f continuation codes before the
    id); ``id`` is the 7-bit code with its odd-parity bit, as a chip sends it.
    """
    text = cparse.strip_comments((root / JEP106).read_text())
    out = []
    for m in re.finditer(r'\[(\d+)\]\s*\[\s*(0x[0-9a-fA-F]+)\s*-\s*1\s*\]\s*=\s*"([^"]*)"', text):
        bank, code, name = int(m.group(1)), int(m.group(2), 16), m.group(3)
        out.append({"bank": bank, "id": _with_parity(code), "name": name})
    return out


def _with_parity(code: int) -> int:
    """JEP106 codes are 7 bits plus an odd-parity bit in bit 7."""
    return code | (0 if bin(code).count("1") % 2 else 0x80)
