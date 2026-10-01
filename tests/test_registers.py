"""Register bits, quad enable requirements and protection layouts, and how a
record and a chip give them."""

from __future__ import annotations

import pickle

import pytest

from spiflash import Database
from spiflash.enums import Feature
from spiflash.model import COMPARED_VALUES, Record
from spiflash.registers import (
    QE_NONE,
    ROLES,
    Protection,
    QuadEnableRequirement,
    Register,
    RegisterBit,
    Writability,
    quad_enable_from_json,
    quad_enable_to_json,
)
from spiflash.sfdp import QUAD_ENABLE
from test_db import rec

SR1, SR2 = Register.SR1, Register.SR2


def test_a_register_is_named_by_the_command_reading_it() -> None:
    assert [r.read_opcode for r in Register] == [0x05, 0x35, 0x15, 0x48, 0x2B, 0x0F, 0x0F]
    assert Register.SR3.description == "SR3, read with 0x15"
    assert Register.NAND_CONFIG.description.endswith(", read with GET FEATURE (0x0f) at 0xb0")
    die = "die select feature, read with GET FEATURE (0x0f) at 0xd0"
    assert Register.NAND_DIE.description == die


def test_register_bit() -> None:
    b = RegisterBit(SR2, 1)
    assert str(b) == "SR2 bit 1"
    assert str(RegisterBit(Register.SR3, 3, Writability.OTP)) == "SR3 bit 3, OTP"
    assert b.to_json() == {"register": "sr2", "bit": 1}
    otp = {"register": "sr3", "bit": 3, "writability": "otp"}
    assert RegisterBit.from_json(otp).to_json() == otp
    with pytest.raises(ValueError, match="a register bit is 0 to 7"):
        RegisterBit(SR1, 8)


def test_no_quad_enable_bit_is_its_own_value() -> None:
    assert quad_enable_from_json("none") is QE_NONE
    assert quad_enable_to_json(QE_NONE) == "none"
    assert quad_enable_from_json(None) is None
    assert quad_enable_from_json({"register": "sr1", "bit": 6}) == RegisterBit(SR1, 6)
    assert pickle.loads(pickle.dumps(QE_NONE)) is QE_NONE
    with pytest.raises(ValueError, match="not a quad enable"):
        quad_enable_from_json("sr1")


def test_quad_enable_requirements_are_jesd216s() -> None:
    assert [q.code for q in QuadEnableRequirement] == list(range(7))
    assert QuadEnableRequirement.from_code(5) is QuadEnableRequirement.S2B1V5
    assert QuadEnableRequirement.from_code(7) is None  # reserved
    assert QuadEnableRequirement.from_code(None) is None
    assert QuadEnableRequirement.NONE.bit is QE_NONE
    assert QuadEnableRequirement.S1B6.bit == RegisterBit(SR1, 6)
    assert QuadEnableRequirement.S2B1V6.bit == RegisterBit(SR2, 1)
    # Its SR2 is read with 0x3f, which no Register is.
    assert QuadEnableRequirement.S2B7.bit is None
    # One table of what each code means, the decoder's.
    assert QuadEnableRequirement.S2B1V4.description == QUAD_ENABLE[4]


def test_protection() -> None:
    p = Protection(bp0=RegisterBit(SR1, 2), tb=RegisterBit(SR1, 5))
    assert list(p.roles()) == ["bp0", "tb"]
    assert p.bp == (RegisterBit(SR1, 2),)
    assert p.blocks
    assert not Protection(srp=RegisterBit(SR1, 7)).blocks  # no block-protect bit
    assert Protection.from_json(p.to_json()) == p
    # Positional: a source giving BP3 alone gives bp3, not bp0.
    assert Protection.from_json({"bp3": {"register": "sr1", "bit": 6}}).bp3 == RegisterBit(SR1, 6)
    with pytest.raises(ValueError, match="unknown protection roles"):
        Protection.from_json({"qe": {"register": "sr1", "bit": 6}})
    with pytest.raises(ValueError, match="bp3 and tb at SR1 bit 5"):
        Protection(bp3=RegisterBit(SR1, 5), tb=RegisterBit(SR1, 5))
    assert ROLES[:5] == ("bp0", "bp1", "bp2", "bp3", "bp4")


def test_compatible_is_equal_where_both_say() -> None:
    full = Protection(bp0=RegisterBit(SR1, 2), tb=RegisterBit(SR1, 5))
    assert full.compatible(Protection(tb=RegisterBit(SR1, 5)))
    assert full.compatible(Protection(srp=RegisterBit(SR1, 7)))
    assert not full.compatible(Protection(tb=RegisterBit(SR1, 6)))


