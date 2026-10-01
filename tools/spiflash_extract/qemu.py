"""QEMU: :upstream:`qemu:hw/block/m25p80.c`, the SPI NOR flash model.

Its ``known_devices[]`` table keeps the shape of Linux's 2012 table::

    /* Winbond -- w25x "blocks" are 64k, "sectors" are 4KiB */
    { INFO("w25q256",     0xef4019,      0,  64 << 10, 512, ER_4K),
      .sfdp_read = m25p80_sfdp_w25q256 },

``INFO(name, jedec_id, ext_id, sector_size, n_sectors, flags)`` with a
two-byte ``ext_id``, ``INFO6`` with a three-byte one, and ``INFO_STACKED``
(``INFO`` plus a die count). The vendor is the comment heading each group.

The model decodes every opcode for every part (the values are its
``FlashCMD`` enum), so the table says little per part beyond its geometry
and flags. What makes it worth reading is
``.sfdp_read``: thirteen entries point at a complete SFDP dump in
:upstream:`qemu:hw/block/m25p80_sfdp.c`, which no other upstream carries.
Those records store the dump (``sfdp``, with ``.sfdp_read`` as its
``via``), and derive at load everything :mod:`spiflash.sfdp` decodes from
it (:meth:`~spiflash.sfdp.Sfdp.facts`): erase types, fast reads with their
dummy clocks, 4-byte opcodes, page size. The entry's own geometry, which the
model uses, is stored only where it differs from the dump's.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from . import cparse
from .ops import Opcodes
from .record import Record, feature_via, make

if TYPE_CHECKING:
    from pathlib import Path

M25P80 = "hw/block/m25p80.c"
SFDP_C = "hw/block/m25p80_sfdp.c"

_FEATURES = {
    "ER_4K": "erase_4k",
    "ER_32K": "erase_32k",
    "EEPROM": "no_erase",
}

# The status register bits the model gives a part by its flags (write and
# read of the status register, m25p80.c): TB at bit 5, BP3 at bit 6. BP0 to
# BP2 at bits 2 to 4 it gives every part, so they are no part's own.
_PROTECTION = {"HAS_SR_TB": ("tb", 5), "HAS_SR_BP3_BIT6": ("bp3", 6)}

# (macro, ext_id bytes the macro keeps, has a die count)
_MACROS = (("INFO_STACKED", 2, True), ("INFO6", 3, False), ("INFO", 2, False))

_EVERY_PART = "m25p80 decodes it for every part"


def extract(root: Path) -> list[Record]:
    raw = (root / M25P80).read_text()
    text = cparse.drop_preprocessor(cparse.strip_comments(raw))
    table = cparse.array_body(text, r"FlashPartInfo\s+known_devices\s*\[\s*\]")
    if table is None:
        msg = f"{M25P80}: no known_devices[] table"
        raise ValueError(msg)
    commands = flash_commands(text)
    dumps = sfdp_dumps(root)
    markers = _vendor_markers(raw, table.offset, table.offset + len(table.body))
    records = []
    for entry in cparse.braced_items(table.body, table.offset):
        vendor = None
        for pos, name in markers:
            if pos > entry.offset:
                break
            vendor = name
        records.append(_record(entry, raw, vendor, dumps, commands))
    return records


def flash_commands(text: str) -> dict[str, int]:
    """The model's ``FlashCMD`` enum (``BULK_ERASE = 0xc7``): the opcode of
    each command it decodes, by name. ``text`` has its comments stripped."""
    m = re.search(r"typedef\s+enum\s*\{([^}]*)\}\s*FlashCMD\s*;", text)
    if m is None:
        msg = f"{M25P80}: no FlashCMD enum"
        raise ValueError(msg)
    out: dict[str, int] = {}
    for item in cparse.split_top(m.group(1)):
        if not item.strip():
            continue
        name, eq, value = item.partition("=")
        if not eq:
            msg = f"{M25P80}: FlashCMD {name.strip()} has no explicit value"
            raise ValueError(msg)
        out[name.strip()] = cparse.evaluate(value)
    return out


def sfdp_dumps(root: Path) -> dict[str, bytes]:
    """The SFDP areas in m25p80_sfdp.c, by the read function that serves
    each (``m25p80_sfdp_w25q256``, as ``.sfdp_read`` names it)."""
    text = cparse.strip_comments((root / SFDP_C).read_text())
    out = {}
    for m, block in cparse.initialisers(text, r"static\s+const\s+uint8_t\s+sfdp_(\w+)\s*\[\s*\]"):
        items = [b for b in cparse.split_top(block.body) if b.strip()]
        out[f"m25p80_sfdp_{m.group(1)}"] = bytes(cparse.evaluate(b) for b in items)
    return out


def _vendor_markers(raw: str, start: int, end: int) -> list[tuple[int, str]]:
    """The vendor headings in the table: a comment whose first line, before
    any ``--``, is a name of a few words (``/* ST Microelectronics -- newer
    production may have feature updates */``). Longer comments are notes on
    the entries, not headings."""
    markers = []
    for m in re.finditer(r"/\*(.*?)\*/", raw[start:end], re.DOTALL):
        lines = [line.strip(" *\t") for line in m.group(1).splitlines()]
        first = next((line for line in lines if line), "")
        name, dashes, _rest = first.partition(" -- ")
        name = name.strip()
        if name and len(name.split()) <= 3 and (dashes or len(lines) == 1):
            markers.append((start + m.start(), name))
    return markers


def _macro(body: str) -> tuple[str, int, bool, list[str]] | None:
    """Which INFO macro an entry uses, and its arguments."""
    for macro, ext_bytes, stacked in _MACROS:
        args = cparse.macro_call(body, macro)
        if args is not None:
            return macro, ext_bytes, stacked, args
    return None


def _record(
    entry: cparse.Block,
    raw: str,
    vendor: str | None,
    dumps: dict[str, bytes],
    commands: dict[str, int],
) -> Record:
    body = entry.body
    notes = cparse.comments(raw[entry.offset : entry.offset + len(body)])
    found = _macro(body)
    if found is None:
        msg = f"{M25P80}:{cparse.line_of(raw, entry.offset)}: entry is not INFO/INFO6/INFO_STACKED"
        raise ValueError(msg)
    macro, ext_bytes, stacked, args = found
    name = cparse.c_string(args[0])
    jedec = cparse.evaluate(args[1])
    ext_val = cparse.evaluate(args[2])
    sector = cparse.evaluate(args[3])
    n_sectors = cparse.evaluate(args[4])
    flags = cparse.flag_names(args[5])
    die_cnt = cparse.evaluate(args[6]) if stacked else 0
    fields = cparse.designated(body)

    kept = ext_val & ((1 << (8 * ext_bytes)) - 1)
    if kept != ext_val:
        notes.append(f"{macro} keeps {ext_bytes} bytes of the ext_id 0x{ext_val:x}")
    id_hex = f"{jedec:06x}" if jedec else None
    ext = f"{kept:0{2 * ext_bytes}x}" if kept else None
    size = sector * n_sectors
    eeprom = "EEPROM" in flags
    claims = [(_FEATURES[f], f) for f in flags if f in _FEATURES]
    features = {feat for feat, _ in claims}
    if die_cnt:
        flags.append(f"die_cnt={die_cnt}")

    erasers = []
    if not eeprom:
        if "ER_4K" in flags:
            erasers.append({"opcode": commands["ERASE_4K"], "blocks": [[4096, size // 4096]]})
        if "ER_32K" in flags:
            erasers.append(
                {"opcode": commands["ERASE_32K"], "blocks": [[32 * 1024, size // (32 * 1024)]]}
            )
        erasers.append({"opcode": commands["ERASE_SECTOR"], "blocks": [[sector, n_sectors]]})

    # The values are the model's own FlashCMD enum.
    ops = Opcodes(commands)
    if id_hex:
        ops.add("RDID", "JEDEC_READ: the entry's id bytes", "JEDEC_READ")
    ops.add("READ_1_1_1", _EVERY_PART, "READ", assumed=True)
    ops.add("READ_1_1_1_FAST", _EVERY_PART, "FAST_READ", assumed=True)
    ops.add("PP_1_1_1", _EVERY_PART, "PP", assumed=True)
    if not eeprom:
        ops.add("SE", "ERASE_SECTOR: the entry's sector size", "ERASE_SECTOR")
        ops.add("CHIP_ERASE", f"BULK_ERASE: {_EVERY_PART}", "BULK_ERASE", assumed=True)
        ops.add("CHIP_ERASE_ALT", f"BULK_ERASE_60: {_EVERY_PART}", "BULK_ERASE_60", assumed=True)
        if "ER_4K" in flags:
            ops.add("BE_4K", "ER_4K", "ERASE_4K")
        if "ER_32K" in flags:
            ops.add("BE_32K", "ER_32K", "ERASE_32K")
    if die_cnt:
        ops.add("DIE_ERASE", f"die_cnt = {die_cnt}", "DIE_ERASE")

    # The dump's facts (spiflash.sfdp) are derived at load; make() drops
    # the INFO geometry they repeat.
    dump = None
    via = feature_via(claims)
    reader = fields.get("sfdp_read")
    if reader is not None:
        if reader not in dumps:
            msg = f"{M25P80}:{cparse.line_of(raw, entry.offset)}: no {reader}() in {SFDP_C}"
            raise ValueError(msg)
        dump = dumps[reader]
        via["sfdp"] = f".sfdp_read = {reader}"
    layout: dict[str, object] = {}
    for flag, (role, bit) in _PROTECTION.items():
        if flag in flags:
            layout[role] = {"register": "sr1", "bit": bit}
            via[f"protection.{role}"] = flag

    return make(
        "qemu",
        M25P80,
        cparse.line_of(raw, entry.offset),
        name,
        vendor=vendor,
        id=id_hex,
        ext_id=ext,
        id_method="rdid" if id_hex else None,
        size=size,
        page_size=256,  # INFO()'s, for every part
        erasers=erasers or None,
        features=features,
        flags=flags,
        via=via,
        protection=layout or None,
        opcodes=ops.to_json(),
        sfdp=dump.hex() if dump else None,
        notes=notes,
    )
