"""The SFDP (JESD216) decoder, on the dumps QEMU's m25p80 model carries
(hw/block/m25p80_sfdp.c) and on hand-built tables."""

from __future__ import annotations

import json
import struct

import pytest

from spiflash import sfdp
from spiflash.enums import Feature
from spiflash.opcodes import OPERATIONS
from spiflash.sfdp import AddressBytes, FourByteMethod, parse

# Macronix MX25L25635E: JESD216 (1.0), a 9-dword BFPT and a vendor table in
# the one-byte-id encoding of the first revision.
MX25L25635E = bytes.fromhex(
    "53464450000101ff00000109300000ffc2000104600000ff"
    "ffffffffffffffffffffffffffffffffffffffffffffffff"
    "e520f3ffffffff0f44eb086b083b04bbeeffffffffff00ff"
    "ffff00ff0c200f5210d800ffffffffffffffffffffffffff"
    "00360027f74fffffd9c8ffffffffffffffffffffffffffff"
    "ffffffffffffffff"
)

# Winbond W25Q512JV: JESD216B, a 16-dword BFPT and a 4BAIT, plus a third
# header the count does not cover.
W25Q512JV = bytes.fromhex(
    "53464450060101ff00060110800000ff84000102d00000ff03000102f00000ff"
    + "ff" * 96
    + "e520fbffffffff1f44eb086b083b42bbfeffffffffff0000ffff40eb0c200f52"
    "10d800003602a60082ea14e2e96376337a757a75f7a2d55c19f74dffe970f9a5"
    "ffffffffffffffffffffffffffffffffff0af0ff21ffdcffffffffffffffffff" + "ff" * 40
)

# ISSI IS25WP256: JESD216B, BFPT says 3-byte addresses only, yet lists
# 4-byte-mode methods; a vendor table with id MSB 0x02.
IS25WP256 = bytes.fromhex(
    "53464450060101ff00060110300000ff9d05010380000002"
    + "ff" * 24
    + "e520f9ffffffff0f44eb086b083b80bbfeffffffffff00ffffff44eb0c200f52"
    "10d800ff234ac90082d811cecccd68467a757a75f7aed55c4a422cfff030faa9"
    "ffffffffffffffffffffffffffffffff501950169ff9c0648fefffffffffffff" + "ff" * 104
)

# Micron MT35XU01G: an octal part whose BFPT (as QEMU has it) lists no fast
# read at all, a reserved quad-enable code, and a 4BAIT with a 32 KiB erase.
MT35XU01G = bytes.fromhex(
    "53464450060101ff00060110300000ff84000102800000ff"
    + "ff" * 24
    + "e5208affffffff3f0000000000000000eeffffffffff0000ffff00000c2011d8"
    "0f520000245a99008b8e03e1ac0127387a757a75fbbdd55c000070ff81b03836"
    "ffffffffffffffffffffffffffffffff430effff21dc5cffffffffffffffffff" + "ff" * 104
)


def dump(*tables: tuple[int, int, int, int, list[int]], minor: int = 6) -> bytes:
    """An SFDP area from ``(id, major, minor, pointer, dwords)`` tables."""
    out = bytearray(b"SFDP" + bytes([minor, 1, len(tables) - 1, 0xFF]))
    for table_id, major, tminor, pointer, dwords in tables:
        out += struct.pack(
            "<8B",
            table_id & 0xFF,
            tminor,
            major,
            len(dwords),
            pointer & 0xFF,
            pointer >> 8 & 0xFF,
            pointer >> 16,
            table_id >> 8,
        )
    for _id, _major, _minor, pointer, dwords in tables:
        end = pointer + 4 * len(dwords)
        if len(out) < end:
            out += b"\xff" * (end - len(out))
        out[pointer:end] = struct.pack(f"<{len(dwords)}I", *dwords)
    return bytes(out)


MX_BFPT = [
    0xFFF320E5,
    0x0FFFFFFF,
    0x6B08EB44,
    0xBB043B08,
    0xFFFFFFEE,
    0xFF00FFFF,
    0xFF00FFFF,
    0x520F200C,
    0xFF00D810,
]


def ops(s: sfdp.Sfdp) -> dict[str | None, tuple[int, int | None]]:
    return {o.name: (o.opcode, o.dummy_clocks) for o in s.operations()}


