"""MediaTek: the mtk-snand driver of MediaTek's OpenWrt U-Boot,
:upstream:`mediatek:drivers/mtd/mtk-snand/mtk-snand-ids.c` (SPI NAND only).

Each entry is a ``SNAND_INFO`` of the part name, its id, its memory
organisation, the read-from-cache and program-load I/O modes it takes, and,
for a part of two dies, how to select a die::

    SNAND_INFO("W25M02GV", SNAND_ID(SNAND_ID_DYMMY, 0xef, 0xab, 0x21),
               SNAND_MEMORG_2G_2K_64_2D,
               &snand_cap_read_from_cache_quad,
               &snand_cap_program_load_x4,
               mtk_snand_winbond_select_die),

``SNAND_MEMORG`` (:upstream:`mediatek:drivers/mtd/mtk-snand/mtk-snand-def.h`)
is the page size, the spare (OOB) size, pages per block, blocks per die,
planes per die and dies: the record's ``oob_size``, ``planes`` and ``dies``.
What each means is what ``mtk_snand_setup()`` in
:upstream:`mediatek:drivers/mtd/mtk-snand/mtk-snand.c` does with it: the size
is page x pages per block x blocks per die x dies, the main area only. The
driver never reads the planes: a two-plane part's blocks per die are all
its blocks, and ``mtk_snand_get_plane_address()`` takes the plane bit from
the page address.

Each read-from-cache and program-load ``SNAND_IO_CAP`` lists the I/O modes
the part takes, each an ``SNAND_OP`` of its opcode and dummy clocks
(``mtk_snand_read_cache()`` writes them to the controller as "dummy
cycles"): the record's operations, each with its dummy clocks. The page
read, program execute and feature commands the driver sends every part
are its defaults. A part of two dies selects one with Winbond's command
(0xc2 and the die) or Micron's feature 0xd0 bit 6; the Micron one writes
``SNAND_MICRON_DIE_SEL_1`` (bit 6 set) whatever the die asked for, so it
always selects die 1, an upstream bug the record notes.

``mtk_snand_id_probe()`` sends 0x9f and a zero byte, then 0x9f alone, and
matches both answers against the entries, first to last.
``snand_flash_id_lookup()`` compares the entry's id type, but both probes
pass ``SNAND_ID_DYMMY`` and every entry's type is that same value
(``SNAND_ID_ADDR`` is defined as it), so the label decides nothing: every
entry is tried with and without the dummy byte. The record's id method is
the one the entry names.

The table names no vendor; the records name none. Left out, and counted by
:func:`skipped`, are the entries in :data:`WRONG`. Anything else it does not
understand raises, naming the line.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any

from spiflash import derive
from spiflash.enums import DataPhase, FlashType
from spiflash.opcodes import OPERATIONS

from . import cparse
from .ops import Opcodes
from .record import Record, make

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

IDS = "drivers/mtd/mtk-snand/mtk-snand-ids.c"
DEF_H = "drivers/mtd/mtk-snand/mtk-snand-def.h"

ID_METHODS = {"SNAND_ID_DYMMY": "rdid_opcode_dummy", "SNAND_ID_ADDR": "rdid_opcode_addr"}

# The die-select functions the table uses, and what each selects a die
# with: Winbond's 0xc2 command and the die index (mtk-snand-ids.c:462-475),
# Micron's die-select feature bit, 0xd0 bit 6 (:477-494).
SELECT_DIE: dict[str, tuple[str, Any]] = {
    "mtk_snand_winbond_select_die": ("op", "NAND_DIE_SELECT"),
    "mtk_snand_micron_select_die": ("bit", {"register": "nand-d0", "bit": 6}),
}

#: What the Micron die select does wrong, noted on its records.
MICRON_SELECT_BUG = (
    "mtk_snand_micron_select_die() writes SNAND_MICRON_DIE_SEL_1 (bit 6) whatever "
    "the die asked for, so it always selects die 1 (an upstream bug)"
)

#: The commands the driver sends every part, whatever its entry says: its
#: defaults. The block erase is the eraser's.
DEFAULTS = {
    "NAND_PAGE_READ": "SNAND_CMD_READ_TO_CACHE",
    "NAND_PROGRAM_EXECUTE": "SNAND_CMD_PROGRAM_EXECUTE",
    "NAND_GET_FEATURE": "SNAND_CMD_GET_FEATURE",
    "NAND_SET_FEATURE": "SNAND_CMD_SET_FEATURE",
}

_ID = "wrong id: the table gives the part again, with another id"
_SIZE = "size contradicts its part number's density"

#: Entries that are wrong, and why; they are left out
#: (:repo:`docs/_source_notes/mediatek.md` gives the evidence). Each is keyed
#: by its part, its id and, for a wrong size, that size, so an entry
#: corrected upstream is taken again; a key that matches no entry raises, to
#: be removed.
WRONG: dict[tuple[str, str, int | None], str] = {
    # 2 Gbit; its own entry is d5 1e, and d5 1d the 1 Gbit EM73C044SND's.
    ("EM73D044SND", "d51d", None): _ID,
    # E is Etron's 4 Gbit, as in every other EM73E044 entry; this is 8 Gbit.
    ("EM73E044SNE", "d50e", 1 << 30): _SIZE,
}


def _symbols(root: Path, text: str) -> dict[str, str | int]:
    return {
        **cparse.defines(cparse.strip_comments((root / DEF_H).read_text())),
        **cparse.defines(text),
    }


def _io_caps(text: str, symbols: Mapping[str, str | int]) -> dict[str, dict[str, tuple[int, int]]]:
    """Each ``SNAND_IO_CAP`` table: its I/O modes (``1_1_4``), and the
    opcode and dummy clocks of each. The modes it allows must be the ones it
    gives an opcode for."""
    caps = {}
    for m in re.finditer(r"\bSNAND_IO_CAP\s*\(", text):
        start = m.end() - 1
        name, allowed, *ops = cparse.split_top(text[start + 1 : cparse.matching(text, start)])
        modes = {}
        for op in ops:
            args = cparse.macro_call(op, "SNAND_OP")
            if args is None or not args[0].startswith("SNAND_IO_"):
                msg = f"{IDS}:{cparse.line_of(text, start)}: {name}: cannot read {op!r}"
                raise ValueError(msg)
            opcode, dummy = (cparse.evaluate(a, symbols) for a in args[1:3])
            modes[args[0].removeprefix("SNAND_IO_")] = (opcode, dummy)
        given = sorted(cparse.flag_names(allowed))
        if given != sorted(f"SPI_IO_{mode}" for mode in modes):
            msg = f"{IDS}:{cparse.line_of(text, start)}: {name}: allows {given}, gives {modes}"
            raise ValueError(msg)
        caps[name] = modes
    return caps


def _entries(root: Path) -> Iterator[tuple[int, str, dict[str, Any]]]:
    """Each ``SNAND_INFO``: its line, its part name and its record fields."""
    raw = (root / IDS).read_text()
    text = cparse.strip_comments(raw)
    symbols = _symbols(root, text)
    caps = _io_caps(text, symbols)
    found = False
    for m in re.finditer(r"\bSNAND_INFO\s*\(", text):
        start = m.end() - 1
        args = cparse.split_top(text[start + 1 : cparse.matching(text, start)])
        found = True
        line = cparse.line_of(raw, m.start())
        name = cparse.c_string(args[0])
        try:
            fields = _fields(args, symbols, caps)
        except ValueError as e:
            msg = f"{IDS}:{line}: {name}: {e}"
            raise ValueError(msg) from e
        yield line, name, fields
    if not found:
        msg = f"{IDS}: no SNAND_INFO entries"
        raise ValueError(msg)


#: The two kinds of ``SNAND_IO_CAP`` table, by their names' start.
READ_CAPS = "snand_cap_read_from_cache"
LOAD_CAPS = "snand_cap_program_load"


def _common(
    caps: Mapping[str, dict[str, tuple[int, int]]], kind: str
) -> set[tuple[str, tuple[int, int]]]:
    """The I/O modes (with their opcode and dummy clocks) every table of
    ``kind`` has: the read from cache on one line (0x0b, 8 dummy clocks)
    and the program load on one line (0x02), which the driver can fall back
    to on every part, so its defaults, not the part's."""
    tables = [set(modes.items()) for name, modes in caps.items() if name.startswith(kind)]
    return set.intersection(*tables) if tables else set()


