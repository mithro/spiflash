"""Opcodes for a record, checked against spiflash's table of operations.

Each extractor says which operations an entry implies and why (``via``: the
upstream flag, field or default behind it), and, where the upstream defines
the opcode in a header (``SPINOR_OP_READ_1_1_4``, ``JEDEC_FAST_READ_QOUT``),
the value from that header. :class:`Opcodes` refuses a value that disagrees
with :data:`spiflash.opcodes.OPERATIONS`, so a changed or misread header
fails the extraction instead of shipping a wrong opcode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from spiflash.opcodes import OPERATIONS, sort_key

from . import cparse

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


class Opcodes:
    """The operations one record implies, each with its reasons."""

    def __init__(self, symbols: Mapping[str, str | int] | None = None) -> None:
        self.symbols = symbols or {}
        self._ops: dict[str, tuple[int, list[str]]] = {}

    def add(self, op: str, via: str, *symbols: str, value: int | None = None) -> None:
        """Record ``op``, implied by ``via``. Its opcode is ``value``, or the
        first of ``symbols`` the upstream's headers define, or failing both
        the table's; a value that differs from the table's raises."""
        if op not in OPERATIONS:
            msg = f"unknown operation {op}"
            raise KeyError(msg)
        expected = OPERATIONS[op].opcode
        if value is None:
            for sym in symbols:
                if sym in self.symbols:
                    value = cparse.evaluate(sym, self.symbols)
                    break
        if value is None:
            value = expected
        if value != expected:
            msg = f"{op} ({via}): upstream says 0x{value:02x}, spiflash's table 0x{expected:02x}"
            raise ValueError(msg)
        _, vias = self._ops.setdefault(op, (value, []))
        if via not in vias:
            vias.append(via)

    def __contains__(self, op: str) -> bool:
        return op in self._ops

    def __iter__(self) -> Iterator[str]:
        """The operations added so far, by name."""
        return iter(list(self._ops))

    def discard(self, op: str) -> None:
        self._ops.pop(op, None)

    def to_json(self) -> list[dict[str, Any]]:
        return [
            {"op": op, "opcode": value, "via": "; ".join(vias)}
            for op, (value, vias) in sorted(self._ops.items(), key=lambda kv: sort_key(kv[0]))
        ]


# Erase opcodes by value: flashrom's spi_block_erase_<xx> sends 0x<xx>, and
# OpenOCD's erase_cmd/chip_erase_cmd hold the byte.
ERASE_BY_OPCODE = {
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

# Linux's and U-Boot's conversion of an operation to its 4-byte-address form
# (spi_nor_convert_3to4_read/_program/_erase in drivers/mtd/spi-nor/core.c).
TO_4B = {
    "READ_1_1_1": "READ_1_1_1_4B",
    "READ_1_1_1_FAST": "READ_1_1_1_FAST_4B",
    "READ_1_1_2": "READ_1_1_2_4B",
    "READ_1_2_2": "READ_1_2_2_4B",
    "READ_1_1_4": "READ_1_1_4_4B",
    "READ_1_4_4": "READ_1_4_4_4B",
    "READ_1_1_8": "READ_1_1_8_4B",
    "READ_1_8_8": "READ_1_8_8_4B",
    "PP_1_1_1": "PP_1_1_1_4B",
    "PP_1_1_4": "PP_1_1_4_4B",
    "BE_4K": "BE_4K_4B",
    "BE_32K": "BE_32K_4B",
    "SE": "SE_4B",
}

# The Linux / U-Boot header name of each operation's opcode.
SPINOR_OP = {
    "READ_1_1_1": "SPINOR_OP_READ",
    "READ_1_1_1_FAST": "SPINOR_OP_READ_FAST",
    "READ_1_1_2": "SPINOR_OP_READ_1_1_2",
    "READ_1_2_2": "SPINOR_OP_READ_1_2_2",
    "READ_1_1_4": "SPINOR_OP_READ_1_1_4",
    "READ_1_4_4": "SPINOR_OP_READ_1_4_4",
    "READ_1_1_8": "SPINOR_OP_READ_1_1_8",
    "READ_1_8_8": "SPINOR_OP_READ_1_8_8",
    "READ_8D_8D_8D": "SPINOR_OP_READ_FAST",
    "READ_1_1_1_4B": "SPINOR_OP_READ_4B",
    "READ_1_1_1_FAST_4B": "SPINOR_OP_READ_FAST_4B",
    "READ_1_1_2_4B": "SPINOR_OP_READ_1_1_2_4B",
    "READ_1_2_2_4B": "SPINOR_OP_READ_1_2_2_4B",
    "READ_1_1_4_4B": "SPINOR_OP_READ_1_1_4_4B",
    "READ_1_4_4_4B": "SPINOR_OP_READ_1_4_4_4B",
    "READ_1_1_8_4B": "SPINOR_OP_READ_1_1_8_4B",
    "READ_1_8_8_4B": "SPINOR_OP_READ_1_8_8_4B",
    "PP_1_1_1": "SPINOR_OP_PP",
    "BP": "SPINOR_OP_BP",
    "AAI_WP": "SPINOR_OP_AAI_WP",
    "PP_1_1_4": "SPINOR_OP_PP_1_1_4",
    "PP_8D_8D_8D": "SPINOR_OP_PP",
    "PP_1_1_1_4B": "SPINOR_OP_PP_4B",
    "PP_1_1_4_4B": "SPINOR_OP_PP_1_1_4_4B",
    "BE_4K": "SPINOR_OP_BE_4K",
    "BE_4K_PMC": "SPINOR_OP_BE_4K_PMC",
    "BE_32K": "SPINOR_OP_BE_32K",
    "SE": "SPINOR_OP_SE",
    "BE_4K_4B": "SPINOR_OP_BE_4K_4B",
    "BE_32K_4B": "SPINOR_OP_BE_32K_4B",
    "SE_4B": "SPINOR_OP_SE_4B",
    "CHIP_ERASE": "SPINOR_OP_CHIP_ERASE",
    "RDID": "SPINOR_OP_RDID",
    "RDSFDP": "SPINOR_OP_RDSFDP",
    "RDFSR": "SPINOR_OP_RDFSR",
    "CLSR": "SPINOR_OP_CLSR",
}


def add_spinor(ops: Opcodes, op: str, via: str) -> None:
    """Add a Linux/U-Boot operation, its value from the SPINOR_OP_* header."""
    ops.add(op, via, SPINOR_OP[op])


def add_4b_variants(ops: Opcodes, via: str) -> None:
    """Add the 4-byte-address form of every operation that has one."""
    for op in [o for o in ops if o in TO_4B]:
        add_spinor(ops, TO_4B[op], via)
