"""Linux: :upstream:`linux:drivers/mtd/spi-nor/*.c` (SPI NOR) and
:upstream:`linux:drivers/mtd/nand/spi/*.c` (SPI NAND).

SPI NOR entries are ``struct flash_info`` designated initialisers (Linux 6.8+)::

    {
        .id = SNOR_ID(0xef, 0x40, 0x18),
        .name = "w25q128",
        .size = SZ_16M,
        .flags = SPI_NOR_HAS_LOCK | SPI_NOR_HAS_TB,
        .no_sfdp_flags = SECT_4K | SPI_NOR_DUAL_READ | SPI_NOR_QUAD_READ,
    },

``.name`` is obsolete for new entries, which carry the part name in a comment
instead; ``.size`` is left out when the kernel reads it from SFDP.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, NamedTuple

from spiflash import derive

from . import cparse
from .ops import Opcodes, add_4b_variants, add_spinor
from .record import Record, feature_via, make

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

NOR_DIR = "drivers/mtd/spi-nor"
NAND_DIR = "drivers/mtd/nand/spi"
SPINOR_H = "include/linux/mtd/spi-nor.h"  # the SPINOR_OP_* opcodes

# Files in NOR_DIR that hold no part tables.
_NOT_TABLES = {"core.c", "sfdp.c", "swp.c", "otp.c", "sysfs.c", "debugfs.c"}

_NOR_FEATURES = {
    "SECT_4K": "erase_4k",
    "SPI_NOR_DUAL_READ": "dual_read",
    "SPI_NOR_QUAD_READ": "quad_read",
    "SPI_NOR_OCTAL_READ": "octal_read",
    "SPI_NOR_OCTAL_DTR_READ": "octal_dtr_read",
    "SPI_NOR_OCTAL_DTR_PP": "octal_dtr_pp",
    "SPI_NOR_HAS_LOCK": "lock",
    "SPI_NOR_QUAD_PP": "quad_pp",
    "SPI_NOR_RWW": "rww",
    "SPI_NOR_NO_ERASE": "no_erase",
    "SPI_NOR_4B_OPCODES": "4byte_opcodes",
}


def split_id(data: list[int]) -> tuple[str, str | None]:
    """An id as (the JEDEC id, any further bytes): the manufacturer byte with
    its 0x7f continuation codes, then two device bytes; the rest is extended."""
    n = 0
    while n < len(data) and data[n] == 0x7F:
        n += 1
    head, ext = data[: n + 3], data[n + 3 :]
    return bytes(head).hex(), bytes(ext).hex() or None


def _manufacturers(text: str) -> dict[str, str]:
    """``parts array name -> manufacturer name`` from the file's
    ``struct spi_nor_manufacturer`` initialisers."""
    out = {}
    for _, init in cparse.initialisers(text, r"struct\s+spi_nor_manufacturer\s+\w+"):
        fields = cparse.designated(init.body)
        if "parts" in fields and "name" in fields:
            out[fields["parts"].strip()] = cparse.c_string(fields["name"])
    return out


def _entry_name(fields: dict[str, str], raw_body: str) -> tuple[str | None, list[str]]:
    """The part name and the entry's comments. Entries without ``.name``
    usually say which part they are in a comment (``/* W25Q01JV */``)."""
    notes = cparse.comments(raw_body)
    if "name" in fields:
        return cparse.c_string(fields["name"]), notes
    for note in notes:
        m = re.fullmatch(r"([A-Za-z0-9]{4,}[A-Za-z0-9_/.-]*)", note.strip())
        if m:
            return m.group(1), [n for n in notes if n is not note]
    return None, notes


#: The fixups that set an entry's quad enable method (``params->quad_enable``)
#: for the part, and the bit it is (``"none"``: ``NULL``, no QE bit).
#: The manufacturers' ``default_init`` (macronix.c, issi.c, micron-st.c) and
#: the core's default (core.c, ``spi_nor_init_default_params``) set it for
#: every part of a maker or of all of them, and only where the part's own
#: SFDP tables do not override it: driver defaults, so no part's bit.
_QE_FIXUPS: dict[str, tuple[str, str | dict[str, object]]] = {
    "mx25l3255e_fixups": ("spi_nor_sr1_bit6_quad_enable", {"register": "sr1", "bit": 6}),
    "mt35xu512aba_fixups": ("NULL", "none"),
    "mt35_two_die_fixups": ("NULL", "none"),
}

#: The manufacturers' fixups whose ``default_init`` sets the quad enable
#: method for every part of theirs that takes its parameters from its
#: entry: driver defaults, which no entry gets.
_QE_DEFAULTS = frozenset({"issi_fixups", "macronix_nor_fixups", "micron_st_nor_fixups"})

#: The fixups that set a quad enable method that is not one bit for the
#: part, and why the entry gets none.
_QE_NOT_A_BIT = {
    # GD25Q256C has SFDP 1.0 and QE at SR1 bit 6; the D and E have 1.6 and
    # take theirs from it (SR2 bit 1): Rockchip's driver splits them alike.
    "gd25q256_fixups": (
        "no quad enable bit: gd25q256_post_bfpt() sets SR1 bit 6 for the GD25Q256C "
        "(a JESD216 1.0 BFPT) only; the GD25Q256D and E take the bit their BFPT gives"
    ),
    # Spansion's S25HL/HS-T: written by address (CFR1V), after the BFPT's.
    "s25hx_t_fixups": None,
}

#: The fixups whose operations unlock the part, where they replace the
#: status register block protection (``locking_ops``): the operation.
_LOCKING_OPS = {"sst26vf_nor_fixups": ("ULBPR", "SPINOR_OP_GBULK")}


def _fixups(text: str, member: str) -> dict[str, list[str]]:
    """Each ``struct spi_nor_fixups`` in ``text`` whose functions set
    ``params->member`` (``quad_enable``, ``locking_ops``): its name, and the
    values they set."""
    sets: dict[str, list[str]] = {}
    for m in re.finditer(r"\b(\w+)\s*\([^;{}()]*\)\s*\{", text):
        start = m.end() - 1
        body = text[start : cparse.matching(text, start)]
        found = re.findall(rf"->\s*{member}\s*=\s*([^;]+);", body)
        if found:
            sets.setdefault(m.group(1), []).extend(v.strip() for v in found)
    out = {}
    for m, init in cparse.initialisers(text, r"struct\s+spi_nor_fixups\s+(\w+)"):
        functions = [v.strip().lstrip("&") for v in cparse.designated(init.body).values()]
        values = [v for f in functions for v in sets.get(f, [])]
        if values:
            out[m.group(1)] = values
    return out


def extract_nor(root: Path) -> list[Record]:
    core_h = cparse.strip_comments((root / NOR_DIR / "core.h").read_text())
    spinor_h = cparse.strip_comments((root / SPINOR_H).read_text())
    symbols: dict[str, str | int] = {**cparse.defines(spinor_h), **cparse.defines(core_h)}
    records = []
    for path in sorted((root / NOR_DIR).glob("*.c")):
        if path.name in _NOT_TABLES:
            continue
        raw = path.read_text()
        stripped = cparse.strip_comments(raw)
        text = cparse.drop_preprocessor(stripped)
        local = {**symbols, **cparse.defines(stripped)}
        vendors = _manufacturers(text)
        rel = f"{NOR_DIR}/{path.name}"
        fixups = _Fixups(_fixups(text, "quad_enable"), set(_fixups(text, "locking_ops")))
        for name, values in fixups.quad_enable.items():
            known = _QE_FIXUPS.get(name, (None,))[0]
            mapped = name in _QE_NOT_A_BIT or name in _QE_DEFAULTS or values == [known]
            if not mapped:
                msg = f"{rel}: {name} sets quad_enable to {values}, which no table here maps"
                raise ValueError(msg)
        for m, table in cparse.initialisers(text, r"struct\s+flash_info\s+(\w+)\s*\[\s*\]"):
            vendor = vendors.get(m.group(1))
            for entry in cparse.braced_items(table.body, table.offset):
                rec = _nor_record(entry, raw, rel, vendor, local, fixups)
                if rec is not None:
                    records.append(rec)
    return records


class _Fixups(NamedTuple):
    """What a file's fixups set for the entries naming them."""

    #: The quad enable methods, by fixups name.
    quad_enable: dict[str, list[str]]
    #: The fixups replacing the status register block protection.
    locking: set[str]


