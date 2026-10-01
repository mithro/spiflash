"""Load the database and answer questions of it."""

from __future__ import annotations

import fnmatch
import json
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Any

from .enums import FlashType, IdFamily, Source
from .model import (
    BINARY_SOURCES,
    Datasheet,
    Flash,
    Record,
    name_distance,
    name_matches,
    parse_id,
    same_part,
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


#: The data files' format; :repo:`tools/update_db.py` writes the same number.
#: 2: records' ``opcodes`` became a list of {op, opcode, via}.
#: 4: ``opcodes`` lose ``opcode``; erase and id operations are derived;
#: ``via``; consumed flags and notes removed.
#: 5: ``sector_size`` dropped (derived from the erasers, which Linux, U-Boot,
#: openFPGALoader and the SPI NAND records now give); implied capability
#: claims dropped; ``opcodes`` gain ``assumed`` for driver defaults.
#: 6: records gain ``sfdp_tables`` (Zephyr's copied tables); what a record's
#: SFDP tables say is derived, not stored (QEMU's and Zephyr's decoded
#: operations, capabilities, sizes, page sizes and erasers dropped).
#: 7: records gain ``quad_enable``, ``quad_enable_requirement`` and
#: ``protection``; ``lock`` and ``quad_read`` claims they imply dropped; new
#: register operations.
#: 8: records gain ``oob_size``, ``planes``, ``dies``, ``die_select_bit``,
#: ``max_bad_blocks`` and ``ecc``; the SPI NAND records gain their own
#: operations, and a use its ``dummy_clocks``; a die erase layout is derived
#: from ``dies``, not stored.
#: 9: records gain ``four_byte_modes`` (the 4-byte mode operations they
#: give dropped), ``supply_mv``, ``otp`` and ``legacy_ids`` (their flags,
#: notes and ``otp`` claims dropped); new OTP operations.
#: 10: records gain ``timings`` (Dediprog's chip erase time, Zephyr's deep
#: power-down and reset times; those their SFDP tables give derived) and
#: ``listed_clock_hz`` (the clock Dediprog lists); Zephyr's ``has-dpd`` is the new
#: ``DP`` and ``RDPD`` operations, its DPD flags gone.
FORMAT = 10


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


def _bank(banks: dict[int, set[Source]]) -> int:
    """A chip's JEP106 bank: the one the most sources give its id with (Eon's
    chips mostly answer 1c alone, not 7f 1c); on a tie the higher, as an
    upstream may drop the continuation codes but never adds them (ATXP032's
    7f x 7 43)."""
    return max(banks, key=lambda b: (len(banks[b]), b))


_Key = tuple[FlashType, IdFamily, bytes]


def _nand_folds(groups: Mapping[_Key, list[Record]]) -> dict[_Key, _Key]:
    """The SPI NAND ids that are the start of one other, longer SPI NAND id
    of the same part, and that id: sources match different numbers of id
    bytes for one part (Rockchip's rkflash ``c2 26`` and Linux's
    ``c2 26 03`` for the MX35LF2GE4AD). A group folds where every record of
    it names a part of the longer ids starting with it, and those are one
    line of ids, each starting the next (the F50L1G41LB's ``c8 01``,
    ``c8 01 7f`` and ``c8 01 7f 7f 7f``): it goes into the longest. Linux's
    GD5F1GM7REXXG (``c8 81``) does not fold into the GD5F1GM9REXXG
    (``c8 81 01``), because Dediprog lists both at ``c8 81``. Nor does a
    group whose sizes, pages or erase blocks the longer id's records do not
    give: a part of another size is another part.

    Each fold is resolved to the id it ends at, where the id it goes into
    folds on in turn."""

    def names(recs: Iterable[Record]) -> set[str]:
        return {n for r in recs for n in r.part_names if "." not in n}

    def one_part(recs: list[Record], theirs: list[Record]) -> bool:
        mine = [names([r]) for r in recs]
        into = names(theirs)
        if not all(any(same_part(a, b) for a in m for b in into) for m in mine):
            return False
        # Every size, page and erase block the group gives, the longer id's
        # records give too, where they give any.
        for attr in ("size", "page_size", "sector_size"):
            given = {getattr(r, attr) for r in theirs} - {None}
            if given and not {getattr(r, attr) for r in recs} - {None} <= given:
                return False
        return True

    nand = {k: recs for k, recs in groups.items() if k[0] is FlashType.NAND}
    folds = {}
    for key, recs in nand.items():
        longer = [
            k
            for k, theirs in nand.items()
            if k[1] is key[1] and len(k[2]) > len(key[2]) and k[2].startswith(key[2])
            if one_part(recs, theirs)
        ]
        longest = max(longer, key=lambda k: len(k[2]), default=None)
        if longest is not None and all(longest[2].startswith(k[2]) for k in longer):
            folds[key] = longest

    def end(key: _Key) -> _Key:
        return end(folds[key]) if key in folds else key

    return {key: end(into) for key, into in folds.items()}


def infer_manufacturer(flash: Flash, chips: Iterable[Flash]) -> str | None:
    """The manufacturer of a chip whose records name none (Rockchip's never
    do): the one all of ``chips`` are made by (as their sources agree) that
    answer the chip's manufacturer byte and bank with a part name starting
    with the same three characters as one of the chip's. The byte alone is
    not enough, as clones answer another maker's (GSS's GSS01GSAK1 answers
    Alliance Memory's 0x52), and nor is a driver's list of manufacturer bytes
    (Rockchip's sfc.h makes ESMT's F50L2G41KA, answering 0xc8, GigaDevice's).
    Each chip counts with the maker its sources agree on, not each record's,
    so one source's slip (Dediprog's FM25Q64 made by Fidelix, on Fudan's
    0xa1) does not stop the inference; and only with the names records
    naming that maker give, so another maker's part a source names no maker
    for, sharing the id, does not either (MediaTek's ESMT F50L1G41A on
    GigaDevice's GD5F1GQ5REXXH's ``c8 21``)."""
    prefixes = {n[:3] for n in flash.names}
    found = {
        g.manufacturer
        for g in chips
        if g.manufacturer
        and (g.type, g.key) != (flash.type, flash.key)  # other chips
        and (g.bank, g.id[0]) == (flash.bank, flash.id[0])
        and any(
            n[:3] in prefixes
            for r in g.records
            if r.manufacturer == g.manufacturer
            for n in r.part_names
        )
    }
    return found.pop() if len(found) == 1 else None


class Database:
    """The records, grouped into one :class:`Flash` per chip id.

    SPI NAND sources match different numbers of id bytes for one part, so a
    SPI NAND id that is the start of another's, for the same part, is folded
    into it (see :func:`_nand_folds`); :attr:`Flash.ids` lists both."""

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
        banks: dict[tuple[FlashType, IdFamily, bytes], dict[int, set[Source]]] = defaultdict(dict)
        for r in self.records:
            if r.id is None or r.id_method is None:
                continue  # no id: nothing to look it up by
            bank, core = strip_continuation(r.id)
            # Grouped by how the id is read: every JEDEC read-id variant (the
            # NAND ones send a dummy or address byte first, but answer the
            # same bytes) together, each legacy command on its own.
            key = (r.type, r.id_method.family, core)
            groups[key].append(r)
            banks[key].setdefault(bank, set()).add(r.source)
        # Each fold goes to where it ends, which never folds itself, so no
        # key is read after it has been popped.
        for short, into in _nand_folds(groups).items():
            groups[into].extend(groups.pop(short))
            for n, giving in banks.pop(short).items():
                banks[into].setdefault(n, set()).update(giving)
        #: Every datasheet known, each once (a chip's own are in its
        #: :attr:`Flash.datasheets`).
        self.datasheets: tuple[Datasheet, ...] = tuple(datasheets)
        sheets: dict[str, list[Datasheet]] = defaultdict(list)
        for d in self.datasheets:
            for chip in d.ids:
                sheets[chip].append(d)

        def flash(typ: FlashType, fam: IdFamily, core: bytes, recs: list[Record]) -> Flash:
            f = Flash(core, typ, tuple(recs), _bank(banks[(typ, fam, core)]), fam)
            found = {id(d): d for k in f.keys for d in sheets.get(k, ())}
            mine = sorted(found.values(), key=lambda d: d.rank(*f.keys))
            return replace(f, datasheets=tuple(mine)) if mine else f

        self.flashes: tuple[Flash, ...] = tuple(
            flash(typ, fam, core, recs)
            for (typ, fam, core), recs in sorted(groups.items(), key=lambda kv: (kv[0][2], kv[0]))
        )
        # A JEDEC chip no record names a manufacturer for gets one inferred.
        self._named = [f for f in self.flashes if f.family is IdFamily.JEDEC and f.manufacturer]
        self.flashes = tuple(self._inferred(f) for f in self.flashes)

    def _inferred(self, f: Flash) -> Flash:
        """``f``, with its manufacturer inferred if no record names one."""
        if f.family is not IdFamily.JEDEC or f.manufacturer is not None:
            return f
        return replace(f, inferred_manufacturer=infer_manufacturer(f, self._named))

    def narrow(self, flash: Flash, ext: bytes) -> Flash:
        """``flash`` narrowed to the parts sending ``ext`` after its id
        (:meth:`Flash.with_ext_id`), with its manufacturer inferred again if
        those parts' records name none (the F50L2G41KA at ``c8 41``, then
        ``7f``, is ESMT's, where the GD5F1GQ5REXXG there is GigaDevice's)."""
        narrowed = flash.with_ext_id(ext)
        # Only the datasheets of the parts left (not GigaDevice's for the
        # F50L2G41KA).
        sheets = tuple(
            d
            for d in flash.datasheets
            if any(same_part(p, n) for p in d.parts for n in narrowed.names)
        )
        narrowed = replace(narrowed, datasheets=sheets)
        if any(r.manufacturer for r in narrowed.records):
            return replace(narrowed, inferred_manufacturer=None)
        return self._inferred(replace(narrowed, inferred_manufacturer=None))

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
        instead (``"rems"``, ``"res1"``, ``"res2"``, ``"at25f"``): the chips
        grouped by that id, then the JEDEC chips whose records say their
        part answers it too (:attr:`Flash.legacy_ids
        <spiflash.model.Flash.legacy_ids>`), each with
        :attr:`~spiflash.model.Flash.answers_legacy` set. A RES id is one
        byte, which parts of several makers share: those are candidates.

        Only the longest ids of each type that fit come back, longest first
        and NOR before NAND among equals: a SPI NAND id is two bytes, so a
        NOR id can start with one (``c22018`` also fits the MX35LF2G14AC's
        ``c220``); pass ``flash_type="nor"`` to rule that out. A one-byte id
        (Linux's "any Macronix part", ``c2``) comes back only where no
        longer id of either type fits: ``c22603`` is the SPI NAND
        MX35LF2GE4AD alone."""
        family = IdFamily(method)
        wanted = FlashType(flash_type) if flash_type is not None else None
        _bank, core = strip_continuation(parse_id(chip_id))
        found: list[tuple[int, Flash]] = []
        for f in self.flashes:
            if f.family is not family or (wanted is not None and f.type is not wanted):
                continue
            # The longest of the chip's ids that fits (a SPI NAND chip can
            # have a shorter one too: see Flash.ids).
            fits = next((i for i in f.ids if core[: len(i)] == i), None)
            if fits is not None:
                ext = core[len(f.id) :]
                found.append((len(fits), self.narrow(f, ext) if ext else f))
        # Of the ids that fit, only the longest of each type: Linux's
        # one-byte "any Macronix part" entry (c2) is not an answer when the
        # MX25L12835F's c22018 is.
        longest: dict[FlashType, int] = {}
        for n, f in found:
            longest[f.type] = max(longest.get(f.type, 0), n)
        best = [(n, f) for n, f in found if n == longest[f.type]]
        # A one-byte id is a maker's catch-all (Linux's MACRONIX-C2, "any
        # Macronix part: read its SFDP"), not an answer when a longer id of
        # either type fits: c22603 is the SPI NAND MX35LF2GE4AD alone.
        if any(n > 1 for n, _ in best):
            best = [(n, f) for n, f in best if n > 1]
        chips = [
            f for n, f in sorted(best, key=lambda nf: (-nf[0], nf[1].type is not FlashType.NOR))
        ]
        if family is IdFamily.JEDEC:
            return chips
        # Then the JEDEC chips whose records list the id as one their part
        # also answers (Record.legacy_ids), each marked with it.
        for f in self.flashes:
            if f.family is not IdFamily.JEDEC or (wanted is not None and f.type is not wanted):
                continue
            legacy = next((i for i in f.legacy_ids if i.family is family and i.id == core), None)
            if legacy is not None:
                chips.append(replace(f, answers_legacy=legacy))
        return chips

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
        file, for a binary one (:data:`~spiflash.model.BINARY_SOURCES`)."""
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
