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
from .record import Record, make

if TYPE_CHECKING:
    from pathlib import Path

SPI_C = "src/flash/nor/spi.c"
JEP106 = "src/helper/jep106.inc"


def device_id_hex(device_id: int) -> str:
    """The RDID bytes, as the chip sends them, of an OpenOCD ``device_id``."""
    cont = (device_id >> 24) & 0xFF
    b = [device_id & 0xFF, (device_id >> 8) & 0xFF, (device_id >> 16) & 0xFF]
    return "7f" * cont + "".join(f"{x:02x}" for x in b)


def extract(root: Path) -> list[Record]:
    raw = (root / SPI_C).read_text()
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
        nums = [cparse.evaluate(a) for a in args[1:]]
        eol = raw.find("\n", end)
        notes = cparse.comments(raw[end:eol])
        if m.group(1) == "FLASH_ID":
            read, qread, pp, erase, chip_erase, dev, page, sector, size = nums
            features = set()
            if qread:
                features.add("quad_read")
            if size > 16 * 1024 * 1024:
                features.add("4byte_addr")
            erase_feature = {4096: "erase_4k", 32768: "erase_32k", 65536: "erase_64k"}
            if sector in erase_feature:
                features.add(erase_feature[sector])
            opcodes = {"read": read, "pp": pp, "erase": erase, "chip_erase": chip_erase}
            if qread:
                opcodes["qread"] = qread
            erasers = [{"opcode": erase, "blocks": [[sector, size // sector]]}] if sector else []
            if chip_erase:
                erasers.append({"opcode": chip_erase, "blocks": [[size, 1]]})
        else:
            read, qread, pp, dev, size = nums
            features = {"no_erase"}
            page = sector = 0
            opcodes = {"read": read, "pp": pp}
            erasers = []
            notes.append("FRAM")
        opcodes = {k: v for k, v in opcodes.items() if v}
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