def protection(flags: list[str], symbols: dict[str, str | int]) -> tuple[Any, dict[str, str]]:
    """The block-protection layout Linux's (and U-Boot's) status register
    locking gives an entry (``spi_nor_sr_lock``, swp.c), and its ``via``:
    for ``SPI_NOR_HAS_LOCK``, BP0 to BP2 and SRWD (``SR_BP0``...,
    ``SR_SRWD``) in SR1; BP3 for ``SPI_NOR_4BIT_BP`` (``SR_BP3``, or
    ``SR_BP3_BIT6`` with ``SPI_NOR_BP3_SR_BIT6``); TB for
    ``SPI_NOR_HAS_TB`` (``SR_TB_BIT5``, U-Boot's ``SR_TB``, or
    ``SR_TB_BIT6`` with ``SPI_NOR_TB_SR_BIT6``); CMP in SR2 for
    ``SPI_NOR_HAS_CMP``. ``SPI_NOR_SWP_IS_VOLATILE`` makes the BP bits
    volatile. ``None`` without ``SPI_NOR_HAS_LOCK``."""
    if "SPI_NOR_HAS_LOCK" not in flags:
        return None, {}

    def bit(symbol: str, register: str = "sr1", **more: str) -> dict[str, object]:
        mask = cparse.evaluate(symbol, symbols)
        return {"register": register, "bit": mask.bit_length() - 1, **more}

    tokens = ["SPI_NOR_HAS_LOCK"]
    volatile: dict[str, str] = {}
    if "SPI_NOR_SWP_IS_VOLATILE" in flags:
        tokens.append("SPI_NOR_SWP_IS_VOLATILE")
        volatile["writability"] = "volatile"
    out = {f"bp{i}": bit(f"SR_BP{i}", **volatile) for i in range(3)}
    out["srp"] = bit("SR_SRWD")
    via = {"protection": "; ".join(tokens)}
    if "SPI_NOR_4BIT_BP" in flags:
        bit6 = "SPI_NOR_BP3_SR_BIT6" in flags
        out["bp3"] = bit("SR_BP3_BIT6" if bit6 else "SR_BP3", **volatile)
        via["protection.bp3"] = "SPI_NOR_4BIT_BP" + ("; SPI_NOR_BP3_SR_BIT6" if bit6 else "")
    if "SPI_NOR_HAS_TB" in flags:
        bit6 = "SPI_NOR_TB_SR_BIT6" in flags
        five = "SR_TB_BIT5" if "SR_TB_BIT5" in symbols else "SR_TB"
        out["tb"] = bit("SR_TB_BIT6" if bit6 else five)
        via["protection.tb"] = "SPI_NOR_HAS_TB" + ("; SPI_NOR_TB_SR_BIT6" if bit6 else "")
    if "SPI_NOR_HAS_CMP" in flags:
        out["cmp"] = bit("SR2_CMP_BIT6", "sr2")
        via["protection.cmp"] = "SPI_NOR_HAS_CMP"
    return out, via


