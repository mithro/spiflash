"""A JESD216 Basic Flash Parameter table (SFDP), decoded.

The BFP table is what a chip answers to Read SFDP (0x5a): its density, the
fast reads it supports and their opcodes, its erase types and its page size.
Some upstreams keep a copy read from a real part (Zephyr's ``sfdp-bfp``
devicetree property); :func:`decode` turns one into the facts the database
holds. The field layout is JESD216's (revision B), DWORD by DWORD, each
DWORD little-endian:

======  =====================================================================
DWORD   Fields read
======  =====================================================================
1       4 KiB erase (bits 1:0, opcode 15:8), 1-1-2 (16), address bytes
        (18:17), DTR (19), 1-2-2 (20), 1-4-4 (21), 1-1-4 (22)
2       density: bit 31 clear, bits 30:0 + 1 bits; set, 2^(bits 30:0) bits
3       1-4-4 opcode (15:8), 1-1-4 opcode (31:24)
4       1-1-2 opcode (15:8), 1-2-2 opcode (31:24)
5       2-2-2 (0), 4-4-4 (4)
8, 9    erase types 1 to 4: size 2^N bytes (7:0), opcode (15:8), twice each
11      page size 2^N bytes (7:4) (JESD216A and later)
15      quad enable requirements (22:20) (JESD216A and later)
======  =====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field

# DWORD 15 bits 22:20, as Zephyr's quad-enable-requirements names them.
QUAD_ENABLE = {
    0: "NONE",
    1: "S2B1v1",
    2: "S1B6",
    3: "S2B7",
    4: "S2B1v4",
    5: "S2B1v5",
    6: "S2B1v6",
}


@dataclass
class Bfp:
    """What a Basic Flash Parameter table says about a chip."""

    size: int  # bytes
    #: The fast reads it supports, by protocol ("1-1-4"), with their opcodes.
    reads: dict[str, int] = field(default_factory=dict)
    #: (opcode, block size in bytes) for each erase type.
    erases: list[tuple[int, int]] = field(default_factory=list)
    address_bytes: tuple[int, ...] = (3,)
    dtr: bool = False
    page_size: int | None = None
    quad_enable: str | None = None


def dwords(data: bytes) -> list[int]:
    """The table's 32-bit words, from its little-endian bytes."""
    if len(data) % 4 or len(data) < 9 * 4:
        msg = f"a BFP table is at least 9 whole DWORDs, not {len(data)} bytes"
        raise ValueError(msg)
    return [int.from_bytes(data[i : i + 4], "little") for i in range(0, len(data), 4)]


def decode(data: bytes) -> Bfp:
    """The facts in the BFP table ``data`` (its bytes, as read)."""
    dw = [0, *dwords(data)]  # dw[1] is DWORD 1, as the standard numbers them
    density = dw[2]
    bits = 1 << (density & 0x7FFFFFFF) if density >> 31 else density + 1
    bfp = Bfp(size=bits // 8)
    d1 = dw[1]
    bfp.address_bytes = {0: (3,), 1: (3, 4), 2: (4,)}.get((d1 >> 17) & 3, (3,))
    bfp.dtr = bool(d1 >> 19 & 1)
    if d1 >> 16 & 1:
        bfp.reads["1-1-2"] = dw[4] >> 8 & 0xFF
    if d1 >> 20 & 1:
        bfp.reads["1-2-2"] = dw[4] >> 24
    if d1 >> 22 & 1:
        bfp.reads["1-1-4"] = dw[3] >> 24
    if d1 >> 21 & 1:
        bfp.reads["1-4-4"] = dw[3] >> 8 & 0xFF
    if dw[5] & 1:
        bfp.reads["2-2-2"] = dw[6] >> 24
    if dw[5] >> 4 & 1:
        bfp.reads["4-4-4"] = dw[7] >> 24
    for word in dw[8:10]:
        for half in (word & 0xFFFF, word >> 16):
            exponent, opcode = half & 0xFF, half >> 8
            if exponent:
                bfp.erases.append((opcode, 1 << exponent))
    if not bfp.erases and d1 & 3 == 1:
        bfp.erases.append((d1 >> 8 & 0xFF, 4096))
    if len(dw) > 11:
        bfp.page_size = 1 << (dw[11] >> 4 & 0xF)
    if len(dw) > 15:
        bfp.quad_enable = QUAD_ENABLE.get(dw[15] >> 20 & 7)
    return bfp