def _operation(opcode: int, mode: str, phase: DataPhase) -> str:
    """The SPI NAND operation of an opcode sent in an I/O mode (``1_1_4``):
    a read from cache or a program load, with its 2-byte column address."""
    protocol = mode.replace("_", "-")
    for op in OPERATIONS.values():
        if op.flash_type is FlashType.NAND and (
            op.opcode,
            op.protocol,
            op.data,
            op.address_bytes,
        ) == (opcode, protocol, phase, 2):
            return op.name
    msg = f"no SPI NAND operation 0x{opcode:02x} {protocol}"
    raise ValueError(msg)


def _fields(
    args: list[str],
    symbols: Mapping[str, str | int],
    caps: Mapping[str, dict[str, tuple[int, int]]],
) -> dict[str, Any]:
    """The record fields of one ``SNAND_INFO``."""
    if len(args) not in (5, 6):
        msg = f"{len(args)} arguments, not 5 or 6"
        raise ValueError(msg)
    id_args = cparse.macro_call(args[1], "SNAND_ID")
    if id_args is None or id_args[0] not in ID_METHODS or not 2 <= len(id_args) <= 5:
        msg = f"cannot read the id {args[1]!r}"
        raise ValueError(msg)
    ident = bytes(cparse.evaluate(a) for a in id_args[1:])
    org_text = str(symbols.get(args[2], args[2]))
    org = cparse.macro_call(org_text, "SNAND_MEMORG")
    if org is None or len(org) != 6:
        msg = f"cannot read the memory organisation {args[2]!r}"
        raise ValueError(msg)
    page, oob, ppb, blocks, planes, dies = (cparse.evaluate(a) for a in org)
    # mtk_snand_get_plane_address() has one plane bit; the select_die
    # functions take die 0 or 1.
    if planes not in (1, 2) or dies not in (1, 2) or page & (page - 1) or ppb & (ppb - 1):
        msg = f"page {page}, {ppb} pages per block, {planes} planes, {dies} dies"
        raise ValueError(msg)
    rd, pl = (a.lstrip("&").strip() for a in args[3:5])
    if rd not in caps or pl not in caps:
        msg = f"unknown I/O capabilities {rd!r} or {pl!r}"
        raise ValueError(msg)
    select_die = args[5].strip() if len(args) == 6 else None
    if (select_die is not None) != (dies > 1) or select_die not in (None, *SELECT_DIE):
        msg = f"{dies} dies, die select {select_die}"
        raise ValueError(msg)
    # Each I/O mode's operation, with the dummy clocks its SNAND_OP gives;
    # the one-line mode, which every table of its kind has, is a default.
    opcodes = []
    for via, table, phase, kind in (
        (f"cap_rd={rd}", rd, DataPhase.READ, READ_CAPS),
        (f"cap_pl={pl}", pl, DataPhase.WRITE, LOAD_CAPS),
    ):
        common = _common(caps, kind)
        for mode, (opcode, dummy) in caps[table].items():
            # The one-line mode only: every read table also has the quad
            # output read, but which table a part has is its entry's choice.
            default = mode == "1_1_1" and (mode, (opcode, dummy)) in common
            opcodes.append(
                {
                    "op": _operation(opcode, mode, phase),
                    "via": f"every {kind}* table" if default else via,
                    "dummy_clocks": dummy,
                    **({"assumed": True} if default else {}),
                }
            )
    defaults = Opcodes(symbols)
    for op, symbol in DEFAULTS.items():
        defaults.add(op, f"every part ({symbol})", symbol, assumed=True)
    out: dict[str, Any] = {
        "type": "nand",
        "id": ident.hex(),
        "id_method": ID_METHODS[id_args[0]],
        "size": page * ppb * blocks * dies,
        "page_size": page,
        "erasers": [derive.block_eraser(0xD8, page * ppb, page * ppb * blocks * dies).to_json()],
        "oob_size": oob,
        "planes": planes,
        "dies": dies,
        "flags": [f"cap_rd={rd}", f"cap_pl={pl}"],
        "via": {},
        "opcodes": opcodes + defaults.to_json(),
        "notes": [],
    }
    if select_die is not None:
        how, what = SELECT_DIE[select_die]
        token = f"select_die={select_die}"
        if how == "op":
            select = Opcodes(symbols)
            select.add(what, token, "SNAND_CMD_WINBOND_SELECT_DIE")
            out["opcodes"] += select.to_json()
        else:
            out["die_select_bit"] = what
            out["via"]["die_select_bit"] = token
            out["notes"].append(MICRON_SELECT_BUG)
    return out


