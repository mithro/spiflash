"""SFDP tables to and from the database, and SFDP tables compared.

- :func:`encode` writes the SFDP area (JESD216) a database entry or chip
  describes: the header, a Basic Flash Parameter Table, and a 4-byte
  Address Instruction Table where the part has 4-byte operations. It never
  writes as fact a value the database does not hold: what it cannot fill it
  leaves out, lowering the revision, and lists (:attr:`EncodedSfdp.missing`),
  and every value it writes without the database saying so is listed too
  (:attr:`EncodedSfdp.assumed`).
- :func:`to_entry` reads SFDP tables as one more source: a record in the
  data's own shape, holding what the tables say as stored values.
- :func:`diff` compares two SFDP areas: their tables, each dword, and each
  decoded field.

So a part's tables can be checked against what the sources say of it, and
two parts' tables against each other::

    spiflash sfdp-encode W25Q512JV          # the tables the database describes
    spiflash sfdp W25Q512JV --entry         # the shipped dump, as an entry
    spiflash sfdp-diff ef4020 encoded:ef4020

They live here rather than in :mod:`spiflash.sfdp`, the decoder, because
they read the database's types (:class:`~spiflash.model.Record`,
:class:`~spiflash.model.Flash`), which the decoder's users need not load.
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import derive
from .enums import AddressBytes, Bound, FlashType, FourByteMethod, TimedEvent
from .model import Eraser, Flash, Record
from .opcodes import OPERATIONS
from .registers import QE_NONE, QuadEnableRequirement, Register
from .timings import Timings
from .units import human_duration
from .sfdp import (
    BFPT_ID,
    FOUR_BYTE_ID,
    QUAD_ENABLE,
    SIGNATURE,
    Sfdp,
    Table,
    parse,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .registers import NoQuadEnable, RegisterBit

#: The revisions :func:`encode` writes: JESD216 (1.0, nine dwords), and
#: JESD216A and B (1.5 and 1.6, sixteen).
REVISIONS = ((1, 0), (1, 5), (1, 6))

# The BFPT's reads: operation, protocol, DW1 support bit (None: DW5),
# (dword, shift) of its settings, DW5 support bit.
_BFPT_READS = (
    ("READ_1_1_2", "1-1-2", 16, (4, 0), None),
    ("READ_1_2_2", "1-2-2", 20, (4, 16), None),
    ("READ_1_4_4", "1-4-4", 21, (3, 0), None),
    ("READ_1_1_4", "1-1-4", 22, (3, 16), None),
    ("READ_4_4_4", "4-4-4", None, (7, 16), 4),
)

# 4BAIT DW1: bit -> the operation it says the part has.
_FOUR_BYTE_BITS = {
    0: "READ_1_1_1_4B",
    1: "READ_1_1_1_FAST_4B",
    2: "READ_1_1_2_4B",
    3: "READ_1_2_2_4B",
    4: "READ_1_1_4_4B",
    5: "READ_1_4_4_4B",
    6: "PP_1_1_1_4B",
    7: "PP_1_1_4_4B",
    8: "PP_1_4_4_4B",
    20: "READ_1_1_8_4B",
    21: "READ_1_8_8_4B",
}
# A 4-byte read's 3-byte form, which the BFPT must list for the bit to count.
_THREE_BYTE_READ = {
    "READ_1_1_2_4B": "READ_1_1_2",
    "READ_1_2_2_4B": "READ_1_2_2",
    "READ_1_1_4_4B": "READ_1_1_4",
    "READ_1_4_4_4B": "READ_1_4_4",
    "READ_1_1_8_4B": "READ_1_1_8",
    "READ_1_8_8_4B": "READ_1_8_8",
}
# A BFPT erase opcode's 4-byte-address form.
_FOUR_BYTE_ERASE = {0x20: "BE_4K_4B", 0x52: "BE_32K_4B", 0xD8: "SE_4B"}

#: BFPT DW16's bit for each way into 4-byte mode (bit 29, dedicated 4-byte
#: opcodes, is written from the operations); flashrom's ``ear_bit7`` has
#: none.
_ENTER_BITS = {
    24: FourByteMethod.EN4B,
    25: FourByteMethod.WREN_EN4B,
    26: FourByteMethod.WREAR,
    27: FourByteMethod.BRWR,
    28: FourByteMethod.NV_CR,
    30: FourByteMethod.ALWAYS_4B,
}

#: The ways in that are a register, which is also cleared to leave 4-byte
#: mode: their way-out bit is 10 below the way-in bit (DW16[18:16]).
_REGISTERS = frozenset({FourByteMethod.WREAR, FourByteMethod.BRWR, FourByteMethod.NV_CR})

_ADDRESS_CODE = {AddressBytes.THREE: 0, AddressBytes.THREE_OR_FOUR: 1, AddressBytes.FOUR: 2}

#: What :func:`encode` cannot write, so what a round trip through
#: :func:`to_entry` and :func:`encode` loses, by :func:`diff` field: the
#: split of each read's dummy clocks into mode and wait clocks (the database
#: keeps their total), and everything the database holds nothing of yet.
ENCODE_LOSSES = {
    "reads": "the mode/wait split of each read's dummy clocks (written as 0 mode + N wait)",
    "revision": "the revision: lowered to the highest one encode can fill",
    "access_protocol": "the access protocol (encode writes 0xff, legacy)",
    "address_bytes": (
        "the address bytes, as the database derives them: 3 or 4 for a part over "
        "16 MiB, whatever its BFPT says (QEMU's IS25WP256 says 3)"
    ),
    "dtr": "DTR reads, which have no operation here",
    "erase_types": "erase times (DW10), where the maxima are not one multiplier of them",
    "erase_multiplier": "the erase multiplier (DW10), where the erase times are not written",
    "page_size": "the page size, in a revision encode cannot fill",
    "program_multiplier": "the program multiplier (DW11), where the program times are not written",
    "page_program_ns": "program times (DW11), where the maxima are not one multiplier of them",
    "byte_program": "byte program times (DW11), likewise",
    "chip_erase_ns": "chip erase time (DW11), likewise",
    "suspend_resume": "suspend and resume (DW12-13): no suspend operation is modelled",
    "suspend": "the suspend latencies and intervals (DW12), with suspend and resume",
    "deep_power_down": "deep power-down (DW14), where it is not released with 0xab",
    "dpd_exit_delay": "the deep power-down exit delay (DW14), likewise",
    "quad_enable": "the quad enable requirement (DW15), where it is the reserved 7",
    "qpi_enable": "the QPI enable sequence, in a revision encode cannot fill",
    "qpi_disable": "the QPI disable sequence, in a revision encode cannot fill",
    "mode_0_4_4": "0-4-4 (continuous read) mode (DW15), written as not supported",
    "four_byte_enter": "4-byte mode entry (DW16), in a revision encode cannot fill",
    "four_byte_exit": "4-byte mode exit (DW16), in a revision encode cannot fill",
    "soft_reset": "soft reset (DW16)",
    "profile1": "the xSPI profile 1.0 table",
    "dice": "the multi-chip SCCR map",
    "octal_reads": "the octal reads of DW17 (JESD216C)",
}


@dataclass(frozen=True)
class EncodedSfdp:
    """What :func:`encode` wrote: the SFDP area, its revision, and what it
    wrote without the database saying so (``assumed``) or could not write
    (``missing``), one line each."""

    data: bytes
    revision: tuple[int, int]
    assumed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def sfdp(self) -> Sfdp:
        """The area, decoded."""
        return parse(self.data)

    def to_json(self) -> dict[str, Any]:
        return {
            "revision": f"{self.revision[0]}.{self.revision[1]}",
            "data": self.data.hex(),
            "assumed": list(self.assumed),
            "missing": list(self.missing),
        }


@dataclass
class _Part:
    """What :func:`encode` reads of an entry or a chip."""

    name: str
    size: int | None
    page_size: int | None
    #: Uniform block erasers with a 3-byte erase opcode, smallest block first.
    erasers: list[tuple[int, int]]
    #: The operations the part has (not driver defaults), with the dummy
    #: clocks a source gives it (``None``: none does).
    ops: dict[str, int | None]
    address_bytes: AddressBytes | None
    quad_enable_requirement: QuadEnableRequirement | None = None
    quad_enable: RegisterBit | NoQuadEnable | None = None
    four_byte_modes: frozenset[FourByteMethod] = frozenset()
    #: Its times: a record's own (stated, or from its SFDP tables), or each
    #: (key, bound) as a chip's sources agree on it.
    timings: Timings = field(default_factory=Timings)

    def time(self, event: TimedEvent, bound: Bound, opcode: int | None = None) -> int | None:
        return self.timings.get(event, bound, opcode)


def _uniform(erasers: Iterable[Eraser], size: int | None) -> list[tuple[int, int]]:
    """(opcode, block) of each uniform block eraser of a 3-byte erase
    opcode over the whole of a ``size``-byte part."""
    out = []
    for e in erasers:
        if e.opcode is None or e.assumed or len(e.blocks) != 1:
            continue
        name = derive.ERASE_BY_OPCODE.get(e.opcode)
        if name is None or name.endswith("_4B") or e.opcode in derive.WHOLE_CHIP_ERASES:
            continue
        (block,) = e.blocks
        if size is None or block.size * block.count == size:
            out.append((e.opcode, block.size))
    return out


def _from_record(r: Record) -> _Part:
    ops: dict[str, int | None] = {}
    for u in r.opcodes:
        if not u.assumed and ops.get(u.op) is None:
            ops[u.op] = u.dummy_clocks
    erasers = sorted(set(_uniform(r.erasers, r.size)), key=lambda e: (e[1], e[0]))
    return _Part(
        r.name,
        r.size,
        r.page_size,
        erasers,
        ops,
        r.address_bytes,
        r.quad_enable_requirement,
        r.quad_enable,
        r.four_byte_modes,
        r.timings,
    )


def _from_flash(f: Flash) -> _Part:
    """A chip as its sources agree on it: its size and page size, the
    operations some source gives it (not as a default), each eraser as the
    most sources give it, its address bytes (:attr:`Flash.address_bytes
    <spiflash.model.Flash.address_bytes>`), and every way into 4-byte mode
    a source gives."""
    records = sorted(f.records, key=lambda r: r.source.priority)
    ops: dict[str, int | None] = {}
    for name, op in f.opcodes.items():
        if any(not c.assumed for c in op.because):
            clocks = [u.dummy_clocks for r in records for u in r.opcodes if u.op == name]
            ops[name] = next((c for c in clocks if c is not None), None)
    votes: dict[int, Counter[int]] = {}
    for r in records:
        for opcode, block in set(_uniform(r.erasers, f.size)):
            votes.setdefault(opcode, Counter())[block] += 1
    erasers = sorted(
        ((opcode, c.most_common(1)[0][0]) for opcode, c in votes.items()),
        key=lambda e: (e[1], e[0]),
    )
    return _Part(
        f.name,
        f.size,
        f.page_size,
        erasers,
        ops,
        f.address_bytes,
        f.quad_enable_requirement,
        f.quad_enable,
        f.four_byte_modes,
        Timings(
            {
                (key, bound): ns
                for key, bound in f.timings
                if (ns := f.timing(key.event, bound, key.opcode)) is not None
            }
        ),
    )


def _settings(opcode: int, mode: int, wait: int) -> int:
    """A read's 16-bit BFPT settings: wait states, mode clocks, opcode."""
    return opcode << 8 | (mode & 7) << 5 | (wait & 0x1F)