def _nor_record(
    entry: cparse.Block,
    raw: str,
    rel: str,
    vendor: str | None,
    symbols: dict[str, str | int],
    fixups: _Fixups,
) -> Record | None:
    fields = cparse.designated(entry.body)
    raw_body = raw[entry.offset : entry.offset + len(entry.body)]
    name, notes = _entry_name(fields, raw_body)
    id_hex = ext = None
    if "id" in fields:
        args = cparse.macro_call(fields["id"], "SNOR_ID")
        if args is None:
            msg = f"{rel}: unexpected .id {fields['id']!r}"
            raise ValueError(msg)
        id_hex, ext = split_id([cparse.evaluate(a, symbols) for a in args])
    if name is None and id_hex is None:
        return None
    if name is None:
        name = f"{vendor or 'unknown'}-{id_hex}"
        notes.append("Linux gives this entry no name")

    flags: list[str] = []
    for key in ("flags", "no_sfdp_flags", "fixup_flags", "mfr_flags"):
        if key in fields:
            flags += cparse.flag_names(fields[key])
    claims = [(_NOR_FEATURES[f], f) for f in flags if f in _NOR_FEATURES]
    if "otp" in fields:
        claims.append(("otp", ".otp"))
    features = {feat for feat, _ in claims}
    if "4byte_opcodes" in features:
        features.add("4byte_addr")

    size = cparse.evaluate(fields["size"], symbols) if "size" in fields else None
    if size == 0:
        size = None
    # SPI_NOR_DEFAULT_PAGE_SIZE (256) where the entry gives none is the
    # driver's, for every part: no page size of the part's own.
    page = cparse.evaluate(fields["page_size"], symbols) if "page_size" in fields else None
    # "non-legacy flash entries in flash_info will have a size of zero iff
    # SFDP should be used" (struct flash_info, core.h): such a part's
    # erasers come from its SFDP tables at run time (its RDSFDP says so).
    fixup = fields.get("fixups", "").strip().lstrip("&")
    unlock = _LOCKING_OPS.get(fixup) if fixup in fixups.locking else None
    opcodes = _nor_opcodes(
        fields, symbols, features, has_id=id_hex is not None, legacy=size is not None
    )
    if unlock is not None:
        op, symbol = unlock
        ops = Opcodes(symbols)
        ops.add(op, f"{fixup} (spi_nor_global_block_unlock)", symbol)
        opcodes += ops.to_json()
    via = feature_via(claims)
    layout = None
    if fixup in fixups.locking:
        notes.append(f"no block protection bits: its {fixup} replace the status register locking")
    else:
        layout, protection_via = protection(flags, symbols)
        via |= protection_via
    quad_enable = None
    if fixup in _QE_FIXUPS:
        quad_enable = _QE_FIXUPS[fixup][1]
        via["quad_enable"] = f".fixups = &{fixup}"
    elif note := _QE_NOT_A_BIT.get(fixup):
        notes.append(note)
    erasers = []
    if size is not None and "no_erase" not in features:
        erasers = _nor_erasers(fields, symbols, size)
    return make(
        "linux",
        rel,
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
        via=via,
        quad_enable=quad_enable,
        protection=layout,
        opcodes=opcodes,
        notes=notes,
    )


