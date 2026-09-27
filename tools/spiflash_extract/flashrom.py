"""flashrom and flashprog: ``struct flashchip`` initialisers.

flashrom keeps one file per vendor under ``flashchips/``; its fork flashprog
still has the single ``flashchips.c``. The entries look alike::

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

The ids are ``#define``\\ d in ``include/flashchips.h``, where a manufacturer
in a later JEP106 bank carries its 0x7f continuation codes (``EON_ID
0x7F1C``) and has a ``_NOPREFIX`` twin for chips that leave them out. Names
use ``.`` as a wildcard (``W25Q128.V`` is the BV, FV and JV).

Only SPI chips are kept; parallel, LPC and FWH parts are not SPI flash.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from . import cparse
from .record import Record, make

if TYPE_CHECKING:
    from pathlib import Path

HEADER = "include/flashchips.h"
FLASH_H = "include/flash.h"  # the FEATURE_* bits

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

_ERASE_FEATURE = {4096: "erase_4k", 32 * 1024: "erase_32k", 64 * 1024: "erase_64k"}

_SKIP_IDS = {"GENERIC_MANUF_ID", "PROGMANUF_ID", "GENERIC_DEVICE_ID", "SFDP_DEVICE_ID"}


def chip_files(root: Path) -> list[Path]:
    """flashrom's per-vendor files, or flashprog's single table."""
    per_vendor = sorted((root / "flashchips").glob("*.c"))
    return per_vendor or [root / "flashchips.c"]


def _entries(path: Path, text: str) -> list[cparse.Block]:
    if path.name == "flashchips.c" and path.parent.name != "flashchips":
        table = cparse.array_body(text, r"struct\s+flashchip\s+flashchips\s*\[\s*\]")
        if table is None:
            raise ValueError(f"{path}: no flashchips[] table")
        return list(cparse.braced_items(table.body, table.offset))
    return list(cparse.braced_items(text))


def extract(root: Path, source: str) -> list[Record]:
    header_raw = (root / HEADER).read_text()
    header = cparse.strip_comments(header_raw)
    symbols: dict[str, str | int] = {
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
                raise ValueError(f"{rel}:{cparse.line_of(raw, entry.offset)}: {e}") from e
            if rec is not None:
                records.append(rec)
    return records


def _hex_bytes(value: int, min_bytes: int = 1) -> str:
    n = max(min_bytes, (value.bit_length() + 7) // 8)
    return f"{value:0{2 * n}x}"


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
        raise ValueError(f"{rel}: unknown probe {probe!r} for {f.get('name')}")
    name = cparse.c_string(f["name"])
    notes = cparse.comments(raw[entry.offset : entry.offset + len(entry.body)])
    for sym in (mfr_sym, model_sym):
        if sym in id_notes:
            notes.append(f"{sym}: {id_notes[sym]}")

    mfr = cparse.evaluate(mfr_sym, symbols)
    model = cparse.evaluate(model_sym, symbols)
    ext = None
    id_hex: str | None
    if method is None:
        id_hex = None  # no probe: a part with no id command (the M95320 EEPROM)
    elif method == "rdid":
        if model > 0xFFFF:
            # PROBE_SPI_BIG_SPANSION: RDID bytes 1-2 are the device id, and
            # bytes 4-5 (skipping 3, the id length) the extended id.
            ext = f"{model & 0xFFFF:04x}"
            model >>= 16
        id_hex = _hex_bytes(mfr) + f"{model:04x}"
    else:
        id_hex = _hex_bytes(mfr) + _hex_bytes(model)

    flags = cparse.bit_names(f.get("feature_bits", "0"), symbols, "FEATURE_")
    features = {feat for flag in flags for rx, feat in _FEATURES if rx.fullmatch(flag)}
    erasers = _erasers(f.get("block_erasers", "{}"), symbols)
    for e in erasers:
        if len(e["blocks"]) == 1 and e["opcode"] is not None:
            feat = _ERASE_FEATURE.get(e["blocks"][0][0])
            if feat:
                features.add(feat)
    if any(re.search(r"supports SFDP", n, re.IGNORECASE) for n in notes):
        features.add("sfdp")
    reg_bits = f.get("reg_bits", "")
    if re.search(r"\.bp\s*=", reg_bits):
        features.add("lock")
    size = cparse.evaluate(f["total_size"], symbols) * 1024
    if size > 16 * 1024 * 1024:
        features.add("4byte_addr")
    voltage = None
    if "voltage" in f:
        limits = cparse.split_top(f["voltage"].strip()[1:-1])
        voltage = [cparse.evaluate(v, symbols) for v in limits]
    tested = " ".join(f.get("tested", "").split()) or None
    uniform = [e["blocks"][0][0] for e in erasers if e["opcode"] == 0xD8 and len(e["blocks"]) == 1]
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
        sector_size=uniform[0] if uniform else None,
        erasers=erasers or None,
        features=sorted(features),
        flags=flags,
        voltage=voltage,
        tested=tested,
        notes=notes,
    )