def _header(table_id: int, major: int, minor: int, length: int, pointer: int) -> bytes:
    return struct.pack(
        "<8B",
        table_id & 0xFF,
        minor,
        major,
        length,
        pointer & 0xFF,
        pointer >> 8 & 0xFF,
        pointer >> 16 & 0xFF,
        table_id >> 8,
    )


def encode(
    source: Flash | Record, *, revision: tuple[int, int] = (1, 6), assume: bool = False
) -> EncodedSfdp:
    """The SFDP area the database describes for a chip (its sources'
    consensus) or one entry: an SFDP header, a BFPT, and a 4BAIT where the
    part has 4-byte operations.

    The BFPT's first nine dwords (JESD216) are what the database holds: the
    density, the address bytes, the 4 KiB erase, up to four erase types
    (uniform block erasers, smallest first), and the 1-1-2, 1-2-2, 1-1-4,
    1-4-4 and 4-4-4 reads with their dummy clocks. A read the database has
    no dummy clocks for is left out, and so listed in ``missing``. An
    operation the database does not list is written as not supported, as
    the format has no other way to say "not known". What the format needs
    and the database cannot say (the write granularity of a part without a
    page size, DW1's status-register bits, how a read's dummy clocks split
    into mode and wait clocks, a read's usual dummy clocks where no source
    gives the part's) is written with a documented value and listed in
    ``assumed``.

    ``revision`` 1.5 and 1.6 add DW10 to DW16: erase and program times,
    suspend and resume, deep power-down, the quad enable requirement and
    4-byte address mode. The times are written where the database holds
    exactly what a dword says (:func:`_dw10`, :func:`_dw11_times`,
    :func:`_dw14`): each typical time one the BFPT can write, in the finest
    unit that holds it, and the maxima one multiplier of them; a time is
    never rounded, nor a maximum made up. Suspend and resume are not
    modelled, and the rest is what the database holds of the quad enable
    requirement and the ways into 4-byte mode.
    Without ``assume``, ``encode`` then lowers the revision to 1.0, the
    highest it can fill, and lists what it left out in ``missing``; with
    ``assume``, it writes JESD216's "not supported" encodings or the
    shortest times, and the ways out of 4-byte mode and the QPI sequences
    the operations suggest, each listed in ``assumed``. ``ValueError`` for
    a SPI NAND part, a part of no known size, or a revision it cannot write
    (:data:`REVISIONS`)."""
    if revision not in REVISIONS:
        known = ", ".join(f"{a}.{b}" for a, b in REVISIONS)
        msg = f"encode writes SFDP {known}, not {revision[0]}.{revision[1]}"
        raise ValueError(msg)
    if source.type is not FlashType.NOR:
        msg = f"{source.name}: SFDP is for SPI NOR parts"
        raise ValueError(msg)
    part = _from_flash(source) if isinstance(source, Flash) else _from_record(source)
    if part.size is None:
        msg = f"{part.name}: no size, which SFDP must give"
        raise ValueError(msg)
    assumed: list[str] = []
    missing: list[str] = []
    bfpt, reads = _bfpt_dwords(part, assumed, missing)

    later = _later_dwords(part, assume=assume)
    if revision >= (1, 5):
        missing.extend(f"{dwords}: {what}" for dwords, what in later.blocked)
        if assume:
            assumed.extend(f"{dwords}: {what}, {how}" for dwords, what, how in later.unknown)
            bfpt += later.dwords
        else:
            missing.extend(f"{dwords}: {what}" for dwords, what, _ in later.unknown)
            missing.extend(f"{dwords}: {what}, known but left out" for dwords, what in later.lost)
            revision = (1, 0)

    four_byte = _four_byte_dwords(part, reads, missing)
    tables = [(BFPT_ID, revision, bfpt)]
    if four_byte is not None:
        tables.append((FOUR_BYTE_ID, (1, 0), four_byte))
    pointer = 8 + 8 * len(tables)
    head = SIGNATURE + bytes([revision[1], revision[0], len(tables) - 1, 0xFF])
    headers = b""
    body = b""
    for table_id, (major, minor), dwords in tables:
        headers += _header(table_id, major, minor, len(dwords), pointer + len(body))
        body += struct.pack(f"<{len(dwords)}I", *dwords)
    return EncodedSfdp(head + headers + body, revision, tuple(assumed), tuple(missing))


