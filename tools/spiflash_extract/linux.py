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
from spiflash.enums import DataPhase, FlashType
from spiflash.opcodes import OPERATIONS, sort_key

from . import cparse
from .ops import Opcodes, add_4b_variants, add_spinor
from .record import Record, feature_via, make

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

NOR_DIR = "drivers/mtd/spi-nor"
NAND_DIR = "drivers/mtd/nand/spi"
SPINOR_H = "include/linux/mtd/spi-nor.h"  # the SPINOR_OP_* opcodes
SPINAND_H = "include/linux/mtd/spinand.h"  # the SPINAND_*_OP operations

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

#: The dies of the parts whose fixups set ``params->n_dice`` from the size,
#: which for these entries the part's SFDP tables give at run time, by
#: (fixups, part): Winbond's ``size / SZ_64M`` (winbond.c:159), and
#: Spansion's 2 for the 2 Gbit S25H and S28H parts only ("The 2 Gb parts
#: duplicate info and advertise 4 dice instead of 2", spansion.c:644-646
#: and 715-717); its 512 Mbit and 1 Gbit parts' come from their SFDP tables
#: alone, which Linux does not carry.
_SIZED_DIES = {
    ("winbond_nor_multi_die_fixups", "W25Q01JV"): 2,  # 1 Gbit
    ("winbond_nor_multi_die_fixups", "W25Q02JV"): 4,  # 2 Gbit
    ("s25hx_t_fixups", "S25HL02GT"): 2,
    ("s25hx_t_fixups", "S25HS02GT"): 2,
    ("s28hx_t_fixups", "S28HL02GT"): 2,
    ("s28hx_t_fixups", "S28HS02GT"): 2,
}

#: The fixups whose ``n_dice`` depends on the size, and so is in
#: :data:`_SIZED_DIES` for the parts it is known for.
_SIZED_DIES_FIXUPS = frozenset(f for f, _ in _SIZED_DIES)

#: The ``params->ready`` functions that select each die in turn to poll
#: it, and the operation selecting one (winbond.c:109-145).
_DIE_SELECT_READY = {"winbond_nor_multi_die_ready": ("DIE_SELECT", "WINBOND_NOR_OP_SELDIE")}


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
        fixups = _Fixups(
            _fixups(text, "quad_enable"),
            set(_fixups(text, "locking_ops")),
            _fixups(text, "n_dice"),
            _fixups(text, "die_erase_opcode"),
            _fixups(text, "ready"),
        )
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
    #: The dies (``n_dice``), the die erase opcode and the ready function
    #: they set, by fixups name.
    dies: dict[str, list[str]]
    die_erase: dict[str, list[str]]
    ready: dict[str, list[str]]


def _dies(
    fixup: str, name: str, fixups: _Fixups, symbols: dict[str, str | int]
) -> tuple[int | None, list[dict[str, object]], dict[str, str]]:
    """The dies an entry's fixups set (``params->n_dice``), its die erase
    (``params->die_erase_opcode``, where it has more than one die) and die
    select, and the ``via`` of the dies. ``n_dice`` is a number
    (micron-st.c's 4 and 2, spansion.c's 1 for the S25FS256T), or one the
    size gives (:data:`_SIZED_DIES`); any other value raises."""
    ops = Opcodes(symbols)
    for ready in fixups.ready.get(fixup, ()):
        if ready in _DIE_SELECT_READY:
            op, symbol = _DIE_SELECT_READY[ready]
            ops.add(op, f"{fixup}: ready = {ready}", symbol)
    values = fixups.dies.get(fixup)
    if not values:
        return None, ops.to_json(), {}
    if fixup in _SIZED_DIES_FIXUPS:
        dies = _SIZED_DIES.get((fixup, name.upper()))
    elif len(values) == 1 and values[0].isdigit():
        dies = int(values[0])
    else:
        msg = f"{fixup} sets n_dice to {values}, which no table here maps"
        raise ValueError(msg)
    if dies is None:
        return None, ops.to_json(), {}
    if dies > 1:
        for symbol in fixups.die_erase.get(fixup, ()):
            op = derive.ERASE_BY_OPCODE[cparse.evaluate(symbol, symbols)]
            ops.add(op, f"{fixup}: die_erase_opcode = {symbol}", symbol)
    return dies, ops.to_json(), {"dies": f"{fixup}: n_dice = {' or '.join(values)}"}


