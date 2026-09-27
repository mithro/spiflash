"""The SPI flash operations the database knows, by name.

Names follow LiteSPI's ``SpiNorFlashOpCodes`` (``READ_1_1_4``, ``PP_1_1_1_4B``,
``BE_4K``, ...), so a part's list can be used there as it is. ``a_b_c`` is the
number of data lines for command, address and data; ``_4B`` is the variant
taking a 4-byte address; ``D`` marks double transfer rate.

The opcode values are the ones in Linux's
:upstream:`linux:include/linux/mtd/spi-nor.h` (``SPINOR_OP_*``) and flashrom's
:upstream:`flashrom:include/spi.h` (``JEDEC_*``); the
extractors read them from those headers and check them against this table,
so a disagreement fails the build of the data rather than shipping.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import OperationKind


@dataclass(frozen=True, slots=True)
class Operation:
    name: str
    opcode: int
    kind: OperationKind
    description: str


_ALL = [
    # Read. 1-1-1 plain read has no dummy cycles; FAST adds 8.
    Operation("READ_1_1_1", 0x03, OperationKind.READ, "Read data (low frequency)"),
    Operation("READ_1_1_1_FAST", 0x0B, OperationKind.READ, "Fast read"),
    Operation("READ_1_1_2", 0x3B, OperationKind.READ, "Dual output fast read"),
    Operation("READ_1_2_2", 0xBB, OperationKind.READ, "Dual I/O fast read"),
    Operation("READ_1_1_4", 0x6B, OperationKind.READ, "Quad output fast read"),
    Operation("READ_1_4_4", 0xEB, OperationKind.READ, "Quad I/O fast read"),
    Operation("READ_1_1_8", 0x8B, OperationKind.READ, "Octal output fast read"),
    Operation("READ_1_8_8", 0xCB, OperationKind.READ, "Octal I/O fast read"),
    Operation(
        "READ_8D_8D_8D", 0x0B, OperationKind.READ, "Octal DTR read (xSPI; the fast read opcode)"
    ),
    Operation("READ_1_1_1_4B", 0x13, OperationKind.READ, "Read data, 4-byte address"),
    Operation("READ_1_1_1_FAST_4B", 0x0C, OperationKind.READ, "Fast read, 4-byte address"),
    Operation("READ_1_1_2_4B", 0x3C, OperationKind.READ, "Dual output fast read, 4-byte address"),
    Operation("READ_1_2_2_4B", 0xBC, OperationKind.READ, "Dual I/O fast read, 4-byte address"),
    Operation("READ_1_1_4_4B", 0x6C, OperationKind.READ, "Quad output fast read, 4-byte address"),
    Operation("READ_1_4_4_4B", 0xEC, OperationKind.READ, "Quad I/O fast read, 4-byte address"),
    Operation("READ_4_4_4_4B", 0xEC, OperationKind.READ, "QPI fast read, 4-byte address"),
    Operation("READ_1_1_8_4B", 0x7C, OperationKind.READ, "Octal output fast read, 4-byte address"),
    Operation("READ_1_8_8_4B", 0xCC, OperationKind.READ, "Octal I/O fast read, 4-byte address"),
    # Program.
    Operation("PP_1_1_1", 0x02, OperationKind.PROGRAM, "Page program"),
    Operation("BP", 0x02, OperationKind.PROGRAM, "Byte program (one byte per write enable)"),
    Operation("AAI_WP", 0xAD, OperationKind.PROGRAM, "Auto address increment word program (SST)"),
    Operation("PP_1_1_4", 0x32, OperationKind.PROGRAM, "Quad input page program"),
    Operation("PP_1_4_4", 0x38, OperationKind.PROGRAM, "Quad I/O page program"),
    Operation("PP_1_1_8", 0x82, OperationKind.PROGRAM, "Octal input page program"),
    Operation("PP_1_8_8", 0xC2, OperationKind.PROGRAM, "Octal I/O page program"),
    Operation("PP_8D_8D_8D", 0x02, OperationKind.PROGRAM, "Octal DTR page program (xSPI)"),
    Operation("PP_1_1_1_4B", 0x12, OperationKind.PROGRAM, "Page program, 4-byte address"),
    Operation(
        "PP_1_1_4_4B", 0x34, OperationKind.PROGRAM, "Quad input page program, 4-byte address"
    ),
    # Erase.
    Operation("BE_256", 0xDB, OperationKind.ERASE, "Erase a 256 B page"),
    Operation("BE_4K", 0x20, OperationKind.ERASE, "Erase a 4 KiB sector"),
    Operation("BE_4K_PMC", 0xD7, OperationKind.ERASE, "Erase a 4 KiB sector (PMC)"),
    Operation("BE_32K", 0x52, OperationKind.ERASE, "Erase a 32 KiB block"),
    Operation("SE", 0xD8, OperationKind.ERASE, "Erase a sector (usually 64 KiB)"),
    Operation("BE_ALT1", 0x50, OperationKind.ERASE, "Erase a block (vendor-specific, 0x50)"),
    Operation("BE_ALT2", 0x81, OperationKind.ERASE, "Erase a block/page (vendor-specific, 0x81)"),
    Operation(
        "BE_40", 0x40, OperationKind.ERASE, "Erase a parameter block (Intel S33, Spansion S25FL-P)"
    ),
    Operation(
        "BE_53", 0x53, OperationKind.ERASE, "Erase a 32 KiB block, 4-byte address (Spansion)"
    ),
    Operation("BE_4K_4B", 0x21, OperationKind.ERASE, "Erase a 4 KiB sector, 4-byte address"),
    Operation("BE_32K_4B", 0x5C, OperationKind.ERASE, "Erase a 32 KiB block, 4-byte address"),
    Operation("SE_4B", 0xDC, OperationKind.ERASE, "Erase a sector, 4-byte address"),
    Operation("CHIP_ERASE", 0xC7, OperationKind.ERASE, "Erase the whole chip"),
    Operation(
        "CHIP_ERASE_ALT", 0x60, OperationKind.ERASE, "Erase the whole chip (alternative opcode)"
    ),
    Operation("CHIP_ERASE_ATMEL", 0x62, OperationKind.ERASE, "Erase the whole chip (Atmel)"),
    Operation("DIE_ERASE", 0xC4, OperationKind.ERASE, "Erase one die"),
    # Identification.
    Operation("RDID", 0x9F, OperationKind.ID, "Read JEDEC id"),
    Operation("RDID_ATMEL", 0x15, OperationKind.ID, "Read id (Atmel AT25F)"),
    Operation("RDID_M95", 0x83, OperationKind.ID, "Read identification page (ST M95 EEPROM)"),
    Operation("REMS", 0x90, OperationKind.ID, "Read electronic manufacturer and device id"),
    Operation(
        "RES", 0xAB, OperationKind.ID, "Release from deep power-down and read electronic signature"
    ),
    Operation("RDSFDP", 0x5A, OperationKind.ID, "Read SFDP (JESD216) parameters"),
    # Status and configuration registers.
    Operation("WRSR", 0x01, OperationKind.REGISTER, "Write status register"),
    Operation(
        "EWSR", 0x50, OperationKind.REGISTER, "Enable write status register (instead of WREN)"
    ),
    Operation("WRSR2", 0x31, OperationKind.REGISTER, "Write status register 2"),
    Operation("WRSR3", 0x11, OperationKind.REGISTER, "Write status register 3"),
    Operation("RDFSR", 0x70, OperationKind.REGISTER, "Read flag status register"),
    Operation("CLSR", 0x30, OperationKind.REGISTER, "Clear status register errors"),
    Operation(
        "SET_READ_PARAMS", 0xC0, OperationKind.REGISTER, "Set read parameters (dummy cycles, wrap)"
    ),
    # Modes.
    Operation("EN4B", 0xB7, OperationKind.MODE, "Enter 4-byte address mode"),
    Operation("EX4B", 0xE9, OperationKind.MODE, "Exit 4-byte address mode"),
    Operation("WREAR", 0xC5, OperationKind.MODE, "Write extended address register"),
    Operation("RDEAR", 0xC8, OperationKind.MODE, "Read extended address register"),
    Operation("BRWR", 0x17, OperationKind.MODE, "Write bank address register"),
    Operation("BRRD", 0x16, OperationKind.MODE, "Read bank address register"),
    Operation("EQPI_38", 0x38, OperationKind.MODE, "Enter QPI mode (0x38)"),
    Operation("RSTQIO_FF", 0xFF, OperationKind.MODE, "Exit QPI mode (0xff)"),
    Operation("EQPI_35", 0x35, OperationKind.MODE, "Enter QPI mode (0x35)"),
    Operation("RSTQIO_F5", 0xF5, OperationKind.MODE, "Exit QPI mode (0xf5)"),
]

#: Every operation spiflash knows, by name.
OPERATIONS: dict[str, Operation] = {op.name: op for op in _ALL}


def get(name: str) -> Operation:
    """The operation called ``name``; ``KeyError`` for an unknown one."""
    return OPERATIONS[name]


_KIND_ORDER = {kind: i for i, kind in enumerate(OperationKind)}
_TABLE_ORDER = {op.name: i for i, op in enumerate(_ALL)}


def sort_key(name: str) -> tuple[int, int]:
    """Order operations by kind, then as listed here."""
    return (_KIND_ORDER[OPERATIONS[name].kind], _TABLE_ORDER[name])
