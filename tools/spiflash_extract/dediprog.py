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

The Linux software reads few of them (:upstream:`dediprog:parse.c`): to
identify a chip, its ``FlashIdentifier`` (:upstream:`dediprog:dpcmd.c`)
sends a fixed sequence of commands (0x9f, reading 4, 3 and 2 bytes; 0x15, 2;
0xab, 3 and 2; 0x90, 3, 2 and 5) and compares each answer, as a number, with
every entry's ``JedecDeviceID``. ``RDIDCommand`` and ``IDNumber`` are taken
here as the command an entry's id answers and how many bytes of it the
number holds: an SPI NAND id read with a dummy byte first starts with that
byte, 00 (``0x00EFAA21``, or ``0x00522D`` read as three bytes), and a RES
(0xab) answer read without its three dummy address bytes with three 0xff
(``0xFFFFFF14``). Where ``JedecDeviceID`` is missing, ``UniqueID``, which
otherwise repeats it, gives the id.

``ReadCmd``, ``ProgramCmd`` and ``EraseCmd`` pack one opcode per byte, the
lowest first (``struct ReadCommand`` and its siblings in
:upstream:`dediprog:Macro.h`): single, dual and quad read or program, and
chip, block and die erase; the fourth byte is reserved there, though the
octal parts have an octal opcode in it. Only the single-line read and
program are taken: the wider ones are often a template's defaults (the
single-I/O SST25LF040A lists quad read, 0x6b, and quad program, 0x32).
``BlockSizeInByte`` is a template's too: 64 KiB in nearly every entry, where
the other sources give 0xd8 and 0xdc 32, 128 or 256 KiB, or boot blocks. So
those two erases have no layout, and an SPI NOR record has a sector size only
for 0x20 (``SectorSizeInByte``) and 0x52 (see :func:`_erasers`); an SPI NAND
record's is its erase block, ``BlockSizeInByte``.

Entries of some classes are not what their attributes say. The DataFlash
(``Class="AT45DB..."``) entries carry a SPI NOR template (0xd8 erase, 256-byte
pages), while Dediprog's software reads their page size from the chip
(:upstream:`dediprog:SerialFlash.c`): only their id and size are taken. The
SST ``25xFxx``, ``25xFxxA`` and ``25xFxxB`` classes are written a byte or a
word at a time (``SetProgReadCommand`` in :upstream:`dediprog:project.c`),
so their 0x02 is byte program. And SPI NAND sizes that count the spare area
are scaled back to the data area.

Some ids are under the wrong command, and are read as what they are:

- a 0x90 (REMS) id of three bytes is the part's JEDEC id (the SST25WF512's
  bf 25 01);
- Sanyo's LE25FU and LE25FW parts answer 0x9f with their two id bytes,
  repeated (62 1d 62 1d ...): the entries give two or three of them, and the
  records take the two, as the RES id flashrom gives them;
- a 0x9f id of two bytes whose ``UniqueID`` is three bytes ending in them
  (the Terra TS25L parts: 20 11 and 20 20 11) is the three.

Left out, each a known kind of entry, and counted by :func:`skipped`:

- no id (``JedecDeviceID`` and ``UniqueID`` both missing, or 0): the
  microcontrollers (``Class="MCF"``) and the iCE65 FPGA configuration memory
  it also programs, and two flash parts;
- an id written with an odd number of hex digits (a typo: ``0x00FD585``);
- a two-byte id under 0x9f otherwise: the REMS or RES id of a part that does
  not answer 0x9f.

A command byte that is not an opcode of its slot at all (a status-register
write in the program word) is listed in :data:`_MISPLACED` and left out; an
entry whose read and program words are swapped is read the right way round.
Anything else it does not understand raises, naming the line.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET

from .ops import Opcodes
from .record import ERASE_FEATURES, Record, feature_via, make

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

DB = "ChipInfoDb.dedicfg"

# ICType -> the record's type. SD_NAND marks one SPI NAND part (the
# W25N02KWxIR, whose id and class are the other W25N parts').
_TYPES = {"SPI_NOR": "nor", "SPI_NAND": "nand", "SD_NAND": "nand"}

# The type of an entry with no ICType, by its class.
_CLASS_TYPES = {"UniversalSPINor": "nor"}

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

