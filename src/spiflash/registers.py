"""Status and configuration register bits: where a part's quad enable bit
is (:class:`RegisterBit`, or :data:`QE_NONE` where it has none), its quad
enable requirement as JESD216 codes it (:class:`QuadEnableRequirement`),
and its block-protection bits (:class:`Protection`).

A register is named by the command that reads it (:class:`Register`), not
by what a datasheet calls it: Macronix's configuration register is
:attr:`Register.SR3`, as it is read with 0x15 like Winbond's status
register 3, and Spansion's CR1 is :attr:`Register.SR2`, read with 0x35.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum, StrEnum
from typing import Any


class Register(StrEnum):
    """A register a part's bits are in, named by the command reading it."""

    #: Status register 1, read with RDSR (0x05).
    SR1 = "sr1"
    #: Status register 2, read with 0x35: Winbond's and GigaDevice's SR2,
    #: Spansion's CR1.
    SR2 = "sr2"
    #: Read with 0x15: Winbond's status register 3, Macronix's
    #: configuration register.
    SR3 = "sr3"
    #: ISSI's function register, read with 0x48.
    FUNCTION = "function"
    #: Macronix's security register, read with RDSCUR (0x2b).
    SECURITY = "security"
    #: A SPI NAND part's configuration feature (address 0xb0), read with
    #: GET FEATURE (0x0f).
    NAND_CONFIG = "nand-b0"

    @property
    def read_opcode(self) -> int:
        """The opcode reading the register."""
        return _READ[self][0]

    @property
    def label(self) -> str:
        """``SR2``, ``function register``, ...: as the site writes it."""
        return _READ[self][1]

    @property
    def read_with(self) -> str:
        """How it is read: ``0x35``, ``GET FEATURE (0x0f) at 0xb0``."""
        if self is Register.NAND_CONFIG:
            return "GET FEATURE (0x0f) at 0xb0"
        return f"0x{self.read_opcode:02x}"

    @property
    def description(self) -> str:
        """``SR2, read with 0x35``."""
        return f"{self.label}, read with {self.read_with}"


_READ = {
    Register.SR1: (0x05, "SR1"),
    Register.SR2: (0x35, "SR2"),
    Register.SR3: (0x15, "SR3"),
    Register.FUNCTION: (0x48, "function register"),
    Register.SECURITY: (0x2B, "security register"),
    Register.NAND_CONFIG: (0x0F, "configuration feature"),
}


class Writability(StrEnum):
    """How a register bit is written, as flashrom's ``struct reg_bit_info``
    says: read and write (the default), volatile (lost at power off),
    one-time programmable, or read only (fixed)."""

    RW = "rw"
    VOLATILE = "volatile"
    OTP = "otp"
    RO = "ro"


@dataclass(frozen=True, slots=True)
class RegisterBit:
    """One bit of one register, and how it is written."""

    register: Register
    bit: int
    writability: Writability = Writability.RW

    def __post_init__(self) -> None:
        if not 0 <= self.bit <= 7:
            msg = f"bit {self.bit} of {self.register}: a register bit is 0 to 7"
            raise ValueError(msg)

    @property
    def place(self) -> tuple[Register, int]:
        """The register and bit, without the writability: two roles with
        one place are one bit."""
        return self.register, self.bit

    def __str__(self) -> str:
        """``SR2 bit 1``, and its writability where it is not read and write
        (``SR3 bit 3, OTP``)."""
        rw = "" if self.writability is Writability.RW else f", {self.writability.upper()}"
        return f"{self.register.label} bit {self.bit}{rw}"

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> RegisterBit:
        return cls(Register(d["register"]), d["bit"], Writability(d.get("writability", "rw")))

    def to_json(self) -> dict[str, Any]:
        """``{"register": "sr2", "bit": 1}``, with ``"writability"`` where
        it is not ``rw``."""
        out: dict[str, Any] = {"register": str(self.register), "bit": self.bit}
        if self.writability is not Writability.RW:
            out["writability"] = str(self.writability)
        return out


class NoQuadEnable(Enum):
    """The one value saying a part has no quad enable bit: quad I/O needs
    nothing set first. Not a :class:`RegisterBit`, so it cannot stand for a
    protection bit; ``None`` is "not said"."""

    QE_NONE = "none"

    def __str__(self) -> str:
        return "none"


#: The part has no quad enable bit.
QE_NONE = NoQuadEnable.QE_NONE


def quad_enable_from_json(value: str | dict[str, Any] | None) -> RegisterBit | NoQuadEnable | None:
    """A quad enable as the data stores it: ``"none"``, a register bit, or
    ``null``."""
    if value is None:
        return None
    if value == QE_NONE.value:
        return QE_NONE
    if isinstance(value, dict):
        return RegisterBit.from_json(value)
    msg = f"not a quad enable: {value!r}"
    raise ValueError(msg)


def quad_enable_to_json(value: RegisterBit | NoQuadEnable | None) -> str | dict[str, Any] | None:
    """:func:`quad_enable_from_json` reversed."""
    if value is None:
        return None
    if isinstance(value, NoQuadEnable):
        return str(value)
    return value.to_json()


