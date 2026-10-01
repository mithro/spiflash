"""flashrom and flashprog: ``struct flashchip`` initialisers.

flashrom keeps one file per vendor under :upstream:`flashrom:flashchips/`; its
fork flashprog still has the single :upstream:`flashprog:flashchips.c`. The
entries look alike::

    {
        .vendor         = "Winbond",
        .name           = "W25Q128.V",
        .bustype        = BUS_SPI,
        .manufacture_id = WINBOND_NEX_ID,       // flashprog: .id.manufacture
        .model_id       = WINBOND_NEX_W25Q128_V,  // flashprog: .id.model
        .total_size     = 16384,                // KiB
        .page_size      = 256,
        /* supports SFDP */
        .feature_bits   = FEATURE_WRSR_WREN | FEATURE_OTP | ...,
        .tested         = TEST_OK_PREWB,
        .probe          = PROBE_SPI_RDID,       // flashprog: .id.type = ID_SPI_RDID
        .block_erasers  = { { .eraseblocks = { {4 * 1024, 4096} },
                              .block_erase = SPI_BLOCK_ERASE_20 }, ... },
        .voltage        = {2700, 3600},
    },

The ids are ``#define``\\ d in :upstream:`flashrom:include/flashchips.h`, where a manufacturer
in a later JEP106 bank carries its 0x7f continuation codes (``EON_ID
0x7F1C``) and has a ``_NOPREFIX`` twin for chips that leave them out. Names
use ``.`` as a wildcard (``W25Q128.V`` is the BV, FV and JV).

Only SPI chips are kept; parallel, LPC and FWH parts are not SPI flash.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from spiflash.derive import DIE_ERASES, ERASE_BY_OPCODE
from spiflash.opcodes import OPERATIONS

from . import cparse
from .ops import Opcodes
from .record import Record, feature_via, make

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

HEADER = "include/flashchips.h"
FLASH_H = "include/flash.h"  # the FEATURE_* bits
SPI_H = "include/spi.h"  # the JEDEC_* opcodes

# The probe (flashrom .probe / flashprog .id.type) says how the id is read.
_METHODS = {
    "SPI_RDID": "rdid",
    "SPI_RDID4": "rdid",
    "SPI_AT45DB": "rdid",
    "SPI_AT45DB_E": "rdid",
    "SPI_BIG_SPANSION": "rdid",
    "SPI_REMS": "rems",
    "SPI_RES1": "res1",
    "SPI_RES2": "res2",
    "SPI_AT25F": "at25f",
    "SPI_ST95": "st95",
    "EDI_KB9012": "edi",
}

# Matched against the single-bit FEATURE_* names (composites such as
# FEATURE_QPI_38 are expanded to their bits first, see cparse.bit_names).
_FEATURES = [
    (re.compile(r"FEATURE_4BA_.*"), "4byte_addr"),
    (re.compile(r"FEATURE_4BA_(READ|FAST_READ|WRITE)"), "4byte_opcodes"),
    (re.compile(r"FEATURE_FAST_READ"), "fast_read"),
    (re.compile(r"FEATURE_FAST_READ_D(OUT|IO)"), "dual_read"),
    (re.compile(r"FEATURE_FAST_READ_Q(OUT|IO)"), "quad_read"),
    (re.compile(r"FEATURE_(QPI|QPI_35_F5|QPI_38_FF|FAST_READ_QPI4B)"), "qpi"),
    (re.compile(r"FEATURE_OTP"), "otp"),
    (re.compile(r"FEATURE_NO_ERASE"), "no_erase"),
]

#: The FEATURE_4BA_* bits giving a way into 4-byte mode (:upstream:`flashrom:include/flash.h`;
#: spi25.c's spi_enter_exit_4ba and spi_write_extended_address_register):
#: 0xb7 without and with a write enable first, bit 7 of the extended
#: address register, and 3-byte addresses with the top byte in the
#: extended address register (0xc5/0xc8) or the bank register (0x17/0x16).
#: FEATURE_4BA_READ, _FAST_READ and _WRITE are 4-byte operations.
FOUR_BYTE_MODES = {
    "FEATURE_4BA_ENTER": "en4b",
    "FEATURE_4BA_ENTER_WREN": "wren_en4b",
    "FEATURE_4BA_ENTER_EAR7": "ear_bit7",
    "FEATURE_4BA_EAR_C5C8": "wrear",
    "FEATURE_4BA_EAR_1716": "brwr",
}

_SUPPORTS_SFDP = re.compile(r"\s*[Ss]upports SFDP\.?\s*")

_UNITS = {"B": 1, "bytes": 1, "K": 1024, "KB": 1024, "KiB": 1024}
_SIZE = r"(\d+)\s*(B|bytes|KiB|KB|K)"
# "1024B total", "3x 512B", "4 x 256 bytes", "3*256B total", then what is
# reserved or pre-programmed, or how unequal regions split it.
_OTP = re.compile(
    rf"OTP: (?:(?P<n>\d+)\s*[xX*]\s*)?{_SIZE}(?: total)?"
    r"(?:, (?P<kept>\d+)B (?:reserved|pre-programmed)| \((?:\d+x \d+B(?:, )?)+\))?"
    r"(?:;(?P<ops>.*))?"
)
_OTP_REGIONS = re.compile(rf"(?P<n>\d+) x {_SIZE} Security Region \(OTP\)")

#: The commands an OTP comment names, and the operation each is ("read
#: 0x4b" only beside "write 0x42": :data:`OTP_READ`); ``None`` for one
#: with no operation here, which the comment, the record's ``via``, keeps:
#: ISSI's information row (0x68, 0x62, 0x64), Atmel's security register
#: (0x77, 0x9b, 0x9a) and PMC's 0xb1 program.
OTP_COMMANDS: dict[str, str | None] = {
    "read 0x48": "RSECR",
    "write 0x42": "PSECR",
    "erase 0x44": "ESECR",
    "read ID 0x4B": "RUID",
    "enter 0xB1": "ENSO",
    "exit 0xC1": "EXSO",
    "enter 0x3A": "ENTER_OTP_3A",
    "read 0x4B": None,
    "read 0x68": None,
    "write 0x62": None,
    "erase 0x64": None,
    "read 0x77": None,
    "read 0x77 (4 dummy bytes)": None,
    "write 0x9B": None,
    "write 0x9A (via buffer)": None,
    "write 0xB1": None,
}

#: Micron's, Spansion's, Intel's and AMIC's OTP read: 0x4b by address, with
#: the area programmed with 0x42 (a part whose OTP is programmed otherwise,
#: PMC's with 0xb1, reads it in a way not checked here).
OTP_READ = "READ_OTP"


#: OTP comments whose size is wrong, by entry name and comment: the
#: user-programmable bytes the part's datasheet gives, and why. The record
#: stores the datasheet's, with a note.
OTP_SIZE_WRONG: dict[tuple[str, str], tuple[int, str]] = {
    ("S25FL132K", "OTP: 768B total, 256B reserved; read 0x48; write 0x42, erase 0x44"): (
        768,
        (
            "the S25FL1-K datasheet (8.3) gives four 256-byte security registers, register "
            "0 holding the SFDP tables: three, 768 bytes, are the user's"
        ),
    ),
    ("W25Q40.V", "OTP: 756B total; read 0x48; write 0x42, erase 0x44, read ID 0x4B"): (
        768,
        (
            "the W25Q40BV has four 256-byte security registers, of which Winbond reserves "
            "register 0: 768 bytes are the user's (756 is a typo)"
        ),
    ),
}

#: Makers (their JEDEC byte) whose OTP comments name the wrong commands,
#: and the operations left out, with why: on ISSI's parts 0x48 and 0x42
#: read and write the function register (as openFPGALoader's FUNCR TB is
#: read, spiFlash.cpp), and the OTP area is the information row, read,
#: programmed and erased with 0x68, 0x62 and 0x64 (IS25LP128 datasheet;
#: flashrom's own IS25LP256 comment).
OTP_COMMANDS_WRONG: dict[int, tuple[frozenset[str], str]] = {
    0x9D: (
        frozenset({"RSECR", "PSECR", "ESECR"}),
        (
            "0x48 and 0x42 are ISSI's function register read and write, not its OTP "
            "area, the information row (0x68, 0x62, 0x64)"
        ),
    ),
}


def otp(note: str) -> tuple[dict[str, int], list[str]] | None:
    """The OTP area and the operations an OTP comment gives, where it is
    about the whole entry: ``"OTP: 1024B total; read 0x48; write 0x42,
    erase 0x44"`` is 1 KiB with ``RSECR``, ``PSECR`` and ``ESECR``,
    ``"OTP: 3x 512B"`` 1.5 KiB in three regions. The area is what the user
    can program: ``"1024B total, 256B reserved"`` (Winbond's security
    register 0) is 768 bytes, ``"128B total, 64B pre-programmed"`` (Atmel's
    unique id half) 64, as Linux's ``SNOR_OTP`` counts the regions it
    exposes. A comment qualified to
    another model of the entry (``"(B version only)"``, ``"later 3x
    1024B"``, ``"06E 64B total"``), or in any form not written here, is
    ``None``, and stays a note; a command qualified so (``"(A version
    only:) read ID 0x4B"``) is left out, the rest taken."""
    m = _OTP.fullmatch(note) or _OTP_REGIONS.fullmatch(note)
    if m is None:
        return None
    each = int(m[2]) * _UNITS[m[3]]
    regions = int(m["n"]) if m["n"] else None
    kept = int(m.groupdict().get("kept") or 0)
    area = {"size": each * (regions or 1) - kept}
    if regions is not None:
        area["regions"] = regions
    commands = []
    verb = ""
    for given in re.split(r"[;,]", (m.groupdict().get("ops") or "").strip()):
        item = re.sub(r"0x([0-9a-f]{2})", lambda h: "0x" + h[1].upper(), given.strip())
        if re.match(r"\([^)]* only:?\)", item):
            continue  # "(A version only:) read ID 0x4B": one model's command
        if re.fullmatch(r"0x[0-9A-F]{2}", item):
            item = f"{verb} {item}"  # "read 0x4B, 0x48": the verb before
        if not item:
            continue
        if item not in OTP_COMMANDS:
            return None
        verb = item.split()[0]
        commands.append(item)
    ops = [op for c in commands if (op := OTP_COMMANDS[c]) is not None]
    if "read 0x4B" in commands and "write 0x42" in commands:
        ops.append(OTP_READ)
    return area, ops


_SKIP_IDS = {"GENERIC_MANUF_ID", "PROGMANUF_ID", "GENERIC_DEVICE_ID", "SFDP_DEVICE_ID"}


def chip_files(root: Path) -> list[Path]:
    """flashrom's per-vendor files, or flashprog's single table."""
    per_vendor = sorted((root / "flashchips").glob("*.c"))
    return per_vendor or [root / "flashchips.c"]


def _entries(path: Path, text: str) -> list[cparse.Block]:
    if path.name == "flashchips.c" and path.parent.name != "flashchips":
        table = cparse.array_body(text, r"struct\s+flashchip\s+flashchips\s*\[\s*\]")
        if table is None:
            msg = f"{path}: no flashchips[] table"
            raise ValueError(msg)
        return list(cparse.braced_items(table.body, table.offset))
    return list(cparse.braced_items(text))


def extract(root: Path, source: str) -> list[Record]:
    header_raw = (root / HEADER).read_text()
    header = cparse.strip_comments(header_raw)
    symbols: dict[str, str | int] = {
        **cparse.defines(cparse.strip_comments((root / SPI_H).read_text())),
        **cparse.defines(cparse.strip_comments((root / FLASH_H).read_text())),
        **cparse.defines(header),
    }
    id_notes = cparse.define_comments(header_raw)
    records = []
    for path in chip_files(root):
        raw = path.read_text()
        text = cparse.drop_preprocessor(cparse.strip_comments(raw))
        rel = path.relative_to(root).as_posix()
        for entry in _entries(path, text):
            try:
                rec = _record(entry, raw, rel, source, symbols, id_notes)
            except (ValueError, KeyError) as e:
                msg = f"{rel}:{cparse.line_of(raw, entry.offset)}: {e}"
                raise ValueError(msg) from e
            if rec is not None:
                records.append(rec)
    return records


def _hex_bytes(value: int) -> str:
    """``value`` as hex, in as few whole bytes as hold it."""
    n = max(1, (value.bit_length() + 7) // 8)
    return f"{value:0{2 * n}x}"


def id_bytes(
    method: str | None, mfr: int, model: int, probe: str = ""
) -> tuple[str | None, str | None]:
    """The id and extended id a chip answers, read the way its probe reads them."""
    if method is None:
        return None, None  # no probe: a part with no id command (the M95320 EEPROM)
    if method == "res1":
        # RES (0xab) answers the one-byte electronic signature and nothing
        # else; flashrom gives these parts a manufacturer id of 0.
        return _hex_bytes(model), None
    if method != "rdid":
        return _hex_bytes(mfr) + _hex_bytes(model), None
    if probe == "SPI_BIG_SPANSION":
        # PROBE_SPI_BIG_SPANSION: RDID bytes 1-2 are the device id, and
        # bytes 4-5 the rest the model id holds. Byte 3, which the probe
        # skips, is the length of the id, 4Dh on these parts (the table in
        # probe_spi_big_spansion(), s25f.c), and part of the extended id as
        # the chip sends it and the other sources give it (4d 00 80).
        return _hex_bytes(mfr) + f"{model >> 16:04x}", f"4d{model & 0xFFFF:04x}"
    if model > 0xFFFF:
        msg = f"a model id of more than two bytes, 0x{model:x}, from probe {probe!r}"
        raise ValueError(msg)
    if mfr > 0xFF and probe != "SPI_RDID4":
        # An answer starting 0x7f is 7f, the maker, then the model:
        # flashrom's rdid_get_ids() (spi25.c) reads one model byte for
        # PROBE_SPI_RDID (PMC's 7f 9d 22) and two for PROBE_SPI_RDID4
        # (AMIC's 7f 37 20 10); flashprog's probe_spi_rdid() reads four
        # bytes where it can, three where not, so its ID_SPI_RDID model is
        # as wide as the table writes it (PMC_PM25LD020 0x22, AMIC_A25L05PT
        # 0x2020).
        return _hex_bytes(mfr) + _hex_bytes(model), None
    return _hex_bytes(mfr) + f"{model:04x}", None


def _erasers(expr: str, symbols: dict[str, str | int]) -> list[dict[str, Any]]:
    out = []
    for eraser in cparse.braced_items(expr.strip()[1:-1] if expr.strip().startswith("{") else expr):
        fields = cparse.designated(eraser.body)
        func = fields.get("block_erase", "").strip()
        if not func or func in ("NULL", "NO_BLOCK_ERASE_FUNC"):
            continue
        blocks = []
        for blk in cparse.braced_items(fields.get("eraseblocks", "{}").strip()[1:-1]):
            size, count = (cparse.evaluate(v, symbols) for v in cparse.split_top(blk.body))
            blocks.append([size, count])
        m = re.fullmatch(r"(?i)spi_block_erase_([0-9a-f]{2})", func)
        item: dict[str, Any] = {"opcode": int(m.group(1), 16) if m else None, "blocks": blocks}
        if m is None:
            item["function"] = func.lower()
        out.append(item)
    return out


#: The die erase opcodes: an eraser sending one erases a die at a time.
DIE_ERASE_OPCODES = frozenset(OPERATIONS[op].opcode for op in DIE_ERASES)


def _dies(erasers: list[dict[str, Any]]) -> tuple[int | None, dict[str, str]]:
    """The dies a die erase eraser gives (``spi_block_erase_c4`` over
    ``{64 MiB, 2}``: 2), and its ``via``. The eraser itself is not stored:
    the record's die erase layout is the dies'
    (:func:`spiflash.derive.die_erasers`)."""
    counts = {
        (e["opcode"], count)
        for e in erasers
        if e["opcode"] in DIE_ERASE_OPCODES
        for _, count in e["blocks"]
    }
    if not counts:
        return None, {}
    if len(counts) > 1 or any(
        len(e["blocks"]) > 1 for e in erasers if e["opcode"] in DIE_ERASE_OPCODES
    ):
        msg = f"die erase layouts of more than one die count: {sorted(counts)}"
        raise ValueError(msg)
    ((opcode, count),) = counts
    return count, {"dies": f"spi_block_erase_{opcode:02x}"}


def _record(
    entry: cparse.Block,
    raw: str,
    rel: str,
    source: str,
    symbols: dict[str, str | int],
    id_notes: dict[str, str],
) -> Record | None:
    f = cparse.designated(entry.body)
    if "BUS_SPI" not in f.get("bustype", ""):
        return None
    mfr_sym = (f.get("manufacture_id") or f.get("id.manufacture") or "").strip()
    model_sym = (f.get("model_id") or f.get("id.model") or "").strip()
    probe = (f.get("probe") or f.get("id.type") or "").strip()
    probe = re.sub(r"^(PROBE_|ID_)", "", probe)
    if mfr_sym in _SKIP_IDS or model_sym in _SKIP_IDS or probe in ("SPI_SFDP", "OPAQUE"):
        return None  # the generic "unknown chip" entries
    if not mfr_sym:
        return None  # no id at all: ENE's KB9012 EC, read over its own EDI protocol
    method = _METHODS.get(probe)
    if method is None and probe:
        msg = f"{rel}: unknown probe {probe!r} for {f.get('name')}"
        raise ValueError(msg)
    name = cparse.c_string(f["name"])
    notes = cparse.comments(raw[entry.offset : entry.offset + len(entry.body)])
    for sym in (mfr_sym, model_sym):
        if sym in id_notes:
            notes.append(f"{sym}: {id_notes[sym]}")

    mfr = cparse.evaluate(mfr_sym, symbols)
    model = cparse.evaluate(model_sym, symbols)
    id_hex, ext = id_bytes(method, mfr, model, probe)

    flags = cparse.bit_names(f.get("feature_bits", "0"), symbols, "FEATURE_")
    claims = [(feat, flag) for flag in flags for rx, feat in _FEATURES if rx.fullmatch(flag)]
    features = {feat for feat, _ in claims}
    erasers = _erasers(f.get("block_erasers", "{}"), symbols)
    dies, die_via = _dies(erasers)
    # Only a comment about the entry itself: "the latter supports SFDP", or
    # "F model supports SFDP", is about another part of a multi-part entry.
    # The RDSFDP operation's via holds the comment (and implies ``sfdp``).
    sfdp = [n for n in notes if _SUPPORTS_SFDP.fullmatch(n)]
    notes = [n for n in notes if n not in sfdp]
    # An OTP comment about the whole entry is its OTP area (and the
    # operations it names): the comment leaves the notes, into the area's
    # via, with FEATURE_OTP, which the area implies.
    parsed = {n: found for n in notes if (found := otp(n)) is not None}
    if len(parsed) > 1:
        msg = f"two OTP comments: {sorted(parsed)}"
        raise ValueError(msg)
    otp_area: dict[str, int] | None = None
    otp_ops: list[tuple[str, str]] = []
    otp_via: dict[str, str] = {}
    for note, (given, named) in parsed.items():
        notes.remove(note)
        otp_area = given
        fixed = OTP_SIZE_WRONG.get((name, note))
        if fixed is not None:
            notes.append(f"OTP area {fixed[0]} B, not the comment's {given['size']} B: {fixed[1]}")
            otp_area = {**given, "size": fixed[0]}
        wrong_ops, why = OTP_COMMANDS_WRONG.get(mfr & 0xFF, (frozenset(), ""))
        if wrong_ops & set(named):
            left = sorted(wrong_ops & set(named))
            notes.append(f"OTP comment's {', '.join(left)} left out: {why}")
        otp_ops = [(op, note) for op in named if op not in wrong_ops]
        otp_via = {"otp": "; ".join([note, *(fl for fl in flags if fl == "FEATURE_OTP")])}
    modes = {fl: FOUR_BYTE_MODES[fl] for fl in flags if fl in FOUR_BYTE_MODES}
    mode_via = {f"four_byte_modes:{mode}": fl for fl, mode in modes.items()}
    bits = _reg_bits(f.get("reg_bits", ""))
    quad_enable = bits.pop("qe", None)
    # FEATURE_WRSR_EXT3 is the EXT2 bit and one of its own, which has no
    # name: the bit names alone would give EXT2.
    ext3 = cparse.evaluate("FEATURE_WRSR_EXT3", symbols)
    if cparse.evaluate(f.get("feature_bits", "0"), symbols) & ext3 == ext3:
        flags = [*(fl for fl in flags if fl != "FEATURE_WRSR_EXT2"), "FEATURE_WRSR_EXT3"]
    size = cparse.evaluate(f["total_size"], symbols) * 1024
    voltage = None
    if "voltage" in f:
        limits = cparse.split_top(f["voltage"].strip()[1:-1])
        voltage = [cparse.evaluate(v, symbols) for v in limits]
    tested = " ".join(f.get("tested", "").split()) or None
    return make(
        source,
        rel,
        cparse.line_of(raw, entry.offset),
        name,
        vendor=cparse.c_string(f["vendor"]),
        id=id_hex,
        ext_id=ext,
        id_method=method,
        size=size,
        page_size=cparse.evaluate(f["page_size"], symbols) if "page_size" in f else None,
        erasers=[e for e in erasers if e["opcode"] not in DIE_ERASE_OPCODES] or None,
        features=features,
        flags=flags,
        via=feature_via(claims) | die_via | mode_via | otp_via,
        voltage=voltage,
        quad_enable=quad_enable,
        protection=bits or None,
        dies=dies,
        four_byte_modes=list(modes.values()),
        otp=otp_area,
        tested=tested,
        opcodes=_opcodes(
            f, method, flags, erasers, symbols, sfdp=bool(sfdp), source=source, otp_ops=otp_ops
        ),
        notes=notes,
    )


# enum flash_reg -> the register, named by the command reading it:
# spi25_statusreg.c reads STATUS2 with 0x35, STATUS3 and CONFIG with 0x15
# (Macronix's configuration register), SECURITY with RDSCUR (0x2b).
_REGISTERS = {
    "STATUS1": "sr1",
    "STATUS2": "sr2",
    "STATUS3": "sr3",
    "CONFIG": "sr3",
    "SECURITY": "security",
}
_WRITABILITY = {"RW": "rw", "RO": "ro", "OTP": "otp"}


def _reg_bit(value: str) -> dict[str, Any]:
    """``{STATUS2, 1, RW}`` as a register bit's JSON."""
    reg, bit, how = cparse.split_top(value.strip()[1:-1])
    # flashrom always says how the bit is written: RW too.
    return {
        "register": _REGISTERS[reg.strip()],
        "bit": cparse.evaluate(bit),
        "writability": _WRITABILITY[how.strip()],
    }


def _reg_bits(expr: str) -> dict[str, Any]:
    """``.reg_bits`` (``struct reg_bit_info``s by role, include/flash.h):
    each role's register bit, ``bp`` as ``bp0``, ``bp1``, ... in order; the
    quad enable bit (flashprog's ``.qe``) as ``qe``. flashprog's ``.dc``
    (the dummy-cycle bits) has no field."""
    if not expr.strip():
        return {}
    out: dict[str, Any] = {}
    for role, value in cparse.designated(expr.strip()[1:-1]).items():
        if role == "bp":
            bps = cparse.braced_items(value.strip()[1:-1])
            out.update({f"bp{i}": _reg_bit("{" + b.body + "}") for i, b in enumerate(bps)})
        elif role != "dc":
            out[role] = _reg_bit(value)
    return out


# flashprog's spi25_statusreg.c reads a CONFIG bit with RDCR (0x15) and a
# SECURITY bit with RDSCUR (0x2b) whatever the feature bits; flashrom only
# with FEATURE_CFGR and FEATURE_SCUR, which give those operations.
_FLASHPROG_READS = {"CONFIG": ("RDSR3", "JEDEC_RDCR"), "SECURITY": ("RDSCUR", "JEDEC_RDSCUR")}


def _register_reads(ops: Opcodes, source: str, reg_bits: str) -> None:
    """Add the register reads flashprog sends for the registers its
    ``.reg_bits`` name."""
    if source != "flashprog":
        return
    for reg, (op, symbol) in _FLASHPROG_READS.items():
        if re.search(rf"\b{reg}\b", reg_bits):
            ops.add(op, f".reg_bits {reg}", symbol)


# How flashrom reads an id -> the operation (probe_spi_rdid, probe_spi_rems,
# probe_spi_res1/2, probe_spi_at25f, probe_spi_st95).
_PROBE_OPS = {
    "rdid": ("RDID", "JEDEC_RDID"),
    "rems": ("REMS", "JEDEC_REMS"),
    "res1": ("RES", "JEDEC_RES"),
    "res2": ("RES", "JEDEC_RES"),
    "at25f": ("RDID_ATMEL", "AT25F_RDID"),
    "st95": ("RDID_M95", "ST_M95_RDID"),
}

# .read / .write functions -> the operation they issue.
_IO_OPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "spi_chip_read": ("READ_1_1_1", ("JEDEC_READ",)),
    "spi_chip_write256": ("PP_1_1_1", ("JEDEC_BYTE_PROGRAM",)),
    "spi_chip_write_256": ("PP_1_1_1", ("JEDEC_BYTE_PROGRAM",)),
    "spi_chip_write1": ("BP", ("JEDEC_BYTE_PROGRAM",)),
    "spi_chip_write_1": ("BP", ("JEDEC_BYTE_PROGRAM",)),
    "spi_aai_write": ("AAI_WP", ("JEDEC_AAI_WORD_PROGRAM",)),
    "spi_write_aai": ("AAI_WP", ("JEDEC_AAI_WORD_PROGRAM",)),
}

# Single FEATURE_* bits -> the operations they say the chip has, with the
# spi.h names of their opcodes (flashprog defines more of them than flashrom;
# where neither does, the value is the one flash.h's comment gives, which is
# the table's).
_FEATURE_OPS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "FEATURE_FAST_READ": [("READ_1_1_1_FAST", ("JEDEC_FAST_READ", "JEDEC_READ_FAST"))],
    "FEATURE_FAST_READ_DOUT": [("READ_1_1_2", ("JEDEC_FAST_READ_DOUT",))],
    "FEATURE_FAST_READ_DIO": [("READ_1_2_2", ("JEDEC_FAST_READ_DIO",))],
    "FEATURE_FAST_READ_QOUT": [("READ_1_1_4", ("JEDEC_FAST_READ_QOUT",))],
    "FEATURE_FAST_READ_QIO": [("READ_1_4_4", ("JEDEC_FAST_READ_QIO",))],
    "FEATURE_FAST_READ_QPI4B": [("READ_4_4_4_4B", ("JEDEC_FAST_READ_QIO_4BA",))],
    "FEATURE_4BA_READ": [("READ_1_1_1_4B", ("JEDEC_READ_4BA",))],
    "FEATURE_4BA_FAST_READ": [
        ("READ_1_1_1_FAST_4B", ("JEDEC_FAST_READ_4BA", "JEDEC_READ_4BA_FAST"))
    ],
    "FEATURE_4BA_WRITE": [("PP_1_1_1_4B", ("JEDEC_BYTE_PROGRAM_4BA",))],
    # The ways into 4-byte mode are the record's four_byte_modes
    # (:data:`FOUR_BYTE_MODES`), which give EN4B, the extended address
    # register's 0xc5/0xc8 and the bank register's 0x17/0x16; the way out,
    # 0xe9, is stated here (spi25.c, spi_enter_exit_4ba).
    "FEATURE_4BA_ENTER": [("EX4B", ("JEDEC_EXIT_4_BYTE_ADDR_MODE",))],
    "FEATURE_4BA_ENTER_WREN": [("EX4B", ("JEDEC_EXIT_4_BYTE_ADDR_MODE",))],
    "FEATURE_WRSR_WREN": [("WRSR", ("JEDEC_WRSR",))],
    "FEATURE_WRSR_EWSR": [("EWSR", ("JEDEC_EWSR",)), ("WRSR", ("JEDEC_WRSR",))],
    # How spi25_statusreg.c reads and writes STATUS2, STATUS3 and CONFIG:
    # with their own 0x31 and 0x11 (WRSR2, WRSR3), or with a 2- or 3-byte
    # 0x01 (WRSR_EXT2, WRSR_EXT3, which has EXT2's bit too, and CONFIG's
    # {SR1, CR}); read with 0x35, and 0x15. FEATURE_SCUR is "has security
    # register (RDSCUR/WRSCUR commands)".
    "FEATURE_WRSR2": [("WRSR2", ("JEDEC_WRSR2",)), ("RDSR2", ("JEDEC_RDSR2",))],
    "FEATURE_WRSR3": [("WRSR3", ("JEDEC_WRSR3",)), ("RDSR3", ("JEDEC_RDSR3",))],
    "FEATURE_WRSR_EXT2": [("WRSR_16", ("JEDEC_WRSR",)), ("RDSR2", ("JEDEC_RDSR2",))],
    "FEATURE_WRSR_EXT3": [
        ("WRSR_16", ("JEDEC_WRSR",)),
        ("WRSR_24", ("JEDEC_WRSR",)),
        ("RDSR2", ("JEDEC_RDSR2",)),
        ("RDSR3", ("JEDEC_RDSR3",)),
    ],
    "FEATURE_CFGR": [("RDSR3", ("JEDEC_RDCR",)), ("WRSR_16", ("JEDEC_WRSR",))],
    "FEATURE_SCUR": [("RDSCUR", ("JEDEC_RDSCUR",)), ("WRSCUR", ("JEDEC_WRSCUR",))],
    "FEATURE_QPI_35_F5": [("EQPI_35", ()), ("RSTQIO_F5", ())],
    "FEATURE_QPI_38_FF": [("EQPI_38", ()), ("RSTQIO_FF", ())],
    "FEATURE_SET_READ_PARAMS": [("SET_READ_PARAMS", ())],
}