def protection(flags: list[str], symbols: dict[str, str | int]) -> tuple[Any, dict[str, str]]:
    """The block-protection layout Linux's (and U-Boot's) status register
    locking gives an entry (``spi_nor_sr_lock``, swp.c), and its ``via``:
    for ``SPI_NOR_HAS_LOCK``, BP0 to BP2 and SRWD (``SR_BP0``...,
    ``SR_SRWD``) in SR1; BP3 for ``SPI_NOR_4BIT_BP`` (``SR_BP3``, or
    ``SR_BP3_BIT6`` with ``SPI_NOR_BP3_SR_BIT6``); TB for
    ``SPI_NOR_HAS_TB`` (``SR_TB_BIT5``, or
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
        out["tb"] = bit("SR_TB_BIT6" if bit6 else "SR_TB_BIT5")
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
    # .addr_nbytes = 2: the part takes 2-byte addresses (Everspin's MR25H
    # MRAM); spi_nor_set_addr_nbytes() takes it as given. No other value is
    # in the table.
    if "addr_nbytes" in fields:
        nbytes = cparse.evaluate(fields["addr_nbytes"], symbols)
        if nbytes != 2:
            msg = f".addr_nbytes = {nbytes}: only 2 is known"
            raise ValueError(msg)
        claims.append(("2byte_addr", f".addr_nbytes = {nbytes}"))
    # .n_banks: the banks its read-while-write reads one of while writing
    # another (SPI_NOR_RWW); no field holds it.
    if "n_banks" in fields:
        flags.append(f"n_banks={cparse.evaluate(fields['n_banks'], symbols)}")
    otp, otp_via = _otp(fields, rel, symbols)
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
    dies, die_ops, dies_via = _dies(fixup, name, fixups, symbols)
    if any(o["op"] in derive.DIE_ERASES for o in die_ops):
        # spi_nor_erase() erases a part of several dies die by die with its
        # die_erase_opcode, and never sends the chip erase.
        opcodes = [o for o in opcodes if o["op"] != "CHIP_ERASE"]
    opcodes += die_ops
    if otp is not None:
        ops = Opcodes(symbols)
        for op in ("RSECR", "PSECR", "ESECR"):
            ops.add(op, f"{otp_via['otp']} (winbond_nor_otp_ops)", f"SPINOR_OP_{op}")
        opcodes += ops.to_json()
    via = feature_via(claims) | dies_via | otp_via
    layout = None
    if fixup in fixups.locking:
        notes.append(f"no block protection bits: its {fixup} replace the status register locking")
    else:
        layout, protection_via = protection(flags, symbols)
        via |= protection_via
        if layout and "cmp" in layout:
            # spi_nor_read_cr(): CMP is in the configuration register, 0x35.
            ops = Opcodes(symbols)
            ops.add("RDSR2", "spi_nor_read_cr: CMP in SR2", "SPINOR_OP_RDCR")
            opcodes += ops.to_json()
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
        dies=dies,
        otp=otp,
        opcodes=opcodes,
        notes=notes,
    )


def _otp(
    fields: dict[str, str], rel: str, symbols: dict[str, str | int]
) -> tuple[dict[str, int] | None, dict[str, str]]:
    """The OTP area an entry's ``.otp = SNOR_OTP(len, n_regions, base,
    offset)`` gives (core.h: ``n_regions`` regions of ``len`` bytes, the
    first at ``base``, each ``offset`` after the last), and its ``via``,
    which keeps where the regions are. Only Winbond's entries have one, and
    winbond_nor_late_init() reads, programs and erases it with the
    security-register commands (winbond_nor_otp_ops: spi_nor_otp_read_secr,
    ...), which the caller adds; one elsewhere raises, to be looked at."""
    if "otp" not in fields:
        return None, {}
    args = cparse.macro_call(fields["otp"], "SNOR_OTP")
    if args is None or len(args) != 4:
        msg = f"unexpected .otp {fields['otp']!r}"
        raise ValueError(msg)
    if not rel.endswith("/winbond.c"):
        msg = f"{rel}: an .otp outside winbond.c, whose OTP operations are not known here"
        raise ValueError(msg)
    length, regions = (cparse.evaluate(a, symbols) for a in args[:2])
    given = ", ".join(a.strip() for a in args)
    return {"size": length * regions, "regions": regions}, {"otp": f"SNOR_OTP({given})"}


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

# mfr_flags -> the operations micron-st.c (USE_FSR: micron_st_nor_ready),
# spansion.c (USE_CLSR, USE_CLPEF: spansion_nor_clear_sr) or sst.c
# (SST_WRITE: sst_nor_write, AAI words with a byte program at either end,
# sst_nor_write_data) sends for it.
_MFR_OPS = {
    "USE_FSR": ("RDFSR",),
    "USE_CLSR": ("CLSR",),
    "USE_CLPEF": ("CLPEF",),
    "SST_WRITE": ("AAI_WP", "BP"),
}


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
    # The manufacturer flags micron-st.c, spansion.c and sst.c read: the flag
    # status register for ready, clearing the error bits after a failure,
    # and SST's word-at-a-time write.
    for flag in cparse.flag_names(fields.get("mfr_flags", "0")):
        for op in _MFR_OPS.get(flag, ()):
            add_spinor(ops, op, flag)
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


#: The entries whose ``NAND_MEMORG`` oobsize is not the record's
#: ``oob_size``, the spare bytes per page the part's parameter page gives:
#: their oobsize is another view of the spare area, and is noted instead.
#: Each is keyed by the part, with the oobsize it gives (a changed one
#: raises, to be looked at again).
_OOB_OTHER_VIEW = {
    # Datasheet Rev. 1.6, Table 8: 2048+64 with ECC enabled, 2048+128 with
    # it disabled; the parameter page's spare bytes per page: 128.
    "MX35LF2GE4AD": (
        64,
        (
            "oobsize 64 B is the spare left with the on-die ECC enabled; "
            "its parameter page gives 128 B (datasheet Table 8)"
        ),
    ),
    "MX35LF4GE4AD": (
        128,
        (
            "oobsize 128 B is the spare left with the on-die ECC enabled; "
            "its parameter page gives 256 B (datasheet Table 8)"
        ),
    ),
    # Datasheet Rev. H: 64 B of spare (0x800-0x83f), then 32 B of ECC
    # parity (0x840-0x85f); the parameter page's spare bytes per page: 0x40.
    "W25N01KV": (
        96,
        (
            "oobsize 96 B is the spare area and the ECC parity area; "
            "its parameter page gives 64 B of spare"
        ),
    ),
}


class _Shape(NamedTuple):
    """What one ``SPI_MEM_OP`` macro puts on the bus, single transfer rate:
    the opcode (an expression of the macro's parameters, ``reset ? 0x02 :
    0x84``), the address bytes (an expression) and lines, the dummy bytes
    (an expression) and lines, and the data's direction and lines."""

    params: tuple[str, ...]
    opcode: str
    address_bytes: str
    address_lines: int
    dummy: str | None
    dummy_lines: int
    data: str | None  # "in", "out"
    data_lines: int