def _bfpt_dwords(part: _Part, assumed: list[str], missing: list[str]) -> tuple[list[int], set[str]]:
    """DW1 to DW9, and the reads written."""
    size = part.size or 0
    dw = [0] * 9
    four_k = next((op for op, block in part.erasers if block == 4096), None)
    dw1 = 0b111 << 5 | 0x1FF << 23  # unused bits are ones
    dw1 |= (0b01 if four_k is not None else 0b11) | (four_k if four_k is not None else 0xFF) << 8
    if part.page_size is None:
        dw1 |= 1 << 2
        assumed.append("DW1 bit 2: a write granularity of 64 bytes or more (no page size known)")
    elif part.page_size >= 64:
        dw1 |= 1 << 2
    assumed.append("DW1 bits 3-4: non-volatile status register block protection")
    address = part.address_bytes or AddressBytes.THREE
    dw1 |= _ADDRESS_CODE[address] << 17
    dw[0] = dw1
    bits = size * 8
    dw[1] = bits - 1 if bits <= 1 << 31 else 1 << 31 | (bits.bit_length() - 1)
    dw[4] = 0xFFFFFFEE
    dw[5] = 0xFF00FFFF
    dw[6] = 0xFF00FFFF
    written: set[str] = set()
    split = []
    for name, _protocol, dw1_bit, (dword, shift), dw5_bit in _BFPT_READS:
        if name not in part.ops:
            continue
        clocks = part.ops[name]
        if clocks is None:
            clocks = OPERATIONS[name].dummy_clocks
            if clocks is None:
                missing.append(f"{name}: no dummy clocks known, so not in the BFPT")
                continue
            assumed.append(f"{name}: {clocks} dummy clocks, the operation's usual number")
        if clocks > 31:
            missing.append(f"{name}: {clocks} dummy clocks do not fit the BFPT")
            continue
        if dw1_bit is not None:
            dw[0] |= 1 << dw1_bit
        if dw5_bit is not None:
            dw[4] |= 1 << dw5_bit
        settings = _settings(OPERATIONS[name].opcode, 0, clocks)
        mask = 0xFFFF << shift
        dw[dword - 1] = dw[dword - 1] & ~mask | settings << shift
        written.add(name)
        split.append(name)
    if split:
        assumed.append(
            "the dummy clocks of " + ", ".join(split) + " as 0 mode + N wait clocks "
            "(the database keeps their total, not the split)"
        )
    types = [(op, block) for op, block in part.erasers if block & (block - 1) == 0]
    for op, block in part.erasers:
        if (op, block) not in types:
            missing.append(f"eraser 0x{op:02x}: {block}-byte blocks are not a power of two")
    if len(types) > 4:
        missing.extend(f"eraser 0x{op:02x}: the BFPT has four erase types" for op, _ in types[4:])
        types = types[:4]
    halves = [block.bit_length() - 1 | op << 8 for op, block in types]
    halves += [0xFF00] * (4 - len(halves))
    dw[7] = halves[0] | halves[1] << 16
    dw[8] = halves[2] | halves[3] << 16
    return dw, written


