"""Dediprog: :upstream:`dediprog:ChipInfoDb.dedicfg`, the chip database of
the Linux software for its SF100 and SF600 programmers.

A UTF-16 XML file, one ``<Chip .../>`` per line, each with some seventy
attributes::

    <Chip TypeName="W25Q128FV" ICType="SPI_NOR" Manufacturer="Winbond"
      ChipSizeInKByte="16384" SectorSizeInByte="4096" BlockSizeInByte="65536"
      PageSizeInByte="256" AddrWidth="3" RDIDCommand="0x0000009F"
      IDNumber="3" JedecDeviceID="0xEF4018" ReadCmd="0x006B3B0B"
      ProgramCmd="0x00320002" EraseCmd="0x0000D8C7" QPIEnable="true"
      ProtectBlockMask="0x9C" Voltage="3.3V" Clock="104MHz" .../>

``JedecDeviceID`` is what the chip answers to ``RDIDCommand``, read
``IDNumber`` bytes at a time: the programmer's software sends the command,
reads that many bytes and compares the number
(:upstream:`dediprog:dpcmd.c`). So an SPI NAND id read with a dummy byte
first starts with that byte, 00 (``0x00EFAA21``), a number's leading zeros
dropped (``0x00522D`` read as three bytes); and RES (0xab), read without its
three dummy address bytes, starts with three 0xff (``0xFFFFFF14``).

``ReadCmd``, ``ProgramCmd`` and ``EraseCmd`` each pack an opcode per byte,
the lowest first: single, dual, quad and octal read (or program), and chip,
block and die erase (``struct ReadCommand`` and its siblings in
:upstream:`dediprog:Macro.h`). Only the single-line read and program are
taken: the wider ones are often a template's defaults (the single-I/O
SST25LF040A lists quad read, 0x6b, and quad program, 0x32). The block the
block erase erases is ``BlockSizeInByte``.

Left out, each a known kind of entry:

- no id (``JedecDeviceID`` missing, or 0): the microcontrollers
  (``Class="MCF"``) and iCE65 FPGA configuration memory it also programs;
- an ``ICType`` other than ``SPI_NOR`` or ``SPI_NAND`` (``SD_NAND``, or none);
- an id written with an odd number of hex digits (a typo: ``0x00FD585``);
- an SPI NOR id that does not fit its command: two bytes for read-id
  (0x9f), which answers three (a legacy REMS or RES id, under the wrong
  command), or three for REMS (0x90), which answers two.

A command byte that is not an opcode of its slot at all (a status-register
write in the program word) is listed in :data:`_MISPLACED` and left out.
Anything else it does not understand raises, naming the line.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET

from .ops import Opcodes
from .record import ERASE_FEATURES, Record, make

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

DB = "ChipInfoDb.dedicfg"

_TYPES = {"SPI_NOR": "nor", "SPI_NAND": "nand"}

# RDIDCommand -> how the id is read. 0xaf is Micron's multiple I/O read-id,
# which answers the same bytes as 0x9f; 0x00af009f lists both.
_METHODS = {
    0x9F: "rdid",
    0xAF: "rdid",
    0x00AF009F: "rdid",
    0x90: "rems",
    0xAB: "res",
    0x15: "at25f",
}

# The operation each id method sends.
_ID_OPS = {"rems": "REMS", "res1": "RES", "res2": "RES", "at25f": "RDID_ATMEL"}

# The operation each opcode of a packed command word is, by slot (the lowest
# byte first). Of the read and program words, only the single-line slot.
_SLOTS: dict[str, tuple[dict[int, str], ...]] = {
    "ReadCmd": (
        {
            0x03: "READ_1_1_1",
            0x0B: "READ_1_1_1_FAST",
            0x13: "READ_1_1_1_4B",
            0x0C: "READ_1_1_1_FAST_4B",
        },
    ),
    "ProgramCmd": ({0x02: "PP_1_1_1", 0x12: "PP_1_1_1_4B"},),
    "EraseCmd": (
        {0xC7: "CHIP_ERASE", 0x60: "CHIP_ERASE_ALT", 0x62: "CHIP_ERASE_ATMEL"},
        {0xD8: "SE", 0xDC: "SE_4B", 0x20: "BE_4K", 0x52: "BE_32K"},
        {0xC4: "DIE_ERASE"},
        {},
    ),
}

# Command bytes in the wrong word or slot: (part, word, slot) -> the byte.
_MISPLACED = {
    # Its read and program words swapped.
    ("BG25Q80A", "ReadCmd", 0): 0x02,
    ("BG25Q80A", "ProgramCmd", 0): 0x0B,
    # The program word (0x00320002) as the read word.
    ("W25Q128JW-DTR", "ReadCmd", 0): 0x02,
    # The status-register write word (0x00113101) as the program word.
    ("W25Q128JW(3MHz)", "ProgramCmd", 0): 0x01,
    ("XT25F128F", "ProgramCmd", 0): 0xFC,
    ("GT25Q20C", "EraseCmd", 0): 0x01,  # EraseCmd="1"
    # Not an erase opcode of the W25M512JW (its dies are selected with 0xc2).
    ("W25M512JW", "EraseCmd", 1): 0xCD,
}

# Attributes set to true, kept as flags; true as Dediprog's own parser
# (parse.c, get_prop_bool) reads them: the value contains "true".
_BOOLEANS = ("QPIEnable", "MXIC_WPmode", "Micron_XIPmode", "Cypress_UnlockDYB")

# The raw attributes kept as flags.
_RAW = ("Class", "ProgramIOMethod", "ReadCmd", "ProgramCmd", "EraseCmd", "AlternativeID")


def entries(root: Path) -> Iterator[tuple[int, dict[str, str]]]:
    """Each ``<Chip>``: its line, and its attributes."""
    text = (root / DB).read_text(encoding="utf-16")
    seen = 0
    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if "<Chip" not in line:
            continue
        if not (line.startswith("<Chip ") and line.endswith("/>")):
            msg = f"{DB}:{n}: not one <Chip .../> on its own line"
            raise ValueError(msg)
        try:
            chip = ET.fromstring(line)
        except ET.ParseError as e:
            msg = f"{DB}:{n}: {e}"
            raise ValueError(msg) from e
        seen += 1
        yield n, dict(chip.attrib)
    if not seen:
        msg = f"{DB}: no <Chip> entries"
        raise ValueError(msg)


def extract(root: Path) -> list[Record]:
    records = []
    for n, chip in entries(root):
        if skip_reason(chip) is not None:
            continue
        try:
            records.append(_record(n, chip))
        except (ValueError, KeyError) as e:
            msg = f"{DB}:{n}: {chip.get('TypeName')}: {e}"
            raise ValueError(msg) from e
    return records


def skipped(root: Path) -> Counter[str]:
    """How many entries are left out, by reason."""
    return Counter(r for _, chip in entries(root) if (r := skip_reason(chip)) is not None)


def skip_reason(chip: dict[str, str]) -> str | None:
    """Why an entry is left out (see the module's docstring), or None."""
    jedec = chip.get("JedecDeviceID")
    if jedec is None or int(jedec, 16) == 0:
        return "no id"
    if chip.get("ICType") not in _TYPES:
        return f"ICType {chip.get('ICType')!r}"
    digits = _digits(jedec)
    if len(digits) % 2:
        return "id with an odd number of hex digits"
    method = _METHODS.get(int(chip["RDIDCommand"], 16))
    if (chip["ICType"], method, len(digits) // 2) in (
        ("SPI_NOR", "rdid", 2),
        ("SPI_NOR", "rems", 3),
    ):
        return "id that does not fit its command"
    return None


def _digits(value: str) -> str:
    return re.sub(r"^0[xX]", "", value.strip()).lower()


def _id(chip: dict[str, str], typ: str) -> tuple[str, str | None, str]:
    """The id, extended id and id method of an entry."""
    command = chip["RDIDCommand"]
    method = _METHODS.get(int(command, 16))
    if method is None:
        msg = f"unknown RDIDCommand {command}"
        raise ValueError(msg)
    raw = bytes.fromhex(_digits(chip["JedecDeviceID"]))
    if typ == "nand":
        if method == "rdid":
            raw = raw.rjust(int(chip["IDNumber"]), b"\0")
            if raw[0] == 0:
                return raw[1:].hex(), None, "rdid_opcode_dummy"
            return raw.hex(), None, "rdid_opcode"
    elif method == "rdid":
        # A manufacturer in a later JEP106 bank has its 0x7f continuation
        # codes first, then one or two device bytes (PMC's 7f 9d 21). Past
        # the third byte of the others, what the programmer also compares.
        rest = raw.lstrip(b"\x7f")
        if len(rest) < len(raw) and len(rest) in (2, 3):
            return raw.hex(), None, method
        if len(rest) == len(raw) >= 3:
            return raw[:3].hex(), raw[3:].hex() or None, method
    elif method == "res":
        sig = raw.lstrip(b"\xff")  # the dummy address bytes, read as 0xff
        if len(sig) == 1:
            return sig.hex(), None, "res1"
        if len(sig) == 2 or (len(sig) == 3 and sig[0] == 0x7F):
            return sig.hex(), None, "res2"
    elif len(raw) == 2:
        return raw.hex(), None, method
    msg = f"cannot read id {chip['JedecDeviceID']} for {typ} RDIDCommand {command}"
    raise ValueError(msg)


def _record(line: int, chip: dict[str, str]) -> Record:
    typ = _TYPES[chip["ICType"]]
    id_hex, ext_id, method = _id(chip, typ)
    size = int(chip["ChipSizeInKByte"]) * 1024
    block = int(chip["BlockSizeInByte"])
    command = int(chip["RDIDCommand"], 16)
    ops = Opcodes()
    if not method.startswith("rdid"):
        ops.add(_ID_OPS[method], f"RDIDCommand={chip['RDIDCommand']}", value=command)
    elif command != 0xAF:
        ops.add("RDID", f"RDIDCommand={chip['RDIDCommand']}", value=0x9F)
    features: set[str] = set()
    erasers: list[dict[str, Any]] = []
    if typ == "nor":
        for word in ("ReadCmd", "ProgramCmd"):
            for _, byte, op in _opcodes(chip, word):
                ops.add(op, f"{word}={chip[word]}", value=byte)
        erasers = _erasers(chip, ops, size, block)
        if "READ_1_1_1_FAST" in ops or "READ_1_1_1_FAST_4B" in ops:
            features.add("fast_read")
        if any(op.endswith("_4B") for op in ops):
            features.add("4byte_opcodes")
        # Not AddrWidth, which is 4 for some 32 KiB parts.
        if size > 16 * 1024 * 1024:
            features.add("4byte_addr")
        # The status register bits to clear to unprotect the chip: BP0 to
        # BP4 are bits 2 to 6.
        if int(chip.get("ProtectBlockMask", "0"), 16) & 0x7C:
            features.add("lock")
    if "true" in chip.get("QPIEnable", ""):
        features.add("qpi")
    flags = [f"{key}={chip[key]}" for key in _RAW if chip.get(key)]
    if int(chip.get("UniqueID", "0"), 16) not in (0, int(chip["JedecDeviceID"], 16)):
        flags.append(f"UniqueID={chip['UniqueID']}")
    if command != 0x9F:
        flags.append(f"RDIDCommand={chip['RDIDCommand']}")
    flags += [key for key in _BOOLEANS if "true" in chip.get(key, "")]
    description = chip.get("Description", "").strip()
    return make(
        "dediprog",
        DB,
        line,
        chip["TypeName"],
        type=typ,
        vendor=chip["Manufacturer"],
        id=id_hex,
        ext_id=ext_id,
        id_method=method,
        size=size,
        page_size=int(chip["PageSizeInByte"]),
        sector_size=block,
        erasers=erasers or None,
        features=features | _erase_features(erasers),
        flags=flags,
        opcodes=ops.to_json(),
        notes=[description] if description else [],
    )


def _opcodes(chip: dict[str, str], word: str) -> Iterator[tuple[int, int, str]]:
    """Each opcode taken from a packed command word: its slot, its value
    and its operation."""
    value = int(chip[word], 16)
    for slot, table in enumerate(_SLOTS[word]):
        byte = (value >> (8 * slot)) & 0xFF
        if not byte or _MISPLACED.get((chip["TypeName"], word, slot)) == byte:
            continue
        if byte not in table:
            msg = f"{word}={chip[word]}: unknown opcode 0x{byte:02x} in slot {slot}"
            raise ValueError(msg)
        yield slot, byte, table[byte]


def _erasers(chip: dict[str, str], ops: Opcodes, size: int, block: int) -> list[dict[str, Any]]:
    """Chip erase; the block erase over ``BlockSizeInByte`` blocks, or for
    0x20 the 4 KiB sectors (0x52's block is not given: 64 KiB in the table
    where the SST25LF020A's erases 32 KiB); and die erase over
    ``DieSizeInKByte`` dies."""
    out: list[dict[str, Any]] = []
    for slot, byte, op in _opcodes(chip, "EraseCmd"):
        ops.add(op, f"EraseCmd={chip['EraseCmd']}", value=byte)
        unit = {
            0: size,
            1: {0xD8: block, 0xDC: block, 0x20: int(chip.get("SectorSizeInByte", "0"))}.get(byte),
            2: int(chip.get("DieSizeInKByte", "0")) * 1024,
        }[slot]
        # None given, or a block larger than the chip (64 KiB on a 32 KiB
        # part): no layout.
        if unit and not size % unit:
            out.append({"opcode": byte, "blocks": [[unit, size // unit]]})
    return out


def _erase_features(erasers: list[dict[str, Any]]) -> set[str]:
    """What the block erases (not the chip or die erase) give."""
    return {
        ERASE_FEATURES[e["blocks"][0][0]]
        for e in erasers
        if e["opcode"] in (0x20, 0x52, 0xD8, 0xDC) and e["blocks"][0][0] in ERASE_FEATURES
    }
