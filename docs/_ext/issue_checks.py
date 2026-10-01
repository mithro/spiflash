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

import re
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from spiflash import database
from spiflash.enums import IdFamily, Source
from spiflash.model import COMPARED_VALUES, register_bits, same_supply_part

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from spiflash import Database, Datasheet, Flash, Record


class IssueKind(StrEnum):
    """A kind of conflict or error in the source data; the members are in
    the order the site lists them."""

    VALUE = "value"
    SAME_SOURCE = "same-source"
    SUPPLY = "supply"
    SFDP = "sfdp"
    TIMING = "timing"
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
            "dies, die select bit, OTP area, or erase, program or "
            "deep power-down time."
        ),
    ),
    IssueKind.SAME_SOURCE: (
        "One source, two values",
        (
            "One source lists one part more than once at a chip id, with different "
            "values, and nothing in the id (no extended id) tells the entries apart. "
            "Entries for different parts sharing the id (the EN25Q32 and the EN25Q32C) "
            "are not compared with each other."
        ),
    ),
    IssueKind.SUPPLY: (
        "A programmer's supply outside the part's range",
        (
            "A programmer's table (Dediprog's or IMSProg's) says to power a part at "
            "a voltage outside the supply range another source gives the same part."
        ),
    ),
    IssueKind.SFDP: (
        "A source disagrees with its own SFDP tables",
        (
            "A source gives a part a size, page size, erase layout, quad enable "
            "requirement, number of dies or time, and with it the part's own SFDP "
            "tables, which say otherwise."
        ),
    ),
    IssueKind.TIMING: (
        "Sources give a typical time above a maximum",
        (
            "One source gives a chip a typical time (or a minimum) above the maximum "
            "another source gives the same thing, for the same chip id."
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

_TWO_PARTS = (
    "Two parts share this id: the N25Q00AA (four 256 Mbit dies) and the {part} "
    "(two 512 Mbit dies), and the sources' entries give each its own: no "
    "source is wrong."
)
_LUNS = (
    "Rockchip's FTL takes every part as one die (its die_num is 1), so its "
    "plane_per_die on this two-LUN part counts the LUNs too; Linux gives one "
    "plane in each of two LUNs."
)

_AT45 = (
    'flashrom\'s comment on the entry: "2.5-3.6V & 2.7-3.6V models available"; it '
    "gives the 2.7 V model's range, and Dediprog's 2.5 V is the other model's: no "
    "source is wrong."
)
_MX25V = (
    "flashrom's entry covers the {l} and the {v}, and gives the {l}'s 2.7 V to 3.6 V; "
    'its comment gives "2.35-3.6V for {v}", which Dediprog\'s 2.5 V for the MX25V is '
    "in: no source is wrong."
)

#: What is known of an issue, by its chip and value: why the sources
#: disagree where that is not plain from their answers.
EXPLAINED = {
    ("c22817", "timings.dpd_exit.maximum"): (
        "The MX25R6435F datasheet (Rev. 1.6) gives tRDP 35 µs in ultra low power mode and "
        "45 µs in high performance mode, and its BFPT 40 µs: nrf7002dk's t-exit-dpd of 5 µs "
        "is wrong."
    ),
    ("c22817", "timings.erase_resume_to_suspend.typical"): (
        "Two versions of the MX25R6435F's BFPT, which Zephyr's boards copy, differ in DW12's "
        "erase resume-to-suspend interval alone."
    ),
    ("ef4017", "timings.dpd_exit.maximum"): (
        "frdm_mcxe247's W25Q64 carries the MX25R6435F's BFPT, whose DW14 gives 40 µs; the "
        "W25Q64JV's tRES1 is 3 µs."
    ),
    ("20ba21", "dies"): _TWO_PARTS.format(part="MT25QL01GBBB"),
    ("20bb21", "dies"): _TWO_PARTS.format(part="MT25QU01GBBB"),
    ("20ba22", "dies"): (
        'The MT25QL02G has four 512 Mbit dies (its datasheet: "Stacked device (four '
        "512Mb die)\"): QEMU's die_cnt of 2 is wrong."
    ),
    ("20bb22", "dies"): (
        "The MT25QU02G has four 512 Mbit dies, as the MT25QL02G: QEMU's die_cnt of 2 is wrong."
    ),
    ("2c5b1c", "dies"): (
        "The MT35XU02G has four dies (the C of its part number, MT35XU02GCBA, is "
        "four dies): Linux's two-die fixup for it (micron-st.c, mt35_two_die_fixups) "
        "is wrong."
    ),
    ("c845", "planes"): _LUNS,
    ("c855", "planes"): _LUNS,
    ("0b51", "oob_size"): (
        "The XT26Q01D datasheet gives 128 B of spare per page: Dediprog's 64 is wrong."
    ),
    ("1f8901", "supply_mv"): (
        "The AT25SF128A datasheet gives a supply of 2.7 V to 3.6 V: flashrom's 1.7 V to "
        "2.0 V range is wrong, and Dediprog's 3.3 V right."
    ),
    ("010219", "supply_mv"): (
        "The S25FL256S is a 2.7 V to 3.6 V part, as flashrom's S25FL256S......0 entry "
        'says; its "S25FL256S Large Sectors" and "Small Sectors" entries give 1.7 V to '
        "2.0 V, the S25FS256S's, so Dediprog's 3.3 V for the S25FL256S is outside a "
        "wrong range."
    ),
    ("1f2400", "supply_mv"): _AT45,
    ("1f2500", "supply_mv"): _AT45,
    ("1f2600", "supply_mv"): _AT45,
    ("c22010", "supply_mv"): _MX25V.format(l="MX25L512(E)", v="MX25V512(C)"),
    ("c22014", "supply_mv"): _MX25V.format(l="MX25L8005", v="MX25V8005"),
    ("c22015", "supply_mv"): (
        "The MX25V16066 datasheet (v1.5) gives 2.3 V to 3.6 V: flashrom's 2.7 V to 3.6 V "
        "for its MX25V16066 entry is wrong, and Dediprog's 2.5 V right."
    ),
    ("1f4502", "supply_mv"): (
        "flashrom's comment on the AT25DF081 says its datasheet gives 1.65 V to 1.95 V: "
        "Dediprog's 3.3 V is wrong for it."
    ),
    ("1c3813", "supply_mv"): (
        "The EN25S40 is a 1.65 V to 1.95 V part (Eon's datasheet): Dediprog's 3.3 V is wrong."
    ),
    ("ef5014", "supply_mv"): (
        "The W25Q80BW is a 1.65 V to 1.95 V part (Winbond's datasheet, Rev. L): Dediprog's "
        "and IMSProg's 3.3 V are wrong."
    ),
    ("c22515", "otp.size"): (
        "The MX25L1635E datasheet (v1.6) gives a 4K-bit (512-byte) secured OTP area: "
        "flashprog's 64 B is wrong for it."
    ),
    ("ef6016", "otp.size"): (
        'The W25Q32DW has four 256-byte security registers, register 0 "Reserved by '
        "Winbond for future use\" (its datasheet, Rev. E): flashrom's 1024 B counts it, "
        "Linux's 768 B (three regions) does not."
    ),
    ("c84018", "otp.size"): (
        "Parts sharing the id: the GD25Q128B, C and E. The GD25Q128B's 768 B is flashrom's "
        '"1024B total, 256B reserved"; whether 256 B are reserved is uncertain (its '
        "datasheet is said to disagree with itself; not checked here)."
    ),
    ("c86318", "otp.size"): (
        "flashrom gives the GD25LF128E 1024 B less 256 B reserved, flashprog three 1 KiB "
        "regions; the datasheet was not to hand to say which is right."
    ),
}


@dataclass(frozen=True, slots=True)
class Answer:
    """One answer to an issue's question, and the records giving it."""

    #: A size in bytes, a :class:`~spiflash.Voltage`, a supply setting in
    #: millivolts, a register bit, a quad
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
    #: Why the sources disagree, where that is known (:data:`EXPLAINED`).
    note: str | None = None

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
        *_supply(db.flashes),
        *_sfdp(db.flashes),
        *_timing(db.flashes),
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


def _value_answers(attr: str, records: Iterable[Record]) -> tuple[Answer, ...]:
    """The records grouped by the value they give for ``attr``, as they are
    compared (:meth:`spiflash.Record.compared`). A time is compared at SFDP
    resolution, but shown as given: its answer's value is the times its
    records give, in order (``(33000, 35000)``, both 40 µs on the grid)."""
    records = list(records)
    answers = _answers((r.compared(attr), r) for r in records)
    if not attr.startswith("timings."):
        return answers
    return tuple(
        Answer(tuple(sorted({r.given(attr) for r in a.records})), a.records) for a in answers
    )


def _compared(f: Flash) -> tuple[str, ...]:
    """:data:`ATTRIBUTES`, but the times where no record of ``f`` gives one
    (most chips): those cannot disagree."""
    if any(r.timings for r in f.records):
        return ATTRIBUTES
    return tuple(a for a in ATTRIBUTES if not a.startswith("timings."))


def _values(flashes: Iterable[Flash]) -> Iterator[Issue]:
    for f in flashes:
        for attr in _compared(f):
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
                answers = _value_answers(attr, disagree.values())
                note = EXPLAINED.get((f.key, attr))
                yield Issue(IssueKind.VALUE, f.key, (f,), answers, attribute=attr, note=note)


#: An ordering code's package and temperature tail after a hyphen
#: (W25Q512JV-IQ, -IN, -IM): the same part.
_ORDER_TAIL = re.compile(r"-[A-Z]{2}$")


def part_key(name: str) -> str:
    """A part name as one source's entries are grouped by it for
    :attr:`IssueKind.SAME_SOURCE`: upper case, without hyphens, underscores
    and spaces, nor an ordering code's two-letter tail (``W25Q512JV-IQ``
    is the W25Q512JV). A revision letter is kept: the EN25Q32 and EN25Q32C
    are two parts."""
    return re.sub(r"[-_ ]", "", _ORDER_TAIL.sub("", name.upper()))


def _same_source(flashes: Iterable[Flash]) -> Iterator[Issue]:
    """One source's entries for one part, at one id, that disagree: the
    entries are grouped by source, extended id and part name
    (:func:`part_key`), as parts sharing an id (the S25FL256S and the
    S25FS256S, the AT25SF321 and the AT25SF321B) may each be right."""
    for f in flashes:
        groups: dict[tuple[Source, bytes | None, str], list[Record]] = defaultdict(list)
        for r in f.records:
            groups[(r.source, r.ext_id, part_key((r.part_names or (r.name,))[0]))].append(r)
        for (_source, _ext, _part), records in sorted(
            groups.items(), key=lambda kv: kv[0][0].priority
        ):
            for attr in _compared(f):
                answers = _value_answers(attr, records)
                if len(answers) > 1:
                    note = EXPLAINED.get((f.key, attr))
                    yield Issue(
                        IssueKind.SAME_SOURCE, f.key, (f,), answers, attribute=attr, note=note
                    )


def _supply(flashes: Iterable[Flash]) -> Iterator[Issue]:
    """One issue per chip a programmer's table says to power outside the
    supply range another source gives the same part
    (:meth:`spiflash.Flash.supply_outside`): the answers are the ranges (a
    :class:`~spiflash.Voltage`) of the parts concerned, then the settings
    outside them (millivolts)."""
    for f in flashes:
        outside = f.supply_outside()
        if not outside:
            continue
        ranges = [(r.voltage, r) for r in f.records if r.voltage is not None]
        settings = [(mv, r) for mv, rs in outside.items() for r in rs]
        answers = (*_answers(ranges), *_answers(settings))
        computed = _other_parts(ranges, settings)
        explained = EXPLAINED.get((f.key, "supply_mv"))
        note = " ".join(n for n in (computed, explained) if n) or None
        yield Issue(IssueKind.SUPPLY, f.key, (f,), answers, attribute="supply_mv", note=note)


def _other_parts(
    ranges: list[tuple[Any, Record]], settings: list[tuple[Any, Record]]
) -> str | None:
    """Where the supply settings outside the ranges are for other parts
    than the ranges (Dediprog's 1.8 V for Puya's P25Q32L, against the
    P25Q32H's 2.3 to 3.6 V, at one id): saying so."""
    ranged = {n for _, r in ranges for n in r.part_names}
    set_for = {n for _, r in settings for n in r.part_names}
    if any(same_supply_part(a, b) for a in ranged for b in set_for):
        return None
    return (
        f"Parts sharing the id: the ranges are given for {', '.join(sorted(ranged))}, "
        f"the supplies outside them for {', '.join(sorted(set_for))}."
    )


def _sfdp(flashes: Iterable[Flash]) -> Iterator[Issue]:
    """One issue per record and field its own SFDP tables give otherwise
    (:meth:`spiflash.Record.sfdp_disagreements`): the answers are what the
    entry states, then what its tables say, both the record's."""
    for f in flashes:
        for r in f.records:
            for d in r.sfdp_disagreements():
                answers = (Answer(d.stored, (r,)), Answer(d.sfdp, (r,)))
                yield Issue(IssueKind.SFDP, f.key, (f,), answers, attribute=d.field)


def _timing(flashes: Iterable[Flash]) -> Iterator[Issue]:
    """One issue per pair of a chip's bounds out of order
    (:meth:`spiflash.Flash.timing_order`): the answers are the lower bound,
    ``(bound, ns)``, and the records giving it, then the higher; the
    attribute is the time's key (``"chip_erase"``)."""
    for f in flashes:
        for key, low, a, high, b in f.timing_order():
            answers = tuple(
                Answer(
                    (bound, ns),
                    tuple(r for r in f.records if r.timings.values.get((key, bound)) == ns),
                )
                for bound, ns in ((low, a), (high, b))
            )
            note = EXPLAINED.get((f.key, f"timings.{key}"))
            yield Issue(IssueKind.TIMING, f.key, (f,), answers, str(key), note=note)


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
