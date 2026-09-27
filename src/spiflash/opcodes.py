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


@dataclass(frozen=True)
class Operation:
    name: str
    opcode: int
    kind: str  # read, program, erase, id, register, mode
    description: str


_ALL = [
    # Read. 1-1-1 plain read has no dummy cycles; FAST adds 8.
    Operation("READ_1_1_1", 0x03, "read", "Read data (low frequency)"),
    Operation("READ_1_1_1_FAST", 0x0B, "read", "Fast read"),
    Operation("READ_1_1_2", 0x3B, "read", "Dual output fast read"),
    Operation("READ_1_2_2", 0xBB, "read", "Dual I/O fast read"),
    Operation("READ_1_1_4", 0x6B, "read", "Quad output fast read"),
    Operation("READ_1_4_4", 0xEB, "read", "Quad I/O fast read"),
    Operation("READ_1_1_8", 0x8B, "read", "Octal output fast read"),
    Operation("READ_1_8_8", 0xCB, "read", "Octal I/O fast read"),
    Operation("READ_8D_8D_8D", 0x0B, "read", "Octal DTR read (xSPI; the fast read opcode)"),
    Operation("READ_1_1_1_4B", 0x13, "read", "Read data, 4-byte address"),
    Operation("READ_1_1_1_FAST_4B", 0x0C, "read", "Fast read, 4-byte address"),
    Operation("READ_1_1_2_4B", 0x3C, "read", "Dual output fast read, 4-byte address"),
    Operation("READ_1_2_2_4B", 0xBC, "read", "Dual I/O fast read, 4-byte address"),
    Operation("READ_1_1_4_4B", 0x6C, "read", "Quad output fast read, 4-byte address"),
    Operation("READ_1_4_4_4B", 0xEC, "read", "Quad I/O fast read, 4-byte address"),
    Operation("READ_4_4_4_4B", 0xEC, "read", "QPI fast read, 4-byte address"),
    Operation("READ_1_1_8_4B", 0x7C, "read", "Octal output fast read, 4-byte address"),
    Operation("READ_1_8_8_4B", 0xCC, "read", "Octal I/O fast read, 4-byte address"),
    # Program.
    Operation("PP_1_1_1", 0x02, "program", "Page program"),
    Operation("BP", 0x02, "program", "Byte program (one byte per write enable)"),
    Operation("AAI_WP", 0xAD, "program", "Auto address increment word program (SST)"),
    Operation("PP_1_1_4", 0x32, "program", "Quad input page program"),
    Operation("PP_1_4_4", 0x38, "program", "Quad I/O page program"),
    Operation("PP_1_1_8", 0x82, "program", "Octal input page program"),
    Operation("PP_1_8_8", 0xC2, "program", "Octal I/O page program"),
    Operation("PP_8D_8D_8D", 0x02, "program", "Octal DTR page program (xSPI)"),
    Operation("PP_1_1_1_4B", 0x12, "program", "Page program, 4-byte address"),
    Operation("PP_1_1_4_4B", 0x34, "program", "Quad input page program, 4-byte address"),
    # Erase.
    Operation("BE_256", 0xDB, "erase", "Erase a 256 B page"),
    Operation("BE_4K", 0x20, "erase", "Erase a 4 KiB sector"),
    Operation("BE_4K_PMC", 0xD7, "erase", "Erase a 4 KiB sector (PMC)"),
    Operation("BE_32K", 0x52, "erase", "Erase a 32 KiB block"),
    Operation("SE", 0xD8, "erase", "Erase a sector (usually 64 KiB)"),
    Operation("BE_ALT1", 0x50, "erase", "Erase a block (vendor-specific, 0x50)"),
    Operation("BE_ALT2", 0x81, "erase", "Erase a block/page (vendor-specific, 0x81)"),
    Operation("BE_40", 0x40, "erase", "Erase a parameter block (Intel S33, Spansion S25FL-P)"),
    Operation("BE_53", 0x53, "erase", "Erase a 32 KiB block, 4-byte address (Spansion)"),
    Operation("BE_4K_4B", 0x21, "erase", "Erase a 4 KiB sector, 4-byte address"),
    Operation("BE_32K_4B", 0x5C, "erase", "Erase a 32 KiB block, 4-byte address"),
    Operation("SE_4B", 0xDC, "erase", "Erase a sector, 4-byte address"),
    Operation("CHIP_ERASE", 0xC7, "erase", "Erase the whole chip"),
    Operation("CHIP_ERASE_ALT", 0x60, "erase", "Erase the whole chip (alternative opcode)"),
    Operation("CHIP_ERASE_ATMEL", 0x62, "erase", "Erase the whole chip (Atmel)"),
    Operation("DIE_ERASE", 0xC4, "erase", "Erase one die"),
    # Identification.
    Operation("RDID", 0x9F, "id", "Read JEDEC id"),
    Operation("RDID_ATMEL", 0x15, "id", "Read id (Atmel AT25F)"),
    Operation("RDID_M95", 0x83, "id", "Read identification page (ST M95 EEPROM)"),
    Operation("REMS", 0x90, "id", "Read electronic manufacturer and device id"),
    Operation("RES", 0xAB, "id", "Release from deep power-down and read electronic signature"),
    Operation("RDSFDP", 0x5A, "id", "Read SFDP (JESD216) parameters"),
    # Status and configuration registers.
    Operation("WRSR", 0x01, "register", "Write status register"),
    Operation("EWSR", 0x50, "register", "Enable write status register (instead of WREN)"),
    Operation("WRSR2", 0x31, "register", "Write status register 2"),
    Operation("WRSR3", 0x11, "register", "Write status register 3"),
    Operation("RDFSR", 0x70, "register", "Read flag status register"),
    Operation("CLSR", 0x30, "register", "Clear status register errors"),
    Operation("SET_READ_PARAMS", 0xC0, "register", "Set read parameters (dummy cycles, wrap)"),
    # Modes.
    Operation("EN4B", 0xB7, "mode", "Enter 4-byte address mode"),
    Operation("EX4B", 0xE9, "mode", "Exit 4-byte address mode"),
    Operation("WREAR", 0xC5, "mode", "Write extended address register"),
    Operation("RDEAR", 0xC8, "mode", "Read extended address register"),
    Operation("BRWR", 0x17, "mode", "Write bank address register"),
    Operation("BRRD", 0x16, "mode", "Read bank address register"),
    Operation("EQPI_38", 0x38, "mode", "Enter QPI mode (0x38)"),
    Operation("RSTQIO_FF", 0xFF, "mode", "Exit QPI mode (0xff)"),
    Operation("EQPI_35", 0x35, "mode", "Enter QPI mode (0x35)"),
    Operation("RSTQIO_F5", 0xF5, "mode", "Exit QPI mode (0xf5)"),
]

#: Every operation spiflash knows, by name.
OPERATIONS: dict[str, Operation] = {op.name: op for op in _ALL}

KINDS = ("id", "read", "program", "erase", "register", "mode")


def get(name: str) -> Operation:
    """The operation called ``name``; ``KeyError`` for an unknown one."""
    return OPERATIONS[name]


def sort_key(name: str) -> tuple[int, int, str]:
    """Order operations by kind, then as listed here."""
    op = OPERATIONS[name]
    return (KINDS.index(op.kind), _ALL.index(op), name)
