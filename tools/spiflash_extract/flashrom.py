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

from spiflash.derive import ERASE_BY_OPCODE

from . import cparse
from .ops import Opcodes
from .record import Record, feature_via, make

if TYPE_CHECKING:
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

_SUPPORTS_SFDP = re.compile(r"\s*[Ss]upports SFDP\.?\s*")

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
    # Only a comment about the entry itself: "the latter supports SFDP", or
    # "F model supports SFDP", is about another part of a multi-part entry.
    # The RDSFDP operation's via holds the comment (and implies ``sfdp``).
    sfdp = [n for n in notes if _SUPPORTS_SFDP.fullmatch(n)]
    notes = [n for n in notes if n not in sfdp]
    reg_bits = f.get("reg_bits", "")
    if re.search(r"\.bp\s*=", reg_bits):
        features.add("lock")
        claims.append(("lock", ".reg_bits .bp"))
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
        erasers=erasers or None,
        features=features,
        flags=flags,
        via=feature_via(claims),
        voltage=voltage,
        tested=tested,
        opcodes=_opcodes(f, method, flags, erasers, symbols, sfdp=bool(sfdp)),
        notes=notes,
    )


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
    "FEATURE_4BA_ENTER": [
        ("EN4B", ("JEDEC_ENTER_4_BYTE_ADDR_MODE",)),
        ("EX4B", ("JEDEC_EXIT_4_BYTE_ADDR_MODE",)),
    ],
    "FEATURE_4BA_ENTER_WREN": [
        ("EN4B", ("JEDEC_ENTER_4_BYTE_ADDR_MODE",)),
        ("EX4B", ("JEDEC_EXIT_4_BYTE_ADDR_MODE",)),
    ],
    "FEATURE_4BA_ENTER_EAR7": [
        ("WREAR", ("JEDEC_WRITE_EXT_ADDR_REG",)),
        ("RDEAR", ("JEDEC_READ_EXT_ADDR_REG",)),
    ],
    "FEATURE_4BA_EAR_C5C8": [
        ("WREAR", ("JEDEC_WRITE_EXT_ADDR_REG",)),
        ("RDEAR", ("JEDEC_READ_EXT_ADDR_REG",)),
    ],
    "FEATURE_4BA_EAR_1716": [
        ("BRWR", ("ALT_WRITE_EXT_ADDR_REG_17",)),
        ("BRRD", ("ALT_READ_EXT_ADDR_REG_16",)),
    ],
    "FEATURE_WRSR_WREN": [("WRSR", ("JEDEC_WRSR",))],
    "FEATURE_WRSR_EWSR": [("EWSR", ("JEDEC_EWSR",)), ("WRSR", ("JEDEC_WRSR",))],
    "FEATURE_WRSR2": [("WRSR2", ("JEDEC_WRSR2",))],
    "FEATURE_WRSR3": [("WRSR3", ("JEDEC_WRSR3",))],
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
) -> list[dict[str, object]]:
    """The operations a flashrom entry says the chip has: its probe, its
    read and write functions, each eraser (flashrom's spi_block_erase_<xx>
    sends 0x<xx>), the feature bits, and SFDP where a comment says so."""
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
    return ops.to_json()
