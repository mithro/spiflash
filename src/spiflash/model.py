"""The database's types: one upstream entry (:class:`Record`), the chip id
they describe (:class:`Flash`), and a datasheet for it (:class:`Datasheet`)."""

from __future__ import annotations

import datetime
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import TYPE_CHECKING, Any, NamedTuple, TypeVar

from .enums import Feature, FlashType, IdFamily, IdMethod, Source
from .opcodes import OPERATIONS, Operation, sort_key
from .sfdp import Sfdp
from .sfdp import parse as parse_sfdp
from .vendors import canonical

if TYPE_CHECKING:
    from collections.abc import Iterable

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
    names it: flashrom's ``spi_block_erase_emulation``, ...)."""

    opcode: int | None
    blocks: tuple[EraseBlock, ...]
    function: str | None = None

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Eraser:
        blocks = tuple(EraseBlock(size, count) for size, count in d["blocks"])
        return cls(d["opcode"], blocks, d.get("function"))


class Claim(NamedTuple):
    """A source's reason for saying a chip has an operation."""

    source: Source
    via: str


class SfdpDump(NamedTuple):
    """One SFDP area the sources carry for a chip id, decoded, and the
    records carrying it, the best source first."""

    tables: Sfdp
    records: tuple[Record, ...]

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


@dataclass(frozen=True, slots=True)
class OpcodeUse:
    """One operation an upstream entry implies: its name in
    :data:`spiflash.opcodes.OPERATIONS`, the opcode byte, and what in the
    upstream implies it (a flag, a field, or the upstream's default)."""

    op: str
    opcode: int
    via: str


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


@dataclass(frozen=True)
class Record:
    """One entry of one upstream's flash table, as that upstream has it.

    See :mod:`spiflash_extract.record` for what each field means."""

    source: Source
    file: str
    line: int
    type: FlashType
    vendor: str | None
    name: str
    id: bytes | None
    ext_id: bytes | None
    id_method: IdMethod | None
    size: int | None
    page_size: int | None
    sector_size: int | None
    erasers: tuple[Eraser, ...]
    features: frozenset[Feature]
    flags: tuple[str, ...]
    voltage: Voltage | None
    opcodes: tuple[OpcodeUse, ...]
    tested: str | None
    notes: tuple[str, ...]
    #: The part's SFDP area, where the upstream carries a dump of it.
    sfdp: bytes | None = field(default=None, repr=False)

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
            size=d["size"],
            page_size=d["page_size"],
            sector_size=d["sector_size"],
            erasers=tuple(Eraser.from_json(e) for e in d["erasers"] or ()),
            features=frozenset(Feature(f) for f in d["features"]),
            flags=tuple(d["flags"]),
            voltage=Voltage(*d["voltage"]) if d["voltage"] else None,
            opcodes=tuple(OpcodeUse(o["op"], o["opcode"], o["via"]) for o in d["opcodes"]),
            tested=d["tested"],
            notes=tuple(d["notes"]),
            sfdp=bytes.fromhex(d["sfdp"]) if d.get("sfdp") else None,
        )

    def sfdp_tables(self) -> Sfdp | None:
        """The entry's SFDP dump, decoded (see :mod:`spiflash.sfdp`); ``None``
        when the upstream has none for it."""
        return parse_sfdp(self.sfdp) if self.sfdp else None

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


