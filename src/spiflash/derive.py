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
- the operations it states, other than driver defaults, its block erasers,
  its size and its SFDP tables give capabilities (:func:`features`, by
  :data:`FEATURE_IMPLIED_BY` and :data:`ERASE_FEATURE`), among them
  ``4byte_addr`` from :func:`address_bytes`;
- its erasers give its sector size (:func:`sector_size`).

:func:`opcodes`, :func:`features` and :func:`sector_size` apply them. The
extractors drop a stored operation or capability claim these rules give, so
it is not stored twice, and store an eraser (:func:`block_eraser`) where an
upstream gives a sector size without a layout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .enums import Feature, FlashType, IdMethod
from .opcodes import OPERATIONS, OpcodeUse
from .sfdp import AddressBytes
from .sfdp import parse as parse_sfdp
from .units import human_size

if TYPE_CHECKING:
    from .model import Eraser, Record

#: The operation behind each way of reading an id. Only SPI NOR records
#: derive it: a SPI NAND read-id is a different shape of command.
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

#: The erase operation each erase opcode is: flashrom's
#: ``spi_block_erase_<xx>`` sends 0x<xx>, and OpenOCD's ``erase_cmd`` and
#: ``chip_erase_cmd`` hold the byte.
ERASE_BY_OPCODE: dict[int, str] = {
    0x20: "BE_4K",
    0x21: "BE_4K_4B",
    0x40: "BE_40",
    0x50: "BE_ALT1",
    0x52: "BE_32K",
    0x53: "BE_53",
    0x5C: "BE_32K_4B",
    0x60: "CHIP_ERASE_ALT",
    0x62: "CHIP_ERASE_ATMEL",
    0x81: "BE_ALT2",
    0xC4: "DIE_ERASE",
    0xC7: "CHIP_ERASE",
    0xD7: "BE_4K_PMC",
    0xD8: "SE",
    0xDB: "BE_256",
    0xDC: "SE_4B",
}


def layout(eraser: Eraser) -> str:
    """The blocks an eraser erases, as a derived use's ``via`` gives them:
    ``"32 x 4096"``, or ``"non-uniform"``."""
    if len(eraser.blocks) != 1:
        return "non-uniform"
    (block,) = eraser.blocks
    return f"{block.count} x {block.size}"


def _reads_id_by_method(record: Record) -> bool:
    """Whether the id read follows from ``record``'s ``id_method``: not where
    the entry names the command it reads the id with (``via["id_method"]``),
    nor where it stores another id read (Dediprog sends 0x9f to its Sanyo
    parts, whose answer is a RES id)."""
    method = record.stored("id_method")
    if method not in ID_OPERATION or "id_method" in record.stored("via"):
        return False
    reads = set(ID_OPERATION.values()) - {ID_OPERATION[method]}
    return not any(u.op in reads for u in record.stored("opcodes"))


def opcodes(record: Record) -> tuple[OpcodeUse, ...]:
    """The operations ``record``'s stored fields (:meth:`Record.stored
    <spiflash.model.Record.stored>`) imply, each ``implied``: the id read of
    its ``id_method`` (unless ``via`` names the command the entry reads the
    id with), and the erase operation of each eraser with an opcode. SPI NOR
    only: a SPI NAND read-id or block erase is another command, so a
    SPI NAND record derives none."""
    if record.type is not FlashType.NOR:
        return ()
    vias: dict[str, list[str]] = {}
    # The operations only a driver-default eraser gives are defaults too.
    stated: set[str] = set()
    method = record.stored("id_method")
    if _reads_id_by_method(record):
        vias[ID_OPERATION[method]] = [f"id read ({method})"]
        stated.add(ID_OPERATION[method])
    for e in record.stored("erasers"):
        if e.opcode is not None:
            op = ERASE_BY_OPCODE[e.opcode]
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
WHOLE_CHIP_ERASES = frozenset({0x60, 0x62, 0xC4, 0xC7})

#: The capability a uniform block eraser of each size gives (SPI NOR).
ERASE_FEATURE: dict[int, Feature] = {
    4096: Feature.ERASE_4K,
    32 * 1024: Feature.ERASE_32K,
    64 * 1024: Feature.ERASE_64K,
}

_FOUR_BYTE_OPS = tuple(op for op in OPERATIONS if op.endswith("_4B"))