class QuadEnableRequirement(StrEnum):
    """JESD216's Quad Enable Requirements (BFPT DW15[22:20]): where the QE
    bit is and how it is written. The values are the tokens JESD216 and
    Zephyr's ``quad-enable-requirements`` use; :attr:`code` is the field's."""

    NONE = "NONE"
    S2B1V1 = "S2B1v1"
    S1B6 = "S1B6"
    S2B7 = "S2B7"
    S2B1V4 = "S2B1v4"
    S2B1V5 = "S2B1v5"
    S2B1V6 = "S2B1v6"

    @classmethod
    def from_code(cls, code: int | None) -> QuadEnableRequirement | None:
        """The requirement of a DW15 code; ``None`` for none or the reserved 7."""
        return next((q for q in cls if q.code == code), None)

    @property
    def code(self) -> int:
        """The DW15[22:20] value: 0 to 6."""
        return list(QuadEnableRequirement).index(self)

    @property
    def description(self) -> str:
        return QUAD_ENABLE_REQUIREMENTS[self.code]

    @property
    def bit(self) -> RegisterBit | NoQuadEnable | None:
        """Where the requirement puts the QE bit: :data:`QE_NONE` for
        ``NONE``; ``None`` for ``S2B7``, whose status register 2 is read
        with 0x3f, which no :class:`Register` is."""
        if self is QuadEnableRequirement.NONE:
            return QE_NONE
        if self is QuadEnableRequirement.S1B6:
            return RegisterBit(Register.SR1, 6)
        if self is QuadEnableRequirement.S2B7:
            return None
        return RegisterBit(Register.SR2, 1)


#: The Quad Enable Requirements codes (BFPT DW15[22:20]), as JESD216B
#: describes each.
QUAD_ENABLE_REQUIREMENTS = {
    0: "no QE bit",
    1: "SR2 bit 1, written with a 2-byte WRSR (a 1-byte WRSR clears SR2)",
    2: "SR1 bit 6, written with a 1-byte WRSR",
    3: "SR2 bit 7, written with WRSR2 (0x3e), read with 0x3f",
    4: "SR2 bit 1, written with a 2-byte WRSR (a 1-byte WRSR leaves SR2)",
    5: "SR2 bit 1, written with a 2-byte WRSR, SR2 read with 0x35",
    6: "SR2 bit 1, written with WRSR2 (0x31), read with 0x35",
}


@dataclass(frozen=True, slots=True)
class Protection:
    """Where a part's block-protection bits are, by role, as flashrom's
    ``struct reg_bit_info`` names them: the block-protect bits ``bp0`` to
    ``bp4`` (by position: a source may give BP3 alone), top/bottom
    ``tb``, sector/block ``sec``, complement ``cmp``, the status register
    protect and lock bits ``srp`` and ``srl``, and write-protect selection
    ``wps``. A role is ``None`` where the source does not say. No two
    roles share a bit."""

    bp0: RegisterBit | None = None
    bp1: RegisterBit | None = None
    bp2: RegisterBit | None = None
    bp3: RegisterBit | None = None
    bp4: RegisterBit | None = None
    tb: RegisterBit | None = None
    sec: RegisterBit | None = None
    cmp: RegisterBit | None = None
    srp: RegisterBit | None = None
    srl: RegisterBit | None = None
    wps: RegisterBit | None = None

    def __post_init__(self) -> None:
        shared = shared_bits(self.roles())
        if shared:
            said = "; ".join(f"{' and '.join(r)} at {b}" for b, r in shared.items())
            msg = f"two protection roles on one bit: {said}"
            raise ValueError(msg)

    def roles(self) -> dict[str, RegisterBit]:
        """The roles given, in :data:`ROLES` order."""
        return {f.name: v for f in fields(self) if (v := getattr(self, f.name)) is not None}

    @property
    def bp(self) -> tuple[RegisterBit, ...]:
        """The block-protect bits given, BP0 first."""
        return tuple(b for b in (self.bp0, self.bp1, self.bp2, self.bp3, self.bp4) if b)

    @property
    def blocks(self) -> bool:
        """Whether it gives a block-protection bit: a BP bit, or TB, SEC or
        CMP, which only change what the BP bits protect."""
        return any(role in BLOCK_ROLES for role in self.roles())

    def compatible(self, other: Protection) -> bool:
        """Whether the two agree on every role both give."""
        mine, theirs = self.roles(), other.roles()
        return all(mine[r] == theirs[r] for r in mine.keys() & theirs.keys())

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Protection:
        unknown = set(d) - set(ROLES)
        if unknown:
            msg = f"unknown protection roles {sorted(unknown)}"
            raise ValueError(msg)
        return cls(**{role: RegisterBit.from_json(bit) for role, bit in d.items()})

    def to_json(self) -> dict[str, Any]:
        """``{"bp0": {"register": "sr1", "bit": 2}, ...}``: the roles given."""
        return {role: bit.to_json() for role, bit in self.roles().items()}

    def __str__(self) -> str:
        return ", ".join(f"{role} {bit}" for role, bit in self.roles().items())


#: The roles of :class:`Protection`, in order.
ROLES = tuple(f.name for f in fields(Protection))

#: The roles that are block protection: the BP bits, and TB, SEC and CMP.
BLOCK_ROLES = frozenset({"bp0", "bp1", "bp2", "bp3", "bp4", "tb", "sec", "cmp"})


def shared_bits(roles: dict[str, RegisterBit]) -> dict[str, tuple[str, ...]]:
    """The bits two or more of ``roles`` are on (``"SR1 bit 5"``), and the
    roles on each: a layout no part has."""
    by: dict[tuple[Register, int], list[str]] = {}
    for role, bit in roles.items():
        by.setdefault(bit.place, []).append(role)
    return {
        f"{register.label} bit {bit}": tuple(rs)
        for (register, bit), rs in by.items()
        if len(rs) > 1
    }
