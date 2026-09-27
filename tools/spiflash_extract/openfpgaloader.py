"""openFPGALoader: ``src/spiFlashdb.hpp``.

A C++ ``std::map<uint32_t, flash_t>`` keyed by the three RDID bytes::

    {0xef4018, {
        .manufacturer = "Winbond",
        .model = "W25Q128",
        .nr_sector = 256,        // 64 KiB sectors
        .sector_erase = true,    // 64 KiB erase
        .subsector_erase = true, // 4 KiB erase
        ...
        .quad_register = STATR,
        .quad_mask = (1 << 9),
    }},
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import cparse
from .record import Record, make

if TYPE_CHECKING:
    from pathlib import Path

DB = "src/spiFlashdb.hpp"


def extract(root: Path) -> list[Record]:
    raw = (root / DB).read_text()
    text = cparse.drop_preprocessor(cparse.strip_comments(raw))
    table = cparse.array_body(text, r"std::map\s*<\s*uint32_t\s*,\s*flash_t\s*>\s*flash_list")
    if table is None:
        raise ValueError(f"{DB}: no flash_list map")
    symbols: dict[str, str | int] = {
        "STATR": 0,
        "FUNCR": 1,
        "CONFR": 2,
        "NVCONFR": 3,
        "NONER": 99,
        "true": 1,
        "false": 0,
    }
    records = []
    for entry in cparse.braced_items(table.body, table.offset):
        key, value = cparse.split_top(entry.body)
        fields = cparse.designated(value.strip()[1:-1])
        nr_sector = cparse.evaluate(fields["nr_sector"], symbols)
        features: set[str] = set()
        if cparse.evaluate(fields.get("sector_erase", "false"), symbols):
            features.add("erase_64k")
        if cparse.evaluate(fields.get("subsector_erase", "false"), symbols):
            features.add("erase_4k")
        if cparse.evaluate(fields.get("bp_len", "0"), symbols):
            features.add("lock")
        quad_reg = fields.get("quad_register", "NONER").strip()
        if quad_reg != "NONER":
            features.add("quad_read")
        size = nr_sector * 64 * 1024
        if size > 16 * 1024 * 1024:
            features.add("4byte_addr")
        flags = [
            f"{k}={v.strip()}"
            for k, v in fields.items()
            if k not in ("manufacturer", "model", "nr_sector")
        ]
        records.append(
            make(
                "openfpgaloader",
                DB,
                cparse.line_of(raw, entry.offset),
                cparse.c_string(fields["model"]),
                vendor=cparse.c_string(fields["manufacturer"]),
                id=f"{cparse.evaluate(key, symbols):06x}",
                size=size,
                sector_size=64 * 1024,
                features=sorted(features),
                flags=flags,
                notes=cparse.comments(raw[entry.offset : entry.offset + len(entry.body)]),
            )
        )
    return records
