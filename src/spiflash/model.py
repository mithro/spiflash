"""The database's types: one upstream entry (:class:`Record`), and the chip
id they describe (:class:`Flash`)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any, TypeVar

from .opcodes import OPERATIONS, Operation, sort_key
from .vendors import canonical

if TYPE_CHECKING:
    from collections.abc import Iterable

T = TypeVar("T")

# When sources disagree on a value and are otherwise tied, the earlier one
# wins. flashrom and flashprog are first: their entries are per part, tested
# on hardware and carry the most detail; OpenOCD and openFPGALoader last,
# since their tables are the smallest and the least specific.
SOURCE_PRIORITY = ("flashrom", "flashprog", "linux", "u-boot", "openocd", "openfpgaloader")


def _priority(source: str) -> int:
    try:
        return SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(SOURCE_PRIORITY)


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


@dataclass(frozen=True)
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
    because: tuple[tuple[str, str], ...]  # (source, via), by source priority

    @property
    def name(self) -> str:
        return self.operation.name

    @property
    def opcode(self) -> int:
        return self.operation.opcode

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(s for s, _ in self.because))


@dataclass(frozen=True)
class Record:
    """One entry of one upstream's flash table, as that upstream has it.

    See ``tools/spiflash_extract/record.py`` for what each field means."""

    source: str
    file: str
    line: int
    type: str
    vendor: str | None
    name: str
    id: bytes | None
    ext_id: bytes | None
    id_method: str | None
    size: int | None
    page_size: int | None
    sector_size: int | None
    erasers: tuple[dict[str, Any], ...] | None
    features: frozenset[str]
    flags: tuple[str, ...]
    voltage: tuple[int, int] | None
    opcodes: tuple[OpcodeUse, ...]
    tested: str | None
    notes: tuple[str, ...]

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Record:
        return cls(
            source=d["source"],
            file=d["file"],
            line=d["line"],
            type=d["type"],
            vendor=d["vendor"],
            name=d["name"],
            id=bytes.fromhex(d["id"]) if d["id"] else None,
            ext_id=bytes.fromhex(d["ext_id"]) if d["ext_id"] else None,
            id_method=d["id_method"],
            size=d["size"],
            page_size=d["page_size"],
            sector_size=d["sector_size"],
            erasers=tuple(d["erasers"]) if d["erasers"] else None,
            features=frozenset(d["features"]),
            flags=tuple(d["flags"]),
            voltage=(d["voltage"][0], d["voltage"][1]) if d["voltage"] else None,
            opcodes=tuple(OpcodeUse(o["op"], o["opcode"], o["via"]) for o in d["opcodes"]),
            tested=d["tested"],
            notes=tuple(d["notes"]),
        )

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
        return self.id is not None and (self.id_method or "").startswith("rdid")

    @property
    def url(self) -> str:
        """Where in the upstream tree the entry is (the path and line)."""
        return f"{self.file}:{self.line}"

    @cached_property
    def part_names(self) -> tuple[str, ...]:
        """The part numbers the entry's name stands for (see :func:`part_names`)."""
        return part_names(self.name)


def part_names(name: str) -> tuple[str, ...]:
    """The part numbers an upstream name stands for, upper case.

    ``"w25q128fv/jv"`` (OpenOCD's shorthand) is W25Q128FV and W25Q128JV;
    ``"S25FL064P / EPCS64"`` is both; ``"S25FL128S_UL Uniform 128 kB Sectors"``
    is S25FL128S_UL. flashrom's ``.`` wildcards (``"W25Q128.V"``) are kept:
    :func:`name_matches` understands them. Handling of the ``/`` forms follows
    LiteSPI's spi_nor_config_generator."""
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


