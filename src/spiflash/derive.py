"""What a record's stored fields imply, worked out at load.

A fact an upstream entry states is stored once, in one field of its
:class:`~spiflash.model.Record`. What follows from it is derived here, when
the record is made, and never written to the data. The rules read only what
the record stores (:data:`~spiflash.model.CLAIMS`). They are:

- how a SPI NOR record reads its id (:data:`ID_OPERATION`) gives the id
  operation, unless the entry names the command it reads the id with
  (``via["id_method"]``: Dediprog's ``RDIDCommand``) or stores another id
  read, which is then what it states;
- each of its erasers with an opcode (:data:`ERASE_BY_OPCODE`) gives that
  erase operation;
- its SFDP tables (an upstream's dump, or tables it copies) give what they
  say in the record's own terms (:meth:`Sfdp.facts
  <spiflash.sfdp.Sfdp.facts>`): the size, page size and quad enable
  requirement where the entry states none, erasers, and operations with
  their dummy clocks;
- its quad enable requirement gives where its QE bit is
  (:attr:`QuadEnableRequirement.bit
  <spiflash.registers.QuadEnableRequirement.bit>`, where the entry states
  no bit), and the register operations writing it
  (:data:`REQUIREMENT_OPERATIONS`);
- the operations it states, other than driver defaults, and those its SFDP
  tables give, its block erasers, its size, and two things only SFDP says
  give capabilities (:func:`features`, by :data:`FEATURE_IMPLIED_BY`,
  :data:`ERASE_FEATURE` and :data:`SFDP_FEATURES`), among them
  ``4byte_addr`` from :func:`address_bytes`; so do its quad enable bit
  (``quad_read``) and its block-protection bits (``lock``);
- its erasers give its sector size (:func:`sector_size`).

:func:`opcodes`, :func:`features` and :func:`sector_size` apply them, and
:func:`sfdp_features` the capability rules to a dump alone. The extractors
drop a stored operation, capability claim, size, page size or eraser these
rules give, so it is not stored twice, and store an eraser
(:func:`block_eraser`) where an upstream gives a sector size without a
layout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from .enums import Feature, FlashType, IdMethod
from .opcodes import OPERATIONS, OpcodeUse
from .registers import QuadEnableRequirement, RegisterBit
from .sfdp import AddressBytes, FourByteMethod
from .units import human_size

if TYPE_CHECKING:
    from .model import Eraser, Record
    from .registers import NoQuadEnable, Protection
    from .sfdp import Sfdp, SfdpFacts

#: The operation behind each way of reading an id, for a SPI NOR record
#: (:data:`NAND_ID_OPERATION` for SPI NAND).
ID_OPERATION: dict[IdMethod, str] = {
    IdMethod.RDID: "RDID",
    IdMethod.RDID_OPCODE: "RDID",
    IdMethod.RDID_OPCODE_DUMMY: "RDID",
    IdMethod.RDID_OPCODE_ADDR: "RDID",
    IdMethod.REMS: "REMS",
    IdMethod.RES1: "RES",
    IdMethod.RES2: "RES",
    IdMethod.AT25F: "RDID_ATMEL",
    IdMethod.ST95: "RDID_M95",
}

#: The SPI NAND read-id behind each way of reading an id: 0x9f, then the
#: id straight away, after a dummy byte, or after an address byte.
NAND_ID_OPERATION: dict[IdMethod, str] = {
    IdMethod.RDID_OPCODE: "NAND_RDID",
    IdMethod.RDID_OPCODE_DUMMY: "NAND_RDID_DUMMY",
    IdMethod.RDID_OPCODE_ADDR: "NAND_RDID_ADDR",
}

#: The erase operation each erase opcode is: flashrom's
#: ``spi_block_erase_<xx>`` sends 0x<xx>, and OpenOCD's ``erase_cmd`` and
#: ``chip_erase_cmd`` hold the byte (SPI NOR; :data:`NAND_ERASE_BY_OPCODE`
#: for SPI NAND).
ERASE_BY_OPCODE: dict[int, str] = {
    0x20: "BE_4K",
    0x21: "BE_4K_4B",
    0x40: "BE_40",
    0x50: "BE_ALT1",
    0x52: "BE_32K",
    0x53: "BE_53",
    0x5C: "BE_32K_4B",
    0x60: "CHIP_ERASE_ALT",
    0x61: "DIE_ERASE_61",
    0x62: "CHIP_ERASE_ATMEL",
    0x81: "BE_ALT2",
    0xC4: "DIE_ERASE",
    0xC7: "CHIP_ERASE",
    0xD7: "BE_4K_PMC",
    0xD8: "SE",
    0xDB: "BE_256",
    0xDC: "SE_4B",
}

#: A SPI NAND part's one erase, of a block.
NAND_ERASE_BY_OPCODE: dict[int, str] = {0xD8: "NAND_BLOCK_ERASE"}

#: The die erases: each erases one die, the address choosing it (Micron's
#: 0xc4, Infineon's 0x61).
DIE_ERASES = frozenset({"DIE_ERASE", "DIE_ERASE_61"})


def die_erasers(record: Record) -> tuple[Eraser, ...]:
    """The die erase layout of each die erase ``record`` states
    (:data:`DIE_ERASES`), over its :attr:`~spiflash.model.Record.dies`
    dies of its size: the layout is never stored, as the dies and the
    operation say it. None for a part of one die, or without a size, or
    whose size the dies do not divide."""
    from .model import EraseBlock, Eraser  # noqa: PLC0415 - model imports this module

    dies, size = record.dies, record.size
    if dies is None or dies < 2 or size is None or size % dies:
        return ()
    stated = {u.op for u in record.stored("opcodes")} & DIE_ERASES
    return tuple(
        Eraser(OPERATIONS[op].opcode, (EraseBlock(size // dies, dies),)) for op in sorted(stated)
    )


def layout(eraser: Eraser) -> str:
    """The blocks an eraser erases, as a derived use's ``via`` gives them:
    ``"32 x 4096"``, or ``"non-uniform"``."""
    if len(eraser.blocks) != 1:
        return "non-uniform"
    (block,) = eraser.blocks
    return f"{block.count} x {block.size}"


def _id_operations(record: Record) -> dict[IdMethod, str]:
    """The id reads of ``record``'s kind of flash, by id method."""
    return ID_OPERATION if record.type is FlashType.NOR else NAND_ID_OPERATION


