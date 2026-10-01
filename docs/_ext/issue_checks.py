"""The checks behind the data issues pages: conflicts and errors in the
source data.

:func:`find` checks the database for each :class:`IssueKind`. Each
:class:`Issue` is one question (a chip id's size, the id a part number
answers) and the different answers the sources give, each with the records
giving it, so every claim can be traced to its upstream file and line
(:meth:`spiflash.Database.link`). The site shows them (:mod:`issue_pages`);
they are not part of the ``spiflash`` package.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from spiflash import database
from spiflash.enums import IdFamily, Source
from spiflash.model import COMPARED_VALUES, register_bits

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from spiflash import Database, Datasheet, Flash, Record


class IssueKind(StrEnum):
    """A kind of conflict or error in the source data; the members are in
    the order the site lists them."""

    VALUE = "value"
    SAME_SOURCE = "same-source"
    SFDP = "sfdp"
    SHARED_BIT = "shared-bit"
    NAME_IDS = "name-ids"
    MANUFACTURER = "manufacturer"
    DATASHEET = "datasheet"

    @property
    def heading(self) -> str:
        return _ISSUE_TITLES[self][0]

    @property
    def description(self) -> str:
        """What the check looks for, in a sentence."""
        return _ISSUE_TITLES[self][1]


_ISSUE_TITLES = {
    IssueKind.VALUE: (
        "Sources disagree on a value",
        (
            "Two sources give one chip id a different size, page size, sector size, "
            "supply voltage, quad enable bit or requirement, block-protection bit, "
            "SPI NAND spare area, planes, bad blocks or ECC requirement, number of "
            "dies, or die select bit."
        ),
    ),
    IssueKind.SAME_SOURCE: (
        "One source, two values",
        (
            "One source lists a chip id more than once, with different values, and "
            "nothing in the id (no extended id) tells the entries apart."
        ),
    ),
    IssueKind.SFDP: (
        "A source disagrees with its own SFDP tables",
        (
            "A source gives a part a size, page size, erase layout, quad enable "
            "requirement or number of dies, and with it the part's own SFDP tables, "
            "which say otherwise."
        ),
    ),
    IssueKind.SHARED_BIT: (
        "Two roles on one register bit",
        (
            "The sources' answers, role by role, put two of a chip's register bits "
            "(two block-protection roles, or one and the quad enable bit) on one bit "
            "of one register, which no part has."
        ),
    ),
    IssueKind.NAME_IDS: (
        "One part, several ids",
        "The same part number is listed under different chip ids.",
    ),
    IssueKind.MANUFACTURER: (
        "Sources disagree on the manufacturer",
        "Sources name different manufacturers for one chip id.",
    ),
    IssueKind.DATASHEET: (
        "Ids the datasheets don't give",
        (
            "A datasheet was found for the part, but the chip id the sources list it "
            "under was not found in it."
        ),
    ),
}


#: The values sources are compared on (:data:`spiflash.model.COMPARED_VALUES`):
#: a protection layout role by role (``"protection.tb"``).
ATTRIBUTES = COMPARED_VALUES


@dataclass(frozen=True, slots=True)
class Answer:
    """One answer to an issue's question, and the records giving it."""

    #: A size in bytes, a :class:`~spiflash.Voltage`, a register bit, a quad
    #: enable requirement, a manufacturer's name, a chip id as
    #: :attr:`spiflash.Flash.key` writes it, or for
    #: :attr:`IssueKind.SHARED_BIT` a role and its bit.
    value: Any
    records: tuple[Record, ...]

    @property
    def sources(self) -> tuple[Source, ...]:
        """The sources giving this answer, most trusted first."""
        return tuple(sorted({r.source for r in self.records}, key=lambda s: s.priority))


@dataclass(frozen=True)
class Issue:
    """One conflict or error: a question the sources answer differently."""

    kind: IssueKind
    #: What the question is about: a chip id (:attr:`spiflash.Flash.key`),
    #: or a part number for :attr:`IssueKind.NAME_IDS`.
    subject: str
    #: The chips concerned.
    flashes: tuple[Flash, ...]
    #: The answers, the most-given first.
    answers: tuple[Answer, ...]
    #: For a value: which one (``"size"``, ``"protection.tb"``, ...; see
    #: :data:`ATTRIBUTES`); for :attr:`IssueKind.SFDP`, the field
    #: (``"page_size"``, ``"erasers"``); for :attr:`IssueKind.SHARED_BIT`,
    #: the bit (``"SR1 bit 5"``).
    attribute: str | None = None
    #: For :attr:`IssueKind.DATASHEET`: the part, and its datasheets.
    part: str | None = None
    datasheets: tuple[Datasheet, ...] = ()

    @property
    def sources(self) -> tuple[Source, ...]:
        """Every source involved, most trusted first."""
        involved = {s for a in self.answers for s in a.sources}
        return tuple(sorted(involved, key=lambda s: s.priority))


def find(db: Database | None = None) -> list[Issue]:
    """Every issue in ``db`` (the shipped database by default), by kind in
    :class:`IssueKind` order, then by chip id or part number."""
    db = db or database()
    return [
        *_values(db.flashes),
        *_same_source(db.flashes),
        *_sfdp(db.flashes),
        *_shared_bits(db.flashes),
        *_name_ids(db.flashes),
        *_manufacturers(db.flashes),
        *_datasheets(db.flashes),
    ]