def _sorted(
    root: Path, wrong: Mapping[tuple[str, str, int | None], str]
) -> Iterator[tuple[int, str, dict[str, Any], str | None]]:
    """Each entry, with why it is left out (None for one that is taken).
    Raises, at the end, for a key of ``wrong`` no entry matched."""
    unused = set(wrong)
    for line, name, fields in _entries(root):
        keys = [(name, fields["id"], fields["size"]), (name, fields["id"], None)]
        found = [k for k in keys if k in wrong]
        unused -= set(found)
        yield line, name, fields, wrong[found[0]] if found else None
    if unused:
        msg = f"{IDS}: no entry is {sorted(unused, key=str)}: remove it from WRONG"
        raise ValueError(msg)


def skipped(root: Path, wrong: Mapping[tuple[str, str, int | None], str] = WRONG) -> Counter[str]:
    """How many entries are left out, by reason."""
    return Counter(why for _, _, _, why in _sorted(root, wrong) if why)


def extract(root: Path, wrong: Mapping[tuple[str, str, int | None], str] = WRONG) -> list[Record]:
    records = []
    first: dict[str, int] = {}
    for line, name, fields, why in _sorted(root, wrong):
        # snand_flash_id_lookup() takes the first entry whose id the answer
        # starts with.
        shadow = next((n for i, n in first.items() if fields["id"].startswith(i)), None)
        first.setdefault(fields["id"], line)
        if why:
            continue
        if shadow is not None:
            fields["notes"] = [
                f"never used: the driver matches the entry on line {shadow} first",
                *fields["notes"],
            ]
        records.append(make("mediatek", IDS, line, name, **fields))
    return records
