"""SFDP (JEDEC JESD216) parameter tables, decoded.

A chip answers ``RDSFDP`` (0x5a) with a small parameter area: a header, then
one 8-byte parameter header per table, then the tables. The Basic Flash
Parameter Table (BFPT) gives the density, the erase types with their
opcodes, the fast-read opcodes with their mode clocks and wait states, the
address width, and (from JESD216A on) the page size, the quad-enable
method, the 4-byte-address mechanisms and the soft-reset sequence. The
4-byte Address Instruction Table (4BAIT) adds the dedicated 4-byte opcodes.

:func:`parse` decodes a dump, such as Linux's
``/sys/bus/spi/devices/*/spi-nor/sfdp``, into an :class:`Sfdp`. The layout
follows Linux's :upstream:`linux:drivers/mtd/spi-nor/sfdp.c`, with the fields
Linux does not read taken from the standard.

What SFDP does not say: the vendor and part name (a vendor table's id
carries the JEP106 code, nothing more), the supply voltage, block
protection, OTP, anything about parts older than JESD216. What it says is
what the part's designers wrote, which is sometimes wrong (Linux keeps
per-part fixups for tables with a wrong density, page size or missing
4-byte method). :class:`Sfdp` reports what is written.

Reading the fields: dwords are little-endian; "DW3[31:16]" is bits 31 to 16
of the third dword, dwords counted from 1 as the standard does. A table is
decoded only as far as its header's ``length`` says it goes, so a field of
a later revision is ``None`` on an older table rather than read from
padding.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any

from . import derive
from .enums import (
    ENTER_METHODS,
    AddressBytes,
    Bound,
    Feature,
    FlashType,
    FourByteMethod,
    OperationKind,
    TimedEvent,
)
from .opcodes import OPERATIONS, OpcodeUse
from .registers import QUAD_ENABLE_REQUIREMENTS, NoQuadEnable, QuadEnableRequirement, RegisterBit
from .timings import Timings
from .units import human_duration, human_size

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from .model import Eraser

SIGNATURE = b"SFDP"

#: The name of each (major, minor) revision.
REVISIONS = {
    (1, 0): "JESD216",
    (1, 5): "JESD216A",
    (1, 6): "JESD216B",
    (1, 7): "JESD216C",
    (1, 8): "JESD216D",
}

BFPT_ID = 0xFF00
SECTOR_MAP_ID = 0xFF81
FOUR_BYTE_ID = 0xFF84
PROFILE1_ID = 0xFF05
SCCR_ID = 0xFF87
SCCR_MC_ID = 0xFF88

#: The parameter tables JEDEC defines, by id.
JEDEC_TABLES = {
    BFPT_ID: "BFPT",
    0xFF03: "RPMC",
    PROFILE1_ID: "xSPI profile 1.0",
    0xFF06: "xSPI profile 2.0",
    0xFF09: "SCCR map for xSPI profile 2.0",
    0xFF0A: "octal DDR mode command sequences",
    0xFF0C: "x4 quad IO with DS",
    0xFF0D: "quad DDR mode command sequences",
    SECTOR_MAP_ID: "sector map",
    FOUR_BYTE_ID: "4BAIT",
    SCCR_ID: "SCCR map",
    SCCR_MC_ID: "SCCR map for multi-chip",
}

_TABLE_LONG_NAMES = {
    BFPT_ID: "Basic Flash Parameter Table",
    FOUR_BYTE_ID: "4-byte Address Instruction Table",
    SCCR_ID: "Status, Control and Configuration Register Map",
    SECTOR_MAP_ID: "Sector Map Parameter Table",
}


def _bits(dword: int, hi: int, lo: int) -> int:
    """Bits ``hi`` down to ``lo`` of ``dword``."""
    return (dword >> lo) & ((1 << (hi - lo + 1)) - 1)


def _bit(dword: int, n: int) -> bool:
    return bool(dword >> n & 1)


#: The ways in and out of 4-byte mode, in the order they are declared.
_ORDER = list(FourByteMethod)

_ENTER_4B = {
    24: FourByteMethod.EN4B,
    25: FourByteMethod.WREN_EN4B,
    26: FourByteMethod.WREAR,
    27: FourByteMethod.BRWR,
    28: FourByteMethod.NV_CR,
    29: FourByteMethod.OPCODES_4B,
    30: FourByteMethod.ALWAYS_4B,
}
_EXIT_4B = {
    14: FourByteMethod.EN4B,
    15: FourByteMethod.WREN_EN4B,
    16: FourByteMethod.WREAR,
    17: FourByteMethod.BRWR,
    18: FourByteMethod.NV_CR,
    19: FourByteMethod.HW_RESET,
    20: FourByteMethod.SW_RESET,
    21: FourByteMethod.POWER_CYCLE,
}

# BFPT DW16[13:8], one bit per sequence; all clear means no soft reset.
_SOFT_RESET = {
    8: "0xf on 4 lines, 8 clocks",
    9: "0xf on 4 lines, 10 clocks (4-byte mode)",
    10: "0xf on 4 lines, 16 clocks",
    11: "0xf0",
    12: "0x66 then 0x99",
    13: "exit 0-4-4 mode first",
}

_SR1_WRITE_ENABLE = {
    0: "non-volatile, WREN (0x06)",
    1: "volatile, WREN (0x06)",
    2: "volatile, 0x50",
    3: "non-volatile and volatile, WREN (0x06) and 0x50",
    4: "mixed, WREN (0x06)",
}

#: The Quad Enable Requirements codes (BFPT DW15[22:20]), as JESD216B
#: describes each (:data:`spiflash.registers.QUAD_ENABLE_REQUIREMENTS`).
QUAD_ENABLE = QUAD_ENABLE_REQUIREMENTS

_QPI_ENABLE = {
    4: "set QE, then 0x38",
    5: "0x38",
    6: "0x35",
    7: "read-modify-write 0x65/0x71, set bit 6 at 0x800003",
}
_QPI_DISABLE = {
    0: "0xff",
    1: "0xf5",
    2: "read-modify-write 0x65/0x71, clear bit 6 at 0x800003",
    3: "0x66 then 0x99",
}


# (protocol, dword, shift of the 16-bit settings half, support test)
_READS: tuple[tuple[str, int, int, Callable[[Sequence[int]], bool]], ...] = (
    ("1-1-2", 4, 0, lambda dw: _bit(dw[0], 16)),
    ("1-2-2", 4, 16, lambda dw: _bit(dw[0], 20)),
    ("1-1-4", 3, 16, lambda dw: _bit(dw[0], 22)),
    ("1-4-4", 3, 0, lambda dw: _bit(dw[0], 21)),
    ("2-2-2", 6, 16, lambda dw: _bit(dw[4], 0)),
    ("4-4-4", 7, 16, lambda dw: _bit(dw[4], 4)),
)

# 4BAIT DW1: bit -> (description, opcode, protocol, address bytes, kind)
_FOUR_BYTE_INSTRUCTIONS: dict[int, tuple[str, int, str, OperationKind]] = {
    0: ("read", 0x13, "1-1-1", OperationKind.READ),
    1: ("fast read", 0x0C, "1-1-1", OperationKind.READ),
    2: ("fast read 1-1-2", 0x3C, "1-1-2", OperationKind.READ),
    3: ("fast read 1-2-2", 0xBC, "1-2-2", OperationKind.READ),
    4: ("fast read 1-1-4", 0x6C, "1-1-4", OperationKind.READ),
    5: ("fast read 1-4-4", 0xEC, "1-4-4", OperationKind.READ),
    6: ("page program", 0x12, "1-1-1", OperationKind.PROGRAM),
    7: ("page program 1-1-4", 0x34, "1-1-4", OperationKind.PROGRAM),
    8: ("page program 1-4-4", 0x3E, "1-4-4", OperationKind.PROGRAM),
    13: ("DTR fast read 1-1-1", 0x0E, "1D-1D-1D", OperationKind.READ),
    14: ("DTR fast read 1-2-2", 0xBE, "1D-2D-2D", OperationKind.READ),
    15: ("DTR fast read 1-4-4", 0xEE, "1D-4D-4D", OperationKind.READ),
    20: ("fast read 1-1-8", 0x7C, "1-1-8", OperationKind.READ),
    21: ("fast read 1-8-8", 0xCC, "1-8-8", OperationKind.READ),
    22: ("DTR fast read 1-8-8", 0xFD, "1D-8D-8D", OperationKind.READ),
    23: ("page program 1-1-8", 0x84, "1-1-8", OperationKind.PROGRAM),
    24: ("page program 1-8-8", 0x8E, "1-8-8", OperationKind.PROGRAM),
}
# What the BFPT must advertise for a 4BAIT bit to count: the 3-byte form of
# the read (Linux takes a 4-byte read only then) and, for DTR, the DTR bit.
# Older tables fill then-reserved bits with ones, so an ungated bit 20 or
# above would claim octal instructions for a Winbond quad part.
_FOUR_BYTE_GATE: dict[int, tuple[str | None, bool]] = {
    2: ("1-1-2", False),
    3: ("1-2-2", False),
    4: ("1-1-4", False),
    5: ("1-4-4", False),
    13: (None, True),
    14: ("1-2-2", True),
    15: ("1-4-4", True),
    20: ("1-1-8", False),
    21: ("1-8-8", False),
    22: ("1-8-8", True),
    23: ("1-1-8", False),
    24: ("1-8-8", False),
}

# Operations by what SFDP gives: reads and programs by (kind, opcode,
# protocol); erases and mode changes by opcode alone. SFDP is SPI NOR's, so
# only its operations.
_NOR = [op for op in OPERATIONS.values() if op.flash_type is FlashType.NOR]
_BY_SHAPE: dict[tuple[OperationKind, int, str], str] = {}
for _op in _NOR:
    if _op.kind in (OperationKind.READ, OperationKind.PROGRAM):
        # The first of a shape is the usual one (PP_1_1_1, not BP, for 0x02).
        _BY_SHAPE.setdefault((_op.kind, _op.opcode, _op.protocol), _op.name)
_ERASE_BY_OPCODE = {op.opcode: op.name for op in _NOR if op.kind is OperationKind.ERASE}
_MODE_BY_OPCODE = {op.opcode: op.name for op in _NOR if op.kind is OperationKind.MODE}


def revision_name(major: int | None, minor: int | None) -> str:
    """``JESD216B`` for 1.6; ``JESD216 rev 1.9`` for one this module has no
    name for; ``"unknown revision"`` where it is not known (``None``)."""
    if major is None or minor is None:
        return "unknown revision"
    return REVISIONS.get((major, minor), f"JESD216 rev {major}.{minor}")


@dataclass(frozen=True, slots=True)
class ParameterHeader:
    """One 8-byte parameter header: which table, its revision, and where it is.

    A header :func:`from_tables` makes up for a table given without its
    SFDP area is ``synthetic``: its revision is unknown (``major`` and
    ``minor`` are ``None``), its ``length`` is the table's, and its
    ``pointer`` is 0."""

    index: int
    id: int
    major: int | None
    minor: int | None
    #: In dwords.
    length: int
    #: Byte offset of the table in the SFDP area.
    pointer: int
    synthetic: bool = False

    @property
    def id_lsb(self) -> int:
        return self.id & 0xFF

    @property
    def id_msb(self) -> int:
        return self.id >> 8

    @property
    def is_jedec(self) -> bool:
        """A table JEDEC defines, as opposed to a vendor's own."""
        return self.id in JEDEC_TABLES

    @property
    def is_vendor(self) -> bool:
        """A vendor table. Its id MSB should be 0 (JESD216A on), but JESD216
        1.0 had only one id byte, so the MSB reads 0xff then: the LSB's
        odd parity (a JEP106 code) tells it from a JEDEC table."""
        if self.is_jedec:
            return False
        return self.id_msb != 0xFF or self.id_lsb.bit_count() % 2 == 1

    @property
    def manufacturer_id(self) -> int | None:
        """The JEP106 code in a vendor table's id (its parity bit included)."""
        return self.id_lsb if self.is_vendor else None

    @property
    def name(self) -> str:
        if self.is_jedec:
            return JEDEC_TABLES[self.id]
        if self.is_vendor:
            return f"vendor table (0x{self.id_lsb:02x})"
        return f"unknown table 0x{self.id:04x}"

    @property
    def revision(self) -> str:
        """``"1.6"``; ``"?"`` for a synthetic header."""
        return "?" if self.major is None else f"{self.major}.{self.minor}"

    @property
    def end(self) -> int:
        return self.pointer + 4 * self.length


