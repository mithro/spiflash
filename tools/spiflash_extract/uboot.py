"""U-Boot: :upstream:`u-boot:drivers/mtd/spi/spi-nor-ids.c`.

U-Boot kept Linux's pre-6.8 table format::

    #ifdef CONFIG_SPI_FLASH_WINBOND
        { INFO("w25q128", 0xef4018, 0, 64 * 1024, 256, SECT_4K | ...) },

``INFO(name, jedec_id, ext_id, sector_size, n_sectors, flags)`` (a two-byte
``ext_id``), ``INFO6`` (the same with a three-byte one), and ``INFO_NAME(name)``
followed by designated fields for the odd parts (FRAM).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from spiflash import derive

from . import cparse
from .ops import Opcodes, add_4b_variants, add_spinor
from .record import Record, feature_via, make

if TYPE_CHECKING:
    from pathlib import Path

IDS = "drivers/mtd/spi/spi-nor-ids.c"
FLAGS_H = "drivers/mtd/spi/sf_internal.h"
SPINOR_H = "include/linux/mtd/spi-nor.h"  # the SPINOR_OP_* opcodes

_FEATURES = {
    "SECT_4K": "erase_4k",
    "SECT_4K_PMC": "erase_4k",
    "SPI_NOR_DUAL_READ": "dual_read",
    "SPI_NOR_QUAD_READ": "quad_read",
    "SPI_NOR_OCTAL_READ": "octal_read",
    "SPI_NOR_OCTAL_DTR_READ": "octal_dtr_read",
    "SPI_NOR_HAS_LOCK": "lock",
    "SPI_NOR_HAS_SST26LOCK": "lock",
    "SPI_NOR_4B_OPCODES": "4byte_opcodes",
    "SPI_NOR_NO_ERASE": "no_erase",
}


def _vendor_at(markers: list[tuple[int, str]], offset: int) -> str | None:
    vendor = None
    for pos, name in markers:
        if pos > offset:
            break
        vendor = name
    return vendor


def extract(root: Path) -> list[Record]:
    raw = (root / IDS).read_text()
    stripped = cparse.strip_comments(raw)
    symbols: dict[str, str | int] = {
        **cparse.defines(cparse.strip_comments((root / SPINOR_H).read_text())),
        **cparse.defines(cparse.strip_comments((root / FLAGS_H).read_text())),
        **cparse.defines(stripped),
    }
    # Which CONFIG_SPI_FLASH_<VENDOR> block each entry sits in.
    markers = [
        (m.start(), m.group(1).lower())
        for m in re.finditer(r"(?m)^\s*#\s*if\w*\s.*?CONFIG_SPI_(?:FLASH|FRAM)_(\w+)", stripped)
    ]
    text = cparse.drop_preprocessor(stripped)
    table = cparse.array_body(text, r"struct\s+flash_info\s+spi_nor_ids\s*\[\s*\]")
    if table is None:
        msg = f"{IDS}: no spi_nor_ids[] table"
        raise ValueError(msg)
    records = []
    for entry in cparse.braced_items(table.body, table.offset):
        rec = _record(entry, raw, symbols, _vendor_at(markers, entry.offset))
        if rec is not None:
            records.append(rec)
    return records


def _record(
    entry: cparse.Block, raw: str, symbols: dict[str, str | int], vendor: str | None
) -> Record | None:
    body = entry.body
    notes = cparse.comments(raw[entry.offset : entry.offset + len(body)])
    id_hex = ext = None
    sector = page = size = None
    flag_expr = "0"
    for macro, ext_bytes in (("INFO6", 3), ("INFO", 2)):
        args = cparse.macro_call(body, macro)
        if args is None:
            continue
        name = cparse.c_string(args[0])
        jedec = cparse.evaluate(args[1], symbols)
        ext_val = cparse.evaluate(args[2], symbols)
        sector = cparse.evaluate(args[3], symbols)
        size = sector * cparse.evaluate(args[4], symbols)
        flag_expr = args[5] if len(args) > 5 else "0"
        page = 256
        id_hex = f"{jedec:06x}" if jedec else None
        ext = f"{ext_val:0{2 * ext_bytes}x}" if ext_val else None
        break
    else:
        m = re.search(r"\bINFO_NAME\s*\(", body)
        if m is None:
            return None
        end = cparse.matching(body, m.end() - 1)
        name = cparse.c_string(body[m.end() : end])
        # INFO_NAME(...) is followed by designated fields with no comma.
        fields = cparse.designated(body[end + 1 :])
        if "id" in fields:
            id_bytes = cparse.split_top(fields["id"].strip("{} "))
            data = [cparse.evaluate(b, symbols) for b in id_bytes]
            n = cparse.evaluate(fields.get("id_len", str(len(data))), symbols)
            id_hex = bytes(data[:n]).hex() or None
        sector = cparse.evaluate(fields.get("sector_size", "0"), symbols) or None
        n_sectors = cparse.evaluate(fields.get("n_sectors", "0"), symbols)
        size = sector * n_sectors if sector and n_sectors else None
        page = cparse.evaluate(fields.get("page_size", "256"), symbols)
        flag_expr = fields.get("flags", "0")
    flags = cparse.flag_names(flag_expr)
    claims = [(_FEATURES[f], f) for f in flags if f in _FEATURES]
    features = {feat for feat, _ in claims}
    if "4byte_opcodes" in features:
        features.add("4byte_addr")
    erasers = []
    if sector and size and "no_erase" not in features:
        for flag, opcode in (("SECT_4K", 0x20), ("SECT_4K_PMC", 0xD7)):
            if flag in flags:
                erasers.append(derive.block_eraser(opcode, 4096, size).to_json())
        erasers.append(derive.block_eraser(0xD8, sector, size).to_json())
    return make(
        "u-boot",
        IDS,
        cparse.line_of(raw, entry.offset),
        name,
        vendor=vendor,
        id=id_hex,
        ext_id=ext,
        id_method="rdid" if id_hex else None,
        size=size,
        page_size=page,
        erasers=erasers or None,
        features=features,
        flags=flags,
        via=feature_via(claims),
        opcodes=_opcodes(flags, symbols, features, has_id=id_hex is not None),
        notes=notes,
    )


# flags -> the operation drivers/mtd/spi/spi-nor-core.c sets up for it.
_FLAG_OPS = {
    "SPI_NOR_DUAL_READ": ["READ_1_1_2"],
    "SPI_NOR_QUAD_READ": ["READ_1_1_4"],
    "SPI_NOR_OCTAL_READ": ["READ_1_1_8"],
    "SECT_4K": ["BE_4K"],
    "SECT_4K_PMC": ["BE_4K_PMC"],
    "SST_WRITE": ["AAI_WP", "BP"],  # sst_write(): AAI words, a byte at the ends
    "USE_FSR": ["RDFSR"],
    "USE_CLSR": ["CLSR"],
}


def _opcodes(
    flags: list[str], symbols: dict[str, str | int], features: set[str], *, has_id: bool
) -> list[dict[str, object]]:
    """The operations U-Boot's spi-nor-core.c sets up for an entry: read,
    fast read unless SPI_NOR_NO_FR, page program, the erase opcode (SECT_4K,
    SECT_4K_PMC, else sector erase), chip erase unless NO_CHIP_ERASE, the
    flag-implied operations above, and the 4-byte forms for
    SPI_NOR_4B_OPCODES.

    Read, fast read, page program and chip erase are driver defaults
    (assumed): U-Boot sets them up for every part an entry does not opt
    out of. So is the quad page program spi_nor_init_params() adds for every
    ``SPI_NOR_QUAD_READ`` part (it says nothing of the part's own quad
    program), and the 4-byte form of each."""
    ops = Opcodes(symbols)
    if has_id:
        add_spinor(ops, "RDID", "JEDEC id match (spi_nor_read_id)")
    add_spinor(ops, "READ_1_1_1", "default (spi_nor_init_params)", assumed=True)
    if "SPI_NOR_NO_FR" not in flags:
        add_spinor(ops, "READ_1_1_1_FAST", "default unless SPI_NOR_NO_FR", assumed=True)
    add_spinor(ops, "PP_1_1_1", "default (spi_nor_init_params)", assumed=True)
    for flag in flags:
        for op in _FLAG_OPS.get(flag, []):
            add_spinor(ops, op, flag)
    if "SPI_NOR_QUAD_READ" in flags:
        quad_pp = "SPI_NOR_QUAD_READ: default (spi_nor_init_params)"
        add_spinor(ops, "PP_1_1_4", quad_pp, assumed=True)
    if "no_erase" not in features:
        # The eraser gives it (_record); added for its 4-byte form.
        add_spinor(ops, "SE", "sector erase (the INFO sector size)")
        if "NO_CHIP_ERASE" not in flags:
            add_spinor(ops, "CHIP_ERASE", "default unless NO_CHIP_ERASE", assumed=True)
    if "SPI_NOR_4B_OPCODES" in flags:
        add_4b_variants(ops, "SPI_NOR_4B_OPCODES")
    return ops.to_json()
