"""The rules in spiflash.derive: what a record's stored fields imply."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from spiflash import derive
from spiflash.enums import Feature
from spiflash.model import EraseBlock, Eraser, Record
from spiflash.opcodes import OPERATIONS, OpcodeUse
from spiflash.sfdp import AddressBytes
from test_sfdp import MX25L25635E

MIB = 1 << 20


def rec(**kw: Any) -> Record:
    """A SPI NOR record of 16 MiB stating nothing but what ``kw`` gives."""
    base: dict[str, Any] = {
        "source": "flashrom",
        "file": "f.c",
        "line": 1,
        "type": "nor",
        "vendor": None,
        "name": "x",
        "id": "ef4018",
        "ext_id": None,
        "id_method": "rdid",
        "size": 16 * MIB,
        "page_size": 256,
        "erasers": None,
        "features": [],
        "flags": [],
        "via": {},
        "voltage": None,
        "opcodes": [],
        "tested": None,
        "notes": [],
    }
    base.update(kw)
    return Record.from_json(base)


def uses(*ops: str, assumed: bool = False) -> list[dict[str, Any]]:
    return [{"op": op, "via": "v", **({"assumed": True} if assumed else {})} for op in ops]


def eraser(opcode: int | None, *blocks: tuple[int, int]) -> dict[str, Any]:
    return {"opcode": opcode, "blocks": [list(b) for b in blocks]}


@pytest.mark.parametrize(
    ("feature", "ops"), [(f, ops) for f, ops in derive.FEATURE_IMPLIED_BY.items()]
)
def test_each_operation_implies_its_feature(feature: Feature, ops: tuple[str, ...]) -> None:
    for op in ops:
        assert op in OPERATIONS
        r = rec(size=None, opcodes=uses(op))
        assert feature in derive.features(r), op
        assert feature in r.features
        assert derive.feature_reasons(r)[feature] == f"implied by {op} (v)"


def test_a_driver_default_implies_nothing() -> None:
    every = [op for ops in derive.FEATURE_IMPLIED_BY.values() for op in ops]
    r = rec(size=None, opcodes=uses(*dict.fromkeys(every), assumed=True))
    assert derive.features(r) == frozenset()
    assert r.features == frozenset()
    assert derive.address_bytes(r) is None


def test_no_erase_operation_implies_a_size() -> None:
    # 0x20 erases 64 KiB on flashrom's MX25L1605; the eraser says so.
    r = rec(opcodes=uses("BE_4K", "BE_32K", "BE_4K_PMC", "SE"))
    assert not {Feature.ERASE_4K, Feature.ERASE_32K, Feature.ERASE_64K} & r.features
    r = rec(erasers=[eraser(0x20, (65536, 256))])
    assert Feature.ERASE_64K in r.features
    assert Feature.ERASE_4K not in r.features


def test_block_erasers_imply_their_size() -> None:
    r = rec(
        erasers=[
            eraser(0x20, (4096, 4096)),
            eraser(0x52, (32768, 512)),
            eraser(0xD8, (65536, 256)),
            eraser(0xC7, (16 * MIB, 1)),
        ]
    )
    assert {Feature.ERASE_4K, Feature.ERASE_32K, Feature.ERASE_64K} <= r.features
    assert derive.feature_reasons(r)[Feature.ERASE_32K] == "implied by eraser 0x52 (512 x 32768)"
    # A chip erase over a 64 KiB chip is no block erase; nor a die erase, a
    # non-uniform eraser or an erase routine.
    for e in (
        eraser(0xC7, (65536, 1)),
        eraser(0x60, (65536, 1)),
        eraser(0x62, (65536, 1)),
        eraser(0xC4, (65536, 2)),
        eraser(0xD8, (4096, 2), (65536, 1)),
        {**eraser(None, (65536, 1)), "function": "spi_block_erase_emulation"},
    ):
        assert not derive.features(rec(size=65536, erasers=[e])), e
    # A SPI NAND block erase implies no erase_*, nor any operation.
    nand = rec(type="nand", id_method="rdid_opcode_dummy", erasers=[eraser(0xD8, (65536, 256))])
    assert derive.features(nand) == frozenset()
    assert derive.opcodes(nand) == ()


def test_four_byte_addresses() -> None:
    assert derive.address_bytes(rec()) is AddressBytes.THREE
    assert Feature.FOUR_BYTE_ADDR not in rec().features
    big = rec(size=32 * MIB)
    assert derive.address_bytes(big) is AddressBytes.THREE_OR_FOUR
    assert derive.feature_reasons(big)[Feature.FOUR_BYTE_ADDR] == (
        "implied by its size, 32 MiB, over 16 MiB"
    )
    for op in ("EN4B", "EX4B", "WREAR", "BRWR", "READ_1_1_1_4B", "SE_4B"):
        r = rec(size=None, opcodes=uses(op))
        assert derive.address_bytes(r) is AddressBytes.THREE_OR_FOUR, op
        assert Feature.FOUR_BYTE_ADDR in r.features, op
    assert Feature.FOUR_BYTE_OPCODES not in rec(size=None, opcodes=uses("WREAR")).features
    assert derive.address_bytes(rec(size=None)) is None
    nand = rec(type="nand", id_method="rdid_opcode_dummy", size=512 * MIB)
    assert derive.address_bytes(nand) is None
    assert Feature.FOUR_BYTE_ADDR not in nand.features


def test_sfdp_tables_imply_their_features() -> None:
    r = rec(size=32 * MIB, sfdp=MX25L25635E.hex())
    assert derive.address_bytes(r) is AddressBytes.THREE_OR_FOUR
    assert {Feature.SFDP, Feature.FAST_READ, Feature.QUAD_READ, Feature.ERASE_4K} <= r.features
    assert derive.feature_reasons(r)[Feature.QUAD_READ] == "implied by its SFDP tables"


def test_claims_come_first_in_the_reasons() -> None:
    r = rec(
        features=["quad_read", "qpi"],
        via={"feature:qpi": "QPIEnable"},
        opcodes=uses("READ_1_1_4"),
    )
    reasons = derive.feature_reasons(r)
    assert reasons[Feature.QPI] == "claimed: QPIEnable"
    assert reasons[Feature.QUAD_READ] == "claimed"
    assert list(reasons) == [Feature.QUAD_READ, Feature.QPI]  # in Feature order
    assert r.feature_reasons() == reasons


def test_sector_size() -> None:
    def sector(*erasers: dict[str, Any], **kw: Any) -> int | None:
        return derive.sector_size(rec(erasers=list(erasers), **kw))

    sizes = 16 * MIB
    assert sector(eraser(0x20, (4096, sizes // 4096))) is None
    assert sector(eraser(0x20, (4096, 4096)), eraser(0xD8, (65536, 256))) == 65536
    # 0xd8 first, then 0xdc, then 0x52.
    assert sector(eraser(0x52, (32768, 512)), eraser(0xDC, (262144, 64))) == 262144
    assert sector(eraser(0x52, (32768, 512)), eraser(0xD8, (131072, 128))) == 131072
    assert sector(eraser(0x52, (32768, 512))) == 32768
    # A non-uniform 0xd8 (boot blocks) gives none.
    assert sector(eraser(0xD8, (8192, 4), (65536, 255))) is None
    # A part needing no erase has none, whatever its erasers.
    assert sector(eraser(0xD8, (65536, 256)), features=["no_erase"]) is None
    nand = {"type": "nand", "id_method": "rdid_opcode_dummy"}
    assert sector(eraser(0xD8, (131072, 1024)), **nand) == 131072
    assert sector(eraser(0x52, (32768, 512)), **nand) is None
    r = rec(erasers=[eraser(0xD8, (65536, 256))])
    assert r.sector_size == 65536
    # Derived, so replace() recomputes it.
    blocks = (EraseBlock(262144, 64),)
    assert replace(r, erasers=(Eraser(0xD8, blocks),)).sector_size == 262144


def test_block_eraser() -> None:
    e = derive.block_eraser(0xD8, 65536, 16 * MIB)
    assert e == Eraser(0xD8, (EraseBlock(65536, 256),))
    assert e.to_json() == {"opcode": 0xD8, "blocks": [[65536, 256]]}
    with pytest.raises(ValueError, match="not a whole number of 65536-byte blocks"):
        derive.block_eraser(0xD8, 65536, 32768)


def test_the_stored_part_is_read_not_the_derived() -> None:
    # The rules read only the stored fields: an implied operation (from an
    # eraser) is not a stated one.
    r = rec(erasers=[eraser(0x52, (65536, 256))])
    assert OpcodeUse("BE_32K", "eraser: 256 x 65536", implied=True) in r.opcodes
    assert Feature.ERASE_32K not in r.features
    assert Feature.ERASE_64K in r.features