def _nor_erasers(
    fields: dict[str, str], symbols: dict[str, str | int], size: int
) -> list[dict[str, object]]:
    """The erasers spi_nor_no_sfdp_init_params() (core.c) sets up for a
    part with a size: 0x20 over 4 KiB sectors for ``SECT_4K``, and 0xd8 over
    the entry's ``.sector_size``.

    Where the entry gives no ``.sector_size``, which is most of them, the
    kernel takes ``SPI_NOR_DEFAULT_SECTOR_SIZE`` (64 KiB) whatever the part:
    that eraser is a driver default (assumed), so gives no sector size and
    no ``erase_64k``. An entry's own ``.sector_size`` (``SZ_256K`` for the
    S25FL512S) and ``SECT_4K`` are its claims."""
    out = []
    if "SECT_4K" in cparse.flag_names(fields.get("no_sfdp_flags", "0")):
        out.append(derive.block_eraser(0x20, 4096, size).to_json())
    if "sector_size" in fields:
        sector = cparse.evaluate(fields["sector_size"], symbols)
        out.append(derive.block_eraser(0xD8, sector, size).to_json())
    else:
        sector = cparse.evaluate("SPI_NOR_DEFAULT_SECTOR_SIZE", symbols)
        out.append(derive.block_eraser(0xD8, sector, size, assumed=True).to_json())
    return out


# no_sfdp_flags -> the operation spi_nor_no_sfdp_init_params() sets up for it.
_NO_SFDP_OPS = {
    "SPI_NOR_DUAL_READ": "READ_1_1_2",
    "SPI_NOR_QUAD_READ": "READ_1_1_4",
    "SPI_NOR_OCTAL_READ": "READ_1_1_8",
    "SPI_NOR_OCTAL_DTR_READ": "READ_8D_8D_8D",
    "SPI_NOR_OCTAL_DTR_PP": "PP_8D_8D_8D",
    "SECT_4K": "BE_4K",
}

