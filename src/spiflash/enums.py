"""The fixed vocabularies of the database, as enums.

Each is a :class:`~enum.StrEnum`, so a member is also its string
(``FlashType.NOR == "nor"``): the data files, the JSON the command prints and
any code that compares with plain strings all keep working.
"""

from __future__ import annotations

from enum import StrEnum


class Source(StrEnum):
    """An upstream project the data is read from.

    The members are declared in priority order: when sources disagree on a
    value and are otherwise tied, the earlier one wins.

    1. flashrom and flashprog: their entries are per part, tested on
       hardware, and carry the most detail.
    2. Linux, then U-Boot, which kept Linux's older table format.
    3. Dediprog: a programmer maker's own table, per part and large, but
       not reviewed in the open, with mistakes the others' reviews would
       catch (ids under the wrong command, SPI NAND sizes counting the
       spare area).
    4. Rockchip: a chip vendor's production driver, whose values its
       boards boot from, per part and with SPI NAND geometry; but a small
       table of the parts its own boards use, not reviewed in the open,
       with entries the driver never reaches (a second entry for an id).
    5. MediaTek: like Rockchip, a chip vendor's production driver, whose
       SPI NAND geometry (spare area, planes, dies) its boards boot from,
       per part; but SPI NAND only, not reviewed in the open, with entries
       its own others contradict (one part under two ids) and id-method
       labels that decide nothing (the driver tries every entry with a
       dummy byte and without).
    6. OpenOCD and openFPGALoader: their tables are the smallest and the
       least specific.
    7. IMSProg: a programmer's table of parts it reads, but every SPI NOR
       entry has the same page and block size, and its format (and some of
       its values) came from the closed databases of commercial programmers
       (EZP2019 to EZP2023, Minipro, XP866+), which cannot be checked.
    8. QEMU: its table is a 2012 copy of Linux's, kept for the parts its
       boards emulate, though its SFDP dumps are the only complete ones any
       upstream has.
    9. Zephyr: it has no table of parts, only boards describing the chip
       each carries, whose values are written (and copied between boards)
       by each board's porter."""

    FLASHROM = "flashrom"
    FLASHPROG = "flashprog"
    LINUX = "linux"
    UBOOT = "u-boot"
    DEDIPROG = "dediprog"
    ROCKCHIP = "rockchip"
    MEDIATEK = "mediatek"
    OPENOCD = "openocd"
    OPENFPGALOADER = "openfpgaloader"
    IMSPROG = "imsprog"
    QEMU = "qemu"
    ZEPHYR = "zephyr"

    @property
    def priority(self) -> int:
        """0 for the most trusted source, counting up."""
        return _SOURCE_PRIORITY[self]

    @property
    def label(self) -> str:
        """The project's name as it writes it."""
        return _SOURCE_LABELS[self]


_SOURCE_PRIORITY = {source: i for i, source in enumerate(Source)}

_SOURCE_LABELS = {
    Source.FLASHROM: "flashrom",
    Source.FLASHPROG: "flashprog",
    Source.LINUX: "Linux",
    Source.UBOOT: "U-Boot",
    Source.DEDIPROG: "Dediprog",
    Source.ROCKCHIP: "Rockchip",
    Source.MEDIATEK: "MediaTek",
    Source.OPENOCD: "OpenOCD",
    Source.OPENFPGALOADER: "openFPGALoader",
    Source.IMSPROG: "IMSProg",
    Source.QEMU: "QEMU",
    Source.ZEPHYR: "Zephyr",
}


class FlashType(StrEnum):
    """The kind of flash a chip is."""

    NOR = "nor"
    NAND = "nand"

    @property
    def label(self) -> str:
        return "SPI NAND" if self is FlashType.NAND else "SPI NOR"


class IdFamily(StrEnum):
    """Which command a chip's id answers: JEDEC read-id (0x9f), or one of the
    legacy commands older parts answer instead."""

    JEDEC = "jedec"
    REMS = "rems"
    RES1 = "res1"
    RES2 = "res2"
    AT25F = "at25f"
    ST95 = "st95"


class IdMethod(StrEnum):
    """Exactly how an upstream reads a chip's id. The SPI NAND variants of
    read-id send a dummy byte, an address byte or nothing after the opcode,
    but all answer JEDEC ids."""

    RDID = "rdid"
    RDID_OPCODE = "rdid_opcode"
    RDID_OPCODE_DUMMY = "rdid_opcode_dummy"
    RDID_OPCODE_ADDR = "rdid_opcode_addr"
    REMS = "rems"
    RES1 = "res1"
    RES2 = "res2"
    AT25F = "at25f"
    ST95 = "st95"

    @property
    def family(self) -> IdFamily:
        """The kind of id this method reads."""
        if self.value.startswith("rdid"):
            return IdFamily.JEDEC
        return IdFamily(self.value)


