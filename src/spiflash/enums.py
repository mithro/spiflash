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
    value and are otherwise tied, the earlier one wins. flashrom and
    flashprog come first (their entries are per part, tested on hardware and
    carry the most detail), OpenOCD and openFPGALoader last (their tables are
    the smallest and the least specific)."""

    FLASHROM = "flashrom"
    FLASHPROG = "flashprog"
    LINUX = "linux"
    UBOOT = "u-boot"
    OPENOCD = "openocd"
    OPENFPGALOADER = "openfpgaloader"

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
    Source.OPENOCD: "OpenOCD",
    Source.OPENFPGALOADER: "openFPGALoader",
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
    Feature.ERASE_32K: "32 KiB blocks can be erased (0x52)",
    Feature.ERASE_64K: "64 KiB blocks can be erased (0xd8)",
    Feature.SFDP: "answers SFDP (JESD216) queries",
    Feature.FAST_READ: "supports fast read (0x0b)",
    Feature.DUAL_READ: "supports dual-output/IO read",
    Feature.QUAD_READ: "supports quad-output/IO read",
    Feature.QUAD_PP: "supports quad-input page program",
    Feature.OCTAL_READ: "supports octal read",
    Feature.OCTAL_DTR_READ: "supports octal DTR read",
    Feature.OCTAL_DTR_PP: "supports octal DTR page program",
    Feature.QPI: "supports QPI (4-4-4) mode",
    Feature.FOUR_BYTE_ADDR: "supports 4-byte addressing",
    Feature.FOUR_BYTE_OPCODES: "has dedicated 4-byte-address opcodes",
    Feature.OTP: "has one-time-programmable area",
    Feature.LOCK: "block protection bits in the status register",
    Feature.NO_ERASE: "no erase needed (FRAM/MRAM)",
    Feature.RWW: "read-while-write",
}


class OperationKind(StrEnum):
    """What an SPI operation is for; the members are in the order the site
    and the command list operations."""

    ID = "id"
    READ = "read"
    PROGRAM = "program"
    ERASE = "erase"
    REGISTER = "register"
    MODE = "mode"