def test_mx25l25635e() -> None:
    s = parse(MX25L25635E)
    assert (s.major, s.minor, s.revision_name) == (1, 0, "JESD216")
    assert [h.name for h in s.headers] == ["BFPT", "vendor table (0xc2)"]
    assert s.headers[1].manufacturer_id == 0xC2
    assert s.headers[1].revision == "1.0"
    assert s.tables[1].dwords == (0x27003600, 0xFFFF4FF7, 0xFFFFC8D9, 0xFFFFFFFF)
    assert s.bfpt is not None
    assert s.bfpt.length == 9
    assert s.size == 32 << 20
    assert s.page_size is None  # not in a JESD216 (9-dword) table
    assert s.address_bytes is AddressBytes.THREE_OR_FOUR
    assert s.bfpt.dtr is False
    assert s.bfpt.erase_4k_opcode == 0x20
    assert s.bfpt.uniform_4k is True
    assert [(e.size, e.opcode, e.opcode_4b) for e in s.erase_types] == [
        (4096, 0x20, None),
        (32 << 10, 0x52, None),
        (64 << 10, 0xD8, None),
    ]
    assert {p: (r.opcode, r.dummy_clocks) for p, r in s.reads.items()} == {
        "1-1-2": (0x3B, 8),
        "1-2-2": (0xBB, 4),
        "1-1-4": (0x6B, 8),
        "1-4-4": (0xEB, 6),
    }
    assert s.reads["1-4-4"].mode_clocks == 2
    assert s.bfpt.quad_enable is None
    assert s.four_byte is None
    assert s.warnings == ()
    # SFDP gives no sign of fast read 0x0b or page program 0x02.
    assert ops(s) == {
        "READ_1_1_1": (0x03, 0),
        "RDSFDP": (0x5A, 8),
        "READ_1_1_2": (0x3B, 8),
        "READ_1_2_2": (0xBB, 4),
        "READ_1_1_4": (0x6B, 8),
        "READ_1_4_4": (0xEB, 6),
        "BE_4K": (0x20, 0),
        "BE_32K": (0x52, 0),
        "SE": (0xD8, 0),
    }
    assert ops(s) == ops(parse(dump((0xFF00, 1, 0, 0x30, MX_BFPT))))
    assert s.features() == {
        "sfdp",
        "erase_4k",
        "erase_32k",
        "erase_64k",
        "dual_read",
        "quad_read",
        "4byte_addr",
    }
    text = s.describe()
    assert "SFDP 1.0 (JESD216), 2 parameter headers" in text
    assert "size 32 MiB, address bytes 3 or 4" in text
    assert "0xeb  READ_1_4_4" in text
    assert "DW1: 0xfff320e5" in s.describe(verbose=True)
    json.dumps(s.to_json())


