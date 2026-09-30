"""IMSProg: :upstream:`imsprog:IMSProg_programmer/database/IMSProg.Dat`.

A binary file of 0x44-byte entries, laid out as its README's "Chip database
format" says and as ``MainWindow`` in
:upstream:`imsprog:IMSProg_programmer/mainwindow.cpp` reads it::

    0x00  "SPI_FLASH,WINBOND,W25Q128FV", NUL padded (type, vendor, part)
    0x30  the id: 0x32 the manufacturer, 0x31 and 0x30 the next bytes
    0x34  size, 4 bytes little-endian
    0x38  page size, 2 bytes little-endian
    0x3a  type: 0 SPI NOR, 6 SPI NAND; 1 to 5 are EEPROMs
    0x3b  algorithm code (security registers)
    0x3c  bus speed factor, 2 bytes little-endian, in thousandths
    0x3e  4-byte addressing: 0x00 none, 0x01 EN4B, 0x11 Winbond, 0x21 Spansion
    0x3f  block size in KiB, 2 bytes big-endian
    0x42  SPI NAND: spare (ECC) bytes per page / 64
    0x43  VCC: 0 3.3 V, 1 1.8 V, 2 5.0 V, 3 2.5 V

An all-zero entry ends the table. Only SPI NOR and SPI NAND are taken; the
I2C, MicroWire and SPI EEPROMs, FRAMs and AT45 DataFlash are not, nor the
entries in :data:`WRONG`. The file has no lines, so a record's ``line`` is
the entry's number in it, from 1.

Every SPI NOR entry has a 256-byte page and a 64 KiB block: IMSProg programs
every part in 256-byte pages and erases it with 0xd8 at every 64 KiB, and
its GUI offers little else, so they are its defaults, not the part's. They
are kept as flags, not as the record's page and sector size.

A SPI NAND id is the three bytes after 0x9f and a dummy byte. A part with a
two-byte id sends its manufacturer byte again as the third; for the makers
in :data:`TWO_BYTE_IDS` that byte is dropped, so the id is the two bytes
other sources give.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from .ops import Opcodes
from .record import Record, make

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

DAT = "IMSProg_programmer/database/IMSProg.Dat"

ENTRY = 0x44

# The chip type byte, and the type text beginning each entry.
TYPES = {
    0: "SPI_FLASH",
    1: "24_EEPROM",
    2: "93_EEPROM",
    3: "25_EEPROM",
    4: "95_EEPROM",
    5: "45_EEPROM",
    6: "SPI_NAND",
}
NOR, NAND = 0, 6

# The other types, which are not taken.
LEFT_OUT = {
    1: "I2C EEPROM or FRAM (24xx)",
    2: "MicroWire EEPROM (93xx)",
    3: "SPI EEPROM or FRAM (25xx)",
    4: "SPI EEPROM (95xx)",
    5: "AT45 DataFlash",
}

_SIZE = "size contradicts its part number and capacity byte"
_ID = "wrong id, per its datasheet"

#: Entries whose id or size is wrong, by part and id bytes as the file has
#: them, and why; they are left out (see docs/_source_notes/imsprog.md).
WRONG = {
    ("ES25P10", "4a2011"): _SIZE,  # 1 Mbit, 0x11: 128 KiB, not 256
    ("ES25P20", "4a2012"): _SIZE,
    ("ES25P40", "4a2013"): _SIZE,
    ("ES25P80", "4a2014"): _SIZE,
    ("ES25P16", "4a2015"): _SIZE,
    ("ES25P32", "4a2016"): _SIZE,
    ("ES25M40A", "4a3213"): _SIZE,
    ("ES25M80A", "4a3214"): _SIZE,
    ("ES25M16A", "4a3215"): _SIZE,
    ("F25L008A", "8c2014"): _SIZE,  # 8 Mbit, 0x14: 1 MiB, not 2
    ("EN25E40A", "1c4213"): _SIZE,  # 4 Mbit, 0x13: 512 KiB, not 256
    ("A25L40PT", "372022"): _ID,  # the A25L20PT's; its own is 7f 37 20 13
    ("P25Q06H", "850010"): _ID,  # 85 40 10
    ("MT29F4G01ABAFD12", "2c362c"): _ID,  # 2c 34
    ("PCT25VF010A", "bf4900"): _ID,  # a REMS id: the part has no JEDEC read-id
}

#: The makers whose SPI NAND ids are two bytes: a third byte repeating the
#: first is the id wrapping round. Anywhere else it raises, rather than be
#: dropped unseen.
TWO_BYTE_IDS = {0x0B, 0x2C, 0xBA, 0xC2, 0xC8, 0xE5}

#: SPI NAND parts that answer read-id straight after the opcode, with no
#: dummy byte (GigaDevice's GD5F1GQ4xF: "9FH MID DID DID").
NO_DUMMY = {"c8a148", "c8a348", "c8b148", "c8b348"}

VCC = {0: "3.3 V", 1: "1.8 V", 2: "5.0 V", 3: "2.5 V"}

# How IMSProg's snor_4byte_mode() enters 4-byte addressing, by the entry's
# 0x3e byte: the operations it sends.
ADDR4 = {
    0x00: (),
    0x01: ("EN4B", "EX4B"),
    0x11: ("EN4B", "EX4B", "WREAR"),
    0x21: ("BRWR", "BRRD"),
}


def entries(root: Path) -> Iterator[tuple[int, list[str], bytes]]:
    """Each entry up to the end one: its number, from 1, its type, vendor
    and part, and its bytes."""
    data = (root / DAT).read_bytes()
    if len(data) % ENTRY:
        msg = f"{DAT}: {len(data)} bytes is not a whole number of {ENTRY}-byte entries"
        raise ValueError(msg)
    for n in range(len(data) // ENTRY):
        e = data[n * ENTRY : (n + 1) * ENTRY]
        text = e[:0x30].split(b"\0")[0].decode("ascii")
        if not text:
            return  # the end entry
        fields = text.split(",")
        if len(fields) != 3 or TYPES.get(e[0x3A]) != fields[0]:
            msg = (
                f"{DAT}: entry {n + 1} ({text}): not a type, vendor and part, or its type disagrees"
            )
            raise ValueError(msg)
        yield n + 1, fields, e


def _id(e: bytes) -> str:
    """The id bytes as the file has them."""
    return bytes([e[0x32], e[0x31], e[0x30]]).hex()


def _left_out(fields: list[str], e: bytes) -> str | None:
    """Why an entry is not taken, or None."""
    return LEFT_OUT.get(e[0x3A]) or WRONG.get((fields[2], _id(e)))


def skipped(root: Path) -> Counter[str]:
    """How many entries are left out, by reason."""
    return Counter(r for _, f, e in entries(root) if (r := _left_out(f, e)))


def extract(root: Path) -> list[Record]:
    records = []
    for n, fields, e in entries(root):
        if _left_out(fields, e):
            continue
        where = f"{DAT}: entry {n} ({','.join(fields)})"
        if e[0x43] not in VCC:
            msg = f"{where}: unknown VCC code 0x{e[0x43]:02x}"
            raise ValueError(msg)
        rec = _nor(e, where) if e[0x3A] == NOR else _nand(e, where)
        records.append(make("imsprog", DAT, n, fields[2], vendor=fields[1].strip(), **rec))
    return records


def _flags(e: bytes) -> list[str]:
    """The fields a record has no place for, named as IMSProg's chip struct
    names them."""
    return [
        f"algorithmCode=0x{e[0x3B]:02x}",
        f"delay={int.from_bytes(e[0x3C:0x3E], 'little')}",
        f"addr4bit=0x{e[0x3E]:02x}",
        f"chipVCC={VCC[e[0x43]]}",
    ]


def _nor(e: bytes, where: str) -> dict[str, object]:
    addr4 = e[0x3E]
    if addr4 not in ADDR4:
        msg = f"{where}: unknown 4-byte addressing 0x{addr4:02x}"
        raise ValueError(msg)
    return {
        "id": _id(e),
        "size": int.from_bytes(e[0x34:0x38], "little"),
        "features": {"4byte_addr"} if addr4 else set(),
        "flags": [
            *_flags(e),
            f"pageSize={int.from_bytes(e[0x38:0x3A], 'little')}",
            f"blockSize={int.from_bytes(e[0x3F:0x41], 'big')}K",
        ],
        "opcodes": _opcodes(addr4),
    }


def _nand(e: bytes, where: str) -> dict[str, object]:
    ident = bytes([e[0x32], e[0x31], e[0x30]])
    if ident[2] == ident[0]:
        if ident[0] not in TWO_BYTE_IDS:
            msg = f"{where}: third id byte repeats the first, from a maker not known for 2-byte ids"
            raise ValueError(msg)
        ident = ident[:2]  # a two-byte id, wrapped round
    return {
        "type": "nand",
        "id": ident.hex(),
        "id_method": "rdid_opcode" if ident.hex() in NO_DUMMY else "rdid_opcode_dummy",
        "size": int.from_bytes(e[0x34:0x38], "little"),
        "page_size": int.from_bytes(e[0x38:0x3A], "little"),
        "sector_size": int.from_bytes(e[0x3F:0x41], "big") * 1024,
        "flags": [*_flags(e), f"ECCsize={e[0x42] * 64}"],
    }


def _opcodes(addr4: int) -> list[dict[str, object]]:
    """What :upstream:`imsprog:IMSProg_programmer/spi_nor_flash.c` sends to
    a SPI NOR part: read-id, read, page program and the 0xd8 block erase
    (its erase loops over that; it never sends a chip erase), and above
    16 MiB the 3-byte opcodes in 4-byte address mode, entered the way the
    entry's 0x3e byte says."""
    ops = Opcodes()
    ops.add("RDID", "JEDEC id match")
    ops.add("READ_1_1_1", "every read")
    ops.add("PP_1_1_1", "every write, in 256-byte pages")
    ops.add("SE", "every erase, at every 64 KiB")
    for op in ADDR4[addr4]:
        ops.add(op, f"4-byte addressing (addr4bit=0x{addr4:02x})")
    return ops.to_json()
