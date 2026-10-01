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
those two erases have no layout: an SPI NOR record has a block eraser only
for 0x20 (``SectorSizeInByte``) and 0x52 (see :func:`_erasers`), so a sector
size only from its 0x52 blocks (:func:`spiflash.derive.sector_size`). An SPI
NAND record's block erase is over its ``BlockSizeInByte`` blocks.
``PageSizeInByte`` is a template's on SPI NOR as well: 256 on all but seven
entries, the SST parts written a byte at a time among them, so only a page
other than the template's is stored (:data:`NOR_PAGE_TEMPLATE`).

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

from spiflash import derive
from spiflash.model import strip_continuation

from .ops import Opcodes
from .record import Record, feature_via, make

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
)

#: ``Voltage``: the supply dpcmd powers the part at once it has found it
#: (parse.c maps "3.3V", "2.5V" and "1.8V" to VoltageInMv, anything else to
#: 3300; project.c, GetFirstDetectionMatch, sets g_Vcc from it): the
#: record's ``supply_mv``, in millivolts. 1.2 V parts are in the table,
#: which dpcmd would power at 3.3 V; the table's value is kept.
VOLTAGES = {"1.2V": 1200, "1.8V": 1800, "2.5V": 2500, "3.3V": 3300}

#: ``AlternativeID`` values that are not an id the part answers to a legacy
#: command, by the id's manufacturer byte and the value: the same for parts
#: of every density (a template), so no part's RES id.
ALTERNATIVE_TEMPLATES = {
    (0x89, "15"): "Intel's S33 parts of 16, 32 and 64 Mbit all give 0x15",
    (0x8C, "8c"): "ESMT's F25L parts of every density give their maker's byte, 0x8c",
}

#: Makers whose RES (0xab) answers two bytes, the maker's and the part's
#: (flashrom's PROBE_SPI_RES2 for Sanyo): a one-byte ``AlternativeID`` is
#: not their answer.
RES2_MAKERS = {0x62: "Sanyo"}


_NO_RES = (
    "the M25PX parts answer 0xab only as release from deep power-down, with no "
    "signature (M25PX80 datasheet, Rev. B, the command table)"
)

#: ``AlternativeID`` (or ``UniqueID``) values that are wrong, by part and
#: value as the table writes them, and why, each checked against the part's
#: datasheet: left out, with a note.
ALTERNATIVE_WRONG = {
    (
        "XM25QH128A",
        "0x2016",
    ): "the XM25QH128A answers REMS with 20 17 (its datasheet, Rev. H, Table 6)",
    ("XM25QU128C", "0x2118"): (
        "the XM25QU128C answers REMS with 20 17 (its datasheet, Rev. 2.1, 7.1.1)"
    ),
    (
        "ZD25Q40",
        "0xEF12",
    ): "0xef is Winbond's maker byte, not Zetta's (0xba): another part's REMS id",
    ("M25PX80", "0x13"): _NO_RES,
    ("M25PX16", "0x14"): _NO_RES,
    ("M25PX32", "0x15"): _NO_RES,
    ("M25PX64", "0x15"): _NO_RES,
}