def _reads_id_by_method(record: Record) -> bool:
    """Whether the id read follows from ``record``'s ``id_method``: not where
    the entry names the command it reads the id with (``via["id_method"]``),
    nor where it stores another id read (Dediprog sends 0x9f to its Sanyo
    parts, whose answer is a RES id)."""
    method = record.stored("id_method")
    by_method = _id_operations(record)
    if method not in by_method or "id_method" in record.stored("via"):
        return False
    reads = set(by_method.values()) - {by_method[method]}
    return not any(u.op in reads for u in record.stored("opcodes"))


def opcodes(record: Record) -> tuple[OpcodeUse, ...]:
    """The operations ``record``'s stored fields (:meth:`Record.stored
    <spiflash.model.Record.stored>`) imply, each ``implied``: the id read of
    its ``id_method`` (unless ``via`` names the command the entry reads the
    id with), the erase operation of each eraser it stores with an opcode,
    and the operations its SFDP tables give (:attr:`SfdpFacts.opcodes
    <spiflash.sfdp.SfdpFacts.opcodes>`, with their dummy clocks), and those
    its quad enable requirement, stated or from its tables, gives
    (:data:`REQUIREMENT_OPERATIONS`). A SPI NAND record's are its own
    command set's: its read-id (:data:`NAND_ID_OPERATION`) and its block
    erase (:data:`NAND_ERASE_BY_OPCODE`)."""
    if record.type is not FlashType.NOR:
        return _stored_opcodes(record)
    facts = record.sfdp_facts
    return _stored_opcodes(record) + (facts.opcodes if facts else ()) + _requirement_opcodes(record)


#: The register operations each quad enable requirement says the part
#: has, to read and write its QE bit (JESD216B, DW15[22:20]): a 2-byte
#: write status (``S2B1v1``, ``S2B1v4``), with status register 2 read with
#: 0x35 (``S2B1v5``), or 0x31 and 0x35 (``S2B1v6``). ``S1B6``'s 1-byte
#: write status and read status are every part's, and ``S2B7``'s 0x3e and
#: 0x3f no operation here.
REQUIREMENT_OPERATIONS: dict[QuadEnableRequirement, tuple[str, ...]] = {
    QuadEnableRequirement.S2B1V1: ("WRSR_16",),
    QuadEnableRequirement.S2B1V4: ("WRSR_16",),
    QuadEnableRequirement.S2B1V5: ("WRSR_16", "RDSR2"),
    QuadEnableRequirement.S2B1V6: ("WRSR2", "RDSR2"),
}


