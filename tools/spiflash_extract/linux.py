"""Linux: ``drivers/mtd/spi-nor/*.c`` (SPI NOR) and ``drivers/mtd/nand/spi/*.c``
(SPI NAND).

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
from typing import TYPE_CHECKING

from . import cparse
from .record import Record, make

if TYPE_CHECKING:
    from pathlib import Path

NOR_DIR = "drivers/mtd/spi-nor"
NAND_DIR = "drivers/mtd/nand/spi"

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
    return "".join(f"{b:02x}" for b in head), ("".join(f"{b:02x}" for b in ext) or None)


def _manufacturers(text: str) -> dict[str, str]:
    """``parts array name -> manufacturer name`` from the file's
    ``struct spi_nor_manufacturer`` initialisers."""
    out = {}
    for m in re.finditer(r"struct\s+spi_nor_manufacturer\s+\w+\s*=\s*\{", text):
        start = m.end() - 1
        fields = cparse.designated(text[start + 1 : cparse.matching(text, start)])
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


def extract_nor(root: Path) -> list[Record]:
    core_h = cparse.strip_comments((root / NOR_DIR / "core.h").read_text())
    symbols: dict[str, str | int] = dict(cparse.defines(core_h))
    records = []
    for path in sorted((root / NOR_DIR).glob("*.c")):
        if path.name in _NOT_TABLES:
            continue
        raw = path.read_text()
        text = cparse.drop_preprocessor(cparse.strip_comments(raw))
        local = {**symbols, **cparse.defines(cparse.strip_comments(raw))}
        vendors = _manufacturers(text)
        rel = f"{NOR_DIR}/{path.name}"
        for m in re.finditer(r"struct\s+flash_info\s+(\w+)\s*\[\s*\]\s*=\s*\{", text):
            start = m.end() - 1
            end = cparse.matching(text, start)
            table = cparse.Block(text[start + 1 : end], start + 1)
            vendor = vendors.get(m.group(1))
            for entry in cparse.braced_items(table.body, table.offset):
                rec = _nor_record(entry, raw, rel, vendor, local)
                if rec is not None:
                    records.append(rec)
    return records


def _nor_record(
    entry: cparse.Block, raw: str, rel: str, vendor: str | None, symbols: dict[str, str | int]
) -> Record | None:
    fields = cparse.designated(entry.body)
    raw_body = raw[entry.offset : entry.offset + len(entry.body)]
    name, notes = _entry_name(fields, raw_body)
    id_hex = ext = None
    if "id" in fields:
        args = cparse.macro_call(fields["id"], "SNOR_ID")
        if args is None:
            raise ValueError(f"{rel}: unexpected .id {fields['id']!r}")
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
    features = {_NOR_FEATURES[f] for f in flags if f in _NOR_FEATURES}
    if "otp" in fields:
        features.add("otp")
    if "4byte_opcodes" in features:
        features.add("4byte_addr")

    size = cparse.evaluate(fields["size"], symbols) if "size" in fields else None
    if size == 0:
        size = None
    if size is None and id_hex is not None:
        # "non-legacy flash entries in flash_info will have a size of zero
        # iff SFDP should be used" (struct flash_info, core.h)
        features.add("sfdp")
    if size is not None and size > 16 * 1024 * 1024:
        features.add("4byte_addr")
    sector = cparse.evaluate(fields.get("sector_size", "SZ_64K"), symbols)
    page = cparse.evaluate(fields.get("page_size", "256"), symbols)
    if "no_erase" not in features and sector == 64 * 1024:
        features.add("erase_64k")
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
        sector_size=sector,
        features=sorted(features),
        flags=flags,
        notes=notes,
    )


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
        vendor = None
        mm = re.search(r"struct\s+spinand_manufacturer\s+\w+\s*=\s*\{", text)
        mfr_id = None
        if mm:
            start = mm.end() - 1
            fields = cparse.designated(text[start + 1 : cparse.matching(text, start)])
            if "name" in fields:
                vendor = cparse.c_string(fields["name"])
            if "id" in fields:
                mfr_id = cparse.evaluate(fields["id"], symbols)
        for m in re.finditer(r"\bSPINAND_INFO\s*\(", text):
            start = m.end() - 1
            end = cparse.matching(text, start)
            args = cparse.split_top(text[start + 1 : end])
            name = cparse.c_string(args[0])
            id_args = cparse.macro_call(args[1], "SPINAND_ID")
            org = cparse.macro_call(args[2], "NAND_MEMORG")
            if id_args is None or org is None or mfr_id is None:
                raise ValueError(f"{rel}: cannot read SPINAND_INFO for {name}")
            method = id_args[0].replace("SPINAND_READID_METHOD_", "").lower()
            dev = [cparse.evaluate(a, symbols) for a in id_args[1:]]
            bpc, page, oob, ppb, bpl, _bad, _planes, luns, targets = (
                cparse.evaluate(a, symbols) for a in org
            )
            # SPINAND_INFO(model, id, memorg, eccreq, op_variants, flags, ...)
            flags = cparse.flag_names(args[5]) if len(args) > 5 else []
            features = ["quad_read"] if "SPINAND_HAS_QE_BIT" in flags else []
            notes = cparse.comments(raw[start:end])
            records.append(
                make(
                    "linux",
                    rel,
                    cparse.line_of(raw, m.start()),
                    name,
                    type="nand",
                    vendor=vendor,
                    id="".join(f"{b:02x}" for b in [mfr_id, *dev]),
                    id_method=f"rdid_{method}",
                    size=page * ppb * bpl * luns * targets,
                    page_size=page,
                    sector_size=page * ppb,
                    features=features,
                    flags=flags,
                    notes=[*notes, f"{bpc} bit(s) per cell, {oob} B OOB per page"],
                )
            )
    return records


def extract(root: Path) -> list[Record]:
    return extract_nor(root) + extract_nand(root)