class Feature(StrEnum):
    """A capability an upstream says a chip has, normalised across upstreams.
    Anything an upstream says that has no member here stays in the record's
    ``flags``."""

    ERASE_4K = "erase_4k"
    ERASE_32K = "erase_32k"
    ERASE_64K = "erase_64k"
    SFDP = "sfdp"
    FAST_READ = "fast_read"
    DUAL_READ = "dual_read"
    QUAD_READ = "quad_read"
    QUAD_PP = "quad_pp"
    OCTAL_READ = "octal_read"
    OCTAL_DTR_READ = "octal_dtr_read"
    OCTAL_DTR_PP = "octal_dtr_pp"
    QPI = "qpi"
    TWO_BYTE_ADDR = "2byte_addr"
    FOUR_BYTE_ADDR = "4byte_addr"
    FOUR_BYTE_OPCODES = "4byte_opcodes"
    OTP = "otp"
    LOCK = "lock"
    NO_ERASE = "no_erase"
    RWW = "rww"

    @property
    def description(self) -> str:
        return _FEATURE_DESCRIPTIONS[self]


_FEATURE_DESCRIPTIONS = {
    Feature.ERASE_4K: "4 KiB sectors can be erased (0x20 or equivalent)",
    Feature.ERASE_32K: "32 KiB blocks can be erased (0x52 or equivalent)",
    Feature.ERASE_64K: "64 KiB blocks can be erased (0xd8 or equivalent)",
    Feature.SFDP: "answers SFDP (JESD216) queries",
    Feature.FAST_READ: "supports fast read (0x0b)",
    Feature.DUAL_READ: "supports dual-output/IO read",
    Feature.QUAD_READ: "supports quad-output/IO read",
    Feature.QUAD_PP: "supports quad-input page program",
    Feature.OCTAL_READ: "supports octal read",
    Feature.OCTAL_DTR_READ: "supports octal DTR read",
    Feature.OCTAL_DTR_PP: "supports octal DTR page program",
    Feature.QPI: "supports QPI (4-4-4) mode",
    Feature.TWO_BYTE_ADDR: "takes 2-byte addresses (small FRAM, MRAM and EEPROM parts)",
    Feature.FOUR_BYTE_ADDR: "supports 4-byte addressing",
    Feature.FOUR_BYTE_OPCODES: "has dedicated 4-byte-address opcodes",
    Feature.OTP: "has one-time-programmable area",
    Feature.LOCK: "block protection bits in the status register",
    Feature.NO_ERASE: "no erase needed (FRAM/MRAM)",
    Feature.RWW: "read-while-write",
}


class AddressBytes(StrEnum):
    """How many address bytes a part takes (BFPT DW1[18:17]; for a record,
    :func:`spiflash.derive.address_bytes`). ``TWO`` is no BFPT code: small
    FRAM, MRAM and EEPROM parts a source says take two."""

    TWO = "2"
    THREE = "3"
    THREE_OR_FOUR = "3 or 4"
    FOUR = "4"


class FourByteMethod(StrEnum):
    """A way into (or out of) 4-byte address mode: BFPT DW16's, and
    flashrom's setting of the extended address register's bit 7. The value
    is the token the data stores; :attr:`label` says it in words.

    A record's :attr:`~spiflash.model.Record.four_byte_modes` holds only the
    ways in (:data:`ENTER_METHODS`), and never ``OPCODES_4B``: the 4-byte
    operations themselves say that (``4byte_opcodes``). ``HW_RESET``,
    ``SW_RESET`` and ``POWER_CYCLE`` are ways out only."""

    EN4B = "en4b"
    WREN_EN4B = "wren_en4b"
    WREAR = "wrear"
    EAR_BIT7 = "ear_bit7"
    BRWR = "brwr"
    NV_CR = "nv_cr"
    OPCODES_4B = "opcodes_4b"
    ALWAYS_4B = "always_4b"
    HW_RESET = "hw_reset"
    SW_RESET = "sw_reset"
    POWER_CYCLE = "power_cycle"

    @property
    def label(self) -> str:
        """The way in, in words: ``"EN4B (0xb7)"``."""
        return _FOUR_BYTE_LABELS[self]

    @property
    def exit_label(self) -> str:
        """The way out, in words (BFPT DW16[18:14]): ``"EX4B (0xe9)"`` for
        ``EN4B``; a register's way out is clearing it, so its label is the
        way in's."""
        return _FOUR_BYTE_EXIT_LABELS.get(self, _FOUR_BYTE_LABELS[self])


_FOUR_BYTE_LABELS = {
    FourByteMethod.EN4B: "EN4B (0xb7)",
    FourByteMethod.WREN_EN4B: "WREN then EN4B (0x06, 0xb7)",
    FourByteMethod.WREAR: "extended address register (0xc5/0xc8)",
    FourByteMethod.EAR_BIT7: "extended address register bit 7",
    FourByteMethod.BRWR: "bank address register (0x17/0x16)",
    FourByteMethod.NV_CR: "16-bit non-volatile configuration register",
    FourByteMethod.OPCODES_4B: "dedicated 4-byte opcodes",
    FourByteMethod.ALWAYS_4B: "always 4-byte",
    FourByteMethod.HW_RESET: "hardware reset",
    FourByteMethod.SW_RESET: "software reset",
    FourByteMethod.POWER_CYCLE: "power cycle",
}