@dataclass(frozen=True, slots=True)
class Table:
    """A parameter table's dwords, as many of them as the dump holds."""

    header: ParameterHeader
    dwords: tuple[int, ...]

    @property
    def truncated(self) -> bool:
        return len(self.dwords) < self.header.length

    def dword(self, n: int) -> int | None:
        """Dword ``n``, counted from 1 as the standard does; ``None`` past
        the table's declared length or the dump."""
        if n < 1 or n > self.header.length or n > len(self.dwords):
            return None
        return self.dwords[n - 1]


@dataclass(frozen=True, slots=True)
class FastRead:
    """A fast-read mode: its opcode, and the clocks between address and data."""

    protocol: str
    opcode: int
    #: Clocks carrying the mode bits (the continuous-read code).
    mode_clocks: int
    wait_states: int

    @property
    def dummy_clocks(self) -> int:
        """Mode clocks plus wait states: what a controller must insert."""
        return self.mode_clocks + self.wait_states

    @property
    def address_dtr(self) -> bool:
        return "D" in self.protocol


@dataclass(frozen=True, slots=True)
class EraseType:
    """One of the BFPT's up to four erase types."""

    index: int
    size: int
    opcode: int
    #: Typical time in nanoseconds, when the table gives it (JESD216A on).
    typical_ns: int | None = None
    #: The 4-byte-address opcode for the same erase, from the 4BAIT.
    opcode_4b: int | None = None


