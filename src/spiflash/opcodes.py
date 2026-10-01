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

Each operation also says what goes over the bus: how many lines the command,
address and data use (:attr:`Operation.protocol`), how many address bytes and
dummy clocks come before the data, and which way the data goes and how much of
it there is. :attr:`Operation.shape_source` says where those numbers come
from. It is no duration: how long a part takes to erase, program or wake is
:attr:`Record.timings <spiflash.model.Record.timings>`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import DataPhase, FlashType, OperationKind, ShapeSource


@dataclass(frozen=True, slots=True)
class Operation:
    """One SPI flash operation, and the transaction it is on the bus.

    ``flash_type`` is the kind of flash it is an operation of: SPI NAND has
    its own command set, some of whose opcodes are SPI NOR ones too (0x13
    is SPI NOR's 4-byte read and SPI NAND's page read), so each SPI NAND
    operation is its own, named ``NAND_...``."""

    name: str
    opcode: int
    kind: OperationKind
    description: str
    #: Data lines for command, address and data, ``"1-1-4"``; ``0`` for a
    #: phase the operation does not have (``"1-0-0"``: the command alone),
    #: and ``D`` for double transfer rate (``"8D-8D-8D"``).
    protocol: str = "1-0-0"
    address_bytes: int = 0
    #: Clocks between the address and the data; ``None`` where they are set
    #: in the part's registers or read from its SFDP tables, so vary.
    dummy_clocks: int | None = 0
    data: DataPhase | None = None
    #: How many data bytes; ``None`` for as many as the host clocks (a read)
    #: or up to a page (a page program).
    data_bytes: int | None = None
    shape_source: tuple[ShapeSource, ...] = ()
    flash_type: FlashType = FlashType.NOR

    @property
    def lines(self) -> tuple[int, int, int]:
        """The data lines used for the command, the address and the data."""
        c, a, d = (int(part.rstrip("D")) for part in self.protocol.split("-"))
        return c, a, d

    @property
    def dtr(self) -> bool:
        """Whether data moves on both clock edges (double transfer rate)."""
        return "D" in self.protocol


READ, WRITE = DataPhase.READ, DataPhase.WRITE
_LINUX_READ = (ShapeSource.LINUX_DEFAULT,)
_LINUX_FAST = (ShapeSource.LINUX_DEFAULT, ShapeSource.FLASHPROG_FEATURES)
_LINUX_WIDE = (ShapeSource.LINUX_NO_SFDP, ShapeSource.FLASHPROG_FEATURES)
_LINUX_4B = (ShapeSource.LINUX_4B,)
_SIZES = (ShapeSource.FLASHROM_SIZES,)

_NAND = FlashType.NAND
_SPINAND = (ShapeSource.LINUX_SPINAND,)


def _nand_read(name: str, opcode: int, what: str, protocol: str, dummy: int | None) -> Operation:
    """A SPI NAND read from cache: a 2-byte column address into the page
    the part has read into its cache, and the data from there on."""
    return Operation(
        name,
        opcode,
        OperationKind.READ,
        what,
        protocol,
        2,
        dummy,
        READ,
        shape_source=_SPINAND,
        flash_type=_NAND,
    )


def _nand_load(name: str, opcode: int, what: str, protocol: str) -> Operation:
    """A SPI NAND program load: data into the cache, from a 2-byte column
    address; program execute (0x10) then writes the cache to a page."""
    return Operation(
        name,
        opcode,
        OperationKind.PROGRAM,
        what,
        protocol,
        2,
        0,
        WRITE,
        shape_source=_SPINAND,
        flash_type=_NAND,
    )


