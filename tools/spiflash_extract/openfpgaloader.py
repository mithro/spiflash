"""openFPGALoader: :upstream:`openfpgaloader:src/spiFlashdb.hpp`.

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

from spiflash import derive

from . import cparse
from .ops import Opcodes
from .record import Record, feature_via, make

if TYPE_CHECKING:
    from pathlib import Path

DB = "src/spiFlashdb.hpp"
FLASH_CPP = "src/spiFlash.cpp"  # the FLASH_* opcodes it sends


def extract(root: Path) -> list[Record]:
    raw = (root / DB).read_text()
    flash_defs: dict[str, str | int] = dict(
        cparse.defines(cparse.strip_comments((root / FLASH_CPP).read_text()))
    )
    text = cparse.drop_preprocessor(cparse.strip_comments(raw))
    table = cparse.array_body(text, r"std::map\s*<\s*uint32_t\s*,\s*flash_t\s*>\s*flash_list")
    if table is None:
        msg = f"{DB}: no flash_list map"
        raise ValueError(msg)
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
        size = nr_sector * 64 * 1024
        features: set[str] = set()
        # The table's sectors are 64 KiB, its subsectors 4 KiB: the blocks
        # block64_erase() (0xd8) and sector_erase() (0x20) erase.
        erasers, via = [], {}
        for field, opcode, block in (
            ("subsector_erase", 0x20, 4096),
            ("sector_erase", 0xD8, 64 << 10),
        ):
            if cparse.evaluate(fields.get(field, "false"), symbols):
                erasers.append(derive.block_eraser(opcode, block, size).to_json())
                via[f"erasers:0x{opcode:02x}"] = f"{field}={fields[field].strip()}"
        claims = []
        if cparse.evaluate(fields.get("bp_len", "0"), symbols):
            features.add("lock")
            claims.append(("lock", f"bp_len={fields['bp_len'].strip()}"))
        quad_reg = fields.get("quad_register", "NONER").strip()
        if quad_reg != "NONER":
            features.add("quad_read")
            claims.append(("quad_read", f"quad_register={quad_reg}"))
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
                erasers=erasers or None,
                features=features,
                flags=flags,
                via={**feature_via(claims), **via},
                opcodes=_opcodes({e["opcode"] for e in erasers}, size, flash_defs),
                notes=cparse.comments(raw[entry.offset : entry.offset + len(entry.body)]),
            )
        )
    return records


def _opcodes(
    erasers: set[int], size: int, symbols: dict[str, str | int]
) -> list[dict[str, object]]:
    """What openFPGALoader's src/spiFlash.cpp sends to a part: read and page
    program for every part (driver defaults: assumed); its sector_erase()
    (0x20) for a table subsector_erase and block64_erase() (0xd8) for
    sector_erase (the erasers give those, but the FLASH_* defines check
    them); and above 16 MiB the 4-byte form of each. The values are
    spiFlash.cpp's FLASH_* defines."""
    big = size > 16 * 1024 * 1024
    ops = Opcodes(symbols)
    ops.add("RDID", "JEDEC id match")
    ops.add("READ_1_1_1", "every read", "FLASH_READ", assumed=True)
    ops.add("PP_1_1_1", "every write", "FLASH_PP", assumed=True)
    if big:
        ops.add("READ_1_1_1_4B", "every read above 16 MiB", "FLASH_4READ", assumed=True)
        ops.add("PP_1_1_1_4B", "every write above 16 MiB", "FLASH_4PP", assumed=True)
    if 0x20 in erasers:
        ops.add("BE_4K", "subsector_erase = true", "FLASH_SE")
        if big:
            ops.add("BE_4K_4B", "subsector_erase = true, above 16 MiB", "FLASH_4SE")
    if 0xD8 in erasers:
        ops.add("SE", "sector_erase = true", "FLASH_BE64")
        if big:
            ops.add("SE_4B", "sector_erase = true, above 16 MiB", "FLASH_4BE64")
    return ops.to_json()