@dataclass(frozen=True, slots=True)
class Bfpt:
    """The Basic Flash Parameter Table, decoded as far as its length goes.

    Fields a table's revision does not have are ``None``; a JESD216 (1.0)
    table has only the first nine dwords."""

    dwords: tuple[int, ...]
    #: The table's revision; ``None`` when given without its header
    #: (:func:`from_tables`).
    major: int | None
    minor: int | None
    length: int
    density_bits: int | None
    #: The 4 KiB erase opcode of DW1, ``None`` when the table says there is none.
    erase_4k_opcode: int | None
    uniform_4k: bool
    write_granularity_64: bool
    address_bytes: AddressBytes | None
    dtr: bool
    reads: tuple[FastRead, ...]
    erase_types: tuple[EraseType, ...]
    page_size: int | None = None
    #: DW10[3:0], as the factor it is: an erase's maximum time is this
    #: times its typical time (2 x (N + 1): 2 to 32).
    erase_max_multiplier: int | None = None
    #: DW11[3:0], likewise for a page or byte program.
    program_max_multiplier: int | None = None
    #: The typical times, in nanoseconds, of a page program (DW11[13:8]),
    #: the first byte programmed (DW11[18:14]), each further byte
    #: (DW11[23:19]) and a chip erase (DW11[30:24]).
    page_program_ns: int | None = None
    byte_program_first_ns: int | None = None
    byte_program_additional_ns: int | None = None
    chip_erase_ns: int | None = None
    suspend_resume: bool | None = None
    #: DW12, where the part can suspend (bit 31 clear): the most time an
    #: erase suspend (DW12[30:24]) and a program suspend (DW12[19:13]) take,
    #: and the typical least interval from an erase resume (DW12[23:20])
    #: and a program resume (DW12[12:9]) to the next suspend, in
    #: nanoseconds.
    erase_suspend_ns: int | None = None
    program_suspend_ns: int | None = None
    erase_resume_to_suspend_ns: int | None = None
    program_resume_to_suspend_ns: int | None = None
    #: DW14's deep power-down opcodes, where the part has deep power-down
    #: (bit 31 clear); an exit opcode of 0xff is "no command needed".
    enter_deep_power_down: int | None = None
    exit_deep_power_down: int | None = None
    #: DW14[14:8]: the most time from the exit to the part being ready, in
    #: nanoseconds.
    exit_deep_power_down_delay_ns: int | None = None
    quad_enable: int | None = None
    qpi_enable: tuple[str, ...] = ()
    qpi_disable: tuple[str, ...] = ()
    mode_0_4_4: bool | None = None
    sr1_write_enable: tuple[str, ...] = ()
    soft_reset: tuple[str, ...] = ()
    four_byte_enter: frozenset[FourByteMethod] = frozenset()
    four_byte_exit: frozenset[FourByteMethod] = frozenset()
    octal_reads: tuple[FastRead, ...] = ()
    command_extension: str | None = None
    byte_order_swapped: bool | None = None

    @property
    def revision(self) -> str:
        return revision_name(self.major, self.minor)

    @property
    def size(self) -> int | None:
        """The density in bytes."""
        return None if self.density_bits is None else self.density_bits // 8

    @property
    def quad_enable_description(self) -> str | None:
        if self.quad_enable is None:
            return None
        return QUAD_ENABLE.get(self.quad_enable, f"reserved code {self.quad_enable}")

    @property
    def soft_reset_66_99(self) -> bool:
        return _SOFT_RESET[12] in self.soft_reset

    @property
    def linux_four_byte_method(self) -> str | None:
        """Which 4-byte-mode method Linux picks from DW16, in its order of
        preference: the bank register, WREN then EN4B, or EN4B alone."""
        enter, leave = self.four_byte_enter, self.four_byte_exit
        if FourByteMethod.BRWR in enter and FourByteMethod.BRWR in leave:
            return "brwr"
        if FourByteMethod.WREN_EN4B in enter and FourByteMethod.WREN_EN4B in leave:
            return "wren_en4b_ex4b"
        if FourByteMethod.EN4B in enter and FourByteMethod.EN4B in leave:
            return "en4b_ex4b"
        return None


@dataclass(frozen=True, slots=True)
class FourByteInstruction:
    """One instruction the 4BAIT says the part has in 4-byte-address form."""

    bit: int
    description: str
    opcode: int
    protocol: str
    kind: OperationKind
    #: False for a read whose 3-byte form the BFPT does not list (Linux
    #: ignores such a bit; JESD216B-era parts set then-reserved bits).
    supported_by_bfpt: bool = True


@dataclass(frozen=True, slots=True)
class FourByteAddressInstructions:
    """The 4-byte Address Instruction Table (0xff84)."""

    dwords: tuple[int, ...]
    instructions: tuple[FourByteInstruction, ...]
    #: The 4-byte opcode of each BFPT erase type (index 1 to 4), or ``None``.
    erase_opcodes: tuple[int | None, int | None, int | None, int | None]

    @property
    def supported(self) -> int:
        """DW1: the raw support bits."""
        return self.dwords[0]

    @property
    def usable(self) -> bool:
        """Linux uses the table only with at least one read, one program and
        one erase in it."""
        kinds = {i.kind for i in self.instructions if i.supported_by_bfpt}
        return (
            OperationKind.READ in kinds
            and OperationKind.PROGRAM in kinds
            and any(o is not None for o in self.erase_opcodes)
        )


@dataclass(frozen=True, slots=True)
class Profile1:
    """The xSPI profile 1.0 table (0xff05): octal DTR read parameters."""

    dwords: tuple[int, ...]
    read_opcode: int
    rdsr_dummy: int
    rdsr_address_bytes: int
    #: Dummy clocks by clock rate in MHz, for the rates the table gives.
    dummy_by_mhz: dict[int, int]


@dataclass(frozen=True, slots=True)
class SfdpOperation:
    """An operation the tables describe, and the name spiflash gives it
    (``None`` when :data:`spiflash.opcodes.OPERATIONS` has no operation of
    that opcode and shape)."""

    name: str | None
    opcode: int
    protocol: str
    address_bytes: int
    dummy_clocks: int | None
    #: Where in the tables it comes from.
    via: str

    @property
    def description(self) -> str:
        if self.name is not None:
            return OPERATIONS[self.name].description
        return f"{self.protocol} operation"


@dataclass(frozen=True)
class SfdpFacts:
    """What an SFDP area says in the database's own terms: what a record's
    other fields would hold if the area were its only source
    (:meth:`Sfdp.facts`). A record carrying the area derives these at load
    (:mod:`spiflash.derive`) and does not store them again.

    ``erasers`` has an eraser per BFPT erase type, over the whole density
    (and one per 4-byte erase opcode the 4BAIT gives), and the 4 KiB erase of
    BFPT DW1 where no erase type has it. ``opcodes`` are the named
    operations the tables describe (:meth:`Sfdp.operations`), each
    ``implied``, its ``via`` saying where in the tables it is, and a read
    with the dummy clocks the tables give it; not those its ways into
    4-byte mode give (:data:`spiflash.derive.FOUR_BYTE_MODE_OPERATIONS`),
    which a record derives from :attr:`four_byte_modes`. The rest is what
    the capability rules read: the BFPT's address bytes, whether DW16 says
    the part has dedicated 4-byte opcodes, and whether there is an xSPI
    profile 1.0 table (octal DTR)."""

    size: int | None
    page_size: int | None
    address_bytes: AddressBytes | None
    erasers: tuple[Eraser, ...]
    opcodes: tuple[OpcodeUse, ...]
    #: The ways into 4-byte address mode BFPT DW16 gives
    #: (:data:`~spiflash.enums.ENTER_METHODS`: not ``OPCODES_4B``, which
    #: is :attr:`opcodes_4b`), where the part has a 4-byte mode
    #: (:attr:`Sfdp.four_byte_mode`).
    four_byte_modes: frozenset[FourByteMethod] = frozenset()
    #: Whether DW16 says the part has dedicated 4-byte opcodes (bit 29).
    opcodes_4b: bool = False
    octal_dtr: bool = False
    #: The BFPT's quad enable requirement (DW15); ``None`` where the BFPT
    #: is too short to have one, or gives the reserved code 7.
    quad_enable_requirement: QuadEnableRequirement | None = None
    #: The dies the SCCR multi-chip table describes (:attr:`Sfdp.dice`);
    #: ``None`` without one.
    dies: int | None = None
    #: The times the BFPT gives (:func:`spiflash.derive.sfdp_timings`).
    timings: Timings = field(default_factory=Timings)

    @property
    def quad_enable(self) -> RegisterBit | NoQuadEnable | None:
        """Where :attr:`quad_enable_requirement` puts the QE bit
        (:attr:`QuadEnableRequirement.bit
        <spiflash.registers.QuadEnableRequirement.bit>`)."""
        qer = self.quad_enable_requirement
        return qer.bit if qer is not None else None