#: SPI NAND's command set: the reads, loads and the rest that Linux's
#: :upstream:`linux:include/linux/mtd/spinand.h` (``SPINAND_*_OP``) and
#: MediaTek's mtk-snand driver send. Single transfer rate only: the double
#: transfer rate forms some Winbond parts take (``1S_1D_4D``, ``8D_8D_8D``,
#: ...) have no operation here yet. ``_3A`` is a read from cache sending a
#: 3-byte address (GigaDevice's GD5F4GQ4xC and GD5F1GQ4UF).
_NAND_OPERATIONS = [
    # Identification: 0x9f, then the id straight away, after a dummy byte,
    # or after an address byte, as the part answers.
    Operation(
        "NAND_RDID",
        0x9F,
        OperationKind.ID,
        "Read JEDEC id (SPI NAND, straight after the opcode)",
        "1-0-1",
        0,
        0,
        READ,
        shape_source=_SPINAND,
        flash_type=_NAND,
    ),
    Operation(
        "NAND_RDID_DUMMY",
        0x9F,
        OperationKind.ID,
        "Read JEDEC id (SPI NAND, after a dummy byte)",
        "1-0-1",
        0,
        8,
        READ,
        shape_source=_SPINAND,
        flash_type=_NAND,
    ),
    Operation(
        "NAND_RDID_ADDR",
        0x9F,
        OperationKind.ID,
        "Read JEDEC id (SPI NAND, after an address byte)",
        "1-1-1",
        1,
        0,
        READ,
        shape_source=_SPINAND,
        flash_type=_NAND,
    ),
    # Read: a page into the cache, then from the cache.
    Operation(
        "NAND_PAGE_READ",
        0x13,
        OperationKind.READ,
        "Read a page into the cache",
        "1-1-0",
        3,
        shape_source=_SPINAND,
        flash_type=_NAND,
    ),
    _nand_read("NAND_READ_CACHE_1_1_1", 0x03, "Read from cache", "1-1-1", 8),
    _nand_read("NAND_READ_CACHE_1_1_1_FAST", 0x0B, "Fast read from cache", "1-1-1", 8),
    _nand_read("NAND_READ_CACHE_1_1_2", 0x3B, "Read from cache, dual output", "1-1-2", 8),
    _nand_read("NAND_READ_CACHE_1_2_2", 0xBB, "Read from cache, dual I/O", "1-2-2", 4),
    _nand_read("NAND_READ_CACHE_1_1_4", 0x6B, "Read from cache, quad output", "1-1-4", 8),
    _nand_read("NAND_READ_CACHE_1_4_4", 0xEB, "Read from cache, quad I/O", "1-4-4", 4),
    _nand_read("NAND_READ_CACHE_1_1_8", 0x8B, "Read from cache, octal output", "1-1-8", 8),
    _nand_read("NAND_READ_CACHE_1_8_8", 0xCB, "Read from cache, octal I/O", "1-8-8", None),
    *(
        Operation(
            name,
            opcode,
            OperationKind.READ,
            f"{what}, 3-byte address",
            protocol,
            3,
            dummy,
            READ,
            shape_source=_SPINAND,
            flash_type=_NAND,
        )
        for name, opcode, what, protocol, dummy in (
            ("NAND_READ_CACHE_1_1_1_3A", 0x03, "Read from cache", "1-1-1", 0),
            ("NAND_READ_CACHE_1_1_1_FAST_3A", 0x0B, "Fast read from cache", "1-1-1", 8),
            ("NAND_READ_CACHE_1_1_2_3A", 0x3B, "Read from cache, dual output", "1-1-2", 8),
            ("NAND_READ_CACHE_1_1_4_3A", 0x6B, "Read from cache, quad output", "1-1-4", 8),
        )
    ),
    # Program: data into the cache (a load clears the rest of it, a random
    # load keeps it), then the cache to a page.
    _nand_load("NAND_PROGRAM_LOAD_1_1_1", 0x02, "Program load", "1-1-1"),
    _nand_load("NAND_PROGRAM_LOAD_1_1_4", 0x32, "Program load, quad input", "1-1-4"),
    _nand_load("NAND_PROGRAM_LOAD_1_1_8", 0x82, "Program load, octal input", "1-1-8"),
    _nand_load("NAND_PROGRAM_LOAD_1_8_8", 0xC2, "Program load, octal I/O", "1-8-8"),
    _nand_load("NAND_RANDOM_LOAD_1_1_1", 0x84, "Random program load", "1-1-1"),
    _nand_load("NAND_RANDOM_LOAD_1_1_4", 0x34, "Random program load, quad input", "1-1-4"),
    _nand_load("NAND_RANDOM_LOAD_1_8_8", 0xC4, "Random program load, octal I/O", "1-8-8"),
    Operation(
        "NAND_PROGRAM_EXECUTE",
        0x10,
        OperationKind.PROGRAM,
        "Program the cache into a page",
        "1-1-0",
        3,
        shape_source=_SPINAND,
        flash_type=_NAND,
    ),
    # Erase.
    Operation(
        "NAND_BLOCK_ERASE",
        0xD8,
        OperationKind.ERASE,
        "Erase a block",
        "1-1-0",
        3,
        shape_source=_SPINAND,
        flash_type=_NAND,
    ),
    # Features (registers), each read and written by its 1-byte address.
    Operation(
        "NAND_GET_FEATURE",
        0x0F,
        OperationKind.REGISTER,
        "Get feature (read a register)",
        "1-1-1",
        1,
        0,
        READ,
        1,
        shape_source=_SPINAND,
        flash_type=_NAND,
    ),
    Operation(
        "NAND_SET_FEATURE",
        0x1F,
        OperationKind.REGISTER,
        "Set feature (write a register)",
        "1-1-1",
        1,
        0,
        WRITE,
        1,
        shape_source=_SPINAND,
        flash_type=_NAND,
    ),
    # The bad block lookup table some parts remap blocks with.
    Operation(
        "NAND_BBM_SWAP",
        0xA1,
        OperationKind.REGISTER,
        "Swap a bad block for a good one (bad block lookup table)",
        "1-0-1",
        0,
        0,
        WRITE,
        4,
        shape_source=(ShapeSource.PART,),
        flash_type=_NAND,
    ),
    Operation(
        "NAND_READ_BBM_LUT",
        0xA5,
        OperationKind.REGISTER,
        "Read the bad block lookup table",
        "1-0-1",
        0,
        8,
        READ,
        shape_source=(ShapeSource.PART,),
        flash_type=_NAND,
    ),
    # Modes. The data byte is the die to select.
    Operation(
        "NAND_DIE_SELECT",
        0xC2,
        OperationKind.MODE,
        "Select a die (Winbond)",
        "1-0-1",
        0,
        0,
        WRITE,
        1,
        shape_source=_SPINAND,
        flash_type=_NAND,
    ),
]