def test_a_record_gives_one_of_a_bit_and_a_requirement() -> None:
    with pytest.raises(ValueError, match="a quad enable bit and a requirement"):
        rec(quad_enable="none", quad_enable_requirement="NONE")
    # The requirement gives the bit, and the register operations; the bit
    # (not QE_NONE) gives quad_read.
    r = rec(quad_enable_requirement="S2B1v6")
    assert r.quad_enable == RegisterBit(SR2, 1)
    assert {u.op for u in r.opcodes} >= {"WRSR2", "RDSR2"}
    assert Feature.QUAD_READ in r.features
    assert Feature.QUAD_READ not in rec(quad_enable="none").features
    assert r.to_json()["quad_enable"] is None  # derived, not stored
    assert r.given("quad_enable") == RegisterBit(SR2, 1)
    # A layout's block-protection bits give lock.
    locked = rec(protection={"tb": {"register": "sr1", "bit": 5}})
    assert Feature.LOCK in locked.features
    assert locked.feature_reasons()[Feature.LOCK] == "implied by its block protection bits (tb)"
    assert locked.given("protection.tb") == RegisterBit(SR1, 5)
    assert locked.given("protection.bp0") is None
    with pytest.raises(KeyError, match="no such value"):
        locked.given("protection.qe")
    # It pickles, and loads back the same.
    assert pickle.loads(pickle.dumps(locked)) == locked


def test_a_chip_takes_the_requirement_that_puts_the_bit_where_it_is() -> None:
    db = Database(
        [
            rec(quad_enable={"register": "sr2", "bit": 1}),
            rec(source="flashrom", name="W25Q128.V", quad_enable={"register": "sr2", "bit": 1}),
            rec(source="zephyr", name="w25q128", quad_enable_requirement="S1B6"),
        ]
    )
    (f,) = db.flashes
    assert f.quad_enable == RegisterBit(SR2, 1)
    # S1B6 puts the bit elsewhere: not this part's requirement.
    assert f.quad_enable_requirement is None
    assert "quad_enable" in f.conflicts
    json = f.to_json()
    assert json["quad_enable"] == {"register": "sr2", "bit": 1}
    assert json["conflicts"]["quad_enable"][1]["value"] == {"register": "sr1", "bit": 6}


def test_compared_values() -> None:
    assert COMPARED_VALUES[:6] == (
        "size",
        "page_size",
        "sector_size",
        "voltage",
        "quad_enable",
        "quad_enable_requirement",
    )
    roles = tuple(f"protection.{r}" for r in ROLES)
    assert COMPARED_VALUES[6 : 6 + len(roles)] == roles
    assert COMPARED_VALUES[6 + len(roles) :] == (
        "oob_size",
        "planes",
        "dies",
        "die_select_bit",
        "max_bad_blocks",
        "ecc.strength_bits",
        "ecc.step_bytes",
        "otp.size",
        "otp.regions",
    )
    # Every one is a value a record gives and a chip has.
    r = Record.from_json(rec().to_json())
    for name in COMPARED_VALUES:
        r.given(name)
        Database([r]).flashes[0].value(name)
    with pytest.raises(KeyError, match="no such value"):
        r.given("ecc.bits")


def test_an_ecc_requirement_is_compared_by_its_components() -> None:
    def nand(ecc: dict[str, int], source: str = "linux") -> Record:
        d = {**rec().to_json(), "type": "nand", "id_method": "rdid_opcode_dummy"}
        return Record.from_json({**d, "source": source, "ecc": ecc})

    eight = {"strength_bits": 8, "step_bytes": 512}
    linux, rockchip = nand(eight), nand({"strength_bits": 8}, "rockchip")
    assert str(linux.ecc) == "8 bits per 512 B"
    assert str(rockchip.ecc) == "8 bits"
    assert linux.ecc is not None
    assert rockchip.ecc is not None
    assert linux.ecc.compatible(rockchip.ecc)
    assert linux.given("ecc.step_bytes") == 512
    assert rockchip.given("ecc.step_bytes") is None
    (chip,) = Database([linux, rockchip]).flashes
    assert not chip.conflicts  # a source giving no step does not vote on it
    assert chip.ecc == linux.ecc
    four = nand({"strength_bits": 4}, "rockchip")
    assert four.ecc is not None
    assert not four.ecc.compatible(linux.ecc)
    (chip,) = Database([linux, four]).flashes
    assert set(chip.conflicts) == {"ecc.strength_bits"}