def legacy_ids(
    chip: dict[str, str], id_hex: str, method: str
) -> tuple[list[list[str]], list[str], bool]:
    """The ids ``AlternativeID`` and ``UniqueID`` say the part also answers,
    notes on those left out, and whether ``UniqueID`` was taken (so is no
    flag). Dediprog does not say which command reads them (parse.c and
    project.c compare them with what the probe read); their forms say: one
    byte is the RES (0xab) electronic signature (M25P16's 0x14, as
    flashrom's res1 entries for the M25P05 to M25P40-OLD give), two bytes the
    REMS (0x90) maker and part (W25Q40's 0xef12, EN25QH128's 0x1c17 and
    EN25P20's 0x1c11, as their datasheets give). ``UniqueID`` is mostly the
    JEDEC id again; only its two-byte REMS forms are taken (Eon's EN25P20,
    EN25T80, EN25B40, EN25S16), a three-byte one that differs from the id
    staying a flag. Left out: none, a copy of the id (of a legacy id, its
    tail), a template (:data:`ALTERNATIVE_TEMPLATES`), a one-byte id of a
    maker whose RES answers two (:data:`RES2_MAKERS`), a wrong one
    (:data:`ALTERNATIVE_WRONG`), and three bytes or more."""
    found: list[list[str]] = []
    notes: list[str] = []
    unique_taken = False
    maker = strip_continuation(bytes.fromhex(id_hex))[1][0]
    for attr in ("AlternativeID", "UniqueID"):
        raw = chip.get(attr, "").strip()
        value = raw.lower().removeprefix("0x").lstrip("0") or ""
        if not value:
            continue
        value = value.zfill(len(value) + len(value) % 2)
        if attr == "UniqueID" and len(value) != 4:
            continue  # the JEDEC id, or another: not a legacy id
        if int(value, 16) == int(id_hex, 16) or (method != "rdid" and id_hex.endswith(value)):
            continue
        token = f"{attr}={raw}"
        wrong = ALTERNATIVE_WRONG.get((chip.get("TypeName", ""), raw))
        if wrong is not None:
            notes.append(f"{token} left out: {wrong}")
            unique_taken |= attr == "UniqueID"
            continue
        if (maker, value) in ALTERNATIVE_TEMPLATES:
            notes.append(f"{token} left out: {ALTERNATIVE_TEMPLATES[(maker, value)]}")
            continue
        if len(value) == 2 and maker in RES2_MAKERS:
            why = f"{RES2_MAKERS[maker]}'s RES answers two bytes, not one"
            notes.append(f"{token} left out: {why}")
            continue
        if len(value) == 2:
            given = ["res1", value]
        elif len(value) == 4:
            if id_hex.endswith(value):
                continue  # the id's last two bytes
            given = ["rems", value]
        else:
            notes.append(f"{token} left out: {len(value) // 2} bytes, not a legacy id")
            continue
        if given not in found:
            found.append(given)
        unique_taken |= attr == "UniqueID"
    return found, notes, unique_taken


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


#: The SPI NOR ``PageSizeInByte`` of the template: 256 on all but seven of
#: the SPI NOR entries, the SST parts Dediprog writes a byte or a word at
#: a time among them (whose flashrom pages are 1 or 32 bytes), so no
#: part's page size; the other value, 512, is the part's.
NOR_PAGE_TEMPLATE = 256