def _requirement_opcodes(record: Record) -> tuple[OpcodeUse, ...]:
    """What :func:`opcodes` gives from the record's quad enable
    requirement (:data:`REQUIREMENT_OPERATIONS`)."""
    qer = record.quad_enable_requirement
    if qer is None:
        return ()
    via = f"quad enable requirement {qer}"
    return tuple(OpcodeUse(op, via, implied=True) for op in REQUIREMENT_OPERATIONS.get(qer, ()))


def _stored_opcodes(record: Record) -> tuple[OpcodeUse, ...]:
    """What :func:`opcodes` gives from the id method and stored erasers."""
    vias: dict[str, list[str]] = {}
    # The operations only a driver-default eraser gives are defaults too.
    stated: set[str] = set()
    method = record.stored("id_method")
    if _reads_id_by_method(record):
        op = _id_operations(record)[method]
        vias[op] = [f"id read ({method})"]
        stated.add(op)
    nor = record.type is FlashType.NOR
    for e in record.stored("erasers"):
        # A SPI NAND part's one erase is its block erase.
        if e.opcode is not None and (nor or e.opcode in NAND_ERASE_BY_OPCODE):
            op = (ERASE_BY_OPCODE if nor else NAND_ERASE_BY_OPCODE)[e.opcode]
            via = f"eraser: {layout(e)}" + (", a driver default" if e.assumed else "")
            ops = vias.setdefault(op, [])
            if via not in ops:
                ops.append(via)
            if not e.assumed:
                stated.add(op)
    return tuple(
        OpcodeUse(op, "; ".join(v), implied=True, assumed=op not in stated)
        for op, v in vias.items()
    )


#: The erase opcodes that erase the whole chip (or a whole die), not a
#: block: an eraser sending one implies no block erase.
WHOLE_CHIP_ERASES = frozenset({0x60, 0x61, 0x62, 0xC4, 0xC7})

#: The capability a uniform block eraser of each size gives (SPI NOR).
ERASE_FEATURE: dict[int, Feature] = {
    4096: Feature.ERASE_4K,
    32 * 1024: Feature.ERASE_32K,
    64 * 1024: Feature.ERASE_64K,
}

_FOUR_BYTE_OPS = tuple(op for op in OPERATIONS if op.endswith("_4B"))

#: The operations that imply each capability, where the entry states the
#: operation (an ``assumed`` one implies nothing), SPI NOR's and SPI
#: NAND's. ``4byte_addr`` is :func:`address_bytes`'s.
#:
#: No erase operation is here: an opcode does not fix the block it erases
#: (flashrom's MX25L1605 erases 64 KiB with 0x20, its AT25F2048 64 KiB with
#: 0x52, its LE25FW106 2 KiB with 0xd7), so only an eraser's layout gives
#: ``erase_4k``, ``erase_32k`` or ``erase_64k`` (:data:`ERASE_FEATURE`).
FEATURE_IMPLIED_BY: dict[Feature, tuple[str, ...]] = {
    Feature.FAST_READ: (
        "READ_1_1_1_FAST",
        "READ_1_1_1_FAST_4B",
        "NAND_READ_CACHE_1_1_1_FAST",
        "NAND_READ_CACHE_1_1_1_FAST_3A",
    ),
    Feature.DUAL_READ: (
        "READ_1_1_2",
        "READ_1_2_2",
        "READ_1_1_2_4B",
        "READ_1_2_2_4B",
        "NAND_READ_CACHE_1_1_2",
        "NAND_READ_CACHE_1_2_2",
        "NAND_READ_CACHE_1_1_2_3A",
    ),
    Feature.QUAD_READ: (
        "READ_1_1_4",
        "READ_1_4_4",
        "READ_1_1_4_4B",
        "READ_1_4_4_4B",
        "READ_4_4_4",
        "READ_4_4_4_4B",
        "NAND_READ_CACHE_1_1_4",
        "NAND_READ_CACHE_1_4_4",
        "NAND_READ_CACHE_1_1_4_3A",
    ),
    Feature.QUAD_PP: (
        "PP_1_1_4",
        "PP_1_4_4",
        "PP_1_1_4_4B",
        "PP_1_4_4_4B",
        "NAND_PROGRAM_LOAD_1_1_4",
        "NAND_RANDOM_LOAD_1_1_4",
    ),
    Feature.OCTAL_READ: (
        "READ_1_1_8",
        "READ_1_8_8",
        "READ_1_1_8_4B",
        "READ_1_8_8_4B",
        "NAND_READ_CACHE_1_1_8",
        "NAND_READ_CACHE_1_8_8",
    ),
    Feature.OCTAL_DTR_READ: ("READ_8D_8D_8D",),
    Feature.OCTAL_DTR_PP: ("PP_8D_8D_8D",),
    Feature.QPI: ("READ_4_4_4", "READ_4_4_4_4B", "EQPI_38", "EQPI_35", "RSTQIO_FF", "RSTQIO_F5"),
    Feature.FOUR_BYTE_OPCODES: _FOUR_BYTE_OPS,
    Feature.SFDP: ("RDSFDP",),
}