_FOUR_BYTE_EXIT_LABELS = {
    FourByteMethod.EN4B: "EX4B (0xe9)",
    FourByteMethod.WREN_EN4B: "WREN then EX4B (0x06, 0xe9)",
}

#: The ways into 4-byte address mode a record's ``four_byte_modes`` may
#: hold: not ``OPCODES_4B`` (the ``_4B`` operations say it) nor the ways out.
ENTER_METHODS = frozenset(FourByteMethod) - {
    FourByteMethod.OPCODES_4B,
    FourByteMethod.HW_RESET,
    FourByteMethod.SW_RESET,
    FourByteMethod.POWER_CYCLE,
}


class TestResult(StrEnum):
    """How well flashrom (or flashprog) says it supports one operation on a
    part (their ``enum test_state``)."""

    __test__ = False  # not a pytest test class

    OK = "ok"
    #: Not tested.
    NT = "nt"
    #: Known not to work.
    BAD = "bad"
    #: Depends on the configuration (an Intel flash descriptor, ...).
    DEP = "dep"
    #: Not applicable (writing a ROM).
    NA = "na"


class OperationKind(StrEnum):
    """What an SPI operation is for; the members are in the order the site
    and the command list operations."""

    ID = "id"
    READ = "read"
    PROGRAM = "program"
    ERASE = "erase"
    REGISTER = "register"
    MODE = "mode"


class DataPhase(StrEnum):
    """Which way an operation's data goes, if it has any."""

    READ = "read"  # the flash drives the data lines
    WRITE = "write"  # the host does


class ShapeSource(StrEnum):
    """Where an operation's bus shape (address size, dummy clocks, bytes
    moved) comes from (:attr:`Operation.shape_source
    <spiflash.opcodes.Operation.shape_source>`): not a duration, which is a
    :class:`TimedEvent`'s. The documentation turns each into a linked
    explanation."""

    LINUX_DEFAULT = "linux-default"
    LINUX_NO_SFDP = "linux-no-sfdp"
    LINUX_4B = "linux-4b"
    FLASHPROG_FEATURES = "flashprog-features"
    FLASHROM_SIZES = "flashrom-sizes"
    JESD216 = "jesd216"
    LINUX_SPINAND = "linux-spinand"
    PART = "part"


class Bound(StrEnum):
    """Which bound of a datasheet parameter a duration is: the bound of the
    part's own parameter (tDP max, tCRDP min), not of the host's wait for
    it. :data:`spiflash.timings.BOUNDS` says which each event may have."""

    #: The part needs at least this: a pulse width, a dwell.
    MINIMUM = "minimum"
    TYPICAL = "typical"
    #: The part takes at most this: an erase, a wake-up.
    MAXIMUM = "maximum"
    #: A source gives the value without saying which bound it is
    #: (Dediprog's ``ChipEraseTime``): never compared with the others.
    UNSPECIFIED = "unspecified"


class TimedEvent(StrEnum):
    """What a part's duration is the time of (:class:`spiflash.timings.Timings`)."""

    #: Erasing one block, by one eraser (keyed by its 3-byte erase opcode:
    #: an SFDP erase type).
    BLOCK_ERASE = "block_erase"
    #: Erasing the whole chip, tCE (per die, on a part of several: JESD216).
    CHIP_ERASE = "chip_erase"
    #: Programming a page, tPP.
    PAGE_PROGRAM = "page_program"
    #: Programming the first byte, tBP1.
    BYTE_PROGRAM_FIRST = "byte_program_first"
    #: Programming each further byte, tBPn.
    BYTE_PROGRAM_ADDITIONAL = "byte_program_additional"
    #: Reading a page from the array into the cache (SPI NAND), tRD.
    PAGE_READ = "page_read"
    #: From an erase suspend command to the part being suspended (BFPT DW12).
    ERASE_SUSPEND = "erase_suspend"
    #: From a program suspend command to the part being suspended (BFPT DW12).
    PROGRAM_SUSPEND = "program_suspend"
    #: From an erase resume to the next suspend (BFPT DW12).
    ERASE_RESUME_TO_SUSPEND = "erase_resume_to_suspend"
    #: From a program resume to the next suspend (BFPT DW12).
    PROGRAM_RESUME_TO_SUSPEND = "program_resume_to_suspend"
    #: From the end of the deep power-down command to deep power-down, tDP.
    DPD_ENTER = "dpd_enter"
    #: From the release (the command, or the chip select pulse) to the part
    #: being ready, tRES1 or tRDP.
    DPD_EXIT = "dpd_exit"
    #: The least time in deep power-down before a release, tDPDD.
    DPD_MIN_TIME = "dpd_min_time"
    #: The chip select low pulse that wakes the part, tCRDP (Zephyr's
    #: binding spells it tCDRP).
    DPD_WAKE_PULSE = "dpd_wake_pulse"
    #: The RESET# pulse width.
    RESET_PULSE = "reset_pulse"
    #: From a reset to the part being ready, tRST.
    RESET_RECOVERY = "reset_recovery"
