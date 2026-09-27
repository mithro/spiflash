"""The SPI flash operations the database knows, by name.

Names follow LiteSPI's ``SpiNorFlashOpCodes`` (``READ_1_1_4``, ``PP_1_1_1_4B``,
``BE_4K``, ...), so a part's list can be used there as it is. ``a_b_c`` is the
number of data lines for command, address and data; ``_4B`` is the variant
taking a 4-byte address; ``D`` marks double transfer rate.

The opcode values are the ones in Linux's ``include/linux/mtd/spi-nor.h``
(``SPINOR_OP_*``) and flashrom's ``include/spi.h`` (``JEDEC_*``); the
extractors read them from those headers and check them against this table,
so a disagreement fails the build of the data rather than shipping.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    name: str
    opcode: int
    kind: str  # read, program, erase, id, register, mode
    description: str


def _op(name: str, opcode: int, kind: str, description: str) -> Operation:
    return Operation(name, opcode, kind, description)


_ALL = [
    # Read. 1-1-1 plain read has no dummy cycles; FAST adds 8.
    _op("READ_1_1_1", 0x03, "read", "Read data (low frequency)"),
    _op("READ_1_1_1_FAST", 0x0B, "read", "Fast read"),
    _op("READ_1_1_2", 0x3B, "read", "Dual output fast read"),
    _op("READ_1_2_2", 0xBB, "read", "Dual I/O fast read"),
    _op("READ_1_1_4", 0x6B, "read", "Quad output fast read"),
    _op("READ_1_4_4", 0xEB, "read", "Quad I/O fast read"),
    _op("READ_1_1_8", 0x8B, "read", "Octal output fast read"),
    _op("READ_1_8_8", 0xCB, "read", "Octal I/O fast read"),
    _op("READ_8D_8D_8D", 0x0B, "read", "Octal DTR read (xSPI; the fast read opcode)"),
    _op("READ_1_1_1_4B", 0x13, "read", "Read data, 4-byte address"),
    _op("READ_1_1_1_FAST_4B", 0x0C, "read", "Fast read, 4-byte address"),
    _op("READ_1_1_2_4B", 0x3C, "read", "Dual output fast read, 4-byte address"),
    _op("READ_1_2_2_4B", 0xBC, "read", "Dual I/O fast read, 4-byte address"),
    _op("READ_1_1_4_4B", 0x6C, "read", "Quad output fast read, 4-byte address"),
    _op("READ_1_4_4_4B", 0xEC, "read", "Quad I/O fast read, 4-byte address"),
    _op("READ_4_4_4_4B", 0xEC, "read", "QPI fast read, 4-byte address"),
    _op("READ_1_1_8_4B", 0x7C, "read", "Octal output fast read, 4-byte address"),
    _op("READ_1_8_8_4B", 0xCC, "read", "Octal I/O fast read, 4-byte address"),
    # Program.
    _op("PP_1_1_1", 0x02, "program", "Page program"),
    _op("BP", 0x02, "program", "Byte program (one byte per write enable)"),
    _op("AAI_WP", 0xAD, "program", "Auto address increment word program (SST)"),
    _op("PP_1_1_4", 0x32, "program", "Quad input page program"),
    _op("PP_1_4_4", 0x38, "program", "Quad I/O page program"),
    _op("PP_1_1_8", 0x82, "program", "Octal input page program"),
    _op("PP_1_8_8", 0xC2, "program", "Octal I/O page program"),
    _op("PP_8D_8D_8D", 0x02, "program", "Octal DTR page program (xSPI)"),
    _op("PP_1_1_1_4B", 0x12, "program", "Page program, 4-byte address"),
    _op("PP_1_1_4_4B", 0x34, "program", "Quad input page program, 4-byte address"),
    # Erase.
    _op("BE_256", 0xDB, "erase", "Erase a 256 B page"),
    _op("BE_4K", 0x20, "erase", "Erase a 4 KiB sector"),
    _op("BE_4K_PMC", 0xD7, "erase", "Erase a 4 KiB sector (PMC)"),
    _op("BE_32K", 0x52, "erase", "Erase a 32 KiB block"),
    _op("SE", 0xD8, "erase", "Erase a sector (usually 64 KiB)"),
    _op("BE_ALT1", 0x50, "erase", "Erase a block (vendor-specific, 0x50)"),
    _op("BE_ALT2", 0x81, "erase", "Erase a block/page (vendor-specific, 0x81)"),
    _op("BE_40", 0x40, "erase", "Erase a parameter block (Intel S33, Spansion S25FL-P)"),
    _op("BE_53", 0x53, "erase", "Erase a 32 KiB block, 4-byte address (Spansion)"),
    _op("BE_4K_4B", 0x21, "erase", "Erase a 4 KiB sector, 4-byte address"),
    _op("BE_32K_4B", 0x5C, "erase", "Erase a 32 KiB block, 4-byte address"),
    _op("SE_4B", 0xDC, "erase", "Erase a sector, 4-byte address"),
    _op("CHIP_ERASE", 0xC7, "erase", "Erase the whole chip"),
    _op("CHIP_ERASE_ALT", 0x60, "erase", "Erase the whole chip (alternative opcode)"),
    _op("CHIP_ERASE_ATMEL", 0x62, "erase", "Erase the whole chip (Atmel)"),
    _op("DIE_ERASE", 0xC4, "erase", "Erase one die"),
    # Identification.
    _op("RDID", 0x9F, "id", "Read JEDEC id"),
    _op("RDID_ATMEL", 0x15, "id", "Read id (Atmel AT25F)"),
    _op("RDID_M95", 0x83, "id", "Read identification page (ST M95 EEPROM)"),
    _op("REMS", 0x90, "id", "Read electronic manufacturer and device id"),
    _op("RES", 0xAB, "id", "Release from deep power-down and read electronic signature"),
    _op("RDSFDP", 0x5A, "id", "Read SFDP (JESD216) parameters"),
    # Status and configuration registers.
    _op("WRSR", 0x01, "register", "Write status register"),
    _op("EWSR", 0x50, "register", "Enable write status register (instead of WREN)"),
    _op("WRSR2", 0x31, "register", "Write status register 2"),
    _op("WRSR3", 0x11, "register", "Write status register 3"),
    _op("RDFSR", 0x70, "register", "Read flag status register"),
    _op("CLSR", 0x30, "register", "Clear status register errors"),
    _op("SET_READ_PARAMS", 0xC0, "register", "Set read parameters (dummy cycles, wrap)"),
    # Modes.
    _op("EN4B", 0xB7, "mode", "Enter 4-byte address mode"),
    _op("EX4B", 0xE9, "mode", "Exit 4-byte address mode"),
    _op("WREAR", 0xC5, "mode", "Write extended address register"),
    _op("RDEAR", 0xC8, "mode", "Read extended address register"),
    _op("BRWR", 0x17, "mode", "Write bank address register"),
    _op("BRRD", 0x16, "mode", "Read bank address register"),
    _op("EQPI_38", 0x38, "mode", "Enter QPI mode (0x38)"),
    _op("RSTQIO_FF", 0xFF, "mode", "Exit QPI mode (0xff)"),
    _op("EQPI_35", 0x35, "mode", "Enter QPI mode (0x35)"),
    _op("RSTQIO_F5", 0xF5, "mode", "Exit QPI mode (0xf5)"),
]

OPERATIONS: dict[str, Operation] = {op.name: op for op in _ALL}

KINDS = ("id", "read", "program", "erase", "register", "mode")


def get(name: str) -> Operation:
    """The operation called ``name``; ``KeyError`` for an unknown one."""
    return OPERATIONS[name]


def sort_key(name: str) -> tuple[int, int, str]:
    """Order operations by kind, then as listed here."""
    op = OPERATIONS[name]
    return (KINDS.index(op.kind), _ALL.index(op), name)
