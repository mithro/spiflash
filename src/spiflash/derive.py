"""What a record's stored fields imply, worked out at load.

A fact an upstream entry states is stored once, in one field of its
:class:`~spiflash.model.Record`. What follows from it is derived here, when
the record is made, and never written to the data. The rules read only what
the record stores (:data:`~spiflash.model.CLAIMS`). They are:

- how a SPI NOR record reads its id (:data:`ID_OPERATION`) gives the id
  operation;
- each of its erasers with an opcode (:data:`ERASE_BY_OPCODE`) gives that
  erase operation.

:func:`opcodes` applies them. The extractors drop a stored operation these
rules give, so it is not stored twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .enums import FlashType, IdMethod
from .opcodes import OpcodeUse

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


def opcodes(record: Record) -> tuple[OpcodeUse, ...]:
    """The operations ``record``'s stored fields (:meth:`Record.stored
    <spiflash.model.Record.stored>`) imply, each ``implied``: the id read of
    its ``id_method``, and the erase operation of each eraser with an opcode.
    SPI NOR only: a SPI NAND read-id or block erase is another command, so a
    SPI NAND record derives none."""
    if record.type is not FlashType.NOR:
        return ()
    vias: dict[str, list[str]] = {}
    method = record.stored("id_method")
    if method in ID_OPERATION:
        vias[ID_OPERATION[method]] = [f"id read ({method})"]
    for e in record.stored("erasers"):
        if e.opcode is not None:
            via = f"eraser: {layout(e)}"
            ops = vias.setdefault(ERASE_BY_OPCODE[e.opcode], [])
            if via not in ops:
                ops.append(via)
    return tuple(OpcodeUse(op, "; ".join(v), implied=True) for op, v in vias.items())