# One phase of a SPI_MEM_OP: SPI_MEM_OP_CMD(0x0b, 1), SPI_MEM_OP_ADDR(2, addr, 1), ...
_PHASE = re.compile(r"SPI_MEM_OP_(CMD|ADDR|DUMMY|DATA_IN|DATA_OUT|NO_\w+|MAX_FREQ)\b(?:\((.*)\))?")

# A function-like macro that is one SPI_MEM_OP.
_OP_MACRO = re.compile(r"^\s*#\s*define\s+(\w+)\(([^)]*)\)\s*SPI_MEM_OP\((.*)$", re.MULTILINE)


def op_shapes(text: str) -> dict[str, _Shape | None]:
    """Each function-like ``#define NAME(params) SPI_MEM_OP(...)`` in
    ``text`` (comments already stripped): its shape, or ``None`` for one
    with a double transfer rate phase (``SPI_MEM_DTR_OP_*``), which
    spiflash has no operation for."""
    out: dict[str, _Shape | None] = {}
    joined = re.sub(r"\\\n", " ", text)
    for m in _OP_MACRO.finditer(joined):
        name, params, rest = m.group(1), m.group(2), m.group(3)
        body = rest[: cparse.matching("(" + rest, 0) - 1]
        if "SPI_MEM_DTR_" in body:
            out[name] = None
            continue
        phases: dict[str, list[str]] = {}
        for part in cparse.split_top(body):
            p = _PHASE.fullmatch(part.strip())
            if p is None:
                msg = f"{name}: cannot read {part.strip()!r}"
                raise ValueError(msg)
            phases[p.group(1)] = cparse.split_top(p.group(2)) if p.group(2) else []
        opcode, _lines = phases["CMD"]
        addr = phases.get("ADDR")
        dummy = phases.get("DUMMY")
        data = phases.get("DATA_IN") or phases.get("DATA_OUT")
        out[name] = _Shape(
            params=tuple(p.strip() for p in params.split(",") if p.strip()),
            opcode=opcode.strip(),
            address_bytes=addr[0].strip() if addr else "0",
            address_lines=cparse.evaluate(addr[2]) if addr else 0,
            dummy=dummy[0].strip() if dummy else None,
            dummy_lines=cparse.evaluate(dummy[1]) if dummy else 0,
            data=("in" if "DATA_IN" in phases else "out") if data else None,
            data_lines=cparse.evaluate(data[2]) if data else 0,
        )
    return out