def _record(line: int, chip: dict[str, str]) -> Record:
    typ, id_hex, ext_id, method = _identify(chip)
    size = int(chip["ChipSizeInKByte"]) * 1024
    page = int(chip["PageSizeInByte"])
    ops = Opcodes()
    command = int(chip["RDIDCommand"], 16)
    # The command the entry names (0xaf alone is no operation spiflash has),
    # but read-id for a JEDEC id under 0x90. A SPI NAND part's read-id is
    # its id method's (spiflash.derive.NAND_ID_OPERATION).
    if typ == "nand":
        pass
    elif method == "rdid" and command == 0x90:
        ops.add("RDID", f"a JEDEC id under RDIDCommand={chip['RDIDCommand']}", value=0x9F)
    elif command in _ID_COMMANDS:
        op, value = _ID_COMMANDS[command]
        ops.add(op, f"RDIDCommand={chip['RDIDCommand']}", value=value)
    features: set[str] = set()
    claims: list[tuple[str, str]] = []
    erasers: list[dict[str, Any]] = []
    nand: dict[str, Any] = {}
    dataflash = chip.get("Class", "").startswith("AT45DB")
    if typ == "nand":
        block = int(chip["BlockSizeInByte"])
        size = _nand_size(chip, size, page, block)
        erasers = [derive.block_eraser(0xD8, block, size).to_json()]
        # The high half of SpareSizeInByte: the whole spare area of a page.
        nand["oob_size"] = (int(chip["SpareSizeInByte"], 16) >> 16) or None
        if "true" in chip.get("SupportLUT", ""):
            # The bad block lookup table: swap a block, read the table.
            ops.add("NAND_BBM_SWAP", "SupportLUT=true", value=0xA1)
            ops.add("NAND_READ_BBM_LUT", "SupportLUT=true", value=0xA5)
    elif not dataflash:
        words = _words(chip)
        for word in ("ReadCmd", "ProgramCmd"):
            for _, byte, op in _opcodes(chip, word, words[word]):
                byte_program = op == "PP_1_1_1" and chip.get("Class") in _BYTE_PROGRAM
                ops.add("BP" if byte_program else op, f"{word}={words[word]}", value=byte)
        # Not AddrWidth, which is 4 for some 32 KiB parts: the size, and
        # the operations, imply 4-byte addressing.
        erasers = _erasers(chip, ops, size)
        # The status register bits to clear to unprotect the chip: BP0 to
        # BP4 are bits 2 to 6.
        if int(chip.get("ProtectBlockMask", "0"), 16) & 0x7C:
            features.add("lock")
            claims.append(("lock", f"ProtectBlockMask={chip['ProtectBlockMask']}"))
    if "true" in chip.get("QPIEnable", "") and not dataflash:
        features.add("qpi")
        claims.append(("qpi", "QPIEnable"))
    flags = [f"{key}={chip[key]}" for key in _RAW if chip.get(key)]
    legacy, legacy_notes, unique_taken = (
        legacy_ids(chip, id_hex, method) if id_hex else ([], [], False)
    )
    jedec = chip.get("JedecDeviceID")
    unique = chip.get("UniqueID")
    if unique and jedec and int(unique, 16) != int(jedec, 16) and not unique_taken:
        flags.append(f"UniqueID={unique}")
    flags += [key for key in _BOOLEANS if "true" in chip.get(key, "")]
    via = feature_via(claims)
    quad_enable, qe_notes = _quad_enable(chip) if typ == "nor" and not dataflash else (None, [])
    if quad_enable is not None:
        via["quad_enable"] = f"QEbitAddr={chip['QEbitAddr']}"
    if command != 0x9F:
        # The command the id is read with, which id_method says.
        via["id_method"] = f"RDIDCommand={chip['RDIDCommand']}"
    dies = _dies(chip, size)
    if dies is not None:
        nand["dies"] = dies
        via["dies"] = f"DieSizeInKByte={chip['DieSizeInKByte']}"
    description = chip.get("Description", "").strip()
    timings = {}
    seconds = int(chip.get("ChipEraseTime") or 0)
    if seconds:
        timings = {"chip_erase": {"unspecified": seconds * 10**9}}
        via["timings.chip_erase"] = f"ChipEraseTime={chip['ChipEraseTime']}"
    clock, clock_notes = _clock(chip)
    if clock is not None:
        via["listed_clock_hz"] = f"{_clock_attribute(chip)}={_clock_value(chip)}"
        described = [int(n) for n in _DESCRIBED_CLOCK.findall(description)]
        if described and clock // 10**6 not in described:
            mhz = " / ".join(f"{n} MHz" for n in described)
            token = via["listed_clock_hz"]
            clock_notes.append(f"{token} is not the {mhz} its Description gives")
    voltage = chip.get("Voltage", "")
    if voltage not in VOLTAGES:
        msg = f"Voltage={voltage!r}: not one of {sorted(VOLTAGES)}"
        raise ValueError(msg)
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
        page_size=None if dataflash or (typ == "nor" and page == NOR_PAGE_TEMPLATE) else page,
        erasers=erasers or None,
        features=features,
        flags=flags,
        via=via,
        quad_enable=quad_enable,
        supply_mv=VOLTAGES[voltage],
        legacy_ids=legacy,
        listed_clock_hz=clock,
        timings=timings,
        opcodes=ops.to_json(),
        notes=([description] if description else []) + qe_notes + legacy_notes + clock_notes,
        **nand,
    )


#: The spellings of the clock attribute: ``Clock`` on nearly every entry,
#: ``clock`` on 40, ``CLOCK`` on one.
CLOCK_ATTRIBUTES = ("Clock", "clock", "CLOCK")

# One clock: "104MHz", "133 MHz", "104Mhz".
_ONE_CLOCK = re.compile(r"(\d+) ?MHz", re.IGNORECASE)
# The clocks a Description names: "... With 104MHz SPI Bus Interface",
# "... With 33 MHz / 100 MHz SPI ...", "104-MHz".
_DESCRIBED_CLOCK = re.compile(r"(\d+)[ -]?MHz", re.IGNORECASE)
# Two: a read's and a fast read's, "33/100MHz".
_TWO_CLOCKS = re.compile(r"(\d+)/(\d+) ?MHz", re.IGNORECASE)

#: ``Clock`` values that are no clock of the part, by value, and why: each
#: checked against the part's datasheet.
CLOCK_WRONG = {
    "416MHz": ("the 104 MHz quad read's 416 Mbit/s (A25LQ64 datasheet: 104 MHz), not a clock"),
    "416MHZ": ("the 104 MHz quad read's 416 Mbit/s (W25Q64FW datasheet: 104 MHz), not a clock"),
}


def _clock_attribute(chip: dict[str, str]) -> str | None:
    return next((a for a in CLOCK_ATTRIBUTES if chip.get(a)), None)


def _clock_value(chip: dict[str, str]) -> str:
    attribute = _clock_attribute(chip)
    return chip[attribute] if attribute else ""