def _answers(pairs: Iterable[tuple[Any, Record]]) -> tuple[Answer, ...]:
    """Records grouped by the value they give: the most-given first, then
    the one from the most trusted source."""
    by: dict[Any, list[Record]] = defaultdict(list)
    for value, r in pairs:
        if value is not None:
            by[value].append(r)
    answers = [Answer(v, tuple(rs)) for v, rs in by.items()]
    answers.sort(key=lambda a: (-len(a.records), a.sources[0].priority))
    return tuple(answers)


def _values(flashes: Iterable[Flash]) -> Iterator[Issue]:
    for f in flashes:
        for attr in ATTRIBUTES:
            # Only records describing one part are compared: an extended id
            # tells two parts at one id apart (Flash.variants). The issue
            # gathers every variant whose sources disagree.
            disagree: dict[int, Record] = {}
            for variant in f.variants:
                answers = _answers((r.compared(attr), r) for r in variant)
                # One source alone giving two values is the next check's.
                sources = {r.source for a in answers for r in a.records}
                if len(answers) > 1 and len(sources) > 1:
                    disagree.update((id(r), r) for r in variant)
            if disagree:
                answers = _answers((r.compared(attr), r) for r in disagree.values())
                yield Issue(IssueKind.VALUE, f.key, (f,), answers, attribute=attr)


def _same_source(flashes: Iterable[Flash]) -> Iterator[Issue]:
    for f in flashes:
        groups: dict[tuple[Source, bytes | None], list[Record]] = defaultdict(list)
        for r in f.records:
            groups[(r.source, r.ext_id)].append(r)
        for (_source, _ext), records in sorted(groups.items(), key=lambda kv: kv[0][0].priority):
            for attr in ATTRIBUTES:
                answers = _answers((r.compared(attr), r) for r in records)
                if len(answers) > 1:
                    yield Issue(IssueKind.SAME_SOURCE, f.key, (f,), answers, attribute=attr)


def _sfdp(flashes: Iterable[Flash]) -> Iterator[Issue]:
    """One issue per record and field its own SFDP tables give otherwise
    (:meth:`spiflash.Record.sfdp_disagreements`): the answers are what the
    entry states, then what its tables say, both the record's."""
    for f in flashes:
        for r in f.records:
            for d in r.sfdp_disagreements():
                answers = (Answer(d.stored, (r,)), Answer(d.sfdp, (r,)))
                yield Issue(IssueKind.SFDP, f.key, (f,), answers, attribute=d.field)


def _shared_bits(flashes: Iterable[Flash]) -> Iterator[Issue]:
    """One issue per bit the sources' answers put two roles on
    (:meth:`spiflash.Flash.shared_bits`): the answers are each role on it,
    ``(role, bit)``, and the records giving that role that bit."""
    for f in flashes:
        for bit, roles in f.shared_bits().items():
            pairs = []
            for role in roles:
                for r in f.records:
                    given = register_bits(r.quad_enable, r.protection).get(role)
                    if given is not None and f"{given.register.label} bit {given.bit}" == bit:
                        pairs.append(((role, given.unqualified), r))
            yield Issue(IssueKind.SHARED_BIT, f.key, (f,), _answers(pairs), attribute=bit)


def _name_ids(flashes: Iterable[Flash]) -> Iterator[Issue]:
    # JEDEC ids only: a legacy id is a second id the same part also answers.
    by_name: dict[str, dict[str, list[Record]]] = defaultdict(lambda: defaultdict(list))
    chips: dict[str, Flash] = {}
    for f in flashes:
        if f.family != IdFamily.JEDEC:
            continue
        chips[f.key] = f
        for r in f.records:
            for name in r.part_names:
                if "." not in name:  # a flashrom wildcard stands for several parts
                    by_name[name][f.key].append(r)
    for name in sorted(by_name):
        ids = by_name[name]
        if len(ids) > 1:
            answers = _answers((key, r) for key, rs in ids.items() for r in rs)
            concerned = tuple(chips[a.value] for a in answers)
            yield Issue(IssueKind.NAME_IDS, name, concerned, answers)


def _manufacturers(flashes: Iterable[Flash]) -> Iterator[Issue]:
    for f in flashes:
        answers = _answers((r.manufacturer, r) for r in f.records)
        if len(answers) > 1:
            yield Issue(IssueKind.MANUFACTURER, f.key, (f,), answers)


def _datasheets(flashes: Iterable[Flash]) -> Iterator[Issue]:
    # Per part: another part's datasheet giving the same id does not make
    # this part's id right.
    for f in flashes:
        for part in f.names:
            own = tuple(d for d in f.datasheets if part in d.parts)
            if own and not any(f.confirms(d) for d in own):
                listing = tuple(r for r in f.records if part in r.part_names)
                yield Issue(
                    IssueKind.DATASHEET,
                    f.key,
                    (f,),
                    (Answer(f.key, listing or f.records),),
                    part=part,
                    datasheets=own,
                )
