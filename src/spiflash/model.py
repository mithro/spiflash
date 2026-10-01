"""The database's types: one upstream entry (:class:`Record`), the chip id
they describe (:class:`Flash`), and a datasheet for it (:class:`Datasheet`)."""

from __future__ import annotations

import datetime
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import cached_property
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NamedTuple, TypeVar, cast

from . import derive
from .enums import Feature, FlashType, IdFamily, IdMethod, Source
from .opcodes import OPERATIONS, OpcodeUse, Operation, sort_key
from .registers import (
    ROLES,
    NoQuadEnable,
    Protection,
    QuadEnableRequirement,
    RegisterBit,
    quad_enable_from_json,
    quad_enable_to_json,
    shared_bits,
)
from .sfdp import Sfdp, SfdpFacts, from_tables
from .sfdp import parse as parse_sfdp
from .vendors import canonical

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

T = TypeVar("T")

#: The sources whose table is a binary file: a record's ``line`` is its
#: entry's number in the file, and its link is to the file.
BINARY_SOURCES = frozenset({Source.IMSPROG})


class Voltage(NamedTuple):
    """A supply voltage range, in millivolts."""

    minimum_mv: int
    maximum_mv: int


@dataclass(frozen=True, slots=True)
class EraseBlock:
    """``count`` blocks of ``size`` bytes, as one eraser erases them."""

    size: int
    count: int


@dataclass(frozen=True, slots=True)
class Eraser:
    """One way to erase a chip: the opcode, and the blocks it erases
    (non-uniform when there is more than one kind). ``opcode`` is ``None``
    for an eraser that is a routine rather than one command (``function``
    names it: flashrom's ``spi_block_erase_emulation``, ...).

    ``assumed`` marks a driver default, as :attr:`OpcodeUse.assumed
    <spiflash.opcodes.OpcodeUse.assumed>` does an operation: Linux's 64 KiB
    0xd8 sector for an entry that gives no ``.sector_size``. It gives no
    capability and no sector size (:mod:`spiflash.derive`)."""

    opcode: int | None
    blocks: tuple[EraseBlock, ...]
    function: str | None = None
    assumed: bool = False

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Eraser:
        blocks = tuple(EraseBlock(size, count) for size, count in d["blocks"])
        return cls(d["opcode"], blocks, d.get("function"), d.get("assumed", False))

    def to_json(self) -> dict[str, Any]:
        """The eraser as the data stores it (:meth:`from_json` reads it back)."""
        out: dict[str, Any] = {
            "opcode": self.opcode,
            "blocks": [[b.size, b.count] for b in self.blocks],
        }
        if self.function is not None:
            out["function"] = self.function
        if self.assumed:
            out["assumed"] = True
        return out


class Claim(NamedTuple):
    """A source's reason for saying a chip has an operation; ``implied``
    when the operation follows from the source's other fields
    (:func:`spiflash.derive.opcodes`) rather than being stated, and
    ``assumed`` when it is the source's driver default
    (:attr:`OpcodeUse.assumed <spiflash.opcodes.OpcodeUse.assumed>`)."""

    source: Source
    via: str
    implied: bool = False
    assumed: bool = False


class SfdpDisagreement(NamedTuple):
    """A value a record states that its own SFDP tables give otherwise
    (:meth:`Record.sfdp_disagreements`): the field, what the entry states
    (the record's value), and what its tables say."""

    field: str
    stored: Any
    sfdp: Any


class FeatureSource(NamedTuple):
    """A source saying a chip has a capability: ``implied`` when its entry
    does not claim the capability but its other fields imply it
    (:func:`spiflash.derive.features`), and ``because`` why
    (:func:`spiflash.derive.feature_reasons`: ``"claimed: QPIEnable"``,
    ``"implied by READ_1_1_4 (SPI_NOR_QUAD_READ)"``)."""

    source: Source
    implied: bool
    because: str


class SfdpDump(NamedTuple):
    """One SFDP area the sources carry for a chip id, decoded, and the
    records carrying it, the best source first: a whole dump
    (:attr:`Record.sfdp`), or tables a source copies without the area
    around them (:attr:`Record.sfdp_tables`; :attr:`Sfdp.partial
    <spiflash.sfdp.Sfdp.partial>`)."""

    sfdp: Sfdp
    records: tuple[Record, ...]

    @property
    def tables(self) -> Sfdp:
        """:attr:`sfdp`, by its old name."""
        return self.sfdp

    @property
    def source(self) -> Source:
        """The best source carrying the dump."""
        return self.records[0].source

    @property
    def parts(self) -> tuple[str, ...]:
        """The part numbers of the records carrying it."""
        return tuple(dict.fromkeys(n for r in self.records for n in r.part_names))


def parse_id(value: str | bytes | bytearray | int | Iterable[int]) -> bytes:
    """Id bytes from any of ``"ef4018"``, ``"0xEF4018"``, ``"ef 40 18"``,
    ``b"\\xef\\x40\\x18"``, ``[0xef, 0x40, 0x18]`` or ``0xef4018`` (an int is
    read as big-endian, in as many bytes as it needs)."""
    if isinstance(value, bytes | bytearray):
        return bytes(value)
    if isinstance(value, int):
        if value < 0:
            msg = "an id cannot be negative"
            raise ValueError(msg)
        return value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    if isinstance(value, str):
        s = re.sub(r"[\s:_-]", "", value.strip().lower())
        s = s.removeprefix("0x")
        if not s or len(s) % 2 or not re.fullmatch(r"[0-9a-f]+", s):
            msg = f"not a hex id: {value!r}"
            raise ValueError(msg)
        return bytes.fromhex(s)
    return bytes(value)


def strip_continuation(data: bytes) -> tuple[int, bytes]:
    """``(bank, id without its 0x7f continuation codes)``."""
    n = 0
    while n < len(data) - 1 and data[n] == 0x7F:
        n += 1
    return n, data[n:]


@dataclass(frozen=True, slots=True)
class Datasheet:
    """A datasheet for one or more chip ids: where to get it, what it is,
    and which part numbers and ids it covers.

    ``official`` is true when ``url`` is the manufacturer's own site, false
    for a copy elsewhere (a distributor, an archive). ``confirmed`` lists the
    ids whose bytes the document itself gives; for the others, the match is
    by part number only."""

    url: str
    title: str
    official: bool
    parts: tuple[str, ...]
    ids: tuple[str, ...]
    confirmed: tuple[str, ...] = ()
    revision: str | None = None
    date: datetime.date | None = None
    also_at: tuple[str, ...] = ()
    #: Of the file as downloaded, so a copy can be checked against it.
    sha256: str | None = None

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Datasheet:
        return cls(
            url=d["url"],
            title=d["title"],
            official=d["official"],
            parts=tuple(d["parts"]),
            ids=tuple(d["ids"]),
            confirmed=tuple(d.get("confirmed") or ()),
            revision=d.get("revision"),
            date=datetime.date.fromisoformat(d["date"]) if d.get("date") else None,
            also_at=tuple(d.get("also_at") or ()),
            sha256=d.get("sha256"),
        )

    def rank(self, key: str, *more: str) -> tuple[bool, bool, int]:
        """Sorts the best datasheet for chip ``key`` (also answering ids
        ``more``) first: one showing the id, then the manufacturer's own,
        then the newest."""
        newest = -self.date.toordinal() if self.date else 0
        shown = any(k in self.confirmed for k in (key, *more))
        return (not shown, not self.official, newest)