def _clock(chip: dict[str, str]) -> tuple[int | None, list[str]]:
    """The fastest SPI clock ``Clock`` gives the part, in hertz, and the
    notes on a value that is not one clock: two clocks (a read's and a fast
    read's, ``33/100MHz``: not one fact, so not taken), a value with no
    unit or the wrong one (``166``, ``166Mbit``, ``A13112``), or a known
    wrong one (:data:`CLOCK_WRONG`). Dpcmd never reads it (parse.c reads no
    clock attribute)."""
    attribute = _clock_attribute(chip)
    if attribute is None:
        return None, []
    value = chip[attribute]
    token = f"{attribute}={value}"
    if value in CLOCK_WRONG:
        return None, [f"{token} not read: {CLOCK_WRONG[value]}"]
    if (m := _ONE_CLOCK.fullmatch(value)) is not None:
        return int(m[1]) * 10**6, []
    if _TWO_CLOCKS.fullmatch(value):
        return None, [f"{token} not read: two clocks (read, fast read), not the part's one"]
    return None, [f"{token} not read: not a clock in MHz"]


def _dies(chip: dict[str, str], size: int) -> int | None:
    """The dies ``DieSizeInKByte`` gives: the size over the die's, where
    the die is smaller than the chip. A die the size of the chip is the
    template's (252 of the 260 entries giving a die size give one), and one
    larger than the chip (the S79FL01GS and S79FS01GS "one die" entries)
    says nothing of it."""
    die = int(chip.get("DieSizeInKByte") or "0") * 1024
    if not die or die >= size:
        return None
    if size % die:
        msg = f"size {size} is not a whole number of {die}-byte dies"
        raise ValueError(msg)
    return size // die


#: ``QEbitAddr`` values that say nothing of the part: the template's 0x200
#: (SR2 bit 1, given to 255 of 356 Macronix parts and 89 of 90 Micron ones,
#: whose QE bit is elsewhere or none) and 0.
_QE_NOT_SAID = frozenset({0, 0x200})

# The status registers, in the order the mask's bytes are (0x05, 0x35, 0x15).
_STATUS_REGISTERS = ("sr1", "sr2", "sr3")

#: The one bit of SR1 a QE bit can be: the others are WIP, WEL, the
#: block-protect bits and SRWD on every part.
_SR1_QE_BIT = 6


def _quad_enable(chip: dict[str, str]) -> tuple[dict[str, object] | None, list[str]]:
    """The QE bit ``QEbitAddr`` gives, and a note where one is left out: a
    one-bit mask over the status registers, SR1 its low byte, then SR2 and
    SR3. ``None`` for a value of :data:`_QE_NOT_SAID`, and for an SR1 bit
    other than 6: the MX25U51271G's 0x80 is its SRWD, the EN25QH256's 0x20
    a block-protect bit (BP3; its SFDP says it has no QE bit)."""
    mask = int(chip.get("QEbitAddr") or "0", 16)
    if mask in _QE_NOT_SAID:
        return None, []
    if mask & (mask - 1) or mask >> 8 * len(_STATUS_REGISTERS):
        msg = f"QEbitAddr {chip['QEbitAddr']} is not one status register bit"
        raise ValueError(msg)
    bit = mask.bit_length() - 1
    if bit < 8 and bit != _SR1_QE_BIT:
        note = (
            f"QEbitAddr={chip['QEbitAddr']} left out: SR1 bit {bit} is no QE bit "
            "(status, block-protect or SRWD)"
        )
        return None, [note]
    return {"register": _STATUS_REGISTERS[bit // 8], "bit": bit % 8}, []


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


def _erasers(chip: dict[str, str], ops: Opcodes, size: int) -> list[dict[str, Any]]:
    """The erase layouts: chip erase; 0x20 over the ``SectorSizeInByte`` sectors; 0x52 over the SST
    parts' 32 KiB blocks, or an AT25F's ``SectorSizeInByte`` where that is
    not the template's 4096. Die erase has none: its layout is the dies'
    (:func:`_dies`, :func:`spiflash.derive.die_erasers`).

    0xd8 and 0xdc have no layout: ``BlockSizeInByte`` is 64 KiB in nearly
    every entry, where other sources give 32 KiB (M25P05, EN25F10, ...),
    128 KiB (the MT35XU parts), 256 KiB (M25P128, S25FL512S) or boot blocks
    (the AMIC A25L..P parts)."""
    out: list[dict[str, Any]] = []
    for slot, byte, op in _opcodes(chip, "EraseCmd", chip["EraseCmd"]):
        ops.add(op, f"EraseCmd={chip['EraseCmd']}", value=byte)
        sectors = int(chip.get("SectorSizeInByte", "0"))
        if slot == 0:
            unit = size
        elif slot == 2:
            unit = 0
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
    return out
