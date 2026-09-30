"""Load the database and answer questions of it."""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Any

from .enums import FlashType, IdFamily, Source
from .model import (
    Datasheet,
    Flash,
    Record,
    name_distance,
    name_matches,
    parse_id,
    squash_name,
    strip_continuation,
)
from .vendors import canonical

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class Manufacturer:
    """A JEP106 manufacturer: ``bank`` counts the 0x7f continuation codes
    before ``id`` (0 for the first bank); ``id`` includes the parity bit, as
    a chip sends it."""

    bank: int
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """Where one upstream's data came from: the repository (``url``), where
    to browse its files (``browse``: the same, or a GitHub mirror), the
    branch and commit read, that commit's date, the files read, their
    licence, and how many entries were taken."""

    url: str
    browse: str
    branch: str
    commit: str
    date: datetime
    paths: tuple[str, ...]
    license: str
    records: int

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> SourceInfo:
        return cls(
            url=d["url"],
            browse=d.get("browse") or d["url"],
            branch=d["branch"],
            commit=d["commit"],
            date=datetime.fromisoformat(d["date"]),
            paths=tuple(d["paths"]),
            license=d["license"],
            records=d["records"],
        )


#: The sources whose table is a binary file: a record's ``line`` is its
#: entry's number in the file, and its link is to the file.
BINARY_SOURCES = frozenset({Source.IMSPROG})

#: The data files' format; :repo:`tools/update_db.py` writes the same number.
#: 2: records' ``opcodes`` became a list of {op, opcode, via}.
FORMAT = 3


def _read(name: str) -> dict[str, Any]:
    text = resources.files("spiflash").joinpath("data", name).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    if data.get("format") != FORMAT:
        msg = f"{name}: unsupported format {data.get('format')!r}"
        raise ValueError(msg)
    return data


@dataclass(frozen=True, slots=True)
class NameMatch:
    """A chip :meth:`Database.find_nearest` found: its part name closest to
    the query, the :func:`~spiflash.model.name_distance` cost (0 for the
    same part), and why, in words."""

    flash: Flash
    name: str
    score: int
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": self.score,
            "reason": self.reason,
            "chip": self.flash.to_json(),
        }


def _reason(query: str, name: str, common: int) -> str:
    """What differs between ``query`` and part ``name``, which share their
    first ``common`` characters (as :func:`squash_name` writes them)."""
    a, b = squash_name(query), squash_name(name, wildcards=True)
    if common == len(a) == len(b):
        return "the same part"
    if common == len(b):
        return f"the query adds {a[common:]}"
    if common == len(a):
        return f"the name adds {b[common:]}"
    if common:
        return f"differs after {a[:common]}"
    return "differs from the first character"


def _glob(pattern: str) -> re.Pattern[str]:
    """A shell-style pattern (``*``, ``?``, ``[...]``) for a whole part name,
    ignoring case and spaces."""
    return re.compile(fnmatch.translate(re.sub(r"\s", "", pattern)), re.IGNORECASE)


def _rank(part: str, query: str) -> int | None:
    """How well part name ``part`` matches ``query``: 0 exactly, 1 as a prefix
    of it, 2 when ``query`` starts with it; None for no match."""
    if name_matches(part, query):
        return 0
    if part.startswith(query):
        return 1
    if len(part) >= 4 and name_matches(part, query, prefix=True):
        return 2
    return None