def _nand_operation(opcode: int, shape: _Shape, args: dict[str, int] | None = None) -> str:
    """The SPI NAND operation of an opcode sent in ``shape``, with the
    macro's arguments ``args``; raises for one spiflash has none of, so a
    new kind of operation is noticed."""
    address_bytes = cparse.evaluate(shape.address_bytes, args or {})
    protocol = f"1-{shape.address_lines}-{shape.data_lines}"
    phase = {"in": DataPhase.READ, "out": DataPhase.WRITE, None: None}[shape.data]
    for op in OPERATIONS.values():
        if (
            op.flash_type is FlashType.NAND
            and op.opcode == opcode
            and op.protocol == protocol
            and op.address_bytes == address_bytes
            and op.data is phase
        ):
            return op.name
    msg = f"no SPI NAND operation 0x{opcode:02x} {protocol}, {address_bytes} address bytes"
    raise ValueError(msg)


class _NandOp(NamedTuple):
    """One operation an op variant gives: its name, and the dummy clocks it
    passes (``None`` for a macro with no dummy phase to give)."""

    op: str
    dummy_clocks: int | None


def _variant(call: str, shapes: dict[str, _Shape | None]) -> _NandOp | None:
    """The operation of one op variant (``SPINAND_PAGE_READ_FROM_CACHE_1S_1S_4S_OP(0,
    1, NULL, 0, 0)``), with the dummy clocks its arguments give: dummy bytes
    x 8 over the dummy phase's lines. ``None`` for a double transfer rate
    one (:func:`op_shapes`)."""
    m = re.fullmatch(r"\s*(\w+)\s*\((.*)\)\s*", call, re.DOTALL)
    if m is None or m.group(1) not in shapes:
        msg = f"unknown op variant {call.strip()!r}"
        raise ValueError(msg)
    shape = shapes[m.group(1)]
    if shape is None:
        return None
    args = dict(zip(shape.params, (a.strip() for a in cparse.split_top(m.group(2))), strict=True))
    choice = re.fullmatch(r"\(?\s*(\w+)\s*\?\s*(\w+)\s*:\s*(\w+)\s*\)?", shape.opcode)
    if choice:  # reset ? 0x02 : 0x84: a program load, or a random one
        flag, yes, no = choice.groups()
        opcode = cparse.evaluate(yes if args[flag] in ("true", "1") else no)
    else:
        opcode = cparse.evaluate(shape.opcode)
    numbers = {k: int(v) for k, v in args.items() if v.isdigit()}
    clocks = None
    if shape.dummy is not None and re.search(r"[A-Za-z_]", shape.dummy):
        dummy_bytes = cparse.evaluate(shape.dummy, numbers)
        clocks, rest = divmod(dummy_bytes * 8, shape.dummy_lines)
        if rest:
            msg = f"{call.strip()}: {dummy_bytes} dummy bytes on {shape.dummy_lines} lines"
            raise ValueError(msg)
    return _NandOp(_nand_operation(opcode, shape, numbers), clocks)