_ALL = [
    # Read.
    Operation(
        "READ_1_1_1",
        0x03,
        OperationKind.READ,
        "Read data (low frequency)",
        "1-1-1",
        3,
        0,
        READ,
        shape_source=_LINUX_READ,
    ),
    Operation(
        "READ_1_1_1_FAST",
        0x0B,
        OperationKind.READ,
        "Fast read",
        "1-1-1",
        3,
        8,
        READ,
        shape_source=_LINUX_FAST,
    ),
    Operation(
        "READ_1_1_2",
        0x3B,
        OperationKind.READ,
        "Dual output fast read",
        "1-1-2",
        3,
        8,
        READ,
        shape_source=_LINUX_WIDE,
    ),
    Operation(
        "READ_1_2_2",
        0xBB,
        OperationKind.READ,
        "Dual I/O fast read",
        "1-2-2",
        3,
        4,
        READ,
        shape_source=(ShapeSource.FLASHPROG_FEATURES,),
    ),
    Operation(
        "READ_1_1_4",
        0x6B,
        OperationKind.READ,
        "Quad output fast read",
        "1-1-4",
        3,
        8,
        READ,
        shape_source=_LINUX_WIDE,
    ),
    Operation(
        "READ_1_4_4",
        0xEB,
        OperationKind.READ,
        "Quad I/O fast read",
        "1-4-4",
        3,
        6,
        READ,
        shape_source=(ShapeSource.FLASHPROG_FEATURES,),
    ),
    Operation(
        "READ_1_1_8",
        0x8B,
        OperationKind.READ,
        "Octal output fast read",
        "1-1-8",
        3,
        8,
        READ,
        shape_source=(ShapeSource.LINUX_NO_SFDP,),
    ),
    Operation(
        "READ_1_8_8",
        0xCB,
        OperationKind.READ,
        "Octal I/O fast read",
        "1-8-8",
        3,
        None,
        READ,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "READ_4_4_4",
        0xEB,
        OperationKind.READ,
        "QPI fast read",
        "4-4-4",
        3,
        None,
        READ,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "READ_8D_8D_8D",
        0x0B,
        OperationKind.READ,
        "Octal DTR read (xSPI; the fast read opcode)",
        "8D-8D-8D",
        4,
        20,
        READ,
        shape_source=(ShapeSource.LINUX_NO_SFDP,),
    ),
    Operation(
        "READ_1_1_1_4B",
        0x13,
        OperationKind.READ,
        "Read data, 4-byte address",
        "1-1-1",
        4,
        0,
        READ,
        shape_source=_LINUX_4B,
    ),
    Operation(
        "READ_1_1_1_FAST_4B",
        0x0C,
        OperationKind.READ,
        "Fast read, 4-byte address",
        "1-1-1",
        4,
        8,
        READ,
        shape_source=_LINUX_4B,
    ),
    Operation(
        "READ_1_1_2_4B",
        0x3C,
        OperationKind.READ,
        "Dual output fast read, 4-byte address",
        "1-1-2",
        4,
        8,
        READ,
        shape_source=_LINUX_4B,
    ),
    Operation(
        "READ_1_2_2_4B",
        0xBC,
        OperationKind.READ,
        "Dual I/O fast read, 4-byte address",
        "1-2-2",
        4,
        4,
        READ,
        shape_source=_LINUX_4B,
    ),
    Operation(
        "READ_1_1_4_4B",
        0x6C,
        OperationKind.READ,
        "Quad output fast read, 4-byte address",
        "1-1-4",
        4,
        8,
        READ,
        shape_source=_LINUX_4B,
    ),
    Operation(
        "READ_1_4_4_4B",
        0xEC,
        OperationKind.READ,
        "Quad I/O fast read, 4-byte address",
        "1-4-4",
        4,
        6,
        READ,
        shape_source=_LINUX_4B,
    ),
    Operation(
        "READ_4_4_4_4B",
        0xEC,
        OperationKind.READ,
        "QPI fast read, 4-byte address",
        "4-4-4",
        4,
        None,
        READ,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "READ_1_1_8_4B",
        0x7C,
        OperationKind.READ,
        "Octal output fast read, 4-byte address",
        "1-1-8",
        4,
        8,
        READ,
        shape_source=_LINUX_4B,
    ),
    Operation(
        "READ_1_8_8_4B",
        0xCC,
        OperationKind.READ,
        "Octal I/O fast read, 4-byte address",
        "1-8-8",
        4,
        None,
        READ,
        shape_source=(ShapeSource.PART,),
    ),
    # Program.
    Operation(
        "PP_1_1_1",
        0x02,
        OperationKind.PROGRAM,
        "Page program",
        "1-1-1",
        3,
        0,
        WRITE,
        shape_source=_LINUX_READ,
    ),
    Operation(
        "BP",
        0x02,
        OperationKind.PROGRAM,
        "Byte program (one byte per write enable)",
        "1-1-1",
        3,
        0,
        WRITE,
        1,
        shape_source=_SIZES,
    ),
    Operation(
        "AAI_WP",
        0xAD,
        OperationKind.PROGRAM,
        "Auto address increment word program (SST)",
        "1-1-1",
        3,
        0,
        WRITE,
        2,
        shape_source=_SIZES,
    ),
    Operation(
        "PP_1_1_4",
        0x32,
        OperationKind.PROGRAM,
        "Quad input page program",
        "1-1-4",
        3,
        0,
        WRITE,
        shape_source=_LINUX_READ,
    ),
    Operation(
        "PP_1_4_4",
        0x38,
        OperationKind.PROGRAM,
        "Quad I/O page program",
        "1-4-4",
        3,
        0,
        WRITE,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "PP_1_1_8",
        0x82,
        OperationKind.PROGRAM,
        "Octal input page program",
        "1-1-8",
        3,
        0,
        WRITE,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "PP_1_8_8",
        0xC2,
        OperationKind.PROGRAM,
        "Octal I/O page program",
        "1-8-8",
        3,
        0,
        WRITE,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "PP_8D_8D_8D",
        0x02,
        OperationKind.PROGRAM,
        "Octal DTR page program (xSPI)",
        "8D-8D-8D",
        4,
        0,
        WRITE,
        shape_source=(ShapeSource.LINUX_NO_SFDP,),
    ),
    Operation(
        "PP_1_1_1_4B",
        0x12,
        OperationKind.PROGRAM,
        "Page program, 4-byte address",
        "1-1-1",
        4,
        0,
        WRITE,
        shape_source=_LINUX_4B,
    ),
    Operation(
        "PP_1_1_4_4B",
        0x34,
        OperationKind.PROGRAM,
        "Quad input page program, 4-byte address",
        "1-1-4",
        4,
        0,
        WRITE,
        shape_source=_LINUX_4B,
    ),
    Operation(
        "PP_1_4_4_4B",
        0x3E,
        OperationKind.PROGRAM,
        "Quad I/O page program, 4-byte address",
        "1-4-4",
        4,
        0,
        WRITE,
        shape_source=(ShapeSource.PART,),
    ),
    # Erase: the command and the address of the block, nothing else.
    Operation("BE_256", 0xDB, OperationKind.ERASE, "Erase a 256 B page", "1-1-0", 3, shape_source=_SIZES),
    Operation(
        "BE_4K", 0x20, OperationKind.ERASE, "Erase a 4 KiB sector", "1-1-0", 3, shape_source=_SIZES
    ),
    Operation(
        "BE_4K_PMC",
        0xD7,
        OperationKind.ERASE,
        "Erase a 4 KiB sector (PMC)",
        "1-1-0",
        3,
        shape_source=_SIZES,
    ),
    Operation(
        "BE_32K", 0x52, OperationKind.ERASE, "Erase a 32 KiB block", "1-1-0", 3, shape_source=_SIZES
    ),
    Operation(
        "SE",
        0xD8,
        OperationKind.ERASE,
        "Erase a sector (usually 64 KiB)",
        "1-1-0",
        3,
        shape_source=_SIZES,
    ),
    Operation(
        "BE_ALT1",
        0x50,
        OperationKind.ERASE,
        "Erase a block (vendor-specific, 0x50)",
        "1-1-0",
        3,
        shape_source=_SIZES,
    ),
    Operation(
        "BE_ALT2",
        0x81,
        OperationKind.ERASE,
        "Erase a block/page (vendor-specific, 0x81)",
        "1-1-0",
        3,
        shape_source=_SIZES,
    ),
    Operation(
        "BE_40",
        0x40,
        OperationKind.ERASE,
        "Erase a parameter block (Intel S33, Spansion S25FL-P)",
        "1-1-0",
        3,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "BE_53",
        0x53,
        OperationKind.ERASE,
        "Erase a 32 KiB block, 4-byte address (Spansion)",
        "1-1-0",
        4,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "BE_4K_4B",
        0x21,
        OperationKind.ERASE,
        "Erase a 4 KiB sector, 4-byte address",
        "1-1-0",
        4,
        shape_source=_LINUX_4B,
    ),
    Operation(
        "BE_32K_4B",
        0x5C,
        OperationKind.ERASE,
        "Erase a 32 KiB block, 4-byte address",
        "1-1-0",
        4,
        shape_source=_LINUX_4B,
    ),
    Operation(
        "SE_4B",
        0xDC,
        OperationKind.ERASE,
        "Erase a sector, 4-byte address",
        "1-1-0",
        4,
        shape_source=_SIZES,
    ),
    Operation("CHIP_ERASE", 0xC7, OperationKind.ERASE, "Erase the whole chip", shape_source=_SIZES),
    Operation(
        "CHIP_ERASE_ALT",
        0x60,
        OperationKind.ERASE,
        "Erase the whole chip (alternative opcode)",
        shape_source=_SIZES,
    ),
    Operation(
        "CHIP_ERASE_ATMEL", 0x62, OperationKind.ERASE, "Erase the whole chip (Atmel)", shape_source=_SIZES
    ),
    Operation(
        "DIE_ERASE", 0xC4, OperationKind.ERASE, "Erase one die (Micron)", "1-1-0", 3, shape_source=_SIZES
    ),
    # Linux's SPINOR_OP_CYPRESS_DIE_ERASE, on parts in 4-byte address mode.
    Operation(
        "DIE_ERASE_61",
        0x61,
        OperationKind.ERASE,
        "Erase one die (Infineon S25H and S28H)",
        "1-1-0",
        4,
        shape_source=(ShapeSource.PART,),
    ),
    # Identification.
    Operation(
        "RDID", 0x9F, OperationKind.ID, "Read JEDEC id", "1-0-1", 0, 0, READ, 3, shape_source=_SIZES
    ),
    Operation(
        "RDID_ATMEL",
        0x15,
        OperationKind.ID,
        "Read id (Atmel AT25F)",
        "1-0-1",
        0,
        0,
        READ,
        2,
        shape_source=_SIZES,
    ),
    Operation(
        "RDID_M95",
        0x83,
        OperationKind.ID,
        "Read identification page (ST M95 EEPROM)",
        "1-1-1",
        2,
        0,
        READ,
        3,
        shape_source=_SIZES,
    ),
    Operation(
        "REMS",
        0x90,
        OperationKind.ID,
        "Read electronic manufacturer and device id",
        "1-1-1",
        3,
        0,
        READ,
        2,
        shape_source=_SIZES,
    ),
    # RES sends three dummy bytes (24 clocks) where others send an address.
    Operation(
        "RES",
        0xAB,
        OperationKind.ID,
        "Release from deep power-down and read electronic signature",
        "1-0-1",
        0,
        24,
        READ,
        1,
        shape_source=_SIZES,
    ),
    Operation(
        "RDSFDP",
        0x5A,
        OperationKind.ID,
        "Read SFDP (JESD216) parameters",
        "1-1-1",
        3,
        8,
        READ,
        shape_source=(ShapeSource.JESD216, ShapeSource.FLASHROM_SIZES),
    ),
    # Winbond's unique id: four dummy bytes, then the 64-bit id. Not an OTP
    # read, though some parts keep the id in their OTP area.
    Operation(
        "RUID",
        0x4B,
        OperationKind.ID,
        "Read unique id",
        "1-0-1",
        0,
        32,
        READ,
        8,
        shape_source=(ShapeSource.PART,),
    ),
    # Status and configuration registers.
    Operation(
        "WRSR",
        0x01,
        OperationKind.REGISTER,
        "Write status register",
        "1-0-1",
        0,
        0,
        WRITE,
        1,
        shape_source=_SIZES,
    ),
    Operation(
        "EWSR",
        0x50,
        OperationKind.REGISTER,
        "Enable write status register (instead of WREN)",
        shape_source=_SIZES,
    ),
    Operation(
        "WRSR2",
        0x31,
        OperationKind.REGISTER,
        "Write status register 2",
        "1-0-1",
        0,
        0,
        WRITE,
        1,
        shape_source=_SIZES,
    ),
    Operation(
        "WRSR3",
        0x11,
        OperationKind.REGISTER,
        "Write status register 3",
        "1-0-1",
        0,
        0,
        WRITE,
        1,
        shape_source=_SIZES,
    ),
    # The same 0x01 with two or three data bytes: SR1, then SR2 (or a
    # Macronix part's configuration register), then SR3.
    Operation(
        "WRSR_16",
        0x01,
        OperationKind.REGISTER,
        "Write status registers 1 and 2 (two bytes)",
        "1-0-1",
        0,
        0,
        WRITE,
        2,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "WRSR_24",
        0x01,
        OperationKind.REGISTER,
        "Write status registers 1 to 3 (three bytes)",
        "1-0-1",
        0,
        0,
        WRITE,
        3,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "RDSR2",
        0x35,
        OperationKind.REGISTER,
        "Read status register 2",
        "1-0-1",
        0,
        0,
        READ,
        1,
        shape_source=_SIZES,
    ),
    # Winbond's status register 3, and Macronix's configuration register.
    Operation(
        "RDSR3",
        0x15,
        OperationKind.REGISTER,
        "Read status register 3 (or configuration register)",
        "1-0-1",
        0,
        0,
        READ,
        1,
        shape_source=_SIZES,
    ),
    Operation(
        "RDSCUR",
        0x2B,
        OperationKind.REGISTER,
        "Read security register",
        "1-0-1",
        0,
        0,
        READ,
        1,
        shape_source=_SIZES,
    ),
    Operation(
        "WRSCUR",
        0x2F,
        OperationKind.REGISTER,
        "Write security register (set its lock bits)",
        shape_source=_SIZES,
    ),
    # The one-time-programmable area (OTP). Linux's names for the
    # security-register commands (spi-nor.h SPINOR_OP_RSECR, ...; otp.c
    # reads with 8 dummy clocks): Winbond's and GigaDevice's OTP regions,
    # read, programmed and erased by address. ISSI's function register is
    # read with 0x48 too, but with no address.
    Operation(
        "RSECR",
        0x48,
        OperationKind.REGISTER,
        "Read security registers (the OTP area)",
        "1-1-1",
        3,
        8,
        READ,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "PSECR",
        0x42,
        OperationKind.REGISTER,
        "Program security registers (the OTP area)",
        "1-1-1",
        3,
        0,
        WRITE,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "ESECR",
        0x44,
        OperationKind.REGISTER,
        "Erase a security register (an OTP region)",
        "1-1-0",
        3,
        shape_source=(ShapeSource.PART,),
    ),
    # Micron's and Spansion's "read OTP array" (OTPR): 0x4b with an address
    # and 8 dummy clocks, unlike Winbond's unique id read (RUID).
    Operation(
        "READ_OTP",
        0x4B,
        OperationKind.REGISTER,
        "Read the OTP area (0x4b)",
        "1-1-1",
        3,
        8,
        READ,
        shape_source=(ShapeSource.PART,),
    ),
    # Macronix: the OTP area is read and written in place of the array
    # between these two.
    Operation(
        "ENSO",
        0xB1,
        OperationKind.MODE,
        "Enter the secured OTP area",
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "EXSO",
        0xC1,
        OperationKind.MODE,
        "Exit the secured OTP area",
        shape_source=(ShapeSource.PART,),
    ),
    # Eon: likewise, until write disable (0x04).
    Operation(
        "ENTER_OTP_3A",
        0x3A,
        OperationKind.MODE,
        "Enter OTP mode (0x3a)",
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "RDFSR",
        0x70,
        OperationKind.REGISTER,
        "Read flag status register",
        "1-0-1",
        0,
        0,
        READ,
        1,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "CLSR",
        0x30,
        OperationKind.REGISTER,
        "Clear status register errors",
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "CLPEF",
        0x82,
        OperationKind.REGISTER,
        "Clear program and erase failure flags",
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "ULBPR",
        0x98,
        OperationKind.REGISTER,
        "Global block protection unlock",
        shape_source=(ShapeSource.PART,),
    ),
    # Sent in QPI mode, with the parameters as one byte on all four lines.
    Operation(
        "SET_READ_PARAMS",
        0xC0,
        OperationKind.REGISTER,
        "Set read parameters (dummy cycles, wrap)",
        "4-0-4",
        0,
        0,
        WRITE,
        1,
        shape_source=(ShapeSource.PART,),
    ),
    # Modes.
    Operation(
        "EN4B", 0xB7, OperationKind.MODE, "Enter 4-byte address mode", shape_source=(ShapeSource.PART,)
    ),
    Operation(
        "EX4B", 0xE9, OperationKind.MODE, "Exit 4-byte address mode", shape_source=(ShapeSource.PART,)
    ),
    Operation(
        "WREAR",
        0xC5,
        OperationKind.MODE,
        "Write extended address register",
        "1-0-1",
        0,
        0,
        WRITE,
        1,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "RDEAR",
        0xC8,
        OperationKind.MODE,
        "Read extended address register",
        "1-0-1",
        0,
        0,
        READ,
        1,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "BRWR",
        0x17,
        OperationKind.MODE,
        "Write bank address register",
        "1-0-1",
        0,
        0,
        WRITE,
        1,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "BRRD",
        0x16,
        OperationKind.MODE,
        "Read bank address register",
        "1-0-1",
        0,
        0,
        READ,
        1,
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "EQPI_38", 0x38, OperationKind.MODE, "Enter QPI mode (0x38)", shape_source=(ShapeSource.PART,)
    ),
    # In QPI mode the command itself goes out on all four lines.
    Operation(
        "RSTQIO_FF",
        0xFF,
        OperationKind.MODE,
        "Exit QPI mode (0xff)",
        "4-0-0",
        shape_source=(ShapeSource.PART,),
    ),
    Operation(
        "EQPI_35", 0x35, OperationKind.MODE, "Enter QPI mode (0x35)", shape_source=(ShapeSource.PART,)
    ),
    Operation(
        "RSTQIO_F5",
        0xF5,
        OperationKind.MODE,
        "Exit QPI mode (0xf5)",
        "4-0-0",
        shape_source=(ShapeSource.PART,),
    ),
    # The data byte is the die to select (0, 1, ...).
    Operation(
        "DIE_SELECT",
        0xC2,
        OperationKind.MODE,
        "Select a die (Winbond)",
        "1-0-1",
        0,
        0,
        WRITE,
        1,
        shape_source=(ShapeSource.PART,),
    ),
    *_NAND_OPERATIONS,
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


@dataclass(frozen=True, slots=True)
class OpcodeUse:
    """One operation an upstream entry implies: its name in
    :data:`OPERATIONS`, and what in the upstream implies it (a flag, a
    field, or the upstream's default). ``implied`` marks a use
    :mod:`spiflash.derive` adds from the entry's other fields (its erasers,
    how it reads the id); it is never stored.

    ``assumed`` marks a driver default: an operation the upstream's driver
    issues to every part (or every part of a class) whatever the entry says,
    such as Linux's fast read or U-Boot's quad page program for every
    ``SPI_NOR_QUAD_READ`` part, and the 4-byte form of one. It is stored (the
    data's ``"assumed": true``), shown as a default, and implies no
    capability (:func:`spiflash.derive.features`). Read 0x03 on a part
    with SFDP tables is that part's own fact, not assumed; SFDP says nothing
    of fast read 0x0b or page program 0x02.

    ``dummy_clocks`` is the clocks between the address and the data that
    the part needs for this operation, where its source says
    (:meth:`Sfdp.facts <spiflash.sfdp.Sfdp.facts>` gives each read's from
    its SFDP tables); ``None`` where it does not, and
    :attr:`Operation.dummy_clocks` is the usual number. The data stores it as
    a use's ``"dummy_clocks"``, where a source gives it."""

    op: str
    via: str
    implied: bool = False
    assumed: bool = False
    dummy_clocks: int | None = None

    @property
    def opcode(self) -> int:
        """The opcode byte, from :data:`OPERATIONS`."""
        return OPERATIONS[self.op].opcode