# mfr_flags -> the operation micron-st.c (USE_FSR: micron_st_nor_ready) or
# spansion.c (USE_CLSR, USE_CLPEF: spansion_nor_clear_sr) sends for it.
_MFR_OPS = {"USE_FSR": "RDFSR", "USE_CLSR": "CLSR", "USE_CLPEF": "CLPEF"}


def _nor_opcodes(
    fields: dict[str, str],
    symbols: dict[str, str | int],
    features: set[str],
    *,
    has_id: bool,
    legacy: bool,
) -> list[dict[str, object]]:
    """The operations the kernel sets up for a part, following
    drivers/mtd/spi-nor/core.c: spi_nor_init_default_params() (read, fast
    read and page program for every part; quad page program for
    SPI_NOR_QUAD_PP), spi_nor_no_sfdp_init_params() (the no_sfdp_flags, and
    sector erase), chip erase unless a fixup opts out, and the 4-byte
    conversion for SPI_NOR_4B_OPCODES. A part whose size is left to SFDP
    (``legacy`` false) gets its read, program and erase set from its SFDP
    tables at run time, so only the defaults and RDSFDP are listed.

    Read, fast read, page program and chip erase are driver defaults
    (assumed): the kernel sets them up for every part, fast read wherever
    the board's devicetree asks for it (``m25p,fast-read``), whatever the
    entry says."""
    ops = Opcodes(symbols)
    if has_id:
        add_spinor(ops, "RDID", "JEDEC id match (spi_nor_match_id)")
    default = "default (spi_nor_init_default_params)"
    add_spinor(ops, "READ_1_1_1", default, assumed=True)
    add_spinor(ops, "READ_1_1_1_FAST", f"{default}, m25p,fast-read", assumed=True)
    add_spinor(ops, "PP_1_1_1", default, assumed=True)
    flags = cparse.flag_names(fields.get("flags", "0"))
    no_sfdp = cparse.flag_names(fields.get("no_sfdp_flags", "0"))
    fixup = cparse.flag_names(fields.get("fixup_flags", "0"))
    if "SPI_NOR_QUAD_PP" in flags:
        add_spinor(ops, "PP_1_1_4", "SPI_NOR_QUAD_PP")
    if not legacy:
        add_spinor(ops, "RDSFDP", "size from SFDP")
    else:
        for flag in no_sfdp:
            if flag in _NO_SFDP_OPS:
                add_spinor(ops, _NO_SFDP_OPS[flag], flag)
        if "no_erase" not in features:
            # The eraser gives it (_nor_erasers); added for its 4-byte form,
            # a default where the sector size is.
            no_sector = "sector_size" not in fields
            add_spinor(ops, "SE", "sector erase (spi_nor_no_sfdp_init_params)", assumed=no_sector)
    if "no_erase" not in features:
        add_spinor(ops, "CHIP_ERASE", "default (spi_nor_erase)", assumed=True)
    # The manufacturer flags micron-st.c and spansion.c read: the flag status
    # register for ready, and clearing the error bits after a failure.
    for flag in cparse.flag_names(fields.get("mfr_flags", "0")):
        if flag in _MFR_OPS:
            add_spinor(ops, _MFR_OPS[flag], flag)
    if "SPI_NOR_4B_OPCODES" in fixup:
        add_4b_variants(ops, "SPI_NOR_4B_OPCODES")
    return ops.to_json()