#: What :func:`name_distance` charges: an edit (a character changed, added,
#: dropped, or two neighbours swapped), and each character one name has past
#: the end of the other (a suffix: package, temperature, ordering code).
EDIT_COST = 4
TAIL_COST = 1


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
        listed first."""
        sources: dict[str, set[int]] = {}
        records: Counter[str] = Counter()
        first: dict[str, int] = {}
        own: set[str] = set()
        for i, r in enumerate(self.records):
            for n in r.part_names:
                sources.setdefault(n, set()).add(r.source.priority)
                records[n] += 1
                first.setdefault(n, i)
                if r.manufacturer == self.manufacturer:
                    own.add(n)
        made_up = f"-{self.id_hex.upper()}"

        def rank(n: str) -> tuple[bool, bool, int, list[int], int, int]:
            return (
                "." in n or n.endswith(made_up),
                n not in own,
                -len(sources[n]),
                sorted(sources[n]),
                -records[n],
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
        return _consensus((r.size, r.source) for r in self.records)

    @cached_property
    def page_size(self) -> int | None:
        return _consensus((r.page_size, r.source) for r in self.records)

    @cached_property
    def sector_size(self) -> int | None:
        return _consensus((r.sector_size, r.source) for r in self.records)

    @cached_property
    def voltage(self) -> Voltage | None:
        return _consensus((r.voltage, r.source) for r in self.records)

    @cached_property
    def features(self) -> frozenset[Feature]:
        """Every capability any source claims for this id. Parts sharing an
        id can differ (a W25Q128BV has no QPI, a W25Q128FV does), so check
        :meth:`feature_sources` before relying on one."""
        return frozenset().union(*(r.features for r in self.records))

    @cached_property
    def opcodes(self) -> dict[str, SupportedOperation]:
        """Every operation any source says the chip has, by name, in the
        order id, read, program, erase, register, mode. Parts sharing an id
        can differ, and some sources only list what their own driver uses,
        so :attr:`SupportedOperation.sources` says who vouches for each."""
        because: dict[str, list[Claim]] = {}
        for r in sorted(self.records, key=lambda r: r.source.priority):
            for use in r.opcodes:
                claim = Claim(r.source, use.via)
                if claim not in because.setdefault(use.op, []):
                    because[use.op].append(claim)
        return {
            name: SupportedOperation(OPERATIONS[name], tuple(because[name]))
            for name in sorted(because, key=sort_key)
        }

    @cached_property
    def sfdp_dumps(self) -> tuple[SfdpDump, ...]:
        """Every distinct SFDP dump the sources carry for this id, decoded,
        each with the records carrying it; the best source's first. What a
        dump says is what one part answered, and parts sharing an id can
        answer differently: QEMU has one dump for the MX25L25635E and
        another for the MX25L25635F, both ``c22019``."""
        carrying: dict[bytes, list[Record]] = {}
        for r in sorted(self.records, key=lambda r: r.source.priority):
            if r.sfdp:
                carrying.setdefault(r.sfdp, []).append(r)
        return tuple(SfdpDump(parse_sfdp(d), tuple(rs)) for d, rs in carrying.items())

    @property
    def sfdp(self) -> Sfdp | None:
        """The chip's SFDP tables, decoded: the first of :attr:`sfdp_dumps`,
        from the highest-priority source that carries one
        (:attr:`sfdp_source`); ``None`` when no source does."""
        return self.sfdp_dumps[0].tables if self.sfdp_dumps else None

    @property
    def sfdp_source(self) -> Source | None:
        """Which source :attr:`sfdp` comes from."""
        return self.sfdp_dumps[0].source if self.sfdp_dumps else None

    def supports(self, operation: str) -> bool:
        """Whether any source says the chip has ``operation`` (``"READ_1_1_4"``)."""
        return operation in self.opcodes

    def feature_sources(self, feature: Feature | str) -> tuple[Source, ...]:
        """The sources claiming ``feature``, in source priority order."""
        claiming = {r.source for r in self.records if feature in r.features}
        return tuple(sorted(claiming, key=lambda s: s.priority))

    def values(self, attribute: str) -> dict[Any, tuple[Source, ...]]:
        """Each value of a :class:`Record` attribute, and the sources giving it:
        ``flash.values("size")`` → ``{16777216: ("flashrom", "linux", ...)}``."""
        out: dict[Any, set[Source]] = {}
        for r in self.records:
            v = getattr(r, attribute)
            if v is not None:
                out.setdefault(v, set()).add(r.source)
        return {k: tuple(sorted(v, key=lambda s: s.priority)) for k, v in out.items()}

    @property
    def conflicts(self) -> dict[str, dict[Any, tuple[Source, ...]]]:
        """The attributes the sources disagree on."""
        out = {}
        for attr in ("size", "page_size", "sector_size", "voltage"):
            v = self.values(attr)
            if len(v) > 1:
                out[attr] = v
        return out

    def with_ext_id(self, ext: bytes) -> Flash:
        """This id narrowed by the bytes a chip sends after it: records whose
        extended id disagrees with ``ext`` are dropped (records with none
        stay, as they cover every variant)."""
        keep = tuple(
            r
            for r in self.records
            if r.ext_id is None or r.ext_id[: len(ext)] == ext[: len(r.ext_id)]
        )
        return replace(self, records=keep or self.records)

    def to_json(self) -> dict[str, Any]:
        """A plain-JSON summary, as the ``spiflash`` command prints it."""
        return {
            "id": self.id_hex,
            "jedec_id": self.jedec_id,
            "id_family": self.family,
            "type": self.type,
            "manufacturer": self.manufacturer,
            "manufacturer_inferred": self.manufacturer_inferred,
            "names": list(self.names),
            "size": self.size,
            "page_size": self.page_size,
            "sector_size": self.sector_size,
            "voltage": list(self.voltage) if self.voltage else None,
            "features": sorted(self.features),
            "opcodes": [
                {
                    "op": o.name,
                    "opcode": o.opcode,
                    "kind": o.operation.kind,
                    "description": o.operation.description,
                    "sources": list(o.sources),
                }
                for o in self.opcodes.values()
            ],
            "sources": list(self.sources),
            "conflicts": {
                k: [
                    {"value": list(v) if isinstance(v, tuple) else v, "sources": list(s)}
                    for v, s in vals.items()
                ]
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
                {"source": d.source, "parts": list(d.parts), **d.tables.to_json()}
                for d in self.sfdp_dumps
            ],
        }