def _variant_tables(text: str) -> dict[str, list[str]]:
    """Each ``SPINAND_OP_VARIANTS(name, ...)`` table in ``text``: its
    variants, each a macro call, in order."""
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"\bSPINAND_OP_VARIANTS\s*\(", text):
        start = m.end() - 1
        name, *calls = cparse.split_top(text[start + 1 : cparse.matching(text, start)])
        out[name.strip()] = calls
    return out


#: The operations Linux's SPI NAND core (core.c) sends every part, whatever
#: its entry says: driver defaults. The block erase is the eraser's.
_NAND_CORE_OPS = (
    "SPINAND_PAGE_READ_1S_1S_0_OP",
    "SPINAND_PROG_EXEC_1S_1S_0_OP",
    "SPINAND_GET_FEATURE_1S_1S_1S_OP",
    "SPINAND_SET_FEATURE_1S_1S_1S_OP",
)

#: How each ``SPINAND_SELECT_TARGET`` function selects a die:
#: ``w25m02gv_select_target`` sends 0xc2 and the die (winbond.c:292-300),
#: ``micron_select_target`` sets bit 6 of feature 0xd0 (micron.c:24-31,
#: 137-149; ``MICRON_SELECT_DIE(x) ((x) << 6)``).
_SELECT_TARGET: dict[str, tuple[str, dict[str, object] | str]] = {
    "w25m02gv_select_target": ("op", "SPINAND_WINBOND_SELECT_TARGET_1S_0_1S"),
    "micron_select_target": ("bit", {"register": "nand-d0", "bit": 6}),
}


def _nand_ops(
    args: list[str],
    tables: dict[str, list[str]],
    shapes: dict[str, _Shape | None],
) -> list[dict[str, object]]:
    """An entry's operations: those of its read from cache, write to cache
    and update cache op variants (``SPINAND_INFO_OP_VARIANTS``; the
    continuous read ones ``_WITH_CONT`` adds are the same opcodes, read
    another way, and not taken), each with the most dummy clocks its
    variants give it (the variant without a clock limit; a part may take
    fewer below a lower clock), and the core's defaults."""
    found = [cparse.macro_call(a, "SPINAND_INFO_OP_VARIANTS") for a in args] + [
        cparse.macro_call(a, "SPINAND_INFO_OP_VARIANTS_WITH_CONT") for a in args
    ]
    names = next((f for f in found if f is not None), None)
    if names is None:
        msg = "no SPINAND_INFO_OP_VARIANTS"
        raise ValueError(msg)
    out: list[dict[str, object]] = []
    for table in (n.strip().lstrip("&") for n in names[:3]):
        clocks: dict[str, int | None] = {}
        for v in (v for c in tables[table] if (v := _variant(c, shapes)) is not None):
            given = clocks.get(v.op)
            clocks[v.op] = v.dummy_clocks if given is None else max(given, v.dummy_clocks or 0)
        for op, n in clocks.items():
            if any(o["op"] == op for o in out):
                continue
            out.append({"op": op, "via": table, **({"dummy_clocks": n} if n is not None else {})})
    for macro in _NAND_CORE_OPS:
        shape = shapes[macro]
        if shape is None:
            msg = f"{macro} is not a single transfer rate operation"
            raise ValueError(msg)
        op = _nand_operation(cparse.evaluate(shape.opcode), shape)
        out.append({"op": op, "via": f"every part ({macro})", "assumed": True})
    return out