#: The operations that mean a part takes 4-byte addresses: every ``_4B``
#: form, and the ways into 4-byte mode (``EN4B``/``EX4B``, and writing the
#: extended or bank address register, ``WREAR``/``BRWR``).
FOUR_BYTE_ADDRESS_OPS = (*_FOUR_BYTE_OPS, "EN4B", "EX4B", "WREAR", "BRWR")

#: More than this many bytes need a fourth address byte.
THREE_BYTE_LIMIT = 16 * 1024 * 1024

_IMPLIES: dict[str, tuple[Feature, ...]] = {}
for _feature, _ops in FEATURE_IMPLIED_BY.items():
    for _op in _ops:
        _IMPLIES[_op] = (*_IMPLIES.get(_op, ()), _feature)


_DW16_OPCODES_4B = "BFPT DW16 bit 29: dedicated 4-byte opcodes"
_PROFILE1 = "xSPI profile 1.0 table"

#: What only SFDP tables say, and the capabilities each gives: BFPT DW16
#: bit 29 (dedicated 4-byte opcodes) gives ``4byte_opcodes``, and an xSPI
#: profile 1.0 table (0xff05) octal DTR read and program, whose opcodes
#: (0xee, and 0x12 with the command extension) have no name here. The
#: BFPT's address bytes and its other ways into 4-byte mode give
#: ``4byte_addr`` (:func:`address_bytes`).
SFDP_FEATURES: dict[str, tuple[Feature, ...]] = {
    _DW16_OPCODES_4B: (Feature.FOUR_BYTE_OPCODES,),
    _PROFILE1: (Feature.OCTAL_DTR_READ, Feature.OCTAL_DTR_PP),
}


class _Given(NamedTuple):
    """What the capability rules read from a record (or from a dump alone)."""

    nor: bool
    #: The operations it states, without the driver defaults, and those its
    #: SFDP tables give.
    ops: tuple[OpcodeUse, ...]
    #: Its erasers, stored and from its SFDP tables.
    erasers: tuple[Eraser, ...]
    size: int | None
    facts: SfdpFacts | None
    #: Its quad enable bit, stated or from its quad enable requirement.
    quad_enable: RegisterBit | NoQuadEnable | None = None
    protection: Protection | None = None


def _given(record: Record) -> _Given:
    nor = record.type is FlashType.NOR
    facts = record.sfdp_facts
    stated = tuple(u for u in record.stored("opcodes") if not u.assumed)
    sfdp_ops = facts.opcodes if facts and nor else ()
    return _Given(
        nor,
        stated + sfdp_ops,
        record.erasers,
        record.size,
        facts,
        record.quad_enable,
        record.protection,
    )


def _block(eraser: Eraser) -> int | None:
    """The block size of a uniform block eraser: one with an opcode that is
    not a whole-chip erase, and one size of block, and not a driver default;
    else ``None``."""
    if eraser.opcode is None or eraser.opcode in WHOLE_CHIP_ERASES or len(eraser.blocks) != 1:
        return None
    if eraser.assumed:
        return None
    return eraser.blocks[0].size


def _four_byte_reason(g: _Given) -> str | None:
    """Why a SPI NOR part takes 4-byte addresses, or ``None``."""
    if not g.nor:
        return None
    if g.size is not None and g.size > THREE_BYTE_LIMIT:
        return f"its size, {human_size(g.size)}, over 16 MiB"
    for u in g.ops:
        if u.op in FOUR_BYTE_ADDRESS_OPS:
            return f"{u.op} ({u.via})"
    if g.facts is not None and g.facts.four_byte_enter:
        ways = ", ".join(sorted(map(str, g.facts.four_byte_enter)))
        return f"its SFDP tables (BFPT DW16, enter 4-byte mode: {ways})"
    return None