@dataclass(frozen=True)
class SupportedOperation:
    """An operation a chip supports, and which upstreams say so, why."""

    operation: Operation
    because: tuple[Claim, ...]  # by source priority

    @property
    def name(self) -> str:
        return self.operation.name

    @property
    def opcode(self) -> int:
        return self.operation.opcode

    @property
    def sources(self) -> tuple[Source, ...]:
        return tuple(dict.fromkeys(claim.source for claim in self.because))

    @property
    def implied_by(self) -> tuple[Source, ...]:
        """The sources whose entries imply the operation without stating it
        (:attr:`Claim.implied`)."""
        return tuple(dict.fromkeys(c.source for c in self.because if c.implied))

    @property
    def assumed_by(self) -> tuple[Source, ...]:
        """The sources listing the operation only as their driver's default
        (:attr:`Claim.assumed`), not for this part."""
        return tuple(dict.fromkeys(c.source for c in self.because if c.assumed))


#: The fields a record both stores and derives, and the attribute holding
#: what it stores: ``features`` is ``feature_claims`` and what the other
#: fields imply; ``size`` is ``size_claim``, or where the entry states
#: none, what its SFDP tables say. A field joins when a rule in
#: :mod:`spiflash.derive` starts adding to it, its stored part taking a
#: ``*_claims`` name (``*_claim`` for a single value); its JSON key stays
#: the field's name. :meth:`Record.stored`, :meth:`Record.to_json` and the
#: rules themselves read only the stored part.
CLAIMS = {
    "size": "size_claim",
    "page_size": "page_size_claim",
    "erasers": "eraser_claims",
    "features": "feature_claims",
    "opcodes": "opcode_claims",
    "quad_enable": "quad_enable_claim",
    "quad_enable_requirement": "quad_enable_requirement_claim",
}

#: The single values a record gives from its SFDP tables where it states
#: none: :meth:`Record.given` reads the whole value, and
#: :meth:`Record.sfdp_disagreements` compares the stated one with the
#: tables'. The quad enable bit is the requirement's
#: (:attr:`QuadEnableRequirement.bit
#: <spiflash.registers.QuadEnableRequirement.bit>`), whether the entry
#: states the requirement or its tables give it.
SFDP_VALUES = ("size", "page_size", "quad_enable_requirement", "quad_enable")

#: The fields a record only derives, never stores: ``sector_size`` is
#: worked out from its erasers (:func:`spiflash.derive.sector_size`).
#: :meth:`Record.stored` refuses them; :meth:`Record.given` reads them.
DERIVED = frozenset({"sector_size"})


class Compared(StrEnum):
    """How the sources are compared on a value (:data:`COMPARED`)."""

    #: The values must be equal.
    EQUAL = "equal"
    #: Each role of the value is compared on its own, and a source that
    #: does not give a role does not vote on it: a layout giving only TB
    #: agrees with a fuller one that has the same TB.
    PER_ROLE = "per-role"


#: The values the sources are compared on, and how: what
#: :attr:`Flash.conflicts`, :meth:`Flash.by_ext_id`, the command's
#: description and the data issues checks read. A record's own value is
#: :meth:`Record.given`'s.
COMPARED: dict[str, Compared] = {
    "size": Compared.EQUAL,
    "page_size": Compared.EQUAL,
    "sector_size": Compared.EQUAL,
    "voltage": Compared.EQUAL,
    "quad_enable": Compared.EQUAL,
    "quad_enable_requirement": Compared.EQUAL,
    "protection": Compared.PER_ROLE,
}

#: :data:`COMPARED`, each value as it is compared: a value compared per
#: role is one name per role (``"protection.tb"``), which
#: :meth:`Record.given` and :meth:`Flash.value` read.
COMPARED_VALUES: tuple[str, ...] = tuple(
    n
    for name, how in COMPARED.items()
    for n in ((name,) if how is Compared.EQUAL else tuple(f"{name}.{r}" for r in ROLES))
)


