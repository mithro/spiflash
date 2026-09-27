"""U-Boot: ``drivers/mtd/spi/spi-nor-ids.c``.

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

from . import cparse
from .record import Record, make

if TYPE_CHECKING:
    from pathlib import Path

IDS = "drivers/mtd/spi/spi-nor-ids.c"
FLAGS_H = "drivers/mtd/spi/sf_internal.h"

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
        raise ValueError(f"{IDS}: no spi_nor_ids[] table")
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
        args = cparse.macro_call(body, "INFO_NAME")
        if args is None:
            return None
        name = cparse.c_string(args[0])
        # INFO_NAME(...) is followed by designated fields with no comma.
        m = re.search(r"\bINFO_NAME\s*\(", body)
        assert m is not None
        rest = body[cparse.matching(body, m.end() - 1) + 1 :]
        fields = cparse.designated(rest)
        if "id" in fields:
            id_bytes = cparse.split_top(fields["id"].strip("{} "))
            data = [cparse.evaluate(b, symbols) for b in id_bytes]
            n = cparse.evaluate(fields.get("id_len", str(len(data))), symbols)
            id_hex = "".join(f"{b:02x}" for b in data[:n]) or None
        sector = cparse.evaluate(fields.get("sector_size", "0"), symbols) or None
        n_sectors = cparse.evaluate(fields.get("n_sectors", "0"), symbols)
        size = sector * n_sectors if sector and n_sectors else None
        page = cparse.evaluate(fields.get("page_size", "256"), symbols)
        flag_expr = fields.get("flags", "0")
    flags = cparse.flag_names(flag_expr)
    features = {_FEATURES[f] for f in flags if f in _FEATURES}
    if "SPI_NOR_NO_FR" not in flags:
        features.add("fast_read")
    if "4byte_opcodes" in features or (size is not None and size > 16 * 1024 * 1024):
        features.add("4byte_addr")
    if sector == 64 * 1024 and "no_erase" not in features:
        features.add("erase_64k")
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
        sector_size=sector,
        features=sorted(features),
        flags=flags,
        notes=notes,
    )