def _nand_manufacturers(
    text: str, symbols: dict[str, str | int]
) -> Callable[[int], tuple[str | None, int | None]]:
    """Which ``struct spinand_manufacturer`` a ``SPINAND_INFO`` belongs to,
    by where it is: the one whose ``.chips`` is the table around it. A file
    can have several (esmt.c: 0x8c and 0xc8, one table each)."""
    makers: dict[str | None, tuple[str | None, int | None]] = {}
    for _, init in cparse.initialisers(text, r"struct\s+spinand_manufacturer\s+\w+"):
        fields = cparse.designated(init.body)
        name = cparse.c_string(fields["name"]) if "name" in fields else None
        mfr = cparse.evaluate(fields["id"], symbols) if "id" in fields else None
        makers[fields.get("chips")] = (name, mfr)
    tables = [
        (m.group(1), init.offset, init.offset + len(init.body))
        for m, init in cparse.initialisers(text, r"struct\s+spinand_info\s+(\w+)\s*\[\s*\]")
    ]

    def at(pos: int) -> tuple[str | None, int | None]:
        table = next((name for name, lo, hi in tables if lo <= pos < hi), None)
        if table in makers:
            return makers[table]
        # No .chips to go by: the file's only manufacturer.
        only = list(makers.values())
        return only[0] if len(only) == 1 else (None, None)

    return at


def extract_nand(root: Path) -> list[Record]:
    """SPI NAND: ``SPINAND_INFO("name", SPINAND_ID(method, bytes...),
    NAND_MEMORG(bits_per_cell, pagesize, oobsize, pages_per_eraseblock,
    eraseblocks_per_lun, max_bad_eraseblocks_per_lun, planes_per_lun,
    luns_per_target, ntargets), ...)``."""
    records = []
    for path in sorted((root / NAND_DIR).glob("*.c")):
        if path.name in ("core.c", "otp.c"):
            continue
        raw = path.read_text()
        stripped = cparse.strip_comments(raw)
        symbols: dict[str, str | int] = dict(cparse.defines(stripped))
        text = cparse.drop_preprocessor(stripped)
        rel = f"{NAND_DIR}/{path.name}"
        makers = _nand_manufacturers(text, symbols)
        for m in re.finditer(r"\bSPINAND_INFO\s*\(", text):
            start = m.end() - 1
            end = cparse.matching(text, start)
            args = cparse.split_top(text[start + 1 : end])
            name = cparse.c_string(args[0])
            id_args = cparse.macro_call(args[1], "SPINAND_ID")
            org = cparse.macro_call(args[2], "NAND_MEMORG")
            vendor, mfr_id = makers(m.start())
            if id_args is None or org is None or mfr_id is None:
                msg = f"{rel}: cannot read SPINAND_INFO for {name}"
                raise ValueError(msg)
            method = id_args[0].replace("SPINAND_READID_METHOD_", "").lower()
            dev = [cparse.evaluate(a, symbols) for a in id_args[1:]]
            bpc, page, oob, ppb, bpl, _bad, _planes, luns, targets = (
                cparse.evaluate(a, symbols) for a in org
            )
            # The flags are SPINAND_INFO's sixth argument, after the model, id,
            # memory organisation, ECC requirement and op variants.
            flags = cparse.flag_names(args[5]) if len(args) > 5 else []
            # spinand_init_quad_enable() (core.c) sets CFG_QUAD_ENABLE, bit 0
            # of the configuration register (REG_CFG, feature 0xb0), for
            # SPINAND_HAS_QE_BIT; without it, the entry says nothing of one.
            qe = "SPINAND_HAS_QE_BIT" in flags
            notes = cparse.comments(raw[start:end])
            size = page * ppb * bpl * luns * targets
            records.append(
                make(
                    "linux",
                    rel,
                    cparse.line_of(raw, m.start()),
                    name,
                    type="nand",
                    vendor=vendor,
                    id=bytes([mfr_id, *dev]).hex(),
                    id_method=f"rdid_{method}",
                    size=size,
                    page_size=page,
                    erasers=[derive.block_eraser(0xD8, page * ppb, size).to_json()],
                    flags=flags,
                    quad_enable={"register": "nand-b0", "bit": 0} if qe else None,
                    via={"quad_enable": "SPINAND_HAS_QE_BIT"} if qe else {},
                    notes=[*notes, f"{bpc} bit(s) per cell, {oob} B OOB per page"],
                )
            )
    return records


def extract(root: Path) -> list[Record]:
    return extract_nor(root) + extract_nand(root)