#: The operations that imply each capability, where the entry states the
#: operation (an ``assumed`` one implies nothing). SPI NOR only: the SPI NAND
#: records have no operations yet. ``4byte_addr`` is :func:`address_bytes`'s.
#:
#: No erase operation is here: an opcode does not fix the block it erases
#: (flashrom's MX25L1605 erases 64 KiB with 0x20, its AT25F2048 64 KiB with
#: 0x52, its LE25FW106 2 KiB with 0xd7), so only an eraser's layout gives
#: ``erase_4k``, ``erase_32k`` or ``erase_64k`` (:data:`ERASE_FEATURE`).
FEATURE_IMPLIED_BY: dict[Feature, tuple[str, ...]] = {
    Feature.FAST_READ: ("READ_1_1_1_FAST", "READ_1_1_1_FAST_4B"),
    Feature.DUAL_READ: ("READ_1_1_2", "READ_1_2_2", "READ_1_1_2_4B", "READ_1_2_2_4B"),
    Feature.QUAD_READ: (
        "READ_1_1_4",
        "READ_1_4_4",
        "READ_1_1_4_4B",
        "READ_1_4_4_4B",
        "READ_4_4_4_4B",
    ),
    Feature.QUAD_PP: ("PP_1_1_4", "PP_1_4_4", "PP_1_1_4_4B", "PP_1_4_4_4B"),
    Feature.OCTAL_READ: ("READ_1_1_8", "READ_1_8_8", "READ_1_1_8_4B", "READ_1_8_8_4B"),
    Feature.OCTAL_DTR_READ: ("READ_8D_8D_8D",),
    Feature.OCTAL_DTR_PP: ("PP_8D_8D_8D",),
    Feature.QPI: ("READ_4_4_4_4B", "EQPI_38", "EQPI_35", "RSTQIO_FF", "RSTQIO_F5"),
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


def _stated(record: Record) -> list[OpcodeUse]:
    """The operations ``record`` states, without the driver defaults."""
    return [u for u in record.stored("opcodes") if not u.assumed]


def _block(eraser: Eraser) -> int | None:
    """The block size of a uniform block eraser: one with an opcode that is
    not a whole-chip erase, and one size of block, and not a driver default;
    else ``None``."""
    if eraser.opcode is None or eraser.opcode in WHOLE_CHIP_ERASES or len(eraser.blocks) != 1:
        return None
    if eraser.assumed:
        return None
    return eraser.blocks[0].size


def _four_byte_reason(record: Record) -> str | None:
    """Why a SPI NOR ``record``'s part takes 4-byte addresses, or ``None``."""
    if record.type is not FlashType.NOR:
        return None
    size = record.stored("size")
    if size is not None and size > THREE_BYTE_LIMIT:
        return f"its size, {human_size(size)}, over 16 MiB"
    for u in _stated(record):
        if u.op in FOUR_BYTE_ADDRESS_OPS:
            return f"{u.op} ({u.via})"
    return None


def address_bytes(record: Record) -> AddressBytes | None:
    """How many address bytes a SPI NOR record's part takes: what its SFDP
    tables say where they say 4 (or 3 or 4); else ``THREE_OR_FOUR`` where its
    size is over 16 MiB or it states a 4-byte operation
    (:data:`FOUR_BYTE_ADDRESS_OPS`); else ``THREE`` where its size or its
    tables are known. ``None`` for SPI NAND, and where nothing says.
    ``4byte_addr`` is implied exactly when this is neither ``THREE`` nor
    ``None``."""
    if record.type is not FlashType.NOR:
        return None
    given = None
    if record.sfdp:
        given = parse_sfdp(record.sfdp).address_bytes
    if given is not None and given is not AddressBytes.THREE:
        return given
    if _four_byte_reason(record) is not None:
        return AddressBytes.THREE_OR_FOUR
    if given is not None or record.stored("size") is not None:
        return AddressBytes.THREE
    return None


def _implied(record: Record) -> dict[Feature, str]:
    """What :func:`features` gives, each with the first thing implying it."""
    out: dict[Feature, str] = {}
    if record.type is FlashType.NOR:
        for u in _stated(record):
            for f in _IMPLIES.get(u.op, ()):
                out.setdefault(f, f"{u.op} ({u.via})")
        for e in record.stored("erasers"):
            block = _block(e)
            if e.opcode is not None and block in ERASE_FEATURE:
                out.setdefault(ERASE_FEATURE[block], f"eraser 0x{e.opcode:02x} ({layout(e)})")
        if address_bytes(record) not in (None, AddressBytes.THREE):
            out[Feature.FOUR_BYTE_ADDR] = _four_byte_reason(record) or "its SFDP tables"
    if record.sfdp:
        for f in parse_sfdp(record.sfdp).features():
            out.setdefault(f, "its SFDP tables")
    return out


def features(record: Record) -> frozenset[Feature]:
    """The capabilities ``record``'s stored fields imply, as against those it
    claims:

    - each operation it states, by :data:`FEATURE_IMPLIED_BY` (SPI NOR); a
      driver default (:attr:`OpcodeUse.assumed
      <spiflash.opcodes.OpcodeUse.assumed>`) implies nothing;
    - each uniform block eraser of 4, 32 or 64 KiB (:data:`ERASE_FEATURE`;
      SPI NOR), not a whole-chip erase however small the chip, nor a driver
      default (:attr:`Eraser.assumed <spiflash.model.Eraser.assumed>`);
    - ``4byte_addr`` where :func:`address_bytes` is neither ``THREE`` nor
      ``None``;
    - everything its SFDP tables support (:meth:`Sfdp.features
      <spiflash.sfdp.Sfdp.features>`), where the upstream carries a dump.
      (A later release derives those from the tables' operations, by the
      rules above.)"""
    return frozenset(_implied(record))


def feature_reasons(record: Record) -> dict[Feature, str]:
    """Each capability ``record`` has, and why: ``"claimed: QPIEnable"``
    for one it states (:attr:`~spiflash.model.Record.feature_claims`, with
    its ``via`` where it has one), ``"implied by READ_1_1_4
    (SPI_NOR_QUAD_READ)"`` for one :func:`features` gives. A claim is not
    also listed as implied; in :class:`~spiflash.enums.Feature` order."""
    implied = _implied(record)
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
    stored erasers: for SPI NAND, the block of its block erase (0xd8); for
    SPI NOR, the block of a uniform 0xd8 eraser, else of a uniform 0xdc
    (0xd8's 4-byte-address form), else of a uniform 0x52 (the blocks of the
    AT25F and SST25LF parts, which have no 0xd8: 32 KiB, or 64 KiB on the
    AT25F2048 and AT25F4096). ``None`` where the part needs no erase
    (it claims ``no_erase``) or has none of those erasers. A driver
    default eraser (:attr:`Eraser.assumed <spiflash.model.Eraser.assumed>`)
    gives none."""
    if Feature.NO_ERASE in record.stored("features"):
        return None
    order = (0xD8,) if record.type is FlashType.NAND else (0xD8, 0xDC, 0x52)
    erasers: tuple[Eraser, ...] = record.stored("erasers")
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