# The operation each RDIDCommand is, and its opcode.
_ID_COMMANDS = {
    0x9F: ("RDID", 0x9F),
    0x00AF009F: ("RDID", 0x9F),
    0x90: ("REMS", 0x90),
    0xAB: ("RES", 0xAB),
    0x15: ("RDID_ATMEL", 0x15),
}

SANYO = 0x62

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
    # The program word (0x00320002) as the read word.
    ("W25Q128JW-DTR", "ReadCmd", 0): 0x02,
    # The status-register write word (0x00113101) as the program word.
    ("W25Q128JW(3MHz)", "ProgramCmd", 0): 0x01,
    ("XT25F128F", "ProgramCmd", 0): 0xFC,
    ("GT25Q20C", "EraseCmd", 0): 0x01,  # EraseCmd="1"
    # Not an erase opcode of the W25M512JW (its dies are selected with 0xc2).
    ("W25M512JW", "EraseCmd", 1): 0xCD,
}

# The SST classes Dediprog writes a byte or a word at a time.
_BYTE_PROGRAM = ("25xFxx", "25xFxxA", "25xFxxB")

# Attributes set to true, kept as flags; true as Dediprog's own parser
# (parse.c, get_prop_bool) reads them: the value contains "true".
_BOOLEANS = ("QPIEnable", "MXIC_WPmode", "Micron_XIPmode", "Cypress_UnlockDYB")

# The raw attributes kept as flags.
_RAW = (
    "Class",
    "ProgramIOMethod",
    "ReadCmd",
    "ProgramCmd",
    "EraseCmd",
    "AlternativeID",
    "Voltage",
)


class LeftOutError(Exception):
    """An entry left out, and why."""


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
        try:
            records.append(_record(n, chip))
        except LeftOutError:
            continue
        except (ValueError, KeyError) as e:
            msg = f"{DB}:{n}: {chip.get('TypeName')}: {_why(e)}"
            raise ValueError(msg) from e
    return records


def skipped(root: Path) -> Counter[str]:
    """How many entries are left out, by reason."""
    reasons: Counter[str] = Counter()
    for n, chip in entries(root):
        try:
            _identify(chip)
        except LeftOutError as e:
            reasons[str(e)] += 1
        except (ValueError, KeyError) as e:
            msg = f"{DB}:{n}: {chip.get('TypeName')}: {_why(e)}"
            raise ValueError(msg) from e
    return reasons


def _why(e: Exception) -> str:
    """What went wrong, in words: a KeyError is a missing attribute."""
    return f"missing attribute {e.args[0]}" if isinstance(e, KeyError) else str(e)


def _digits(value: str) -> str:
    return re.sub(r"^0[xX]", "", value.strip()).lower()


def _type(chip: dict[str, str]) -> str:
    ictype = chip["ICType"]
    typ = _TYPES.get(ictype) or (None if ictype else _CLASS_TYPES.get(chip.get("Class", "")))
    if typ is None:
        msg = f"unknown ICType {ictype!r} (Class {chip.get('Class')!r})"
        raise ValueError(msg)
    return typ


def _raw_id(chip: dict[str, str]) -> bytes:
    """The id bytes as the entry writes them."""
    value = chip.get("JedecDeviceID") or chip.get("UniqueID")
    if value is None or int(value, 16) == 0:
        msg = "no id"
        raise LeftOutError(msg)
    digits = _digits(value)
    if len(digits) % 2:
        msg = "id with an odd number of hex digits"
        raise LeftOutError(msg)
    return bytes.fromhex(digits)