def _address_bytes(g: _Given) -> AddressBytes | None:
    if not g.nor:
        return None
    given = g.facts.address_bytes if g.facts else None
    if given is not None and given is not AddressBytes.THREE:
        return given
    if _four_byte_reason(g) is not None:
        return AddressBytes.THREE_OR_FOUR
    if given is not None or g.size is not None:
        return AddressBytes.THREE
    return None


def address_bytes(record: Record) -> AddressBytes | None:
    """How many address bytes a SPI NOR record's part takes: what its SFDP
    tables say where they say 4 (or 3 or 4); else ``THREE_OR_FOUR`` where its
    size is over 16 MiB, it states a 4-byte operation or its tables give one
    (:data:`FOUR_BYTE_ADDRESS_OPS`), or its tables give a way into 4-byte
    mode (BFPT DW16); else ``THREE`` where its size or its tables are known.
    ``None`` for SPI NAND, and where nothing says. ``4byte_addr`` is implied
    exactly when this is neither ``THREE`` nor ``None``."""
    return _address_bytes(_given(record))


def _implied(g: _Given) -> dict[Feature, str]:
    """What :func:`features` gives, each with the first thing implying it."""
    out: dict[Feature, str] = {}
    for u in g.ops:
        for f in _IMPLIES.get(u.op, ()):
            out.setdefault(f, f"{u.op} ({u.via})")
    if g.nor:
        _nor_implied(g, out)
    if isinstance(g.quad_enable, RegisterBit):
        out.setdefault(Feature.QUAD_READ, f"its quad enable bit, {g.quad_enable}")
    if g.protection is not None and g.protection.blocks:
        roles = ", ".join(g.protection.roles())
        out.setdefault(Feature.LOCK, f"its block protection bits ({roles})")
    return out


def _nor_implied(g: _Given, out: dict[Feature, str]) -> None:
    """What :func:`_implied` gives a SPI NOR record from its erasers, size
    and SFDP tables."""
    for e in g.erasers:
        block = _block(e)
        if e.opcode is not None and block in ERASE_FEATURE:
            out.setdefault(ERASE_FEATURE[block], f"eraser 0x{e.opcode:02x} ({layout(e)})")
    given = _address_bytes(g)
    if given not in (None, AddressBytes.THREE):
        said = g.facts.address_bytes if g.facts else None
        if said in (AddressBytes.FOUR, AddressBytes.THREE_OR_FOUR):
            out[Feature.FOUR_BYTE_ADDR] = f"its SFDP tables (BFPT DW1: {said} address bytes)"
        else:
            out[Feature.FOUR_BYTE_ADDR] = _four_byte_reason(g) or "its SFDP tables"
    if g.facts is not None:
        found = {
            _DW16_OPCODES_4B: FourByteMethod.OPCODES_4B in g.facts.four_byte_enter,
            _PROFILE1: g.facts.octal_dtr,
        }
        for what, feats in SFDP_FEATURES.items():
            for f in feats if found[what] else ():
                out.setdefault(f, f"its SFDP tables ({what})")


def features(record: Record) -> frozenset[Feature]:
    """The capabilities ``record``'s stored fields and SFDP tables imply, as
    against those it claims (SPI NOR):

    - each operation it states, by :data:`FEATURE_IMPLIED_BY`; a driver
      default (:attr:`OpcodeUse.assumed <spiflash.opcodes.OpcodeUse.assumed>`)
      implies nothing;
    - each operation its SFDP tables give (:attr:`SfdpFacts.opcodes
      <spiflash.sfdp.SfdpFacts.opcodes>`), by the same table. SFDP gives
      no sign of the 3-byte 1-1-1 fast read (0x0b), so a BFPT alone does
      not imply ``fast_read``; the 4-byte instruction table's fast read
      (0x0c, ``READ_1_1_1_FAST_4B``) does, as it would from any source;
    - each uniform block eraser of 4, 32 or 64 KiB, stored or from its
      tables (:data:`ERASE_FEATURE`), not a whole-chip erase however small
      the chip, nor a driver default (:attr:`Eraser.assumed
      <spiflash.model.Eraser.assumed>`);
    - ``4byte_addr`` where :func:`address_bytes` is neither ``THREE`` nor
      ``None``;
    - what only SFDP says (:data:`SFDP_FEATURES`);
    - ``quad_read`` from a quad enable bit (stated, or from the quad enable
      requirement), which a part has only to enable quad I/O; not from
      :data:`~spiflash.registers.QE_NONE`;
    - ``lock`` from a block-protection bit (:attr:`Protection.blocks
      <spiflash.registers.Protection.blocks>`: a BP bit, or TB, SEC or CMP,
      which only change what the BP bits protect).

    A SPI NAND record has the first (its own operations: a read from
    cache, a program load) and the last two."""
    return frozenset(_implied(_given(record)))