@dataclass(frozen=True)
class Sfdp:
    """A decoded SFDP area: its headers and tables, and what they say.

    ``warnings`` lists what did not decode cleanly: a table past the end of
    the dump, a header revision this module does not know, a 4BAIT bit the
    BFPT contradicts. None of them stops the rest from decoding.

    A ``partial`` one is parameter tables given without the area around
    them (:func:`from_tables`: Zephyr's boards copy the BFPT alone): its
    ``data`` is empty, its revision and access protocol are ``None``, and
    its headers are made up (:attr:`ParameterHeader.synthetic`)."""

    data: bytes = field(repr=False)
    major: int | None
    minor: int | None
    access_protocol: int | None
    headers: tuple[ParameterHeader, ...]
    tables: tuple[Table, ...]
    bfpt: Bfpt | None
    four_byte: FourByteAddressInstructions | None = None
    sector_map: Table | None = None
    profile1: Profile1 | None = None
    sccr: Table | None = None
    sccr_multi_chip: Table | None = None
    warnings: tuple[str, ...] = ()
    partial: bool = False

    @property
    def revision(self) -> str:
        """``"1.6"``; ``"unknown"`` for a :attr:`partial` one."""
        return "unknown" if self.major is None else f"{self.major}.{self.minor}"

    @property
    def revision_name(self) -> str:
        return revision_name(self.major, self.minor)

    def table(self, table_id: int) -> bytes | None:
        """The bytes of the first table with id ``table_id`` (``0xff00``,
        the BFPT), as many as the area holds; ``None`` where there is none."""
        for t in self.tables:
            if t.header.id == table_id and t.dwords:
                return struct.pack(f"<{len(t.dwords)}I", *t.dwords)
        return None

    @property
    def size(self) -> int | None:
        return self.bfpt.size if self.bfpt else None

    @property
    def page_size(self) -> int | None:
        return self.bfpt.page_size if self.bfpt else None

    @property
    def address_bytes(self) -> AddressBytes | None:
        return self.bfpt.address_bytes if self.bfpt else None

    @property
    def dice(self) -> int | None:
        """How many dice the SCCR multi-chip table describes."""
        if self.sccr_multi_chip is None:
            return None
        return 1 + self.sccr_multi_chip.header.length // 2

    @cached_property
    def erase_types(self) -> tuple[EraseType, ...]:
        """The BFPT's erase types, each with its 4BAIT opcode when there is one."""
        if self.bfpt is None:
            return ()
        if self.four_byte is None:
            return self.bfpt.erase_types
        return tuple(
            EraseType(
                e.index, e.size, e.opcode, e.typical_ns, self.four_byte.erase_opcodes[e.index - 1]
            )
            for e in self.bfpt.erase_types
        )

    @cached_property
    def reads(self) -> dict[str, FastRead]:
        """Every fast read the tables give, by protocol (``"1-4-4"``)."""
        out: dict[str, FastRead] = {}
        if self.bfpt is not None:
            for r in (*self.bfpt.reads, *self.bfpt.octal_reads):
                out[r.protocol] = r
        if self.profile1 is not None and self.profile1.dummy_by_mhz:
            fastest = max(self.profile1.dummy_by_mhz)
            out["8D-8D-8D"] = FastRead(
                "8D-8D-8D", self.profile1.read_opcode, 0, self.profile1.dummy_by_mhz[fastest]
            )
        return out

    def _bfpt_address_bytes(self) -> int:
        return 4 if self.address_bytes is AddressBytes.FOUR else 3

    @property
    def four_byte_mode(self) -> bool:
        """Whether the part has a 4-byte address mode for BFPT DW16's ways in
        and out of it to describe: its BFPT allows 4-byte addresses, or its
        density is over 16 MiB. Zephyr's ``spi_nor_process_bfp()`` reads
        DW16 only for the first; a 3-byte part's DW16 is often left all
        ones (Macronix's MX25R6435F: every way in and out), which says
        nothing. :meth:`operations` and :meth:`facts` read DW16's 4-byte
        fields only where this holds; :attr:`Bfpt.four_byte_enter` reports
        what is written."""
        if self.address_bytes in (AddressBytes.THREE_OR_FOUR, AddressBytes.FOUR):
            return True
        return self.size is not None and self.size > 16 * 1024 * 1024

    def operations(self, *, implied: bool = True) -> Iterator[SfdpOperation]:
        """Every operation the tables describe, in table order.

        With ``implied`` (the default) the read 0x03 that a part with a
        BFPT supports though the table does not list it comes first, and
        ``RDSFDP`` itself. The tables give no sign of fast read 0x0b or page
        program 0x02 (Zephyr's ``jesd216_bfp_read_support()``: "SFDP does
        not provide an indication of support for 1-1-1 Fast Read (0Bh)";
        Linux's ``spi_nor_parse_4bait()``: "4BAIT is the only SFDP table
        that indicates page program support"), so they are not yielded:
        :mod:`spiflash.derive` documents the rule. Names are looked up in
        :data:`spiflash.opcodes.OPERATIONS`; an operation that has none
        (a 3-byte-address 4-4-4 read, a DTR read) is still yielded."""
        bfpt = self.bfpt
        if bfpt is None:
            return
        abytes = self._bfpt_address_bytes()
        if implied:
            yield SfdpOperation(
                _BY_SHAPE.get((OperationKind.READ, 0x03, "1-1-1")),
                0x03,
                "1-1-1",
                abytes,
                0,
                "implied: a part with a BFPT supports read 0x03",
            )
            yield SfdpOperation("RDSFDP", 0x5A, "1-1-1", 3, 8, "the table itself")
        for r in bfpt.reads:
            yield SfdpOperation(
                _BY_SHAPE.get((OperationKind.READ, r.opcode, r.protocol)),
                r.opcode,
                r.protocol,
                abytes,
                r.dummy_clocks,
                f"BFPT {r.protocol} fast read: {r.mode_clocks} mode + {r.wait_states} wait clocks",
            )
        for e in bfpt.erase_types:
            yield SfdpOperation(
                _ERASE_BY_OPCODE.get(e.opcode),
                e.opcode,
                "1-1-0",
                abytes,
                0,
                f"BFPT erase type {e.index}: {e.size} B",
            )
        if (dw1 := self._dw1_erase()) is not None:
            yield SfdpOperation(
                _ERASE_BY_OPCODE.get(dw1), dw1, "1-1-0", abytes, 0, "BFPT DW1: 4096 B erase"
            )
        for r in bfpt.octal_reads:
            yield SfdpOperation(
                _BY_SHAPE.get((OperationKind.READ, r.opcode, r.protocol)),
                r.opcode,
                r.protocol,
                abytes,
                r.dummy_clocks,
                f"BFPT DW17 {r.protocol} fast read: "
                f"{r.mode_clocks} mode + {r.wait_states} wait clocks",
            )
        mode_ops: dict[int, list[str]] = {}
        dw16 = (bfpt.four_byte_enter, bfpt.four_byte_exit) if self.four_byte_mode else ((), ())
        for methods, direction in ((dw16[0], "enter"), (dw16[1], "exit")):
            for method, opcode in (
                (FourByteMethod.EN4B, 0xB7 if direction == "enter" else 0xE9),
                (FourByteMethod.WREN_EN4B, 0xB7 if direction == "enter" else 0xE9),
                (FourByteMethod.WREAR, 0xC5),
                (FourByteMethod.BRWR, 0x17),
            ):
                if method in methods:
                    label = method.label if direction == "enter" else method.exit_label
                    mode_ops.setdefault(opcode, []).append(f"{direction} 4-byte mode: {label}")
        for opcode, reasons in mode_ops.items():
            yield SfdpOperation(
                _MODE_BY_OPCODE.get(opcode),
                opcode,
                "1-0-0" if opcode in (0xB7, 0xE9) else "1-0-1",
                0,
                0,
                "BFPT DW16 " + "; ".join(dict.fromkeys(reasons)),
            )
        if bfpt.enter_deep_power_down is not None:
            # DW14: DP, and the release where it is a command (0xff: "Don't
            # need command", Macronix's reproduced table says).
            yield SfdpOperation(
                _MODE_BY_OPCODE.get(bfpt.enter_deep_power_down),
                bfpt.enter_deep_power_down,
                "1-0-0",
                0,
                0,
                "BFPT DW14: enter deep power-down",
            )
            if bfpt.exit_deep_power_down not in (None, 0xFF):
                yield SfdpOperation(
                    _MODE_BY_OPCODE.get(bfpt.exit_deep_power_down),
                    bfpt.exit_deep_power_down,
                    "1-0-0",
                    0,
                    0,
                    "BFPT DW14: release from deep power-down",
                )
        if self.four_byte is not None:
            for i in self.four_byte.instructions:
                if not i.supported_by_bfpt:
                    continue
                clocks: int | None = 0
                if i.kind is OperationKind.READ:
                    base = self.reads.get(i.protocol)
                    clocks = {0: 0, 1: 8}.get(i.bit, base.dummy_clocks if base else None)
                yield SfdpOperation(
                    _BY_SHAPE.get((i.kind, i.opcode, i.protocol)),
                    i.opcode,
                    i.protocol,
                    4,
                    clocks,
                    f"4BAIT bit {i.bit}: {i.description}, 4-byte address",
                )
            for e in self.erase_types:
                if e.opcode_4b is not None:
                    yield SfdpOperation(
                        _ERASE_BY_OPCODE.get(e.opcode_4b),
                        e.opcode_4b,
                        "1-1-0",
                        4,
                        0,
                        f"4BAIT erase type {e.index}: {e.size} B, 4-byte address",
                    )
        if self.profile1 is not None and "8D-8D-8D" in self.reads:
            r = self.reads["8D-8D-8D"]
            yield SfdpOperation(
                _BY_SHAPE.get((OperationKind.READ, r.opcode, r.protocol)),
                r.opcode,
                r.protocol,
                4,
                r.dummy_clocks,
                "xSPI profile 1.0: octal DTR read",
            )

    def facts(self) -> SfdpFacts:
        """What the tables say, as a record's fields hold it
        (:class:`SfdpFacts`)."""
        return self._facts

    @cached_property
    def _facts(self) -> SfdpFacts:
        from .model import EraseBlock, Eraser  # noqa: PLC0415 - model imports this module

        size = self.size
        erasers: list[Eraser] = []

        def eraser(opcode: int | None, block: int) -> None:
            if opcode is None or size is None or block <= 0 or size % block:
                return
            e = Eraser(opcode, (EraseBlock(block, size // block),))
            if e not in erasers:
                erasers.append(e)

        for e in self.erase_types:
            eraser(e.opcode, e.size)
        for e in self.erase_types:
            eraser(e.opcode_4b, e.size)
        if (dw1 := self._dw1_erase()) is not None:
            eraser(dw1, 4096)
        bfpt = self.bfpt
        enter = bfpt.four_byte_enter if bfpt and self.four_byte_mode else frozenset()
        modes = enter & ENTER_METHODS
        # A record derives these from its ways into 4-byte mode.
        by_mode = {op for m in modes for op in derive.FOUR_BYTE_MODE_OPERATIONS[m]}
        uses: dict[str, OpcodeUse] = {}
        for o in self.operations():
            if o.name is None or o.name in uses:
                continue
            if o.name in by_mode and o.via.startswith("BFPT DW16"):
                continue
            read = OPERATIONS[o.name].kind is OperationKind.READ
            uses[o.name] = OpcodeUse(
                o.name, f"SFDP {o.via}", implied=True, dummy_clocks=o.dummy_clocks if read else None
            )
        # The tables are the answer to RDSFDP, whatever is in them.
        uses.setdefault("RDSFDP", OpcodeUse("RDSFDP", "SFDP: the tables themselves", implied=True))
        return SfdpFacts(
            size=size,
            page_size=self.page_size,
            address_bytes=self.address_bytes,
            erasers=tuple(erasers),
            opcodes=tuple(uses.values()),
            four_byte_modes=modes,
            opcodes_4b=FourByteMethod.OPCODES_4B in enter,
            octal_dtr=self.profile1 is not None,
            quad_enable_requirement=QuadEnableRequirement.from_code(
                bfpt.quad_enable if bfpt else None
            ),
            dies=self.dice,
            timings=derive.sfdp_timings(self),
        )

    def _dw1_erase(self) -> int | None:
        """The 4 KiB erase opcode of BFPT DW1, where DW1[1:0] says the part
        erases 4 KiB uniformly (01; 11 is no such erase, whatever DW1[15:8]
        holds) and no erase type has it."""
        bfpt = self.bfpt
        if bfpt is None or bfpt.erase_4k_opcode is None or not bfpt.uniform_4k:
            return None
        if any(e.opcode == bfpt.erase_4k_opcode and e.size == 4096 for e in bfpt.erase_types):
            return None
        return bfpt.erase_4k_opcode

    def features(self) -> frozenset[Feature]:
        """The :class:`~spiflash.enums.Feature` values the tables imply: what
        :func:`spiflash.derive.features` gives a record whose only source is
        these tables (:meth:`facts`), by the same rules."""
        from . import derive  # noqa: PLC0415 - derive imports this module

        return derive.sfdp_features(self)

    def describe(self, *, verbose: bool = False) -> str:
        """The tables as text, one fact per line; with ``verbose``, every
        table's raw dwords too."""
        plural = "s" if len(self.headers) != 1 else ""
        if self.partial:
            names = ", ".join(t.header.name for t in self.tables)
            head = f"SFDP parameter table{plural} without the SFDP header: {names}"
        else:
            head = (
                f"SFDP {self.revision} ({self.revision_name}), {len(self.headers)} parameter "
                f"header{plural}, access protocol 0x{self.access_protocol or 0:02x}"
            )
        lines = [head]
        for t in self.tables:
            h = t.header
            note = " (truncated)" if t.truncated else ""
            if h.synthetic:
                lines.append(f"    {h.name}, {h.length} dwords{note}")
            else:
                lines.append(
                    f"    {h.name} {h.revision}, {h.length} dwords at 0x{h.pointer:x}{note}"
                )
        bfpt = self.bfpt
        if bfpt is None:
            lines.append("no BFPT")
        else:
            lines.append(f"BFPT {bfpt.revision}, {bfpt.length} dwords")
            geometry = [f"size {human_size(bfpt.size)}"]
            if bfpt.page_size is not None:
                geometry.append(f"page {human_size(bfpt.page_size)}")
            if bfpt.address_bytes is not None:
                geometry.append(f"address bytes {bfpt.address_bytes}")
            if bfpt.dtr:
                geometry.append("DTR")
            lines.append("    " + ", ".join(geometry))
            times = self.facts().timings

            def typ_max(event: TimedEvent, opcode: int | None = None) -> str:
                typ = times.get(event, Bound.TYPICAL, opcode)
                most = times.get(event, Bound.MAXIMUM, opcode)
                text = f"{human_duration(typ)} typ" if typ is not None else ""
                return text + (f", {human_duration(most)} max" if most is not None else "")

            if self.erase_types:
                erases = []
                for e in self.erase_types:
                    s = f"0x{e.opcode:02x} {human_size(e.size)}"
                    if e.opcode_4b is not None:
                        s += f" (4-byte 0x{e.opcode_4b:02x})"
                    if e.typical_ns is not None:
                        s += f" {typ_max(TimedEvent.BLOCK_ERASE, e.opcode)}"
                    erases.append(s)
                lines.append("    erase: " + "; ".join(erases))
            multipliers = []
            if bfpt.erase_max_multiplier is not None:
                multipliers.append(f"erase x{bfpt.erase_max_multiplier} (DW10)")
            if bfpt.program_max_multiplier is not None:
                multipliers.append(f"program x{bfpt.program_max_multiplier} (DW11)")
            if multipliers:
                lines.append("    typical to maximum time: " + ", ".join(multipliers))
            if bfpt.chip_erase_ns is not None:
                lines.append(
                    f"    chip erase {typ_max(TimedEvent.CHIP_ERASE)}"
                    f" ({derive.CHIP_ERASE_MULTIPLIER} multiplier)"
                )
            if bfpt.page_program_ns is not None:
                lines.append(f"    page program {typ_max(TimedEvent.PAGE_PROGRAM)}")
            if bfpt.byte_program_first_ns is not None:
                lines.append(
                    f"    byte program: first {typ_max(TimedEvent.BYTE_PROGRAM_FIRST)}; "
                    f"each further {typ_max(TimedEvent.BYTE_PROGRAM_ADDITIONAL)}"
                )
            if self.reads:
                lines.append(
                    "    fast reads: "
                    + ", ".join(
                        f"{r.protocol} 0x{r.opcode:02x} ({r.dummy_clocks} dummy)"
                        for r in self.reads.values()
                    )
                )
            if bfpt.quad_enable is not None:
                lines.append(f"    quad enable: {bfpt.quad_enable_description}")
            if bfpt.qpi_enable:
                lines.append(f"    enter 4-4-4: {'; '.join(bfpt.qpi_enable)}")
            unused = "" if self.four_byte_mode else " (not read: the part has no 4-byte mode)"
            if bfpt.four_byte_enter:
                lines.append(
                    "    enter 4-byte mode: "
                    + ", ".join(m.label for m in sorted(bfpt.four_byte_enter, key=_ORDER.index))
                    + unused
                )
            if bfpt.four_byte_exit:
                lines.append(
                    "    exit 4-byte mode: "
                    + ", ".join(m.exit_label for m in sorted(bfpt.four_byte_exit, key=_ORDER.index))
                    + unused
                )
            if bfpt.soft_reset:
                lines.append(f"    soft reset: {'; '.join(bfpt.soft_reset)}")
            if bfpt.suspend_resume:
                text = "    program/erase suspend and resume"
                if bfpt.erase_suspend_ns is not None and bfpt.program_suspend_ns is not None:
                    text += (
                        f": suspended within {human_duration(bfpt.erase_suspend_ns)} (erase), "
                        f"{human_duration(bfpt.program_suspend_ns)} (program)"
                    )
                if bfpt.erase_resume_to_suspend_ns and bfpt.program_resume_to_suspend_ns:
                    text += (
                        "; resume to suspend typically "
                        f"{human_duration(bfpt.erase_resume_to_suspend_ns)} (erase), "
                        f"{human_duration(bfpt.program_resume_to_suspend_ns)} (program)"
                    )
                lines.append(text)
            if bfpt.enter_deep_power_down is not None:
                text = f"    deep power-down 0x{bfpt.enter_deep_power_down:02x}, exit "
                text += (
                    "without a command"
                    if bfpt.exit_deep_power_down == 0xFF
                    else f"0x{bfpt.exit_deep_power_down:02x}"
                )
                if bfpt.exit_deep_power_down_delay_ns is not None:
                    text += f", ready within {human_duration(bfpt.exit_deep_power_down_delay_ns)}"
                lines.append(text)
            if bfpt.command_extension is not None:
                lines.append(f"    octal DTR command extension: {bfpt.command_extension}")
        if self.dice is not None:
            lines.append(f"{self.dice} dice")
        ops = list(self.operations())
        if ops:
            lines.append("operations:")
            width = max(len(o.name or "?") for o in ops)
            for o in ops:
                dummy = f"{o.dummy_clocks} dummy" if o.dummy_clocks else ""
                lines.append(
                    f"    0x{o.opcode:02x}  {o.name or '?':<{width}}  {o.description}"
                    f"  [{o.via}{'; ' + dummy if dummy else ''}]"
                )
        if self.features():
            lines.append("features: " + " ".join(sorted(self.features())))
        lines.extend(f"warning: {w}" for w in self.warnings)
        if verbose:
            for t in self.tables:
                lines.append(f"{t.header.name} dwords:")
                lines.extend(f"    DW{i + 1}: 0x{d:08x}" for i, d in enumerate(t.dwords))
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        bfpt = self.bfpt
        return {
            "partial": self.partial,
            "revision": None if self.partial else self.revision,
            "revision_name": self.revision_name,
            "access_protocol": self.access_protocol,
            "tables": [
                {
                    "id": h.id,
                    "name": h.name,
                    "revision": None if h.synthetic else h.revision,
                    "length": h.length,
                    "pointer": None if h.synthetic else h.pointer,
                    "dwords": list(t.dwords),
                }
                for h, t in zip(self.headers, self.tables, strict=True)
            ],
            "size": self.size,
            "page_size": self.page_size,
            "address_bytes": self.address_bytes,
            "dtr": bfpt.dtr if bfpt else None,
            "erase_types": [
                {
                    "size": e.size,
                    "opcode": e.opcode,
                    "opcode_4b": e.opcode_4b,
                    "typical_ns": e.typical_ns,
                }
                for e in self.erase_types
            ],
            "erase_max_multiplier": bfpt.erase_max_multiplier if bfpt else None,
            "program_max_multiplier": bfpt.program_max_multiplier if bfpt else None,
            "page_program_ns": bfpt.page_program_ns if bfpt else None,
            "byte_program_first_ns": bfpt.byte_program_first_ns if bfpt else None,
            "byte_program_additional_ns": bfpt.byte_program_additional_ns if bfpt else None,
            "chip_erase_ns": bfpt.chip_erase_ns if bfpt else None,
            "erase_suspend_ns": bfpt.erase_suspend_ns if bfpt else None,
            "program_suspend_ns": bfpt.program_suspend_ns if bfpt else None,
            "erase_resume_to_suspend_ns": bfpt.erase_resume_to_suspend_ns if bfpt else None,
            "program_resume_to_suspend_ns": bfpt.program_resume_to_suspend_ns if bfpt else None,
            "exit_deep_power_down_delay_ns": bfpt.exit_deep_power_down_delay_ns if bfpt else None,
            "timings": self.facts().timings.to_json(),
            "reads": [
                {
                    "protocol": r.protocol,
                    "opcode": r.opcode,
                    "mode_clocks": r.mode_clocks,
                    "wait_states": r.wait_states,
                }
                for r in self.reads.values()
            ],
            "quad_enable": bfpt.quad_enable if bfpt else None,
            "quad_enable_description": bfpt.quad_enable_description if bfpt else None,
            "four_byte_enter": sorted(bfpt.four_byte_enter) if bfpt else [],
            "four_byte_exit": sorted(bfpt.four_byte_exit) if bfpt else [],
            "soft_reset": list(bfpt.soft_reset) if bfpt else [],
            "dice": self.dice,
            "operations": [
                {
                    "op": o.name,
                    "opcode": o.opcode,
                    "protocol": o.protocol,
                    "address_bytes": o.address_bytes,
                    "dummy_clocks": o.dummy_clocks,
                    "via": o.via,
                }
                for o in self.operations()
            ],
            "features": sorted(self.features()),
            "warnings": list(self.warnings),
        }


def _read_settings(dword: int, shift: int, protocol: str) -> FastRead:
    half = _bits(dword, shift + 15, shift)
    return FastRead(protocol, _bits(half, 15, 8), _bits(half, 7, 5), _bits(half, 4, 0))


def _time(
    dword: int, count_hi: int, count_lo: int, unit_hi: int, unit_lo: int, units: tuple[int, ...]
) -> int:
    """A BFPT time: (count + 1) x its unit, in nanoseconds. A field of one
    unit has no unit bits (``units`` holds one, and the unit bits read 0)."""
    unit = units[_bits(dword, unit_hi, unit_lo)] if len(units) > 1 else units[0]
    return (_bits(dword, count_hi, count_lo) + 1) * unit


def _flags(dword: int, names: dict[int, str]) -> tuple[str, ...]:
    return tuple(name for bit, name in names.items() if _bit(dword, bit))


def _bfpt(table: Table, warnings: list[str]) -> Bfpt:
    dw = table.dwords
    n = min(table.header.length, len(dw))
    if n < 9:
        warnings.append(f"BFPT has {n} dwords, JESD216 needs 9")
    if table.truncated:
        warnings.append(f"BFPT truncated: {len(dw)} of {table.header.length} dwords in the dump")

    def d(i: int) -> int | None:
        return dw[i - 1] if i <= n else None

    dw1 = d(1) or 0
    erase_4k = _bits(dw1, 15, 8)
    codes = {0: AddressBytes.THREE, 1: AddressBytes.THREE_OR_FOUR, 2: AddressBytes.FOUR}
    address = codes.get(_bits(dw1, 18, 17))
    if address is None and n >= 1:
        warnings.append("BFPT DW1 address bytes code 3 is reserved")

    density = None
    if (dw2 := d(2)) is not None:
        if _bit(dw2, 31):
            exponent = _bits(dw2, 30, 0)
            if exponent > 63:
                warnings.append(f"BFPT density 2^{exponent} bits is not credible")
            else:
                density = 1 << exponent
        else:
            density = dw2 + 1
            if density % 8:
                warnings.append(f"BFPT density {density} bits is not a whole number of bytes")

    reads = []
    if n >= 7:
        reads = [
            _read_settings(dw[dword - 1], shift, proto)
            for proto, dword, shift, supported in _READS
            if supported(dw)
        ]

    erase_types = []
    for i, (dword, shift) in enumerate(((8, 0), (8, 16), (9, 0), (9, 16)), start=1):
        if (value := d(dword)) is None:
            break
        exponent, opcode = _bits(value, shift + 7, shift), _bits(value, shift + 15, shift + 8)
        if exponent:
            erase_types.append(EraseType(i, 1 << exponent, opcode))
    extra: dict[str, Any] = {}
    if (dw10 := d(10)) is not None:
        # (count high, count low, unit high, unit low) of each erase type.
        fields = ((8, 4, 10, 9), (15, 11, 17, 16), (22, 18, 24, 23), (29, 25, 31, 30))
        erase_types = [
            EraseType(
                e.index,
                e.size,
                e.opcode,
                _time(dw10, *fields[e.index - 1], derive.ERASE_UNITS_NS),
            )
            for e in erase_types
        ]
        extra["erase_max_multiplier"] = 2 * (_bits(dw10, 3, 0) + 1)
    if (dw11 := d(11)) is not None:
        extra["page_size"] = 1 << _bits(dw11, 7, 4)
        extra["program_max_multiplier"] = 2 * (_bits(dw11, 3, 0) + 1)
        extra["page_program_ns"] = _time(dw11, 12, 8, 13, 13, derive.PAGE_PROGRAM_UNITS_NS)
        extra["byte_program_first_ns"] = _time(dw11, 17, 14, 18, 18, derive.BYTE_PROGRAM_UNITS_NS)
        extra["byte_program_additional_ns"] = _time(
            dw11, 22, 19, 23, 23, derive.BYTE_PROGRAM_UNITS_NS
        )
        extra["chip_erase_ns"] = _time(dw11, 28, 24, 30, 29, derive.CHIP_ERASE_UNITS_NS)
    if (dw12 := d(12)) is not None:
        extra["suspend_resume"] = not _bit(dw12, 31)
        if extra["suspend_resume"]:
            # JESD216B, as Macronix's MX25U25645G datasheet (Rev. 1.4,
            # pp. 102-103) reproduces the table.
            latency = derive.LATENCY_UNITS_NS
            extra["program_resume_to_suspend_ns"] = _time(
                dw12, 12, 9, 0, 0, derive.RESUME_UNITS_NS
            )
            extra["program_suspend_ns"] = _time(dw12, 17, 13, 19, 18, latency)
            extra["erase_resume_to_suspend_ns"] = _time(dw12, 23, 20, 0, 0, derive.RESUME_UNITS_NS)
            extra["erase_suspend_ns"] = _time(dw12, 28, 24, 30, 29, latency)
    if (dw14 := d(14)) is not None and not _bit(dw14, 31):
        extra["exit_deep_power_down_delay_ns"] = _time(dw14, 12, 8, 14, 13, derive.LATENCY_UNITS_NS)
        extra["enter_deep_power_down"] = _bits(dw14, 30, 23)
        extra["exit_deep_power_down"] = _bits(dw14, 22, 15)
    if (dw15 := d(15)) is not None:
        extra["quad_enable"] = _bits(dw15, 22, 20)
        extra["qpi_enable"] = _flags(dw15, _QPI_ENABLE)
        extra["qpi_disable"] = _flags(dw15, _QPI_DISABLE)
        extra["mode_0_4_4"] = _bit(dw15, 9)
    if (dw16 := d(16)) is not None:
        extra["sr1_write_enable"] = _flags(dw16, _SR1_WRITE_ENABLE)
        extra["soft_reset"] = _flags(dw16, _SOFT_RESET)
        extra["four_byte_enter"] = frozenset(m for b, m in _ENTER_4B.items() if _bit(dw16, b))
        extra["four_byte_exit"] = frozenset(m for b, m in _EXIT_4B.items() if _bit(dw16, b))
    if (dw17 := d(17)) is not None:
        octal = []
        for shift, proto in ((16, "1-1-8"), (0, "1-8-8")):
            r = _read_settings(dw17, shift, proto)
            if r.opcode not in (0x00, 0xFF):
                octal.append(r)
        extra["octal_reads"] = tuple(octal)
    if (dw18 := d(18)) is not None:
        extra["command_extension"] = ("repeat", "invert", "reserved", "16-bit opcode")[
            _bits(dw18, 30, 29)
        ]
        extra["byte_order_swapped"] = _bit(dw18, 31)

    return Bfpt(
        dwords=dw,
        major=table.header.major,
        minor=table.header.minor,
        length=table.header.length,
        density_bits=density,
        erase_4k_opcode=None if erase_4k == 0xFF or n < 1 else erase_4k,
        uniform_4k=_bits(dw1, 1, 0) == 1,
        write_granularity_64=_bit(dw1, 2),
        address_bytes=address,
        dtr=_bit(dw1, 19),
        reads=tuple(reads),
        erase_types=tuple(erase_types),
        **extra,
    )


def _four_byte(table: Table, bfpt: Bfpt | None, warnings: list[str]) -> FourByteAddressInstructions:
    dw = table.dwords
    dw1 = dw[0] if dw else 0
    known = {r.protocol for r in (*bfpt.reads, *bfpt.octal_reads)} if bfpt else set()
    dtr = bfpt.dtr if bfpt else False
    instructions = []
    ignored = []
    for bit, (desc, opcode, proto, kind) in _FOUR_BYTE_INSTRUCTIONS.items():
        if not _bit(dw1, bit):
            continue
        protocol, needs_dtr = _FOUR_BYTE_GATE.get(bit, (None, False))
        ok = (protocol is None or protocol in known) and (dtr or not needs_dtr)
        if not ok:
            ignored.append(f"{desc} (bit {bit})")
        instructions.append(FourByteInstruction(bit, desc, opcode, proto, kind, ok))
    if ignored:
        warnings.append(
            "4BAIT claims instructions the BFPT gives no read mode for, ignored: "
            + ", ".join(ignored)
        )
    erases: list[int | None] = [None] * 4
    if len(dw) >= 2:
        for i in range(4):
            if _bit(dw1, 9 + i):
                opcode = _bits(dw[1], 8 * i + 7, 8 * i)
                erases[i] = None if opcode == 0xFF else opcode
    return FourByteAddressInstructions(
        dw, tuple(instructions), (erases[0], erases[1], erases[2], erases[3])
    )


def _profile1(table: Table) -> Profile1 | None:
    dw = table.dwords
    if len(dw) < 5:
        return None
    dummies = {
        200: _bits(dw[3], 11, 7),
        166: _bits(dw[4], 31, 27),
        133: _bits(dw[4], 21, 17),
        100: _bits(dw[4], 11, 7),
    }
    return Profile1(
        dwords=dw,
        read_opcode=_bits(dw[0], 15, 8),
        rdsr_dummy=8 if _bit(dw[0], 28) else 4,
        rdsr_address_bytes=4 if _bit(dw[0], 29) else 0,
        dummy_by_mhz={mhz: n for mhz, n in dummies.items() if n},
    )


def parse(data: bytes) -> Sfdp:
    """Decode an SFDP area. ``ValueError`` when it does not start with the
    ``SFDP`` signature; anything else that is off becomes a warning."""
    if len(data) < 8 or data[:4] != SIGNATURE:
        msg = "not an SFDP dump: no 'SFDP' signature"
        raise ValueError(msg)
    _sig, minor, major, nph, access = struct.unpack_from("<4sBBBB", data, 0)
    warnings: list[str] = []
    if major != 1:
        warnings.append(f"SFDP major revision {major} is not 1")

    headers: list[ParameterHeader] = []
    for i in range(nph + 1):
        offset = 8 + 8 * i
        if offset + 8 > len(data):
            warnings.append(f"parameter header {i} is past the end of the dump")
            break
        lsb, hminor, hmajor, length, p0, p1, p2, msb = struct.unpack_from("<8B", data, offset)
        headers.append(
            ParameterHeader(i, msb << 8 | lsb, hmajor, hminor, length, p0 | p1 << 8 | p2 << 16)
        )

    tables: list[Table] = []
    for h in headers:
        if h.pointer % 4:
            warnings.append(f"{h.name}: table pointer 0x{h.pointer:x} is not dword aligned")
        if h.pointer >= len(data):
            warnings.append(f"{h.name}: table at 0x{h.pointer:x} is past the end of the dump")
            tables.append(Table(h, ()))
            continue
        k = min(h.length, (len(data) - h.pointer) // 4)
        dwords = struct.unpack_from(f"<{k}I", data, h.pointer)
        if k < h.length:
            warnings.append(f"{h.name}: {k} of {h.length} dwords are in the dump")
        tables.append(Table(h, tuple(dwords)))

    if headers and headers[0].id != BFPT_ID:
        warnings.append("the first parameter header is not the BFPT")
    return _decode(bytes(data), major, minor, access, tables, warnings)


def _decode(
    data: bytes,
    major: int | None,
    minor: int | None,
    access: int | None,
    tables: list[Table],
    warnings: list[str],
) -> Sfdp:
    """An :class:`Sfdp` of ``tables``, decoded."""
    # The BFPT: the highest minor revision, then the longest, as Linux picks
    # it. A table given without its header (major None) counts.
    bfpts = [
        t for t in tables if t.header.id == BFPT_ID and t.header.major in (1, None) and t.dwords
    ]
    bfpt = None
    if bfpts:
        best = max(bfpts, key=lambda t: (t.header.minor or 0, t.header.length))
        bfpt = _bfpt(best, warnings)
    else:
        warnings.append("no BFPT")

    def first(table_id: int) -> Table | None:
        return next((t for t in tables if t.header.id == table_id and t.dwords), None)

    four_byte = None
    if (t := first(FOUR_BYTE_ID)) is not None:
        four_byte = _four_byte(t, bfpt, warnings)
    profile1 = None
    if (t := first(PROFILE1_ID)) is not None:
        profile1 = _profile1(t)
    return Sfdp(
        data=data,
        major=major,
        minor=minor,
        access_protocol=access,
        headers=tuple(t.header for t in tables),
        tables=tuple(tables),
        bfpt=bfpt,
        four_byte=four_byte,
        sector_map=first(SECTOR_MAP_ID),
        profile1=profile1,
        sccr=first(SCCR_ID),
        sccr_multi_chip=first(SCCR_MC_ID),
        warnings=tuple(warnings),
        partial=major is None,
    )


def from_tables(tables: Mapping[int, bytes]) -> Sfdp:
    """Decode parameter tables given without the SFDP area around them,
    by table id (``{0xff00: bfpt, 0xff84: four_byte}``), as Zephyr's boards
    copy them (``sfdp-bfp``, ``sfdp-ff84``, ...): a :attr:`~Sfdp.partial`
    :class:`Sfdp`. Each table is as long as its bytes (whole dwords,
    little-endian); the BFPT comes first, then the others by id. Its
    revision is not known, so a BFPT field is read wherever the table is
    long enough to hold it."""
    warnings: list[str] = []
    out: list[Table] = []
    order = sorted(tables, key=lambda i: (i != BFPT_ID, i))
    for i, table_id in enumerate(order):
        raw = tables[table_id]
        if len(raw) % 4:
            warnings.append(f"table 0x{table_id:04x}: {len(raw)} bytes is not whole dwords")
        n = len(raw) // 4
        header = ParameterHeader(i, table_id, None, None, n, 0, synthetic=True)
        out.append(Table(header, struct.unpack_from(f"<{n}I", raw)))
    return _decode(b"", None, None, None, out, warnings)