def _identify(chip: dict[str, str]) -> tuple[str, str, str | None, str]:
    """The type, id, extended id and id method of an entry; :class:`LeftOutError`
    for one left out."""
    raw = _raw_id(chip)
    typ = _type(chip)
    command = chip["RDIDCommand"]
    method = _METHODS.get(int(command, 16))
    if method is None:
        msg = f"unknown RDIDCommand {command}"
        raise ValueError(msg)
    if typ == "nand":
        if method == "rdid":
            raw = raw.rjust(int(chip["IDNumber"]), b"\0")
            if raw[0] == 0:
                return typ, raw[1:].hex(), None, "rdid_opcode_dummy"
            return typ, raw.hex(), None, "rdid_opcode"
    elif method == "rdid":
        # Sanyo's parts repeat their two id bytes.
        if raw[0] == SANYO and len(raw) in (2, 3) and raw[2:] in (b"", raw[:1]):
            return typ, raw[:2].hex(), None, "res2"
        if len(raw) == 2:
            unique = bytes.fromhex(_digits(chip.get("UniqueID", "")))
            if len(unique) != 3 or not unique.endswith(raw):
                msg = "a legacy id under 0x9f"
                raise LeftOutError(msg)
            raw = unique
        # A manufacturer in a later JEP106 bank has its 0x7f continuation
        # codes first, then one or two device bytes (PMC's 7f 9d 21). Past
        # the third byte of the others, what the programmer also compares.
        rest = raw.lstrip(b"\x7f")
        if len(rest) < len(raw) and len(rest) in (2, 3):
            return typ, raw.hex(), None, method
        if len(rest) == len(raw) >= 3:
            return typ, raw[:3].hex(), raw[3:].hex() or None, method
    elif method == "res":
        sig = raw.lstrip(b"\xff")  # the dummy address bytes, read as 0xff
        if len(sig) == 1:
            return typ, sig.hex(), None, "res1"
        if len(sig) == 2 or (len(sig) == 3 and sig[0] == 0x7F):
            return typ, sig.hex(), None, "res2"
    elif method == "rems" and len(raw) == 3:
        return typ, raw.hex(), None, "rdid"  # its JEDEC id
    elif len(raw) == 2:
        return typ, raw.hex(), None, method
    msg = f"cannot read id {raw.hex()} for {typ} RDIDCommand {command}"
    raise ValueError(msg)


def _record(line: int, chip: dict[str, str]) -> Record:
    typ, id_hex, ext_id, method = _identify(chip)
    size = int(chip["ChipSizeInKByte"]) * 1024
    page = int(chip["PageSizeInByte"])
    ops = Opcodes()
    command = int(chip["RDIDCommand"], 16)
    # The command the entry names (0xaf alone is no operation spiflash has),
    # but read-id for a JEDEC id under 0x90.
    if method == "rdid" and command == 0x90:
        ops.add("RDID", f"a JEDEC id under RDIDCommand={chip['RDIDCommand']}", value=0x9F)
    elif command in _ID_COMMANDS:
        op, value = _ID_COMMANDS[command]
        ops.add(op, f"RDIDCommand={chip['RDIDCommand']}", value=value)
    features: set[str] = set()
    claims: list[tuple[str, str]] = []
    erasers: list[dict[str, Any]] = []
    sector: int | None = None
    dataflash = chip.get("Class", "").startswith("AT45DB")
    if typ == "nand":
        sector = int(chip["BlockSizeInByte"])
        size = _nand_size(chip, size, page, sector)
    elif not dataflash:
        words = _words(chip)
        for word in ("ReadCmd", "ProgramCmd"):
            for _, byte, op in _opcodes(chip, word, words[word]):
                byte_program = op == "PP_1_1_1" and chip.get("Class") in _BYTE_PROGRAM
                ops.add("BP" if byte_program else op, f"{word}={words[word]}", value=byte)
        erasers, sector = _erasers(chip, ops, size)
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
            claims.append(("lock", f"ProtectBlockMask={chip['ProtectBlockMask']}"))
    if "true" in chip.get("QPIEnable", "") and not dataflash:
        features.add("qpi")
        claims.append(("qpi", "QPIEnable"))
    flags = [f"{key}={chip[key]}" for key in _RAW if chip.get(key)]
    jedec = chip.get("JedecDeviceID")
    if chip.get("UniqueID") and jedec and int(chip["UniqueID"], 16) != int(jedec, 16):
        flags.append(f"UniqueID={chip['UniqueID']}")
    flags += [key for key in _BOOLEANS if "true" in chip.get(key, "")]
    via = feature_via(claims)
    if command != 0x9F:
        # The command the id is read with, which id_method says.
        via["id_method"] = f"RDIDCommand={chip['RDIDCommand']}"
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
        page_size=None if dataflash else page,
        sector_size=sector,
        erasers=erasers or None,
        features=features | _erase_features(erasers),
        flags=flags,
        via=via,
        opcodes=ops.to_json(),
        notes=[description] if description else [],
    )