@dataclass
class _Later:
    dwords: list[int] = field(default_factory=list)
    #: (dwords, what the database does not hold, how it is written).
    unknown: list[tuple[str, str, str]] = field(default_factory=list)
    #: (dwords, what the database holds and only these dwords can give).
    lost: list[tuple[str, str]] = field(default_factory=list)
    #: (dwords, what the database holds and these dwords cannot say without
    #: saying more): written as JESD216's reserved value, and missing.
    blocked: list[tuple[str, str]] = field(default_factory=list)


def _later_dwords(part: _Part, *, assume: bool) -> _Later:
    """DW10 to DW16 (JESD216A and B), and what in them the database does
    not hold, or holds and only they can give."""
    out = _Later()
    dw10 = _dw10(part)
    if dw10 is None:
        out.unknown.append(("DW10", "erase type times", "written as typically 1 ms"))
    else:
        out.lost.append(("DW10", "the erase types' times"))
    page = part.page_size
    if page is None:
        out.unknown.append(("DW11", "the page size", "written as 256 bytes"))
        page = 256
    elif page & (page - 1) or page > 1 << 15:
        # DW11 gives a power of two: an AT45's 528-byte page has none.
        out.unknown.append(("DW11", f"a page size for {page}-byte pages", "written as 256 bytes"))
        page = 256
    else:
        out.lost.append(("DW11", f"the page size, {page} bytes"))
    dw11 = _dw11_times(part)
    if dw11 is None:
        out.unknown.append(("DW11", "program and chip erase times", "written as the shortest"))
    else:
        out.lost.append(("DW11", "the program and chip erase times"))
    out.unknown.append(("DW12-13", "suspend and resume", "written as not supported"))
    dw14 = _dw14(part, out)
    qer = _requirement(part, out)
    out.unknown.append(("DW15", "0-4-4 mode", "written as not supported"))
    ops = part.ops
    # DW15[8:4], the 4-4-4 enable sequences: bit 5 is "issue 0x38", bit 6
    # "issue 0x35" (so bits 1 and 2 of the field).
    enter = (0 if "EQPI_38" not in ops else 1 << 1) | (0 if "EQPI_35" not in ops else 1 << 2)
    leave = (0 if "RSTQIO_FF" not in ops else 1) | (0 if "RSTQIO_F5" not in ops else 1 << 1)
    if enter or leave:
        out.unknown.append(
            ("DW15", "the QPI enable and disable sequences", "written from the operations")
        )
    # DW16[31:24], the ways in, are the part's own (an extended or bank
    # address register is left at 0 the same way, so is a way out too);
    # DW16[18:14], the ways out, are written from EX4B, with a write enable
    # where the way in has one.
    modes = part.four_byte_modes
    modes_in = sum(1 << bit for bit, m in _ENTER_BITS.items() if m in modes)
    modes_out = sum(1 << (bit - 10) for bit, m in _ENTER_BITS.items() if m in _REGISTERS & modes)
    if "EX4B" in ops:
        modes_out |= 1 << (15 if FourByteMethod.WREN_EN4B in modes else 14)
    if any(op.endswith("_4B") for op in ops):
        modes_in |= 1 << 29
    if modes:
        out.lost.append(("DW16", f"the ways into 4-byte mode, {', '.join(sorted(modes))}"))
    if FourByteMethod.EAR_BIT7 in modes:
        out.blocked.append(("DW16", "setting bit 7 of the extended address register"))
    if modes_out:
        out.unknown.append(
            ("DW16", "how to exit 4-byte address mode", "written from the ways in and EX4B")
        )
    out.unknown.append(("DW16", "soft reset", "written as none"))
    out.unknown.append(("DW16", "the status register 1 write enable", "written as none"))
    if not assume:
        return out
    out.dwords = [
        0x00000000 if dw10 is None else dw10,  # unknown: multiplier 2, every erase type 1 ms
        1 << 31 | (page.bit_length() - 1) << 4 | (dw11 or 0),  # DW11; unknown: the shortest
        0xFFFFFFFF,  # DW12: bit 31, suspend and resume not supported
        0xFFFFFFFF,  # DW13
        0xFFFFFFFF if dw14 is None else dw14,  # DW14; bit 31: deep power-down not supported
        0xFF000000 | qer << 20 | enter << 4 | leave,  # DW15
        modes_in | modes_out | 1 << 7,  # DW16
    ]
    return out