def _alone(sfdp: Sfdp) -> _Given:
    """What the capability rules read from a dump alone: its operations,
    erasers and size, its quad enable requirement's bit, and the rest of
    what it says."""
    facts = sfdp.facts()
    return _Given(
        nor=True,
        ops=facts.opcodes,
        erasers=facts.erasers,
        size=facts.size,
        facts=facts,
        quad_enable=facts.quad_enable,
    )


def sfdp_features(sfdp: Sfdp) -> frozenset[Feature]:
    """The capabilities SFDP tables imply on their own: what
    :func:`features` gives a record whose only source is ``sfdp``."""
    return frozenset(_implied(_alone(sfdp)))


def sfdp_claims(sfdp: Sfdp) -> dict[Feature, str]:
    """What SFDP tables imply that their operations, erasers and size alone
    do not (:data:`SFDP_FEATURES`, and ``4byte_addr`` from the BFPT's
    address bytes or DW16), each with why: the capabilities a record must
    claim to say what the tables say without carrying them
    (:func:`spiflash.sfdp_tools.to_entry`, which stores their quad enable
    requirement, so ``quad_read`` from it is not among them)."""
    alone = _alone(sfdp)
    bare = alone._replace(facts=None)
    without = _implied(bare)
    return {f: why for f, why in _implied(alone).items() if f not in without}


def feature_reasons(record: Record) -> dict[Feature, str]:
    """Each capability ``record`` has, and why: ``"claimed: QPIEnable"``
    for one it states (:attr:`~spiflash.model.Record.feature_claims`, with
    its ``via`` where it has one), ``"implied by READ_1_1_4
    (SPI_NOR_QUAD_READ)"`` for one :func:`features` gives. A claim is not
    also listed as implied; in :class:`~spiflash.enums.Feature` order."""
    implied = _implied(_given(record))
    out: dict[Feature, str] = {}
    for f in Feature:
        if f in record.stored("features"):
            via = record.stored("via").get(f"feature:{f}")
            out[f] = f"claimed: {via}" if via else "claimed"
        elif f in implied:
            out[f] = f"implied by {implied[f]}"
    return out


def sector_size(record: Record) -> int | None:
    """The erase block ``record``'s part is usually erased by, from its
    erasers (stored, and from its SFDP tables): for SPI NAND, the block of
    its block erase (0xd8); for SPI NOR, the block of a uniform 0xd8 eraser, else of a uniform 0xdc
    (0xd8's 4-byte-address form), else of a uniform 0x52 (the blocks of the
    AT25F and SST25LF parts, which have no 0xd8: 32 KiB, or 64 KiB on the
    AT25F2048 and AT25F4096). ``None`` where the part needs no erase
    (it claims ``no_erase``) or has none of those erasers. A driver
    default eraser (:attr:`Eraser.assumed <spiflash.model.Eraser.assumed>`)
    gives none."""
    if Feature.NO_ERASE in record.stored("features"):
        return None
    order = (0xD8,) if record.type is FlashType.NAND else (0xD8, 0xDC, 0x52)
    erasers: tuple[Eraser, ...] = record.erasers
    for opcode in order:
        for e in erasers:
            if e.opcode == opcode and len(e.blocks) == 1 and not e.assumed:
                return e.blocks[0].size
    return None


def block_eraser(opcode: int, block: int, size: int, *, assumed: bool = False) -> Eraser:
    """An eraser of ``opcode`` over the whole of a ``size``-byte part, in
    ``block``-byte blocks: what an extractor stores for an upstream that
    gives an erase block size and no layout (Linux's and U-Boot's sector
    size, openFPGALoader's ``sector_erase``, a SPI NAND block); ``assumed``
    for a driver's default."""
    from .model import EraseBlock, Eraser  # noqa: PLC0415 - model imports this module

    if block <= 0 or size % block:
        msg = f"a {size}-byte part is not a whole number of {block}-byte blocks"
        raise ValueError(msg)
    return Eraser(opcode, (EraseBlock(block, size // block),), assumed=assumed)