def _consensus(values: Iterable[tuple[T | None, str]]) -> T | None:
    """The value most sources give, ties to the higher-priority source."""
    counts: Counter[T] = Counter()
    best: dict[T, int] = {}
    for value, source in values:
        if value is None:
            continue
        counts[value] += 1
        best[value] = min(best.get(value, 99), _priority(source))
    if not counts:
        return None
    return min(counts, key=lambda v: (-counts[v], best[v]))


@dataclass(frozen=True)
class Flash:
    """Everything the upstreams say about one chip id.

    Several parts can answer the same id (W25Q128BV, FV and JV all answer
    ``ef4018``), so a :class:`Flash` holds every :class:`Record` for its id,
    and :attr:`names` lists every part they name. Single values
    (:attr:`size`, :attr:`page_size`, ...) are what most sources agree on;
    :meth:`values` shows who says what."""

    id: bytes
    type: str
    records: tuple[Record, ...] = field(repr=False)
    bank: int = 0
    #: ``"jedec"`` for an id read with JEDEC read-id (0x9F), else the legacy
    #: command's name (``"rems"``, ``"res1"``, ...).
    family: str = "jedec"

    @property
    def id_hex(self) -> str:
        return self.id.hex()

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
        return _consensus((r.manufacturer, r.source) for r in self.records)

    @cached_property
    def names(self) -> tuple[str, ...]:
        """Every part name the sources give, most-cited first."""
        counts: Counter[str] = Counter()
        order: dict[str, tuple[int, int]] = {}
        for i, r in enumerate(self.records):
            for n in r.part_names:
                counts[n] += 1
                order.setdefault(n, (_priority(r.source), i))
        return tuple(sorted(counts, key=lambda n: (-counts[n], order[n], n)))

    @property
    def name(self) -> str:
        return self.names[0]

    @cached_property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({r.source for r in self.records}, key=_priority))

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
    def voltage(self) -> tuple[int, int] | None:
        return _consensus((r.voltage, r.source) for r in self.records)

    @cached_property
    def features(self) -> frozenset[str]:
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
        because: dict[str, list[tuple[str, str]]] = {}
        for r in sorted(self.records, key=lambda r: _priority(r.source)):
            for use in r.opcodes:
                pair = (r.source, use.via)
                if pair not in because.setdefault(use.op, []):
                    because[use.op].append(pair)
        return {
            name: SupportedOperation(OPERATIONS[name], tuple(because[name]))
            for name in sorted(because, key=sort_key)
        }

    def supports(self, operation: str) -> bool:
        """Whether any source says the chip has ``operation`` (``"READ_1_1_4"``)."""
        return operation in self.opcodes

    def feature_sources(self, feature: str) -> tuple[str, ...]:
        """The sources claiming ``feature``, in source priority order."""
        return tuple(
            sorted({r.source for r in self.records if feature in r.features}, key=_priority)
        )

    def values(self, attribute: str) -> dict[Any, tuple[str, ...]]:
        """Each value of a :class:`Record` attribute, and the sources giving it:
        ``flash.values("size")`` → ``{16777216: ("flashrom", "linux", ...)}``."""
        out: dict[Any, set[str]] = {}
        for r in self.records:
            v = getattr(r, attribute)
            if v is not None:
                out.setdefault(v, set()).add(r.source)
        return {k: tuple(sorted(v, key=_priority)) for k, v in out.items()}

    @property
    def conflicts(self) -> dict[str, dict[Any, tuple[str, ...]]]:
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
        return Flash(self.id, self.type, keep or self.records, self.bank, self.family)

    def to_json(self) -> dict[str, Any]:
        """A plain-JSON summary, as the ``spiflash`` command prints it."""
        return {
            "id": self.id_hex,
            "jedec_id": self.jedec_id,
            "id_family": self.family,
            "type": self.type,
            "manufacturer": self.manufacturer,
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
            "records": [
                {
                    "source": r.source,
                    "name": r.name,
                    "at": r.url,
                    "ext_id": r.ext_id.hex() if r.ext_id else None,
                }
                for r in self.records
            ],
        }