def _erase_types(part: _Part) -> list[tuple[int, int]]:
    """(opcode, block) of the BFPT's erase types :func:`encode` writes: the
    part's uniform erasers of a power-of-two block, smallest first, the
    first four."""
    return [(op, block) for op, block in part.erasers if block & (block - 1) == 0][:4]


def _multiplier(typical: int | None, maximum: int | None) -> int | None:
    """The JESD216 multiplier a maximum is of a typical time, 2 x (N + 1)
    for N 0 to 15: an even whole number from 2 to 32; else ``None``."""
    if typical is None or maximum is None or maximum % typical:
        return None
    m = maximum // typical
    return m if 2 <= m <= 32 and m % 2 == 0 else None


def _place(units: tuple[int, ...], counts: int, ns: int | None, count_lo: int, unit_lo: int) -> int:
    """A time's bits in its dword: its count at ``count_lo`` and its unit
    code at ``unit_lo``; ``-1`` where the BFPT cannot write it exactly."""
    found = None if ns is None else derive.sfdp_time(units, counts, ns)
    if found is None:
        return -1
    unit, count = found
    return count << count_lo | (unit << unit_lo if len(units) > 1 else 0)


def _dw10(part: _Part) -> int | None:
    """DW10, where the database holds what it says: every erase type
    written has a typical time a BFPT can write exactly and a maximum, the
    maxima are one multiplier of the typical times, and so is the chip
    erase's, where it is known and takes DW10's
    (:data:`spiflash.derive.CHIP_ERASE_MULTIPLIER`). ``None`` otherwise:
    nothing is rounded, and no maximum made up."""
    types = _erase_types(part)
    if not types:
        return None
    dword = 0
    ratios = set()
    for i, (op, _) in enumerate(types):
        typ = part.time(TimedEvent.BLOCK_ERASE, Bound.TYPICAL, op)
        ratios.add(_multiplier(typ, part.time(TimedEvent.BLOCK_ERASE, Bound.MAXIMUM, op)))
        bits = _place(derive.ERASE_UNITS_NS, 32, typ, 4 + 7 * i, 9 + 7 * i)
        if bits < 0:
            return None
        dword |= bits
    chip = (
        part.time(TimedEvent.CHIP_ERASE, Bound.TYPICAL),
        part.time(TimedEvent.CHIP_ERASE, Bound.MAXIMUM),
    )
    if derive.CHIP_ERASE_MULTIPLIER == "DW10" and None not in chip:
        ratios.add(_multiplier(*chip))
    if len(ratios) != 1 or None in ratios:
        return None
    (m,) = ratios
    assert m is not None
    return dword | (m // 2 - 1)


#: DW11's times: (event, units, counts, count bit, unit bit).
_DW11_TIMES = (
    (TimedEvent.PAGE_PROGRAM, derive.PAGE_PROGRAM_UNITS_NS, 32, 8, 13),
    (TimedEvent.BYTE_PROGRAM_FIRST, derive.BYTE_PROGRAM_UNITS_NS, 16, 14, 18),
    (TimedEvent.BYTE_PROGRAM_ADDITIONAL, derive.BYTE_PROGRAM_UNITS_NS, 16, 19, 23),
    (TimedEvent.CHIP_ERASE, derive.CHIP_ERASE_UNITS_NS, 32, 24, 29),
)


def _dw11_times(part: _Part) -> int | None:
    """DW11's time bits (not its page size), where the database holds what
    they say: the page program's, the byte programs' and the chip erase's
    typical times, each one a BFPT can write exactly, and the programs'
    maxima one multiplier of their typical times (the chip erase's too,
    where it takes DW11's). ``None`` otherwise."""
    dword = 0
    ratios = set()
    for event, units, counts, count_lo, unit_lo in _DW11_TIMES:
        typ = part.time(event, Bound.TYPICAL)
        bits = _place(units, counts, typ, count_lo, unit_lo)
        if bits < 0:
            return None
        dword |= bits
        program = event is not TimedEvent.CHIP_ERASE
        if program or derive.CHIP_ERASE_MULTIPLIER == "DW11":
            ratios.add(_multiplier(typ, part.time(event, Bound.MAXIMUM)))
    if len(ratios) != 1 or None in ratios:
        return None
    (m,) = ratios
    assert m is not None
    return dword | (m // 2 - 1)


def _dw14(part: _Part, out: _Later) -> int | None:
    """DW14, where the part has deep power-down (``DP``, 0xb9) released by
    a command (``RDPD``, 0xab) and its exit delay is known, one a BFPT can
    write exactly; and what is said of it in ``out``. ``None`` (written as
    not supported) otherwise. Its status polling, DW14[7:2], is written as
    legacy 0x05 polling, which every SPI NOR part has, and the flag status
    register's (0x70) where the part has ``RDFSR``: listed assumed."""
    ops = part.ops
    delay = part.time(TimedEvent.DPD_EXIT, Bound.MAXIMUM)
    bits = _place(derive.LATENCY_UNITS_NS, 32, delay, 8, 13)
    if "DP" not in ops:
        out.unknown.append(("DW14", "deep power-down", "written as not supported"))
        return None
    if "RDPD" not in ops or bits < 0:
        out.unknown.append(
            ("DW14", "how deep power-down is left, and how long it takes", "written as none")
        )
        return None
    assert delay is not None
    out.lost.append(
        ("DW14", f"deep power-down, released with 0xab, ready within {human_duration(delay)}")
    )
    flag_status = "RDFSR" in ops
    out.unknown.append(
        (
            "DW14",
            "how to poll for busy",
            "written as 0x05 (legacy)" + (" and 0x70 (flag status)" if flag_status else ""),
        )
    )
    polling = 1 << 2 | (1 << 3 if flag_status else 0)
    reserved = 0b11 | 0b1111 << 4
    return (
        OPERATIONS["DP"].opcode << 23 | OPERATIONS["RDPD"].opcode << 15 | bits | polling | reserved
    )


#: The DW15 QER code JESD216 reserves: what :func:`encode` writes where it
#: knows the QE bit but not how it is written, as the decoders read it as no
#: requirement (:meth:`QuadEnableRequirement.from_code
#: <spiflash.registers.QuadEnableRequirement.from_code>`).
QER_RESERVED = 7


def _requirement(part: _Part, out: _Later) -> int:
    """The QER code DW15 is written with, and what is said of it in
    ``out``: the part's requirement; failing that, ``NONE`` for a part with
    no QE bit, and ``S1B6`` for one at SR1 bit 6, the only code putting it
    there; for one elsewhere (SR2 bit 1 has four codes, written
    differently), the reserved 7, as any code would say more than the
    database does (``out.blocked``); where nothing is known, 0."""
    qer, qe = part.quad_enable_requirement, part.quad_enable
    if qer is not None:
        out.lost.append(("DW15", f"the quad enable requirement, {qer}"))
        return qer.code
    if qe is None:
        out.unknown.append(("DW15", "the quad enable requirement", "written as 0, no QE bit"))
        return 0
    if qe == QE_NONE:
        out.lost.append(("DW15", "the quad enable requirement: no QE bit"))
        return QuadEnableRequirement.NONE.code
    if qe.place == (Register.SR1, 6):
        out.unknown.append(
            ("DW15", "how the QE bit (SR1 bit 6) is written", "written as S1B6, a 1-byte WRSR")
        )
        return QuadEnableRequirement.S1B6.code
    out.blocked.append(
        ("DW15", f"the quad enable requirement: the QE bit is {qe}, but not how it is written")
    )
    return QER_RESERVED


def _four_byte_dwords(part: _Part, reads: set[str], missing: list[str]) -> list[int] | None:
    """The 4BAIT's two dwords, where the part has 4-byte operations."""
    ops = part.ops
    if not any(op.endswith("_4B") for op in ops):
        return None
    dw1 = 0x7F << 25  # reserved bits are ones
    for bit, op in _FOUR_BYTE_BITS.items():
        if op not in ops:
            continue
        base = _THREE_BYTE_READ.get(op)
        if base is not None and base not in reads:
            missing.append(f"{op}: the 4BAIT lists a 4-byte read only with its 3-byte form")
            continue
        dw1 |= 1 << bit
    types = _erase_types(part)
    dw2 = 0
    for i in range(4):
        opcode = 0xFF
        if i < len(types) and (name := _FOUR_BYTE_ERASE.get(types[i][0])) in ops:
            opcode = OPERATIONS[name].opcode
            dw1 |= 1 << (9 + i)
        dw2 |= opcode << (8 * i)
    for name in ("BE_4K_4B", "BE_32K_4B", "SE_4B"):
        three = next(k for k, v in _FOUR_BYTE_ERASE.items() if v == name)
        if name in ops and not any(op == three for op, _ in types):
            missing.append(f"{name}: no erase type of 0x{three:02x} to give its 4-byte form")
    return [dw1, dw2]


# --- SFDP to an entry ---------------------------------------------------------


def to_entry(sfdp: Sfdp) -> dict[str, Any]:
    """A record in the data's shape
    (:repo:`records.json <src/spiflash/data/records.json>`, this format) holding
    what ``sfdp`` says as stored values, so a dump can be read as one more
    source: its size, page size, quad enable requirement, dies, erasers and
    ways into 4-byte mode, its operations with their dummy clocks (not the
    erases its erasers give, nor the operations its ways into 4-byte mode
    give), and the capabilities only SFDP says
    (:func:`spiflash.derive.sfdp_claims`), with their reasons in ``via``.
    The identity (``source``, ``name``, ``id``, ...) is ``None``:
    ``Record.from_json(to_entry(s) | {"source": ..., ...})`` reads it."""
    facts = sfdp.facts()
    erase_ops = {derive.ERASE_BY_OPCODE.get(e.opcode) for e in facts.erasers if e.opcode}
    claims = derive.sfdp_claims(sfdp)
    return {
        "source": None,
        "file": None,
        "line": None,
        "type": str(FlashType.NOR),
        "vendor": None,
        "name": None,
        "id": None,
        "ext_id": None,
        "id_method": None,
        "size": facts.size,
        "page_size": facts.page_size,
        "erasers": [e.to_json() for e in facts.erasers] or None,
        "features": sorted(claims),
        "flags": [],
        "via": {f"feature:{f}": why for f, why in sorted(claims.items())},
        "voltage": None,
        "supply_mv": None,
        "quad_enable": None,
        "quad_enable_requirement": None
        if facts.quad_enable_requirement is None
        else str(facts.quad_enable_requirement),
        "protection": None,
        "oob_size": None,
        "planes": None,
        "dies": facts.dies,
        "die_select_bit": None,
        "max_bad_blocks": None,
        "ecc": None,
        "four_byte_modes": sorted(facts.four_byte_modes),
        "otp": None,
        "legacy_ids": [],
        "max_clock_hz": None,
        "timings": facts.timings.to_json(),
        "opcodes": [
            {
                "op": u.op,
                "via": u.via,
                **({"dummy_clocks": u.dummy_clocks} if u.dummy_clocks is not None else {}),
            }
            for u in facts.opcodes
            if u.op not in erase_ops
        ],
        "sfdp": None,
        "sfdp_tables": {},
        "tested": None,
        "notes": [],
    }


# --- comparing two areas ------------------------------------------------------


@dataclass(frozen=True)
class TableDiff:
    """A parameter table one area has and the other has not, or has in
    another revision or length."""

    id: int
    name: str
    #: Each area's ``"1.6, 16 dwords"``, or ``None`` where it has no such table.
    a: str | None
    b: str | None


@dataclass(frozen=True)
class DwordDiff:
    """One dword of a table both areas have that differs (``None`` past the
    end of the shorter)."""

    table: int
    name: str
    #: Counted from 1, as JESD216 numbers them.
    index: int
    a: int | None
    b: int | None


@dataclass(frozen=True)
class FieldDiff:
    """One decoded field that differs: its path (``"reads.1-4-4"``), each
    area's value, and where the difference is one a round trip through the
    database always makes, why (``expected``)."""

    path: str
    a: Any
    b: Any
    expected: str | None = None


@dataclass(frozen=True)
class SfdpDiff:
    """What differs between two SFDP areas (:func:`diff`); false when
    nothing does."""

    tables: tuple[TableDiff, ...] = ()
    dwords: tuple[DwordDiff, ...] = ()
    fields: tuple[FieldDiff, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.tables or self.dwords or self.fields)

    @property
    def unexpected(self) -> tuple[FieldDiff, ...]:
        """The field differences that are not :attr:`FieldDiff.expected`."""
        return tuple(f for f in self.fields if f.expected is None)

    def to_json(self) -> dict[str, Any]:
        def plain(v: Any) -> Any:
            if isinstance(v, frozenset | set):
                return sorted(map(str, v))
            if isinstance(v, tuple | list):
                return [plain(x) for x in v]
            if isinstance(v, dict):
                return {str(k): plain(x) for k, x in v.items()}
            return v

        return {
            "differ": bool(self),
            "tables": [vars(t) for t in self.tables],
            "dwords": [vars(d) for d in self.dwords],
            "fields": [{**vars(f), "a": plain(f.a), "b": plain(f.b)} for f in self.fields],
        }

    def describe(self, a: str = "A", b: str = "B") -> str:
        """The differences as text, one per line; ``a`` and ``b`` name the
        two areas."""
        if not self:
            return "no differences"
        lines = []
        if self.tables:
            lines.append("tables:")
            lines.extend(
                f"    {t.name}: {t.a or 'none'} in {a}, {t.b or 'none'} in {b}" for t in self.tables
            )
        if self.fields:
            lines.append("fields:")
            lines.extend(
                f"    {f.path}: {_show(f.a)} in {a}, {_show(f.b)} in {b}"
                + (f"  (expected: {f.expected})" if f.expected else "")
                for f in self.fields
            )
        if self.dwords:
            lines.append("dwords:")
            lines.extend(
                f"    {d.name} DW{d.index}: {_hex(d.a)} in {a}, {_hex(d.b)} in {b}"
                for d in self.dwords
            )
        return "\n".join(lines)


def _hex(v: int | None) -> str:
    return "none" if v is None else f"0x{v:08x}"


def _show(v: Any) -> str:
    if v is None:
        return "none"
    if isinstance(v, frozenset | set):
        return "{" + ", ".join(sorted(map(str, v))) + "}" if v else "none"
    return str(v)


def _read(r: Any) -> str | None:
    if r is None:
        return None
    return f"0x{r.opcode:02x}, {r.mode_clocks} mode + {r.wait_states} wait clocks"


def _fields(s: Sfdp) -> dict[str, Any]:
    """The decoded fields :func:`diff` compares, by path."""
    bfpt = s.bfpt
    out: dict[str, Any] = {
        "revision": s.revision,
        "access_protocol": s.access_protocol,
        "size": s.size,
        "page_size": s.page_size,
        "address_bytes": None if s.address_bytes is None else str(s.address_bytes),
        "dtr": bfpt.dtr if bfpt else None,
    }
    for protocol, r in s.reads.items():
        out[f"reads.{protocol}"] = r
    for e in s.erase_types:
        out[f"erase_types.{e.index}"] = (e.size, e.opcode, e.opcode_4b, e.typical_ns)
    if bfpt is not None:
        out |= {
            "erase_multiplier": bfpt.erase_max_multiplier,
            "program_multiplier": bfpt.program_max_multiplier,
            "page_program_ns": bfpt.page_program_ns,
            "byte_program": (bfpt.byte_program_first_ns, bfpt.byte_program_additional_ns)
            if bfpt.byte_program_first_ns is not None
            else None,
            "chip_erase_ns": bfpt.chip_erase_ns,
            "suspend_resume": bfpt.suspend_resume,
            "suspend": (
                bfpt.erase_suspend_ns,
                bfpt.program_suspend_ns,
                bfpt.erase_resume_to_suspend_ns,
                bfpt.program_resume_to_suspend_ns,
            )
            if bfpt.erase_suspend_ns is not None
            else None,
            "deep_power_down": (bfpt.enter_deep_power_down, bfpt.exit_deep_power_down)
            if bfpt.enter_deep_power_down is not None
            else None,
            "dpd_exit_delay": bfpt.exit_deep_power_down_delay_ns,
            "quad_enable": None
            if bfpt.quad_enable is None
            else QUAD_ENABLE.get(bfpt.quad_enable, f"reserved code {bfpt.quad_enable}"),
            "qpi_enable": bfpt.qpi_enable or None,
            "qpi_disable": bfpt.qpi_disable or None,
            "mode_0_4_4": bfpt.mode_0_4_4,
            "four_byte_enter": bfpt.four_byte_enter or None,
            "four_byte_exit": bfpt.four_byte_exit or None,
            "soft_reset": bfpt.soft_reset or None,
        }
    if s.profile1 is not None:
        out["profile1"] = (s.profile1.read_opcode, s.profile1.dummy_by_mhz)
    out["dice"] = s.dice
    return out


def _expected(path: str, a: Any, b: Any) -> str | None:
    """Why a field difference is one a round trip through the database
    makes, or ``None``."""
    reads = path.startswith("reads.") and a is not None and b is not None
    if reads and a.opcode == b.opcode and a.dummy_clocks == b.dummy_clocks:
        return f"the same {a.dummy_clocks} dummy clocks, split differently"
    if path in ("revision", "access_protocol") and (None in (a, b) or "unknown" in (a, b)):
        return "one is tables without their SFDP header"
    return None


def diff(a: Sfdp, b: Sfdp) -> SfdpDiff:
    """What differs between SFDP areas ``a`` and ``b``: the parameter
    tables only one has (or has in another revision or length), each dword
    of the tables both have, and each decoded field (density, page size,
    address bytes, each read's opcode and mode and wait clocks, each erase
    type with its time, the quad enable requirement, 4-byte mode entry and
    exit, soft reset, deep power-down, the xSPI profile, the dice). A read
    whose dummy clocks are the same in total but split differently between
    mode and wait clocks is still a difference, marked
    :attr:`FieldDiff.expected`: the database keeps the total, so
    :func:`encode` cannot give the split back. ``diff(a, a)`` is empty."""
    tables: list[TableDiff] = []
    dwords: list[DwordDiff] = []

    def first(s: Sfdp) -> dict[int, Table]:
        out: dict[int, Table] = {}
        for t in s.tables:
            out.setdefault(t.header.id, t)
        return out

    def shape(t: Table | None, other: Table | None) -> str | None:
        """A table's revision and length; its length alone where either
        table's revision is not known (copied without its header)."""
        if t is None:
            return None
        if t.header.synthetic or (other is not None and other.header.synthetic):
            return f"{len(t.dwords)} dwords"
        return f"{t.header.revision}, {len(t.dwords)} dwords"

    ta, tb = first(a), first(b)
    for table_id in sorted(ta.keys() | tb.keys(), key=lambda i: (i != BFPT_ID, i)):
        x, y = ta.get(table_id), tb.get(table_id)
        name = (ta.get(table_id) or tb[table_id]).header.name
        if shape(x, y) != shape(y, x):
            tables.append(TableDiff(table_id, name, shape(x, y), shape(y, x)))
        if x is None or y is None:
            continue
        for i in range(max(len(x.dwords), len(y.dwords))):
            u = x.dwords[i] if i < len(x.dwords) else None
            v = y.dwords[i] if i < len(y.dwords) else None
            if u != v:
                dwords.append(DwordDiff(table_id, name, i + 1, u, v))
    fa, fb = _fields(a), _fields(b)
    fields = []
    for path in dict.fromkeys([*fa, *fb]):
        u, v = fa.get(path), fb.get(path)
        if u != v:
            shown_u = _read(u) if path.startswith("reads.") else u
            shown_v = _read(v) if path.startswith("reads.") else v
            fields.append(FieldDiff(path, shown_u, shown_v, _expected(path, u, v)))
    return SfdpDiff(tuple(tables), tuple(dwords), tuple(fields))