def _nand_size(chip: dict[str, str], size: int, page: int, block: int) -> int:
    """An SPI NAND size, without the spare area where it counts it (a whole
    number of blocks that is not a power of two). ``SpareSizeInByte`` holds
    two spare sizes (without and with the part's own ECC), one in each
    half."""
    blocks = size // block
    if not size % block and not blocks & (blocks - 1):
        return size
    spare = int(chip["SpareSizeInByte"], 16)
    for s in (spare >> 16, spare & 0xFFFF):
        data, rest = divmod(size * page, page + s)
        n = data // block
        if s and not rest and not data % block and not n & (n - 1):
            return data
    msg = f"size {size} is neither blocks of {block} nor that with a spare area"
    raise ValueError(msg)


def _words(chip: dict[str, str]) -> dict[str, str]:
    """The read and program words, the right way round: an entry with a
    program opcode first in its read word and a read opcode first in its
    program word has them swapped (the BG25Q80A's)."""
    read, program = chip["ReadCmd"], chip["ProgramCmd"]
    first = (int(read, 16) & 0xFF, int(program, 16) & 0xFF)
    if first[0] in _SLOTS["ProgramCmd"][0] and first[1] in _SLOTS["ReadCmd"][0]:
        read, program = program, read
    return {"ReadCmd": read, "ProgramCmd": program}


def _opcodes(chip: dict[str, str], word: str, text: str) -> Iterator[tuple[int, int, str]]:
    """Each opcode taken from a packed command word: its slot, its value
    and its operation."""
    value = int(text, 16)
    for slot, table in enumerate(_SLOTS[word]):
        byte = (value >> (8 * slot)) & 0xFF
        if not byte or _MISPLACED.get((chip["TypeName"], word, slot)) == byte:
            continue
        if byte not in table:
            msg = f"{word}={text}: unknown opcode 0x{byte:02x} in slot {slot}"
            raise ValueError(msg)
        yield slot, byte, table[byte]


def _erasers(
    chip: dict[str, str], ops: Opcodes, size: int
) -> tuple[list[dict[str, Any]], int | None]:
    """The erase layouts, and the sector size the block erase gives: chip
    erase; 0x20 over the ``SectorSizeInByte`` sectors; 0x52 over the SST
    parts' 32 KiB blocks, or an AT25F's ``SectorSizeInByte`` where that is
    not the template's 4096; and die erase over ``DieSizeInKByte`` dies.

    0xd8 and 0xdc have no layout: ``BlockSizeInByte`` is 64 KiB in nearly
    every entry, where other sources give 32 KiB (M25P05, EN25F10, ...),
    128 KiB (the MT35XU parts), 256 KiB (M25P128, S25FL512S) or boot blocks
    (the AMIC A25L..P parts)."""
    out: list[dict[str, Any]] = []
    sector = None
    for slot, byte, op in _opcodes(chip, "EraseCmd", chip["EraseCmd"]):
        ops.add(op, f"EraseCmd={chip['EraseCmd']}", value=byte)
        sectors = int(chip.get("SectorSizeInByte", "0"))
        if slot == 0:
            unit = size
        elif slot == 2:
            unit = int(chip.get("DieSizeInKByte", "0")) * 1024
        elif byte == 0x52 and int(chip["RDIDCommand"], 16) == 0x15:
            unit = 0 if sectors == 4096 else sectors
        elif byte == 0x52:
            unit = 32 * 1024
        elif byte == 0x20:
            unit = sectors
        else:
            unit = 0
        # None given, or a block larger than the chip (64 KiB on a 32 KiB
        # part): no layout.
        if not unit or unit > size:
            continue
        if size % unit:
            msg = f"size {size} is not a whole number of {unit}-byte blocks"
            raise ValueError(msg)
        out.append({"opcode": byte, "blocks": [[unit, size // unit]]})
        if slot == 1:
            sector = unit
    return out, sector


def _erase_features(erasers: list[dict[str, Any]]) -> set[str]:
    """What the block erases (not the chip or die erase) give."""
    return {
        ERASE_FEATURES[e["blocks"][0][0]]
        for e in erasers
        if e["opcode"] in (0x20, 0x52, 0xD8, 0xDC) and e["blocks"][0][0] in ERASE_FEATURES
    }