def test_w25q512jv() -> None:
    s = parse(W25Q512JV)
    assert s.revision_name == "JESD216B"
    assert len(s.headers) == 2  # the third header is past the count
    assert s.bfpt is not None
    assert s.bfpt.revision == "JESD216B"
    assert s.size == 64 << 20
    assert s.page_size == 256
    assert s.bfpt.dtr is True
    assert s.reads["1-2-2"].mode_clocks == 2
    assert s.reads["1-2-2"].wait_states == 2
    assert s.reads["4-4-4"] == sfdp.FastRead("4-4-4", 0xEB, 2, 0)
    assert [(e.size, e.opcode, e.opcode_4b, e.typical_ns) for e in s.erase_types] == [
        (4096, 0x20, 0x21, 64_000_000),
        (32 << 10, 0x52, None, 128_000_000),
        (64 << 10, 0xD8, 0xDC, 160_000_000),
    ]
    assert s.bfpt.page_program_ns == 704_000
    assert s.bfpt.chip_erase_ns == 192_000_000_000
    assert (s.bfpt.erase_max_multiplier, s.bfpt.program_max_multiplier) == (14, 6)
    assert (s.bfpt.byte_program_first_ns, s.bfpt.byte_program_additional_ns) == (32_000, 3_000)
    assert s.bfpt.suspend_resume is True
    # DW12: Winbond's tSUS, 20 µs, for both.
    assert (s.bfpt.erase_suspend_ns, s.bfpt.program_suspend_ns) == (20_000, 20_000)
    assert s.bfpt.exit_deep_power_down_delay_ns == 3_000  # tRES1
    assert (s.bfpt.enter_deep_power_down, s.bfpt.exit_deep_power_down) == (0xB9, 0xAB)
    assert s.bfpt.quad_enable == 4
    assert s.bfpt.quad_enable_description is not None
    assert s.bfpt.quad_enable_description.startswith("SR2 bit 1")
    assert s.bfpt.qpi_enable == ("set QE, then 0x38",)
    assert s.bfpt.mode_0_4_4 is True
    assert s.bfpt.four_byte_enter == {
        FourByteMethod.EN4B,
        FourByteMethod.WREAR,
        FourByteMethod.OPCODES_4B,
    }
    assert s.bfpt.four_byte_exit == {
        FourByteMethod.EN4B,
        FourByteMethod.WREAR,
        FourByteMethod.HW_RESET,
        FourByteMethod.SW_RESET,
        FourByteMethod.POWER_CYCLE,
    }
    assert s.bfpt.linux_four_byte_method == "en4b_ex4b"
    assert s.bfpt.soft_reset_66_99 is True
    assert s.four_byte is not None
    assert s.four_byte.usable
    assert s.four_byte.erase_opcodes == (0x21, None, 0xDC, None)
    named = ops(s)
    assert {
        "READ_1_1_1_4B": (0x13, 0),
        "READ_1_1_1_FAST_4B": (0x0C, 8),
        "READ_1_1_2_4B": (0x3C, 8),
        "READ_1_2_2_4B": (0xBC, 4),
        "READ_1_1_4_4B": (0x6C, 8),
        "READ_1_4_4_4B": (0xEC, 6),
        "PP_1_1_1_4B": (0x12, 0),
        "PP_1_1_4_4B": (0x34, 0),
        "BE_4K_4B": (0x21, 0),
        "SE_4B": (0xDC, 0),
        "EN4B": (0xB7, 0),
        "EX4B": (0xE9, 0),
        "WREAR": (0xC5, 0),
    }.items() <= named.items()
    assert "BE_32K_4B" not in named
    # The 3-byte 4-4-4 read is QPI's.
    assert named["READ_4_4_4"] == (0xEB, 2)
    assert [o for o in s.operations() if o.name is None] == []
    # WREAR is both the way in and the way out: listed once.
    assert sum(o.opcode == 0xC5 for o in s.operations()) == 1
    # Filler bits 20 to 31 claim octal instructions this quad part has not.
    assert len(s.warnings) == 1
    assert s.warnings[0].startswith("4BAIT claims instructions the BFPT gives no read mode for")
    assert not any(i.supported_by_bfpt for i in s.four_byte.instructions if i.bit >= 20)
    assert {"qpi", "quad_pp", "4byte_opcodes", "4byte_addr"} <= s.features()
    assert "octal_read" not in s.features()


def test_is25wp256() -> None:
    s = parse(IS25WP256)
    assert s.headers[1].id == 0x029D
    assert s.headers[1].is_vendor
    assert s.headers[1].manufacturer_id == 0x9D
    assert s.headers[1].name == "vendor table (0x9d)"
    assert s.address_bytes is AddressBytes.THREE
    assert s.bfpt is not None
    assert s.bfpt.quad_enable == 2
    assert s.bfpt.linux_four_byte_method == "brwr"
    assert s.bfpt.qpi_enable == ("0x35",)
    # "3-byte only", but 32 MiB and a bank register: 4-byte addressing all the same.
    assert "4byte_addr" in s.features()
    assert ops(s)["BRWR"] == (0x17, 0)


def test_mt35xu01g() -> None:
    s = parse(MT35XU01G)
    assert s.bfpt is not None
    assert s.bfpt.reads == ()
    assert s.bfpt.quad_enable == 7
    assert s.bfpt.quad_enable_description == "reserved code 7"
    assert s.bfpt.four_byte_enter >= {FourByteMethod.WREN_EN4B, FourByteMethod.NV_CR}
    assert s.bfpt.linux_four_byte_method == "wren_en4b_ex4b"
    assert [(e.size, e.opcode_4b) for e in s.erase_types] == [
        (4096, 0x21),
        (128 << 10, 0xDC),
        (32 << 10, 0x5C),
    ]
    assert ops(s)["BE_32K_4B"] == (0x5C, 0)
    assert "octal_read" not in s.features()
    assert "quad_read" not in s.features()
    assert len(s.warnings) == 1


@pytest.mark.parametrize("data", [b"", b"SFD", b"SFDQ\x00\x01\x00\xff", MX25L25635E[4:]])
def test_bad_signature(data: bytes) -> None:
    with pytest.raises(ValueError, match="SFDP"):
        parse(data)