def extract_nand(root: Path) -> list[Record]:
    """SPI NAND: ``SPINAND_INFO("name", SPINAND_ID(method, bytes...),
    NAND_MEMORG(bits_per_cell, pagesize, oobsize, pages_per_eraseblock,
    eraseblocks_per_lun, max_bad_eraseblocks_per_lun, planes_per_lun,
    luns_per_target, ntargets), NAND_ECCREQ(strength, step),
    SPINAND_INFO_OP_VARIANTS(&read, &write, &update), flags, ...)``.

    The op variant tables are each file's ``SPINAND_OP_VARIANTS``, of the
    ``SPINAND_*_OP`` macros in :upstream:`linux:include/linux/mtd/spinand.h`
    and the file's own. The dies are ``luns_per_target`` x ``ntargets``;
    only a part of more than one target (``ntargets``) selects a die
    (``SPINAND_SELECT_TARGET``): the others' dies (LUNs) are row address
    bits."""
    spinand_h = cparse.strip_comments((root / SPINAND_H).read_text())
    records = []
    for path in sorted((root / NAND_DIR).glob("*.c")):
        if path.name in ("core.c", "otp.c"):
            continue
        raw = path.read_text()
        stripped = cparse.strip_comments(raw)
        symbols: dict[str, str | int] = dict(cparse.defines(stripped))
        text = cparse.drop_preprocessor(stripped)
        shapes = op_shapes(spinand_h) | op_shapes(stripped)
        tables = _variant_tables(text)
        rel = f"{NAND_DIR}/{path.name}"
        makers = _nand_manufacturers(text, symbols)
        for m in re.finditer(r"\bSPINAND_INFO\s*\(", text):
            start = m.end() - 1
            end = cparse.matching(text, start)
            args = cparse.split_top(text[start + 1 : end])
            name = cparse.c_string(args[0])
            try:
                fields = _nand_fields(args, symbols, tables, shapes)
            except (ValueError, KeyError) as e:
                msg = f"{rel}:{cparse.line_of(raw, m.start())}: {name}: {e}"
                raise ValueError(msg) from e
            vendor, mfr_id = makers(m.start())
            if mfr_id is None:
                msg = f"{rel}: no manufacturer for {name}"
                raise ValueError(msg)
            records.append(
                make(
                    "linux",
                    rel,
                    cparse.line_of(raw, m.start()),
                    name,
                    vendor=vendor,
                    id=bytes([mfr_id, *fields.pop("device")]).hex(),
                    **{**fields, "notes": [*cparse.comments(raw[start:end]), *fields["notes"]]},
                )
            )
    return records


