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
planes per die and dies. What each means is what ``mtk_snand_setup()`` in
:upstream:`mediatek:drivers/mtd/mtk-snand/mtk-snand.c` does with it: the size
is page x pages per block x blocks per die x dies, the main area only. The
driver never reads the planes: a two-plane part's blocks per die are all
its blocks, and ``mtk_snand_get_plane_address()`` takes the plane bit from
the page address.

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

from . import cparse
from .record import Record, make

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

IDS = "drivers/mtd/mtk-snand/mtk-snand-ids.c"
DEF_H = "drivers/mtd/mtk-snand/mtk-snand-def.h"

ID_METHODS = {"SNAND_ID_DYMMY": "rdid_opcode_dummy", "SNAND_ID_ADDR": "rdid_opcode_addr"}

# The die-select functions the table uses: each sends the die index its own
# way (Winbond's 0xc2 command, Micron's die-select feature bit).
SELECT_DIE = {"mtk_snand_winbond_select_die", "mtk_snand_micron_select_die"}

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


def _io_caps(text: str, symbols: Mapping[str, str | int]) -> dict[str, dict[str, int]]:
    """Each ``SNAND_IO_CAP`` table: its I/O modes (``1_1_4``) and the opcode
    of each. The modes it allows must be the ones it gives an opcode for."""
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
            modes[args[0].removeprefix("SNAND_IO_")] = cparse.evaluate(args[1], symbols)
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


def _fields(
    args: list[str], symbols: Mapping[str, str | int], caps: Mapping[str, dict[str, int]]
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
    select_die = args[5] if len(args) == 6 else None
    if (select_die is not None) != (dies > 1) or select_die not in (None, *SELECT_DIE):
        msg = f"{dies} dies, die select {select_die}"
        raise ValueError(msg)
    features = set()
    if {"1_1_2", "1_2_2"} & set(caps[rd]):
        features.add("dual_read")
    if {"1_1_4", "1_4_4"} & set(caps[rd]):
        features.add("quad_read")
    if "1_1_4" in caps[pl]:
        features.add("quad_pp")
    return {
        "type": "nand",
        "id": ident.hex(),
        "id_method": ID_METHODS[id_args[0]],
        "size": page * ppb * blocks * dies,
        "page_size": page,
        "sector_size": page * ppb,
        "features": features,
        "flags": [
            f"sparesize={oob}",
            f"planes_per_die={planes}",
            f"ndies={dies}",
            f"cap_rd={rd}",
            f"cap_pl={pl}",
            f"read_from_cache={','.join(caps[rd])}",
            f"program_load={','.join(caps[pl])}",
            *([f"select_die={select_die}"] if select_die else []),
        ],
        # The flags hold the geometry (sparesize, planes_per_die, ndies) and
        # the size the blocks, so no note restates it.
        "notes": [],
    }


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