def test_truncated_dump() -> None:
    s = parse(MX25L25635E[:0x40])
    assert s.size == 32 << 20
    assert s.erase_types == ()
    assert any("4 of 9 dwords" in w for w in s.warnings)
    assert any("truncated" in w for w in s.warnings)
    s = parse(MX25L25635E[:0x20])
    assert s.bfpt is None
    assert s.size is None
    assert s.features() == {"sfdp"}
    assert list(s.operations()) == []
    assert any("past the end" in w for w in s.warnings)
    assert "no BFPT" in s.describe()
    # A header past the end of the data.
    s = parse(b"SFDP\x00\x01\x03\xff" + bytes(8))
    assert len(s.headers) == 1
    assert any("parameter header 1 is past the end" in w for w in s.warnings)


def test_bfpt_selection() -> None:
    newer = [*MX_BFPT, 0, 0x00000060, 0, 0, 0, 0, 0]
    # The later, higher-revision BFPT wins over the first header.
    s = parse(dump((0xFF00, 1, 0, 0x30, MX_BFPT), (0xFF00, 1, 6, 0x60, newer)))
    assert s.bfpt is not None
    assert s.bfpt.minor == 6
    assert s.page_size == 64
    # Same revision: the longer one.
    s = parse(dump((0xFF00, 1, 6, 0x30, newer[:12]), (0xFF00, 1, 6, 0x80, newer)))
    assert s.bfpt is not None
    assert s.bfpt.length == 16
    # A first header that is not the BFPT is a warning, not an error.
    s = parse(dump((0xFF84, 1, 0, 0x30, [1, 0x21]), (0xFF00, 1, 0, 0x40, MX_BFPT)))
    assert s.size == 32 << 20
    assert "the first parameter header is not the BFPT" in s.warnings
    # A BFPT of another major revision does not count.
    s = parse(dump((0xFF00, 2, 0, 0x30, MX_BFPT)))
    assert s.bfpt is None
    assert "no BFPT" in s.warnings


def test_density_forms() -> None:
    def size_of(dw2: int) -> tuple[int | None, tuple[str, ...]]:
        s = parse(dump((0xFF00, 1, 0, 0x30, [MX_BFPT[0], dw2, *MX_BFPT[2:]])))
        return s.size, s.warnings

    assert size_of(0x0FFFFFFF) == (32 << 20, ())
    assert size_of(0x80000000 | 28) == (32 << 20, ())
    size, warnings = size_of(0x80000000 | 64)
    assert size is None
    assert "not credible" in warnings[0]
    size, warnings = size_of(100)
    assert size == 12
    assert "whole number of bytes" in warnings[0]


def test_header_kinds() -> None:
    h = sfdp.ParameterHeader(0, 0xFF00, 1, 6, 16, 0x30)
    assert h.is_jedec
    assert not h.is_vendor
    assert h.end == 0x70
    assert sfdp.ParameterHeader(1, 0x01BF, 1, 0, 2, 0).name == "vendor table (0xbf)"
    assert sfdp.ParameterHeader(1, 0xFF03, 1, 0, 2, 0).name == "RPMC"
    legacy = sfdp.ParameterHeader(1, 0xFFC2, 1, 0, 4, 0)
    assert legacy.is_vendor
    assert legacy.manufacturer_id == 0xC2
    unknown = sfdp.ParameterHeader(1, 0xFF0F, 1, 0, 4, 0)
    assert not unknown.is_vendor
    assert unknown.manufacturer_id is None
    assert unknown.name == "unknown table 0xff0f"
    assert sfdp.revision_name(1, 9) == "JESD216 rev 1.9"


def test_profile1_and_sccr() -> None:
    profile = [0xEE << 8 | 1 << 28, 0, 0, 20 << 7, 16 << 27 | 12 << 17 | 8 << 7]
    s = parse(
        dump(
            (0xFF00, 1, 6, 0x40, MX_BFPT),
            (0xFF05, 1, 0, 0x70, profile),
            (0xFF87, 1, 0, 0x90, [0x800000]),
            (0xFF88, 1, 0, 0xA0, [1, 2, 3, 4]),
            (0xFF81, 1, 0, 0xB0, [0xFF0C, 0x0]),
        )
    )
    assert s.profile1 is not None
    assert s.profile1.read_opcode == 0xEE
    assert s.profile1.rdsr_dummy == 8
    assert s.profile1.rdsr_address_bytes == 0
    assert s.profile1.dummy_by_mhz == {200: 20, 166: 16, 133: 12, 100: 8}
    assert s.reads["8D-8D-8D"].dummy_clocks == 20
    octal = [o for o in s.operations() if o.protocol == "8D-8D-8D"]
    assert len(octal) == 1
    assert octal[0].opcode == 0xEE
    assert octal[0].name is None  # READ_8D_8D_8D in the table is 0x0b
    assert {"octal_dtr_read", "octal_dtr_pp"} <= s.features()
    assert s.sccr is not None
    assert s.dice == 3
    assert s.facts().dies == 3  # a record carrying it has 3 dies
    assert s.sector_map is not None
    assert s.sector_map.dwords == (0xFF0C, 0)
    assert "3 dice" in s.describe()
    # A profile table too short for its dummy fields is ignored.
    s = parse(dump((0xFF00, 1, 6, 0x40, MX_BFPT), (0xFF05, 1, 0, 0x70, profile[:2])))
    assert s.profile1 is None