@dataclass(frozen=True)
class Record:
    """One entry of one upstream's flash table, as that upstream has it.

    The fields made from arguments are what the entry states, as the data
    stores them; :attr:`size`, :attr:`page_size`, :attr:`erasers`,
    :attr:`features`, :attr:`opcodes`, :attr:`sector_size`,
    :attr:`quad_enable_requirement` and :attr:`quad_enable` are worked
    out from them and from its SFDP tables (:mod:`spiflash.derive`). See
    :mod:`spiflash_extract.record` for what each field means."""

    source: Source
    file: str
    line: int
    type: FlashType
    vendor: str | None
    name: str
    id: bytes | None
    ext_id: bytes | None
    id_method: IdMethod | None
    #: The size the entry states, where it differs from its SFDP tables'
    #: (or it has none).
    size_claim: int | None
    #: The page size the entry states, likewise.
    page_size_claim: int | None
    #: The erasers the entry states, but those its SFDP tables give.
    eraser_claims: tuple[Eraser, ...]
    #: The capabilities the entry states.
    feature_claims: frozenset[Feature]
    flags: tuple[str, ...]
    voltage: Voltage | None
    #: The operations the entry states.
    opcode_claims: tuple[OpcodeUse, ...]
    tested: str | None
    notes: tuple[str, ...]
    #: The part's SFDP area, where the upstream carries a dump of it.
    sfdp: bytes | None = field(default=None, repr=False)
    #: Which upstream token gave a stored value that has no other
    #: provenance: ``{"feature:qpi": "QPIEnable"}`` (see
    #: :mod:`spiflash_extract.record` for the keys).
    via: Mapping[str, str] = field(default_factory=dict, hash=False, repr=False)
    #: The part's SFDP parameter tables by id (``0xff00``, the BFPT), where
    #: the upstream copies them without the area around them (Zephyr's
    #: ``sfdp-bfp``). A record has this or :attr:`sfdp`, not both.
    sfdp_tables: Mapping[int, bytes] = field(default_factory=dict, hash=False, repr=False)
    #: Where the entry says the quad enable bit is, or that the part has
    #: none (:data:`~spiflash.registers.QE_NONE`). A record states this or
    #: :attr:`quad_enable_requirement_claim`, not both.
    quad_enable_claim: RegisterBit | NoQuadEnable | None = None
    #: The quad enable requirement (JESD216's code) the entry states, where
    #: it differs from its SFDP tables' (or it has none).
    quad_enable_requirement_claim: QuadEnableRequirement | None = None
    #: Where the entry says the part's block-protection bits are.
    protection: Protection | None = None
    #: The size: :attr:`size_claim`, or failing that its SFDP tables' density.
    size: int | None = field(init=False, compare=False, repr=False)
    #: The page size: :attr:`page_size_claim`, or failing that its SFDP tables'.
    page_size: int | None = field(init=False, compare=False, repr=False)
    #: Every eraser: :attr:`eraser_claims`, and those its SFDP tables give.
    erasers: tuple[Eraser, ...] = field(init=False, compare=False, repr=False)
    #: Every capability: :attr:`feature_claims`, and what the other fields
    #: imply (:func:`spiflash.derive.features`): its operations other than
    #: driver defaults, its block erasers, its size and its SFDP tables.
    features: frozenset[Feature] = field(init=False, compare=False, repr=False)
    #: Every operation: :attr:`opcode_claims`, and the ``implied`` ones the
    #: other fields give (:func:`spiflash.derive.opcodes`), by kind; a claimed
    #: use comes before an implied one of the same operation.
    opcodes: tuple[OpcodeUse, ...] = field(init=False, compare=False, repr=False)
    #: The erase block the part is usually erased by, from its erasers
    #: (:func:`spiflash.derive.sector_size`): a SPI NOR part's 0xd8 block
    #: (failing that its 0xdc, then its 0x52 block), a SPI NAND part's block.
    sector_size: int | None = field(init=False, compare=False, repr=False)
    #: The quad enable requirement: :attr:`quad_enable_requirement_claim`,
    #: or failing that its SFDP tables' (BFPT DW15).
    quad_enable_requirement: QuadEnableRequirement | None = field(
        init=False, compare=False, repr=False
    )
    #: Where the quad enable bit is: :attr:`quad_enable_claim`, or failing
    #: that where :attr:`quad_enable_requirement` puts it.
    quad_enable: RegisterBit | NoQuadEnable | None = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "via", MappingProxyType(dict(self.via)))
        object.__setattr__(self, "sfdp_tables", MappingProxyType(dict(self.sfdp_tables)))
        if self.quad_enable_claim is not None and self.quad_enable_requirement_claim is not None:
            msg = f"{self.source} {self.name}: a quad enable bit and a requirement, not one"
            raise ValueError(msg)
        facts = self.sfdp_facts
        size, page = self.size_claim, self.page_size_claim
        qer = self.quad_enable_requirement_claim
        erasers = self.eraser_claims
        if facts is not None:
            size = facts.size if size is None else size
            page = facts.page_size if page is None else page
            qer = facts.quad_enable_requirement if qer is None else qer
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "page_size", page)
        object.__setattr__(self, "quad_enable_requirement", qer)
        qe = self.quad_enable_claim
        object.__setattr__(self, "quad_enable", qer.bit if qe is None and qer else qe)
        erasers += tuple(e for e in self.sfdp_erasers if e not in erasers)
        object.__setattr__(self, "erasers", erasers)
        features = self.feature_claims | derive.features(self)
        object.__setattr__(self, "features", features)
        uses = (*self.opcode_claims, *derive.opcodes(self))
        object.__setattr__(self, "opcodes", tuple(sorted(uses, key=_use_order)))
        object.__setattr__(self, "sector_size", derive.sector_size(self))

    def __getstate__(self) -> dict[str, Any]:
        # A mapping proxy does not pickle; the dict it wraps does.
        return {**self.__dict__, "via": dict(self.via), "sfdp_tables": dict(self.sfdp_tables)}

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(
            state,
            via=MappingProxyType(state["via"]),
            sfdp_tables=MappingProxyType(state["sfdp_tables"]),
        )

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Record:
        return cls(
            source=Source(d["source"]),
            file=d["file"],
            line=d["line"],
            type=FlashType(d["type"]),
            vendor=d["vendor"],
            name=d["name"],
            id=bytes.fromhex(d["id"]) if d["id"] else None,
            ext_id=bytes.fromhex(d["ext_id"]) if d["ext_id"] else None,
            id_method=IdMethod(d["id_method"]) if d["id_method"] else None,
            size_claim=d["size"],
            page_size_claim=d["page_size"],
            eraser_claims=tuple(Eraser.from_json(e) for e in d["erasers"] or ()),
            feature_claims=frozenset(Feature(f) for f in d["features"]),
            flags=tuple(d["flags"]),
            voltage=Voltage(*d["voltage"]) if d["voltage"] else None,
            opcode_claims=tuple(
                OpcodeUse(
                    o["op"],
                    o["via"],
                    assumed=o.get("assumed", False),
                    dummy_clocks=o.get("dummy_clocks"),
                )
                for o in d["opcodes"]
            ),
            tested=d["tested"],
            notes=tuple(d["notes"]),
            sfdp=bytes.fromhex(d["sfdp"]) if d.get("sfdp") else None,
            via=d.get("via") or {},
            sfdp_tables={
                int(k, 16): bytes.fromhex(v) for k, v in (d.get("sfdp_tables") or {}).items()
            },
            quad_enable_claim=quad_enable_from_json(d.get("quad_enable")),
            quad_enable_requirement_claim=QuadEnableRequirement(d["quad_enable_requirement"])
            if d.get("quad_enable_requirement")
            else None,
            protection=Protection.from_json(d["protection"]) if d.get("protection") else None,
        )

    def to_json(self) -> dict[str, Any]:
        """The record as the data stores it: what it states
        (:meth:`stored`), in :data:`spiflash_extract.record.KEYS` order;
        :meth:`from_json` reads it back."""
        return {
            "source": str(self.source),
            "file": self.file,
            "line": self.line,
            "type": str(self.type),
            "vendor": self.vendor,
            "name": self.name,
            "id": self.id.hex() if self.id else None,
            "ext_id": self.ext_id.hex() if self.ext_id else None,
            "id_method": str(self.id_method) if self.id_method else None,
            "size": self.stored("size"),
            "page_size": self.stored("page_size"),
            "erasers": [e.to_json() for e in self.stored("erasers")] or None,
            "features": sorted(self.stored("features")),
            "flags": list(self.flags),
            "via": dict(self.via),
            "voltage": list(self.voltage) if self.voltage else None,
            "quad_enable": quad_enable_to_json(self.stored("quad_enable")),
            "quad_enable_requirement": _str_or_none(self.stored("quad_enable_requirement")),
            "protection": self.protection.to_json() if self.protection else None,
            "opcodes": [
                {
                    "op": u.op,
                    "via": u.via,
                    **({"assumed": True} if u.assumed else {}),
                    **({"dummy_clocks": u.dummy_clocks} if u.dummy_clocks is not None else {}),
                }
                for u in self.stored("opcodes")
            ],
            "sfdp": self.sfdp.hex() if self.sfdp else None,
            "sfdp_tables": {f"{k:04x}": v.hex() for k, v in sorted(self.sfdp_tables.items())},
            "tested": self.tested,
            "notes": list(self.notes),
        }

    def stored(self, name: str) -> Any:
        """What the entry states for field ``name``, without what is derived
        (:data:`CLAIMS`): ``record.stored("features")`` is its
        :attr:`feature_claims`, ``record.stored("size")`` its :attr:`size`.
        ``KeyError`` for a field it only derives (:data:`DERIVED`)."""
        if name in DERIVED:
            msg = f"{name} is derived, not stored"
            raise KeyError(msg)
        return getattr(self, CLAIMS.get(name, name))

    def given(self, name: str) -> Any:
        """The record's own value for field ``name``, as the sources are
        compared on it: what it stores (:meth:`stored`); for a field it
        only derives (:data:`DERIVED`: ``sector_size``), what its stored
        fields give; for those of :data:`SFDP_VALUES` (``size``,
        ``page_size``, and the quad enable bit and requirement), what it
        states or, where it states none, what its SFDP tables say. A role
        of its block protection is ``"protection.<role>"``
        (``"protection.tb"``)."""
        field_name, _, role = name.partition(".")
        if role:
            if field_name != "protection" or role not in ROLES:
                msg = f"no such value: {name}"
                raise KeyError(msg)
            return getattr(self.protection, role) if self.protection else None
        if name in DERIVED or name in SFDP_VALUES:
            return getattr(self, name)
        return self.stored(name)

    def feature_reasons(self) -> dict[Feature, str]:
        """Each capability in :attr:`features`, and why the entry gives it
        (:func:`spiflash.derive.feature_reasons`)."""
        return derive.feature_reasons(self)

    def opcode_reasons(self) -> dict[str, tuple[OpcodeUse, ...]]:
        """Each operation in :attr:`opcodes`, and the uses giving it."""
        out: dict[str, tuple[OpcodeUse, ...]] = {}
        for use in self.opcodes:
            out[use.op] = (*out.get(use.op, ()), use)
        return out

    @cached_property
    def parsed_sfdp(self) -> Sfdp | None:
        """The entry's SFDP dump (:attr:`sfdp`) or tables
        (:attr:`sfdp_tables`, a :attr:`~spiflash.sfdp.Sfdp.partial` one),
        decoded (see :mod:`spiflash.sfdp`); ``None`` when the upstream has
        neither for it."""
        if self.sfdp:
            return parse_sfdp(self.sfdp)
        if self.sfdp_tables:
            return from_tables(self.sfdp_tables)
        return None

    @cached_property
    def sfdp_facts(self) -> SfdpFacts | None:
        """What its SFDP tables say, in a record's terms
        (:meth:`Sfdp.facts <spiflash.sfdp.Sfdp.facts>`); ``None`` without."""
        parsed = self.parsed_sfdp
        return parsed.facts() if parsed is not None else None

    @property
    def sfdp_erasers(self) -> tuple[Eraser, ...]:
        """The erasers its SFDP tables give, over the record's own
        :attr:`size`: where the entry states a size the tables contradict
        (Zephyr's P25Q16H, 2 MiB, carrying a 16 MiB part's BFPT), the
        record's size is the stated one, and each erase type's blocks are
        counted over it. An erase type whose block does not divide that size
        gives none."""
        facts = self.sfdp_facts
        if facts is None:
            return ()
        if self.size is None or self.size == facts.size:
            return facts.erasers
        out = []
        for e in facts.erasers:
            (block,) = e.blocks
            if self.size % block.size == 0:
                out.append(replace(e, blocks=(EraseBlock(block.size, self.size // block.size),)))
        return tuple(out)

    def sfdp_disagreements(self) -> tuple[SfdpDisagreement, ...]:
        """The values the entry states that its own SFDP tables give
        otherwise: its size, page size, quad enable requirement or quad
        enable bit (the stated one is the record's value, as the upstream's
        own code uses it), or an eraser whose
        opcode the tables give with other blocks (over the record's size:
        :attr:`sfdp_erasers`)."""
        facts = self.sfdp_facts
        if facts is None:
            return ()
        out = []
        for name in SFDP_VALUES:
            stated, said = self.stored(name), getattr(facts, name)
            if stated is not None and said is not None and stated != said:
                out.append(SfdpDisagreement(name, stated, said))
        out.extend(
            SfdpDisagreement("erasers", e, f)
            for e in self.stored("erasers")
            for f in self.sfdp_erasers
            if e.opcode is not None and f.opcode == e.opcode and f.blocks != e.blocks
        )
        return tuple(out)

    def shared_bits(self) -> dict[str, tuple[str, ...]]:
        """The bits its quad enable bit and its protection roles share
        (``{"SR1 bit 6": ("tb", "quad_enable")}``): a layout no part has.
        Empty for every record the data holds."""
        return shared_bits(register_bits(self.quad_enable, self.protection))

    @property
    def manufacturer(self) -> str | None:
        """The vendor, spelled one way across all upstreams."""
        return canonical(self.vendor)

    @property
    def id_hex(self) -> str | None:
        return self.id.hex() if self.id else None

    @property
    def is_jedec(self) -> bool:
        """Whether ``id`` is what the chip answers to a JEDEC read-id (0x9F),
        rather than to a legacy command (REMS 0x90, RES 0xAB, ...)."""
        return self.id_method is not None and self.id_method.family is IdFamily.JEDEC

    @property
    def url(self) -> str:
        """Where in the upstream tree the entry is: the path and line, or
        for a binary file the entry's number in it."""
        if self.source in BINARY_SOURCES:
            return f"{self.file} entry {self.line}"
        return f"{self.file}:{self.line}"

    @cached_property
    def part_names(self) -> tuple[str, ...]:
        """The part numbers the entry's name stands for (see :func:`part_names`)."""
        return part_names(self.name)


def _str_or_none(value: object) -> str | None:
    return None if value is None else str(value)


def plain_value(value: Any) -> Any:
    """A compared value (:data:`COMPARED_VALUES`) as plain JSON: a voltage
    as its ``[min, max]``, a register bit as its :meth:`RegisterBit.to_json
    <spiflash.registers.RegisterBit.to_json>`, ``QE_NONE`` as ``"none"``."""
    if isinstance(value, RegisterBit | NoQuadEnable):
        return quad_enable_to_json(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, StrEnum):
        return str(value)
    return value


def register_bits(
    quad_enable: RegisterBit | NoQuadEnable | None, protection: Protection | None
) -> dict[str, RegisterBit]:
    """The register bits of a quad enable and a protection layout, by role
    (``"quad_enable"``, ``"bp0"``, ...)."""
    out = dict(protection.roles()) if protection else {}
    if isinstance(quad_enable, RegisterBit):
        out["quad_enable"] = quad_enable
    return out


def _use_order(use: OpcodeUse) -> tuple[tuple[int, int], bool]:
    """Operations by kind, as :func:`~spiflash.opcodes.sort_key` orders
    them; a claimed use before an implied one."""
    return sort_key(use.op), use.implied


# A part number and a group of suffixes: "S25FL032(A/P)", "EN25Q32(/A/B)".
_SUFFIXES = re.compile(r"(\w+)\(([\w]*(?:/[\w]*)+)\)")


def part_names(name: str) -> tuple[str, ...]:
    """The part numbers an upstream name stands for, upper case.

    ``"w25q128fv/jv"`` (OpenOCD's shorthand) is W25Q128FV and W25Q128JV;
    ``"S25FL064P / EPCS64"`` is both; ``"S25FL128S_UL Uniform 128 kB Sectors"``
    is S25FL128S_UL. flashrom's ``.`` wildcards (``"W25Q128.V"``) are kept:
    :func:`name_matches` understands them. Handling of the ``/`` forms follows
    LiteSPI's spi_nor_config_generator; flashrom's suffix groups
    (``"S25FL032(A/P)"``, ``"EN25Q32(/A/B)"``, an empty one meaning the bare
    name) are spelled out first."""
    name = _SUFFIXES.sub(
        lambda m: "/".join(m[1] + s for s in m[2].split("/")),
        name,
    )
    out: list[str] = []
    for alt in (a.strip() for a in name.split("/")):
        if not alt:
            continue
        # Drop what follows the part number: "Uniform 128 kB Sectors", "(EDI)".
        tok = alt.split()[0].split("(")[0]
        if not tok:
            continue
        # "w25q128fv/jv": a second half that is not itself a part number
        # (letters then two digits) replaces the tail of the first.
        if out and len(tok) < len(out[0]) and not re.match(r"[a-z]{1,5}\d{2}", tok, re.IGNORECASE):
            tok = out[0][: len(out[0]) - len(tok)] + tok
        out.append(tok.upper())
    return tuple(dict.fromkeys(out))


def name_matches(pattern: str, query: str, *, prefix: bool = False) -> bool:
    """Whether part name ``pattern`` (with flashrom's ``.`` wildcards) is the
    part ``query``, ignoring case. With ``prefix``, ``query`` may run on past
    the pattern, as an order code does (``S25FL128S......0`` covers
    ``S25FL128SAGMFI001``: the extra characters are package and grade)."""
    rx = "".join("." if c == "." else re.escape(c) for c in pattern.upper())
    match = re.match if prefix else re.fullmatch
    return match(rx, query.upper()) is not None


def same_part(a: str, b: str) -> bool:
    """Whether part names ``a`` and ``b`` name one part: they agree up to
    the end of the shorter, so one may add a suffix (ZB35Q01B, ZB35Q01BYIG:
    package and grade), and flashrom's ``.`` and a datasheet's ``XX``
    placeholder match any character (GD5F1GQ5REXXG and GD5F1GQ5REYIG;
    S25FL128S......0 and S25FL128S_UL). W25Q64.W (1.8 V) is not the W25Q64FV,
    nor B.25D80A the BY25Q80BS."""
    a, b = a.upper().replace("XX", ".."), b.upper().replace("XX", "..")
    return all(x == y or "." in (x, y) for x, y in zip(a, b, strict=False))


#: What :func:`name_distance` charges: an edit (a character changed, added,
#: dropped, or two neighbours swapped), and each character one name has past
#: the end of the other (a suffix: package, temperature, ordering code).
EDIT_COST = 4
TAIL_COST = 1


def _spelling(name: str) -> str:
    """A part name without its hyphens, underscores and spaces, which
    sources write differently (``CS11G1-T0A0AA``, ``CS11G1T0A0AA``)."""
    return re.sub(r"[-_ ]", "", name)


def squash_name(name: str, *, wildcards: bool = False) -> str:
    """A part name as :func:`name_distance` compares it: upper case, letters
    and digits only (``"W25Q16JV-IM"`` is ``W25Q16JVIM``); with
    ``wildcards``, flashrom's ``.`` stays."""
    return re.sub(r"[^0-9A-Z.]" if wildcards else r"[^0-9A-Z]", "", name.upper())


def name_distance(query: str, name: str) -> tuple[int, int]:
    """How far a database part name is from ``query``: ``(cost, common)``,
    ``common`` being how many leading characters they share.

    Both are compared as :func:`squash_name` writes them, a ``.`` in ``name``
    matching any character. The cost is an edit distance where an edit costs
    :data:`EDIT_COST` and a character past the end of the other name costs
    :data:`TAIL_COST`. Part numbers go from the general to the specific
    (vendor, family, density, variant, then package and grade), so a
    difference at the end costs least: ``W25Q128JVSIQ`` is 3 from
    ``W25Q128JV``, 5 from ``W25Q128``, and 7 from ``W25Q128JW`` and
    ``W25Q128FV`` (an edit and a tail of three). A typo is one edit:
    ``W25Q182JV`` is 4 from ``W25Q128JV``."""
    a, b = squash_name(query), squash_name(name, wildcards=True)

    def same(i: int, j: int) -> bool:
        return b[j] in (".", a[i])

    # d[i][j]: the cheapest edits turning a[:i] into b[:j] (optimal string
    # alignment, so a swap of neighbours is one edit).
    d = [[j * EDIT_COST for j in range(len(b) + 1)]]
    for i in range(1, len(a) + 1):
        row = [i * EDIT_COST]
        for j in range(1, len(b) + 1):
            cost = min(
                d[i - 1][j - 1] + (0 if same(i - 1, j - 1) else EDIT_COST),
                d[i - 1][j] + EDIT_COST,
                row[j - 1] + EDIT_COST,
            )
            if i > 1 and j > 1 and same(i - 1, j - 2) and same(i - 2, j - 1):
                cost = min(cost, d[i - 2][j - 2] + EDIT_COST)
            row.append(cost)
        d.append(row)
    # What one has past the end of the other is a tail, not edits.
    cost = min(
        *(d[i][len(b)] + (len(a) - i) * TAIL_COST for i in range(len(a) + 1)),
        *(d[len(a)][j] + (len(b) - j) * TAIL_COST for j in range(len(b) + 1)),
    )
    common = 0
    while common < min(len(a), len(b)) and same(common, common):
        common += 1
    return cost, common


def _consensus(values: Iterable[tuple[T | None, Source]]) -> T | None:
    """The value the most sources give; on a tie, the one the
    higher-priority sources give, then the one more records give.

    Sources are counted, not records: a source listing a part five times
    does not outvote five sources listing it once, and a source giving two
    values counts for each."""
    sources: dict[T, set[int]] = {}
    records: Counter[T] = Counter()
    for value, source in values:
        if value is not None:
            sources.setdefault(value, set()).add(source.priority)
            records[value] += 1
    if not sources:
        return None
    return min(sources, key=lambda v: (-len(sources[v]), sorted(sources[v]), -records[v]))


@dataclass(frozen=True)
class Flash:
    """Everything the upstreams say about one chip id.

    Several parts can answer the same id (W25Q128BV, FV and JV all answer
    ``ef4018``), so a :class:`Flash` holds every :class:`Record` for its id,
    and :attr:`names` lists every part they name. Single values
    (:attr:`size`, :attr:`page_size`, ...) are what most sources agree on;
    :meth:`values` shows who says what."""

    id: bytes
    type: FlashType
    records: tuple[Record, ...] = field(repr=False)
    bank: int = 0
    #: Which command the id answers: JEDEC read-id (0x9F), or a legacy one.
    family: IdFamily = IdFamily.JEDEC
    #: Datasheets for the id's parts, the best first (see :meth:`Datasheet.rank`).
    datasheets: tuple[Datasheet, ...] = field(default=(), repr=False, compare=False)
    #: The manufacturer, where no record names one, inferred from the other
    #: sources' parts (see :func:`~spiflash.db.infer_manufacturer`).
    inferred_manufacturer: str | None = field(default=None, repr=False, compare=False)
    #: For a chip narrowed by extended id (:meth:`with_ext_id`): its records
    #: from the most specific to the least, each value taken from the first
    #: layer that gives one. Empty: all the records are one layer.
    layers: tuple[tuple[Record, ...], ...] = field(default=(), repr=False, compare=False)

    def _value(self, get: Callable[[Record], T | None]) -> T | None:
        """The value the sources agree on, from the most specific records
        that give one (:attr:`layers`)."""
        for layer in self.layers or (self.records,):
            found = _consensus((get(r), r.source) for r in layer)
            if found is not None:
                return found
        return None

    @property
    def key(self) -> str:
        """The id as the command line and the datasheet list write it:
        ``jedec_id``, or ``family:id`` for a legacy id (``rems:bf48``)."""
        return self.jedec_id if self.family == IdFamily.JEDEC else f"{self.family}:{self.id_hex}"

    @property
    def id_hex(self) -> str:
        return self.id.hex()

    @cached_property
    def ids(self) -> tuple[bytes, ...]:
        """Every id the records give, longest first: :attr:`id`, and the
        shorter ids of sources that match fewer bytes of a SPI NAND part
        (Rockchip's ``c226`` for the MX35LF2GE4AD's ``c22603``)."""
        found = {strip_continuation(r.id)[1] for r in self.records if r.id is not None}
        return tuple(sorted(found | {self.id}, key=lambda i: (-len(i), i)))

    @property
    def keys(self) -> tuple[str, ...]:
        """:attr:`key` for each of :attr:`ids`."""
        prefix = "7f" * self.bank if self.family == IdFamily.JEDEC else f"{self.family}:"
        return tuple(prefix + i.hex() for i in self.ids)

    def confirms(self, sheet: Datasheet) -> bool:
        """Whether ``sheet`` gives the bytes of one of this chip's :attr:`ids`."""
        return any(k in sheet.confirmed for k in self.keys)

    @property
    def jedec_id(self) -> str:
        """The id with its JEP106 continuation codes, as a chip in a later
        bank should send it (``7f1c7018`` for an Eon part)."""
        return "7f" * self.bank + self.id.hex()

    @property
    def manufacturer_id(self) -> int:
        return self.id[0]

    @cached_property
    def manufacturer(self) -> str | None:
        """The one the sources name, or failing that the one inferred
        (:attr:`manufacturer_inferred`)."""
        named = _consensus((r.manufacturer, r.source) for r in self.records)
        return named or self.inferred_manufacturer

    @property
    def manufacturer_inferred(self) -> bool:
        """Whether no source names the manufacturer, and it is inferred from
        the id and the part name."""
        return self.inferred_manufacturer is not None and not any(
            r.manufacturer for r in self.records
        )

    @cached_property
    def names(self) -> tuple[str, ...]:
        """Every part name the sources give, the chip's own part first: a
        part name rather than a pattern (flashrom's wildcards, ``W25Q16.V``,
        or the vendor-and-id name Linux gives an entry it does not name,
        ``SPANSION-345B19``); one a record of the chip's :attr:`manufacturer`
        gives (not a rebrand's, like Spansion's S25FL016K on a Winbond id);
        then the one the most sources give, the higher-priority sources, the
        most records (as :func:`_consensus` ranks values), and the one
        listed first. Names that differ only in hyphens, underscores and
        spaces (``CS11G1-T0A0AA``, ``CS11G1T0A0AA``) vote together, and the
        spelling shown is the higher-priority source's."""
        sources: dict[str, set[int]] = {}
        votes: dict[str, set[int]] = {}
        records: Counter[str] = Counter()
        first: dict[str, int] = {}
        own: set[str] = set()
        for i, r in enumerate(self.records):
            for n in r.part_names:
                sources.setdefault(n, set()).add(r.source.priority)
                votes.setdefault(_spelling(n), set()).add(r.source.priority)
                records[_spelling(n)] += 1
                first.setdefault(n, i)
                if r.manufacturer == self.manufacturer:
                    own.add(_spelling(n))
        made_up = f"-{self.id_hex.upper()}"

        def rank(n: str) -> tuple[bool, bool, int, list[int], int, list[int], int]:
            part = _spelling(n)
            return (
                "." in n or n.endswith(made_up),
                part not in own,
                -len(votes[part]),
                sorted(votes[part]),
                -records[part],
                sorted(sources[n]),
                first[n],
            )

        return tuple(sorted(sources, key=rank))

    @property
    def name(self) -> str:
        return self.names[0]

    @cached_property
    def sources(self) -> tuple[Source, ...]:
        return tuple(sorted({r.source for r in self.records}, key=lambda s: s.priority))

    @cached_property
    def size(self) -> int | None:
        return self._value(lambda r: r.size)

    @cached_property
    def page_size(self) -> int | None:
        return self._value(lambda r: r.page_size)

    @cached_property
    def sector_size(self) -> int | None:
        return self._value(lambda r: r.sector_size)

    @cached_property
    def voltage(self) -> Voltage | None:
        return self._value(lambda r: r.voltage)

    @cached_property
    def quad_enable(self) -> RegisterBit | NoQuadEnable | None:
        """Where the quad enable bit is (:data:`~spiflash.registers.QE_NONE`:
        the part has none), as most sources say: each record's own, stated
        or from its quad enable requirement (:attr:`Record.quad_enable`)."""
        # mypy joins the two types to object; the value is one of them.
        return cast("RegisterBit | NoQuadEnable | None", self._value(lambda r: r.quad_enable))

    @cached_property
    def quad_enable_requirement(self) -> QuadEnableRequirement | None:
        """The quad enable requirement (JESD216's code) most sources give,
        of those that put the QE bit where :attr:`quad_enable` says: a
        requirement putting it elsewhere is not this part's (and the
        sources disagree on the bit; ``None`` where none agrees)."""
        qe = self.quad_enable
        return self._value(
            lambda r: q if (q := r.quad_enable_requirement) and q.bit == qe else None
        )

    @cached_property
    def _protection_roles(self) -> dict[str, RegisterBit]:
        """Each protection role, as most of the sources giving that role
        say (a source not giving a role does not vote on it)."""
        out = {}
        for role in ROLES:

            def given(r: Record, role: str = role) -> RegisterBit | None:
                bit: RegisterBit | None = r.given(f"protection.{role}")
                return bit

            if (bit := self._value(given)) is not None:
                out[role] = bit
        return out

    @cached_property
    def protection(self) -> Protection | None:
        """Where the block-protection bits are: each role as most of the
        sources giving it say. Where that puts two roles on one bit
        (:meth:`shared_bits`), a layout no part has, the best source's own
        layout instead."""
        roles = self._protection_roles
        if not roles:
            return None
        if shared_bits(roles):
            best = min(
                (r for r in self.records if r.protection),
                key=lambda r: r.source.priority,
            )
            return best.protection
        return Protection(**roles)

    def shared_bits(self) -> dict[str, tuple[str, ...]]:
        """The bits that the sources' answers, role by role, put two roles
        on: two protection roles (``{"SR1 bit 5": ("bp3", "tb")}``), or a
        protection role and the quad enable bit. A part has no such
        layout, so one source is wrong."""
        return shared_bits(register_bits(self.quad_enable, None) | self._protection_roles)

    def value(self, name: str) -> Any:
        """The chip's value of ``name``, one of :data:`COMPARED_VALUES`:
        ``flash.value("size")`` is :attr:`size`, ``flash.value("protection.tb")``
        the ``tb`` of :attr:`protection`."""
        field_name, _, role = name.partition(".")
        if role:
            return getattr(self.protection, role) if self.protection else None
        return getattr(self, field_name)

    @cached_property
    def features(self) -> frozenset[Feature]:
        """Every capability any source claims for this id, or implies by
        the operations, erasers, size or SFDP tables it gives
        (:attr:`Record.features`; a driver default implies nothing). Parts
        sharing an id can differ (a W25Q128BV has no QPI, a W25Q128FV does),
        so check :meth:`feature_sources` before relying on one."""
        return frozenset().union(*(r.features for r in self.records))

    @cached_property
    def opcodes(self) -> dict[str, SupportedOperation]:
        """Every operation any source says the chip has, by name, in the
        order id, read, program, erase, register, mode. Parts sharing an id
        can differ, and some sources only list what their own driver uses,
        so :attr:`SupportedOperation.sources` says who vouches for each.
        A source that states an operation is not also listed as implying
        it, and one that states or implies it for the part is not also
        listed as assuming it (its driver's default)."""
        because: dict[str, list[Claim]] = {}
        for r in sorted(self.records, key=lambda r: r.source.priority):
            for use in r.opcodes:
                claim = Claim(r.source, use.via, use.implied, use.assumed)
                if claim not in because.setdefault(use.op, []):
                    because[use.op].append(claim)
        out = {}
        for name in sorted(because, key=sort_key):
            stating = {c.source for c in because[name] if not c.implied}
            per_part = {c.source for c in because[name] if not c.assumed}
            claims = (
                c
                for c in because[name]
                if not (c.implied and c.source in stating)
                and not (c.assumed and c.source in per_part)
            )
            out[name] = SupportedOperation(OPERATIONS[name], tuple(claims))
        return out

    @cached_property
    def sfdp_dumps(self) -> tuple[SfdpDump, ...]:
        """Every distinct SFDP dump the sources carry for this id, decoded,
        each with the records carrying it: the whole dumps
        (:attr:`Record.sfdp`), then the tables copied without the area around
        them (:attr:`Record.sfdp_tables`), each by the best source carrying
        it. What a dump says is what one part answered, and parts sharing
        an id can answer differently: QEMU has one dump for the MX25L25635E
        and another for the MX25L25635F, both ``c22019``."""
        carrying: dict[Any, list[Record]] = {}
        for r in sorted(self.records, key=lambda r: (not r.sfdp, r.source.priority)):
            if r.sfdp:
                carrying.setdefault(r.sfdp, []).append(r)
            elif r.sfdp_tables:
                carrying.setdefault(tuple(sorted(r.sfdp_tables.items())), []).append(r)
        return tuple(
            SfdpDump(parsed, tuple(rs))
            for rs in carrying.values()
            if (parsed := rs[0].parsed_sfdp) is not None
        )

    @property
    def sfdp(self) -> Sfdp | None:
        """The chip's SFDP tables, decoded: the first of :attr:`sfdp_dumps`,
        a whole dump from the highest-priority source that carries one, else
        the first partial one (:attr:`sfdp_source`); ``None`` when no source
        carries any."""
        return self.sfdp_dumps[0].sfdp if self.sfdp_dumps else None

    @property
    def sfdp_source(self) -> Source | None:
        """Which source :attr:`sfdp` comes from."""
        return self.sfdp_dumps[0].source if self.sfdp_dumps else None

    def supports(self, operation: str) -> bool:
        """Whether any source says the chip has ``operation`` (``"READ_1_1_4"``)."""
        return operation in self.opcodes

    def feature_sources(self, feature: Feature | str) -> tuple[FeatureSource, ...]:
        """The sources giving ``feature``, one each, in source priority
        order: whether the source claims it or only implies it
        (:attr:`FeatureSource.implied`; a claim in any of its records wins),
        and why. ``[s.source for s in flash.feature_sources("qpi")]`` is
        the sources alone."""
        found: dict[Source, FeatureSource] = {}
        for r in sorted(self.records, key=lambda r: r.source.priority):
            if feature not in r.features:
                continue
            implied = feature not in r.feature_claims
            given = FeatureSource(r.source, implied, r.feature_reasons()[Feature(feature)])
            if r.source not in found or (found[r.source].implied and not given.implied):
                found[r.source] = given
        return tuple(found.values())

    def values(self, attribute: str) -> dict[Any, tuple[Source, ...]]:
        """Each value the records give for an attribute
        (:meth:`Record.given`: what they store, or for ``sector_size`` what
        their erasers give), and the sources giving it:
        ``flash.values("size")`` → ``{16777216: ("flashrom", "linux", ...)}``."""
        out: dict[Any, set[Source]] = {}
        for r in self.records:
            v = r.given(attribute)
            if v is not None:
                out.setdefault(v, set()).add(r.source)
        return {k: tuple(sorted(v, key=lambda s: s.priority)) for k, v in out.items()}

    @property
    def conflicts(self) -> dict[str, dict[Any, tuple[Source, ...]]]:
        """The values (:data:`COMPARED_VALUES`) the sources disagree on,
        for one part: records that extended ids tell apart
        (:attr:`variants`) are not compared."""
        out = {}
        for attr in COMPARED_VALUES:
            if any(len({r.given(attr) for r in v} - {None}) > 1 for v in self.variants):
                out[attr] = self.values(attr)
        return out

    def by_ext_id(self, attribute: str) -> dict[bytes, Any]:
        """Where parts that extended ids tell apart differ on ``attribute``
        (``"size"``, ``"page_size"``, ...): each extended id's value, as
        :meth:`with_ext_id` narrows the chip; ``{}`` where they agree. The
        GD5F1GQ5REYIG (``c8``) is 128 MiB and the F50L2G41KA (``7f``)
        256 MiB, both at ``c8 41``. The extended ids are those of
        :attr:`variants`: a shorter one that starts longer ones is not a
        part of its own. (A lookup narrows through
        :meth:`Database.narrow <spiflash.db.Database.narrow>`, which only adds
        the maker and the datasheets to these values.)"""
        values = {e: self.with_ext_id(e).value(attribute) for e in self._part_ext_ids}
        return values if len(set(values.values()) - {None}) > 1 else {}

    @cached_property
    def _part_ext_ids(self) -> tuple[bytes, ...]:
        """The extended ids that each stand for one part: all of them but a
        shorter one that starts a longer one (4d 00, of 4d 00 80 and
        4d 00 81), which covers several."""
        given = {r.ext_id for r in self.records if r.ext_id}
        return tuple(sorted(e for e in given if not any(o != e and o.startswith(e) for o in given)))

    @cached_property
    def variants(self) -> tuple[tuple[Record, ...], ...]:
        """The records, in groups that each describe one part: all of them,
        where no record has an extended id; otherwise those with none, and
        for each extended id what :meth:`with_ext_id` keeps for it."""
        if not self._part_ext_ids:
            return (self.records,)
        groups = [tuple(r for r in self.records if r.ext_id is None)]
        groups += [self.with_ext_id(e).records for e in self._part_ext_ids]
        return tuple(dict.fromkeys(g for g in groups if g))

    def with_ext_id(self, ext: bytes) -> Flash:
        """This id narrowed by the bytes a chip sends after it: the records
        whose extended id agrees with ``ext``, and those with none, which
        cover every variant, except those naming only a part that another
        extended id belongs to (:func:`same_part`). Two parts can share an
        id and differ only after it: the GD5F1GQ5REYIG answers ``c8 41``
        then ``c8``, the F50L2G41KA ``c8 41`` then ``7f``, so a lookup of
        ``c8 41 7f`` leaves out the GD5F1GQ5REXXG the other sources list at
        ``c8 41``. Where no extended id agrees, the records with none.

        A record whose extended id agrees but is shorter than the one that
        agrees best covers several variants (U-Boot's S25FL512S_256K at
        ``4d 00``, for the S25FS512S's ``4d 00 81``), so it is kept or
        dropped as one with none is."""

        def agrees(r: Record) -> bool:
            return r.ext_id is not None and r.ext_id[: len(ext)] == ext[: len(r.ext_id)]

        best = max((len(r.ext_id) for r in self.records if r.ext_id and agrees(r)), default=0)

        def exact(r: Record) -> bool:
            return r.ext_id is not None and agrees(r) and len(r.ext_id) == best

        mine = {n for r in self.records if exact(r) for n in r.part_names}
        others = {n for r in self.records if r.ext_id and not agrees(r) for n in r.part_names}

        def elsewhere(r: Record) -> bool:
            # Names another extended id's part, and not the one looked up.
            def named(parts: set[str]) -> bool:
                return any(same_part(n, p) for n in r.part_names for p in parts)

            return bool(mine) and named(others) and not named(mine)

        keep = tuple(
            r
            for r in self.records
            if exact(r) or ((r.ext_id is None or agrees(r)) and not elsewhere(r))
        )
        if not keep:
            return self
        # Each value from the records naming this variant exactly, then from
        # the shorter extended ids that agree, then from those with none.
        layers = (
            tuple(r for r in keep if exact(r)),
            tuple(r for r in keep if r.ext_id is not None and not exact(r)),
            tuple(r for r in keep if r.ext_id is None),
        )
        return replace(self, records=keep, layers=tuple(la for la in layers if la))

    def to_json(self) -> dict[str, Any]:
        """A plain-JSON summary, as the ``spiflash`` command prints it."""
        return {
            "id": self.id_hex,
            "jedec_id": self.jedec_id,
            "ids": [i.hex() for i in self.ids],
            "id_family": self.family,
            "type": self.type,
            "manufacturer": self.manufacturer,
            "manufacturer_inferred": self.manufacturer_inferred,
            "names": list(self.names),
            "size": self.size,
            "page_size": self.page_size,
            "sector_size": self.sector_size,
            "voltage": list(self.voltage) if self.voltage else None,
            "quad_enable": quad_enable_to_json(self.quad_enable),
            "quad_enable_requirement": _str_or_none(self.quad_enable_requirement),
            "protection": self.protection.to_json() if self.protection else None,
            "features": sorted(self.features),
            "feature_sources": {
                f: [s._asdict() for s in self.feature_sources(f)] for f in sorted(self.features)
            },
            "opcodes": [
                {
                    "op": o.name,
                    "opcode": o.opcode,
                    "kind": o.operation.kind,
                    "description": o.operation.description,
                    "sources": list(o.sources),
                    "implied_by": list(o.implied_by),
                    "assumed_by": list(o.assumed_by),
                }
                for o in self.opcodes.values()
            ],
            "sources": list(self.sources),
            "conflicts": {
                k: [{"value": plain_value(v), "sources": list(s)} for v, s in vals.items()]
                for k, vals in self.conflicts.items()
            },
            "datasheets": [
                {
                    "url": d.url,
                    "title": d.title,
                    "revision": d.revision,
                    "date": d.date.isoformat() if d.date else None,
                    "official": d.official,
                    "id_confirmed": self.confirms(d),
                }
                for d in self.datasheets
            ],
            "records": [
                {
                    "source": r.source,
                    "name": r.name,
                    "at": r.url,
                    "ext_id": r.ext_id.hex() if r.ext_id else None,
                }
                for r in self.records
            ],
            "sfdp": [
                {"source": d.source, "parts": list(d.parts), **d.sfdp.to_json()}
                for d in self.sfdp_dumps
            ],
        }