class Database:
    """The records, grouped into one :class:`Flash` per chip id."""

    def __init__(
        self,
        records: Iterable[Record],
        manufacturers: Iterable[Manufacturer] = (),
        sources: Mapping[str, SourceInfo] | None = None,
        datasheets: Iterable[Datasheet] = (),
    ) -> None:
        self.records: tuple[Record, ...] = tuple(records)
        self.manufacturers: tuple[Manufacturer, ...] = tuple(manufacturers)
        #: Where each upstream's data came from, by name (the sources, and
        #: ``jep106`` for the manufacturer list).
        self.sources: dict[str, SourceInfo] = dict(sources or {})
        self._jep106 = {(m.bank, m.id): m.name for m in self.manufacturers}

        groups: dict[tuple[FlashType, IdFamily, bytes], list[Record]] = defaultdict(list)
        banks: dict[tuple[FlashType, IdFamily, bytes], Counter[int]] = defaultdict(Counter)
        for r in self.records:
            if r.id is None or r.id_method is None:
                continue  # no id: nothing to look it up by
            bank, core = strip_continuation(r.id)
            # Grouped by how the id is read: every JEDEC read-id variant (the
            # NAND ones send a dummy or address byte first, but answer the
            # same bytes) together, each legacy command on its own.
            key = (r.type, r.id_method.family, core)
            groups[key].append(r)
            banks[key][bank] += 1
        #: Every datasheet known, each once (a chip's own are in its
        #: :attr:`Flash.datasheets`).
        self.datasheets: tuple[Datasheet, ...] = tuple(datasheets)
        sheets: dict[str, list[Datasheet]] = defaultdict(list)
        for d in self.datasheets:
            for chip in d.ids:
                sheets[chip].append(d)

        def flash(typ: FlashType, fam: IdFamily, core: bytes, recs: list[Record]) -> Flash:
            f = Flash(core, typ, tuple(recs), banks[(typ, fam, core)].most_common(1)[0][0], fam)
            mine = sorted(sheets.get(f.key, ()), key=lambda d: d.rank(f.key))
            return replace(f, datasheets=tuple(mine)) if mine else f

        self.flashes: tuple[Flash, ...] = tuple(
            flash(typ, fam, core, recs)
            for (typ, fam, core), recs in sorted(groups.items(), key=lambda kv: (kv[0][2], kv[0]))
        )

    @classmethod
    def load(cls) -> Database:
        """The database shipped in this package."""
        recs = _read("records.json")["records"]
        mfrs = _read("manufacturers.json")["manufacturers"]
        return cls(
            (Record.from_json(r) for r in recs),
            (Manufacturer(m["bank"], m["id"], m["name"]) for m in mfrs),
            {
                name: SourceInfo.from_json(info)
                for name, info in _read("sources.json")["sources"].items()
            },
            (Datasheet.from_json(d) for d in _read("datasheets.json")["datasheets"]),
        )

    def lookup(
        self,
        chip_id: str | bytes | int | Iterable[int],
        *,
        flash_type: FlashType | str | None = None,
        method: IdFamily | str = IdFamily.JEDEC,
    ) -> list[Flash]:
        """The chips answering ``chip_id``.

        Give the bytes a chip sent to JEDEC read-id (0x9F): ``"ef4018"``,
        ``b"\\xef\\x40\\x18"``, ``0xef4018``. Leading 0x7f continuation codes
        are ignored, since many chips leave them out. Bytes past the id narrow
        the answer to the variants whose extended id agrees (an S25FL128S
        answers ``01 20 18 4d 01 80``). ``method`` selects a legacy id
        instead (``"rems"``, ``"res1"``, ``"res2"``, ``"at25f"``).

        Only the longest ids that fit come back, NOR before NAND: a SPI NAND
        id is two bytes, so a NOR id can start with one (``c22018`` also
        fits the MX35LF2G14AC's ``c220``); pass ``flash_type="nor"`` to rule that
        out."""
        family = IdFamily(method)
        wanted = FlashType(flash_type) if flash_type is not None else None
        _bank, core = strip_continuation(parse_id(chip_id))
        found = []
        for f in self.flashes:
            if f.family is not family or (wanted is not None and f.type is not wanted):
                continue
            if core[: len(f.id)] == f.id:
                ext = core[len(f.id) :]
                found.append(f.with_ext_id(ext) if ext else f)
        # Of the ids that fit, only the longest of each type: Linux's
        # one-byte "any Macronix part" entry (c2) is not an answer when the
        # MX25L12835F's c22018 is.
        longest: dict[FlashType, int] = {}
        for f in found:
            longest[f.type] = max(longest.get(f.type, 0), len(f.id))
        found = [f for f in found if len(f.id) == longest[f.type]]
        return sorted(found, key=lambda f: (f.type is not FlashType.NOR, -len(f.id)))

    def find(self, name: str) -> list[Flash]:
        """The chips whose part names match ``name``, best first.

        Exact names (and flashrom wildcards: ``W25Q128.V`` matches
        ``W25Q128JV``) come first, then parts whose name starts with ``name``
        (``w25q128`` finds the FV and the JV), then parts named by a prefix of
        ``name`` (Linux's generic ``w25q128`` for ``W25Q128JVSIQ``)."""
        q = name.strip().upper()
        if not q:
            return []
        ranked: list[tuple[int, Flash]] = []
        for f in self.flashes:
            ranks = [r for part in f.names if (r := _rank(part, q)) is not None]
            if ranks:
                ranked.append((min(ranks), f))
        ranked.sort(key=lambda t: t[0])  # stable: equal ranks keep database order
        return [f for _, f in ranked]

    def find_regex(self, pattern: str | re.Pattern[str]) -> list[Flash]:
        """The chips with a part name ``pattern`` matches, in database order.

        The pattern is searched for anywhere in each of :attr:`Flash.names`
        (anchor it with ``^`` and ``$``), ignoring case unless it is already
        compiled: ``^W25Q(64|128)J[VW]$``. The names are as the sources give
        them, so flashrom's ``W25Q128.V`` is matched as written. A pattern
        that is not a regular expression raises :class:`ValueError`."""
        if isinstance(pattern, str):
            try:
                pattern = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                msg = f"not a regular expression: {pattern!r} ({e})"
                raise ValueError(msg) from e
        return [f for f in self.flashes if any(pattern.search(n) for n in f.names)]

    def find_glob(self, pattern: str) -> list[Flash]:
        """The chips with a part name matching the shell-style ``pattern``, in
        database order: ``*`` is any run of characters, ``?`` any one,
        ``[...]`` one of a set (``[!...]`` one not in it). The pattern covers
        the whole name, ignoring case and spaces: ``W25Q128*``, ``MX25?12835F``,
        ``S25FL*S``."""
        rx = _glob(pattern)
        return [f for f in self.flashes if any(rx.fullmatch(n) for n in f.names)]

    def find_nearest(self, name: str, count: int = 10) -> list[NameMatch]:
        """The ``count`` chips with the part names closest to ``name``, the
        closest first, each with its closest name: for a marking read off a
        chip, an order code, or a typo.

        The score is :func:`~spiflash.model.name_distance`: an edit distance
        on the names' letters and digits, where a character one name has past
        the end of the other costs a quarter of an edit, since part numbers
        end in their least important parts (package, temperature, ordering).
        Equal scores go to the name sharing more leading characters, then to
        a name without flashrom's wildcards, then alphabetically."""
        if count < 1:
            msg = f"count must be at least 1, not {count}"
            raise ValueError(msg)
        if not squash_name(name):
            return []

        def key(part: str) -> tuple[int, int, bool, str]:
            cost, common = name_distance(name, part)
            return cost, -common, "." in part, part

        # Each chip's closest name, then the chips by it (stable: database
        # order last).
        ranked = sorted(((min(map(key, f.names)), f) for f in self.flashes), key=lambda t: t[0])
        return [
            NameMatch(f, part, cost, _reason(name, part, -common))
            for (cost, common, _, part), f in ranked[:count]
        ]

    def link(self, record: Record) -> str | None:
        """A web link to the upstream line a record came from, at the commit
        the data was extracted from (GitHub and its mirrors only), or to the
        file, for a binary one (:data:`BINARY_SOURCES`)."""
        src = self.sources.get(record.source)
        if not src:
            return None
        base = src.browse.rstrip("/")
        if not base.startswith("https://github.com/"):
            return None
        url = f"{base}/blob/{src.commit}/{record.file}"
        return url if record.source in BINARY_SOURCES else f"{url}#L{record.line}"

    def jep106(self, manufacturer_id: int, bank: int = 0) -> str | None:
        """The JEP106 name of a manufacturer id byte (with its parity bit)."""
        return self._jep106.get((bank, manufacturer_id))

    def by_manufacturer(self, name: str) -> list[Flash]:
        """Every chip whose manufacturer is ``name`` (any upstream spelling)."""
        want = (canonical(name) or name).lower()
        return [f for f in self.flashes if (f.manufacturer or "").lower() == want]


@cache
def database() -> Database:
    """The shipped database, loaded once."""
    return Database.load()