def test_four_byte_gating() -> None:
    bfpt = [*MX_BFPT, 0, 0x00000080, 0, 0, 0, 0, 1 << 24 | 1 << 14]
    # Bits for reads the BFPT lacks are ignored; Linux does not use the
    # table without a program instruction, but the read it lists is still
    # a dedicated 4-byte opcode the part has.
    s = parse(dump((0xFF00, 1, 6, 0x40, bfpt), (0xFF84, 1, 0, 0x90, [1 << 20 | 1 << 5, 0])))
    assert s.four_byte is not None
    assert not s.four_byte.usable
    assert [i.bit for i in s.four_byte.instructions if i.supported_by_bfpt] == [5]
    assert [o.name for o in s.operations() if o.address_bytes == 4] == ["READ_1_4_4_4B"]
    assert "4byte_opcodes" in s.features()
    assert "octal_read" not in s.features()
    # With read, program and erase it is usable, and DTR needs the DTR bit.
    s = parse(
        dump(
            (0xFF00, 1, 6, 0x40, bfpt),
            (0xFF84, 1, 0, 0x90, [1 << 15 | 1 << 13 | 1 << 8 | 1 << 6 | 1 << 9 | 1, 0xFFFFFF21]),
        )
    )
    assert s.four_byte is not None
    assert s.four_byte.usable
    assert s.four_byte.erase_opcodes == (0x21, None, None, None)
    assert {"4byte_opcodes", "quad_pp"} <= s.features()
    assert not any(i.supported_by_bfpt for i in s.four_byte.instructions if i.bit in (13, 15))
    assert ops(s)["EN4B"] == (0xB7, 0)


def test_dw17_octal_reads() -> None:
    bfpt = [*MX_BFPT, 0, 0x00000080, 0, 0, 0, 0, 0, 0x8B10CB08, 1 << 29 | 1 << 31]
    s = parse(dump((0xFF00, 1, 7, 0x40, bfpt)))
    assert s.bfpt is not None
    assert s.bfpt.revision == "JESD216C"
    reads = {p: (r.opcode, r.dummy_clocks) for p, r in s.reads.items()}
    assert reads["1-1-8"] == (0x8B, 16)
    assert reads["1-8-8"] == (0xCB, 8)
    assert ops(s)["READ_1_1_8"] == (0x8B, 16)
    assert s.bfpt.command_extension == "invert"
    assert s.bfpt.byte_order_swapped is True
    assert "octal_read" in s.features()


def test_dw15_dw16_codes() -> None:
    # DW15: quad enable code 6 (JESD216C); DW16: soft reset bits 8, 11 and 12.
    bfpt = [*MX_BFPT, 0, 0, 0, 0, 0, 6 << 20, 1 << 8 | 1 << 11 | 1 << 12]
    s = parse(dump((0xFF00, 1, 6, 0x40, bfpt)))
    assert s.bfpt is not None
    assert s.bfpt.quad_enable_description == "SR2 bit 1, written with WRSR2 (0x31), read with 0x35"
    assert s.bfpt.soft_reset == ("0xf on 4 lines, 8 clocks", "0xf0", "0x66 then 0x99")
    assert s.bfpt.soft_reset_66_99 is True
    no_reset = parse(dump((0xFF00, 1, 6, 0x40, [*bfpt[:15], 0])))
    assert no_reset.bfpt is not None
    assert no_reset.bfpt.soft_reset == ()
    assert no_reset.bfpt.soft_reset_66_99 is False


def test_every_named_operation_is_in_the_table() -> None:
    for s in map(parse, (MX25L25635E, W25Q512JV, IS25WP256, MT35XU01G)):
        for o in s.operations():
            if o.name is not None:
                assert OPERATIONS[o.name].opcode == o.opcode
                assert o.description == OPERATIONS[o.name].description
        assert all(isinstance(f, Feature) for f in s.features())