def _nand_fields(
    args: list[str],
    symbols: dict[str, str | int],
    tables: dict[str, list[str]],
    shapes: dict[str, _Shape | None],
) -> dict[str, Any]:
    """The record fields of one ``SPINAND_INFO``, and its device id bytes
    (``"device"``)."""
    id_args = cparse.macro_call(args[1], "SPINAND_ID")
    org = cparse.macro_call(args[2], "NAND_MEMORG")
    ecc = cparse.macro_call(args[3], "NAND_ECCREQ")
    if id_args is None or org is None or ecc is None:
        msg = "cannot read its SPINAND_ID, NAND_MEMORG or NAND_ECCREQ"
        raise ValueError(msg)
    method = id_args[0].replace("SPINAND_READID_METHOD_", "").lower()
    bpc, page, oob, ppb, bpl, bad, planes, luns, targets = (
        cparse.evaluate(a, symbols) for a in org
    )
    if bpc != 1:  # every entry's: no field holds it
        msg = f"{bpc} bits per cell"
        raise ValueError(msg)
    strength, step = (cparse.evaluate(a, symbols) for a in ecc)
    # The flags are SPINAND_INFO's sixth argument, after the model, id,
    # memory organisation, ECC requirement and op variants.
    flags = cparse.flag_names(args[5]) if len(args) > 5 else []
    opcodes = _nand_ops(args, tables, shapes)
    via: dict[str, str] = {}
    # spinand_init_quad_enable() (core.c:1794-1802) sets CFG_QUAD_ENABLE,
    # bit 0 of the configuration register (REG_CFG, feature 0xb0), for
    # SPINAND_HAS_QE_BIT where an op variant moves data on four lines. It
    # clears the bit on every other part: the core's default, not the
    # part's (the XT26G01D has the flag absent and a QE bit quad reads
    # need), so without the flag the entry says nothing of one.
    quad_enable: dict[str, object] | None = None
    if "SPINAND_HAS_QE_BIT" in flags:
        quad_enable = {"register": "nand-b0", "bit": 0}
        via["quad_enable"] = "SPINAND_HAS_QE_BIT"
    notes: list[str] = []
    oob_size: int | None = oob
    other = _OOB_OTHER_VIEW.get(cparse.c_string(args[0]))
    if other is not None:
        if other[0] != oob:
            msg = f"NAND_MEMORG oobsize {oob}, not {other[0]}: remove it from _OOB_OTHER_VIEW"
            raise ValueError(msg)
        notes.append(other[1])
        oob_size = None
    die_select_bit = None
    for a in args[6:]:
        target = cparse.macro_call(a, "SPINAND_SELECT_TARGET")
        if target is None:
            continue
        how, what = _SELECT_TARGET[target[0].strip()]
        token = f"SPINAND_SELECT_TARGET({target[0].strip()})"
        if how == "bit":
            die_select_bit = what
            via["die_select_bit"] = token
        else:
            shape = shapes[str(what)]
            if shape is None:
                msg = f"{what} is not a single transfer rate operation"
                raise ValueError(msg)
            op = _nand_operation(cparse.evaluate(shape.opcode), shape)
            opcodes.append({"op": op, "via": token})
    otp = None
    for a in args[6:]:
        user = cparse.macro_call(a, "SPINAND_USER_OTP_INFO")
        if user is None:
            continue
        npages = cparse.evaluate(user[0], symbols)
        via["otp"] = f"SPINAND_USER_OTP_INFO({', '.join(u.strip() for u in user)})"
        wrong = USER_OTP_WRONG.get(cparse.c_string(args[0]))
        if wrong is not None:
            notes.append(f"user OTP {wrong[0]} pages, not {npages}: {wrong[1]}")
            npages = wrong[0]
        # The user's OTP pages, each a page of data.
        otp = {"size": npages * page}
    if (die_select_bit is not None or any(o["op"] == "NAND_DIE_SELECT" for o in opcodes)) != (
        targets > 1
    ):
        msg = f"{targets} targets, and a die select only where there are several"
        raise ValueError(msg)
    size = page * ppb * bpl * luns * targets
    return {
        "device": [cparse.evaluate(a, symbols) for a in id_args[1:]],
        "type": "nand",
        "id_method": f"rdid_{method}",
        "size": size,
        "page_size": page,
        "erasers": [derive.block_eraser(0xD8, page * ppb, size).to_json()],
        "oob_size": oob_size,
        "planes": planes,
        "dies": luns * targets,
        "die_select_bit": die_select_bit,
        "max_bad_blocks": bad,
        "ecc": {"strength_bits": strength, "step_bytes": step},
        "flags": flags,
        "via": via,
        "quad_enable": quad_enable,
        "otp": otp,
        "opcodes": sorted(opcodes, key=lambda o: sort_key(str(o["op"]))),
        "notes": notes,
    }


#: ``SPINAND_USER_OTP_INFO`` page counts that are wrong, by part: the
#: datasheet's, and why. The record stores the datasheet's, with a note.
USER_OTP_WRONG = {
    "MT29F2G01ABAGD": (
        10,
        (
            'its datasheet (M79A, Rev. G, "Security - One Time Programmable") gives "Ten '
            'full pages per die", at page addresses 02h-0Bh'
        ),
    ),
}


def extract(root: Path) -> list[Record]:
    return extract_nor(root) + extract_nand(root)