def _opcodes(
    f: dict[str, str],
    method: str | None,
    flags: list[str],
    erasers: list[dict[str, Any]],
    symbols: dict[str, str | int],
    *,
    sfdp: bool,
    source: str = "flashrom",
    otp_ops: Sequence[tuple[str, str]] = (),
) -> list[dict[str, object]]:
    """The operations a flashrom entry says the chip has: its probe, its
    read and write functions, each eraser (flashrom's spi_block_erase_<xx>
    sends 0x<xx>), the feature bits, SFDP where a comment says so, and the
    OTP commands its OTP comment names (``otp_ops``: each with the
    comment)."""
    ops = Opcodes(symbols)
    if method in _PROBE_OPS:
        op, sym = _PROBE_OPS[method]
        ops.add(op, f"probe ({method})", sym)
    for field in ("read", "write"):
        func = f.get(field, "").strip().lower()
        if func in _IO_OPS:
            op, syms = _IO_OPS[func]
            ops.add(op, f".{field} = {func}", *syms)
            if op == "AAI_WP":
                ops.add("BP", f".{field} = {func}", "JEDEC_BYTE_PROGRAM")
    for e in erasers:
        if e["opcode"] is None:
            continue
        erase_op = ERASE_BY_OPCODE.get(e["opcode"])
        if erase_op is None:
            msg = f"no operation for erase opcode 0x{e['opcode']:02x}"
            raise ValueError(msg)
        size, count = e["blocks"][0]
        layout = f"{count} x {size}" if len(e["blocks"]) == 1 else "non-uniform"
        ops.add(erase_op, f"block_erasers ({layout})", value=e["opcode"])
    for flag in flags:
        for feature_op, feature_syms in _FEATURE_OPS.get(flag, []):
            ops.add(feature_op, flag, *feature_syms)
    if sfdp:
        ops.add("RDSFDP", "comment: supports SFDP", "JEDEC_SFDP")
    for op, note in otp_ops:
        ops.add(op, note)
    _register_reads(ops, source, f.get("reg_bits", ""))
    return ops.to_json()
