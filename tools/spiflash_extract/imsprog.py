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
I2C, MicroWire and SPI EEPROMs, FRAMs and AT45 DataFlash are not.
The file has no lines, so a record's ``line`` is the entry's number in it,
from 1.

A SPI NAND id is the three bytes after 0x9f and a dummy byte. A part with a
two-byte id sends its manufacturer byte again as the third; that is dropped,
so the id is the two bytes other sources give.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from .ops import Opcodes
from .record import ERASE_FEATURES, Record, make

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


def skipped(root: Path) -> Counter[str]:
    """How many entries are left out, by type."""
    return Counter(LEFT_OUT[e[0x3A]] for _, _, e in entries(root) if e[0x3A] in LEFT_OUT)


def extract(root: Path) -> list[Record]:
    records = []
    for n, fields, e in entries(root):
        kind = e[0x3A]
        if kind not in (NOR, NAND):
            continue
        where = f"{DAT}: entry {n} ({','.join(fields)})"
        if e[0x43] not in VCC:
            msg = f"{where}: unknown VCC code 0x{e[0x43]:02x}"
            raise ValueError(msg)
        rec = _nor(e, where) if kind == NOR else _nand(e)
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
    size = int.from_bytes(e[0x34:0x38], "little")
    block = int.from_bytes(e[0x3F:0x41], "big") * 1024
    addr4 = e[0x3E]
    if addr4 not in ADDR4 or not block:
        msg = f"{where}: unknown 4-byte addressing 0x{addr4:02x}, or no block size"
        raise ValueError(msg)
    features = {"4byte_addr"} if addr4 else set()
    if block in ERASE_FEATURES:
        features.add(ERASE_FEATURES[block])
    return {
        "id": bytes([e[0x32], e[0x31], e[0x30]]).hex(),
        "size": size,
        "page_size": int.from_bytes(e[0x38:0x3A], "little"),
        "sector_size": block,
        "erasers": [
            {"opcode": 0xD8, "blocks": [[block, size // block]]},
            {"opcode": 0xC7, "blocks": [[size, 1]]},
        ],
        "features": features,
        "flags": _flags(e),
        "opcodes": _opcodes(addr4),
    }


def _nand(e: bytes) -> dict[str, object]:
    ident = bytes([e[0x32], e[0x31], e[0x30]])
    if ident[2] == ident[0]:
        ident = ident[:2]  # a two-byte id, wrapped round
    return {
        "type": "nand",
        "id": ident.hex(),
        "id_method": "rdid_opcode_dummy",
        "size": int.from_bytes(e[0x34:0x38], "little"),
        "page_size": int.from_bytes(e[0x38:0x3A], "little"),
        "sector_size": int.from_bytes(e[0x3F:0x41], "big") * 1024,
        "flags": [*_flags(e), f"ECCsize={e[0x42] * 64}"],
    }


def _opcodes(addr4: int) -> list[dict[str, object]]:
    """What :upstream:`imsprog:IMSProg_programmer/spi_nor_flash.c` sends to
    a SPI NOR part: read-id, read, page program, the 64 KiB block erase and
    chip erase, and above 16 MiB the 3-byte opcodes in 4-byte address mode,
    entered the way the entry's 0x3e byte says."""
    ops = Opcodes()
    ops.add("RDID", "JEDEC id match")
    ops.add("READ_1_1_1", "every read")
    ops.add("PP_1_1_1", "every write")
    ops.add("SE", "every block erase")
    ops.add("CHIP_ERASE", "full chip erase")
    for op in ADDR4[addr4]:
        ops.add(op, f"4-byte addressing (addr4bit=0x{addr4:02x})")
    return ops.to_json()
