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
from .enums import FlashType
from .model import Eraser, Flash, Record
from .opcodes import OPERATIONS
from .sfdp import (
    BFPT_ID,
    FOUR_BYTE_ID,
    QUAD_ENABLE,
    SIGNATURE,
    AddressBytes,
    Sfdp,
    Table,
    parse,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .registers import QuadEnableRequirement

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
    "erase_types": "erase times (DW10)",
    "page_size": "the page size, in a revision encode cannot fill",
    "page_program_us": "program times (DW11)",
    "chip_erase_us": "chip erase time (DW11)",
    "suspend_resume": "suspend and resume (DW12-13)",
    "deep_power_down": "deep power-down (DW14)",
    "quad_enable": "the quad enable requirement (DW15), where the BFPT gives the reserved 7",
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
        derive.address_bytes(r),
        r.quad_enable_requirement,
    )


def _from_flash(f: Flash) -> _Part:
    """A chip as its sources agree on it: its size and page size, the
    operations some source gives it (not as a default), each eraser as the
    most sources give it, and the address bytes the most sources imply."""
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
    said = Counter(a for r in records if (a := derive.address_bytes(r)) is not None)
    address = said.most_common(1)[0][0] if said else None
    return _Part(f.name, f.size, f.page_size, erasers, ops, address, f.quad_enable_requirement)


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
    4-byte address mode, which the database does not hold (but for a quad
    enable requirement a source gives). Without
    ``assume``, ``encode`` then lowers the revision to 1.0, the highest it
    can fill, and lists what it left out in ``missing``; with ``assume``, it
    writes JESD216's "not supported" encodings or the shortest times, and
    the 4-byte mode and QPI sequences the operations suggest, each listed in
    ``assumed``. ``ValueError`` for a SPI NAND part, a part of no known size,
    or a revision it cannot write (:data:`REVISIONS`)."""
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


def _later_dwords(part: _Part, *, assume: bool) -> _Later:
    """DW10 to DW16 (JESD216A and B), and what in them the database does
    not hold, or holds and only they can give."""
    out = _Later()
    out.unknown.append(("DW10", "erase type times", "written as typically 1 ms"))
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
    out.unknown.append(("DW11", "program and chip erase times", "written as the shortest"))
    out.unknown.append(("DW12-13", "suspend and resume", "written as not supported"))
    out.unknown.append(("DW14", "deep power-down", "written as not supported"))
    qer = part.quad_enable_requirement
    if qer is None:
        out.unknown.append(("DW15", "the quad enable requirement", "written as 0, no QE bit"))
    else:
        out.lost.append(("DW15", f"the quad enable requirement, {qer}"))
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
    modes_in = modes_out = 0
    if "EN4B" in ops:
        modes_in |= 1 << 24
    if "EX4B" in ops:
        modes_out |= 1 << 14
    if "WREAR" in ops:
        modes_in, modes_out = modes_in | 1 << 26, modes_out | 1 << 16
    if "BRWR" in ops:
        modes_in, modes_out = modes_in | 1 << 27, modes_out | 1 << 17
    if any(op.endswith("_4B") for op in ops):
        modes_in |= 1 << 29
    if modes_in or modes_out:
        out.unknown.append(
            ("DW16", "how to enter and exit 4-byte address mode", "written from the operations")
        )
    out.unknown.append(("DW16", "soft reset", "written as none"))
    out.unknown.append(("DW16", "the status register 1 write enable", "written as none"))
    if not assume:
        return out
    out.dwords = [
        0x00000000,  # DW10: multiplier 2, every erase type 1 ms
        1 << 31 | (page.bit_length() - 1) << 4,  # DW11
        0xFFFFFFFF,  # DW12: bit 31, suspend and resume not supported
        0xFFFFFFFF,  # DW13
        0xFFFFFFFF,  # DW14: bit 31, deep power-down not supported
        0xFF000000 | (qer.code if qer else 0) << 20 | enter << 4 | leave,  # DW15
        modes_in | modes_out | 1 << 7,  # DW16
    ]
    return out


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
    types = [(op, block) for op, block in part.erasers if block & (block - 1) == 0][:4]
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
    source: its size, page size, quad enable requirement and erasers, its
    operations with their dummy clocks (not the erases its erasers give), and
    the capabilities only
    SFDP says (:func:`spiflash.derive.sfdp_claims`), with their reasons in
    ``via``. The identity (``source``, ``name``, ``id``, ...) is ``None``:
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
        "quad_enable": None,
        "quad_enable_requirement": None
        if facts.quad_enable_requirement is None
        else str(facts.quad_enable_requirement),
        "protection": None,
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
        out[f"erase_types.{e.index}"] = (e.size, e.opcode, e.opcode_4b, e.typical_us)
    if bfpt is not None:
        out |= {
            "page_program_us": bfpt.page_program_us,
            "chip_erase_us": bfpt.chip_erase_us,
            "suspend_resume": bfpt.suspend_resume,
            "deep_power_down": (bfpt.enter_deep_power_down, bfpt.exit_deep_power_down)
            if bfpt.enter_deep_power_down is not None
            else None,
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
