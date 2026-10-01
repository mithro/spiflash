"""The data issues pages: every conflict or error :mod:`issue_checks`
finds in the source data, all on one page, by kind, and by source.

Each issue is shown with every answer the sources give and the records
giving it, each linked to its upstream line, so a reader can check it. A
source's label links to its page; the arrow after it, to the line.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from issue_checks import IssueKind, find
from page_markup import (
    EM_DASH,
    EM_SPACE,
    UP_ARROW,
    common_unit,
    count,
    eraser_text,
    esc,
    list_table,
    more,
    size_text,
    spaced,
    vendor_link,
    vendor_of,
    volt,
)
from spiflash.enums import IdFamily, Source
from spiflash.model import Eraser
from spiflash.registers import NoQuadEnable, QuadEnableRequirement, RegisterBit

if TYPE_CHECKING:
    from collections.abc import Callable

    from issue_checks import Answer, Issue
    from spiflash import Database, Flash, Record

#: Per kind: why such issues arise, and how far to trust one.
KIND_NOTES = {
    IssueKind.VALUE: (
        "Not every one is an error. Parts sharing an id can differ: a 3 V and a "
        "1.8 V part, or two sector layouts that only the extended id tells apart "
        "(each entry's extended id is on its chip page). Where one source stands "
        "alone against the rest, check it against the datasheet. The chip pages "
        "show the value most sources give."
    ),
    IssueKind.SAME_SOURCE: (
        "A program that identifies a chip by its id alone cannot tell which of "
        "these entries applies. Often they are variants the source tells apart some "
        "other way; sometimes one entry is simply wrong."
    ),
    IssueKind.SUPPLY: (
        "The supply a programmer's table gives is the voltage that programmer "
        "powers the part at, a setting of its own rather than a datasheet range. "
        "Most of these are parts sharing an id, each with its own supply: Puya's "
        "P25Q32H, which flashrom gives 2.3 V to 3.6 V, and P25Q32L, which Dediprog "
        "powers at 1.8 V, answer one id; Macronix's MX25V and MX25L, and Winbond's "
        "W25X..BL and W25X..BV, likewise. The note says where the parts differ; "
        "where the same part has both, one source is wrong."
    ),
    IssueKind.SFDP: (
        "The value the source states is the one its own code uses, and the one the "
        "database keeps for that source; the tables are shown as it carries them. A "
        "table may be another part's, copied by a board's porter: {sfsrc}`zephyr`'s "
        "wio_tracker_l1 gives its 2 MiB P25Q16H a 16 MiB part's BFPT, with DTR; and "
        "frdm_mcxe247's W25Q64 carries the MX25R6435F's byte for byte, which agrees "
        "with the part's size and page but not with the quad enable requirement the "
        "board gives it (its [source notes](../sources/zephyr.md) say more). "
        "`spiflash sfdp-diff` "
        "compares tables field by field and dword by dword."
    ),
    IssueKind.SHARED_BIT: (
        "Mostly two sources naming one bit by different roles: flashrom names a "
        "bit by what it does (a BP3 that works as TB is its `tb`), openFPGALoader "
        "by the datasheet's name. The chip page shows one source's whole layout "
        "instead of the role-by-role answer."
    ),
    IssueKind.NAME_IDS: (
        "Some are one name for parts with different ids: a generic name, or a 3 V "
        "and a 1.8 V version. Others are a wrong id in one source. "
        "**Datasheet** marks an id that the part's own datasheet gives; "
        "when one side has it, the other side is likely wrong."
    ),
    IssueKind.MANUFACTURER: (
        f"Mostly company history rather than errors: {vendor_link('Atmel')}'s serial "
        f"flash went to {vendor_link('Adesto')}, then to Dialog and Renesas; "
        f"{vendor_link('Spansion')} merged with {vendor_link('Cypress')}, which Infineon "
        "bought. The site uses the name most sources give."
    ),
    IssueKind.DATASHEET: (
        "The check looks for the id's bytes in the text of each of the part's "
        'datasheets ("EFh 40h 18h", "0xEF4018", ...). A document that gives '
        "them in an unusual form, or only in a figure, is flagged wrongly; but "
        "where the document plainly gives a different id, the sources' id is "
        "likely the error. The chip pages link every datasheet."
    ),
}

_TABLE = "sf-table sf-filterable sf-issues"

#: The values sources are compared on (:data:`spiflash.model.COMPARED`),
#: as headings: a value compared component by component is one section.
VALUE_TITLES = {
    "size": "Size",
    "page_size": "Page size",
    "sector_size": "Sector size",
    "voltage": "Supply voltage",
    "quad_enable": "Quad enable bit",
    "quad_enable_requirement": "Quad enable requirement",
    "protection": "Block protection bits",
    "oob_size": "Spare area per page",
    "planes": "Planes",
    "dies": "Dies",
    "die_select_bit": "Die select bit",
    "max_bad_blocks": "Bad blocks per die",
    "ecc": "ECC requirement",
    "otp": "OTP area",
}

#: The compared values that are counts, not sizes in bytes.
COUNTS = frozenset({"planes", "dies", "max_bad_blocks", "ecc.strength_bits", "otp.regions"})


def value_of(attribute: str) -> str:
    """The compared value an issue's attribute is of: ``"protection"`` for
    ``"protection.tb"``."""
    return attribute.partition(".")[0]


#: The kinds of issue about a value, shown a section per value.
BY_ATTRIBUTE = (IssueKind.VALUE, IssueKind.SAME_SOURCE)


def attr_target(kind: IssueKind, attr: str) -> str:
    """The target, on the page of ``kind``, of its section on ``attr``: its
    HTML id too, which is why it has no underscores (Sphinx makes them
    dashes)."""
    return f"{kind_page(kind)}-{attr.replace('_', '-')}"


def kind_page(kind: IssueKind) -> str:
    return str(kind)


def source_page(source: Source) -> str:
    return f"source-{source}"


def source_link(source: Source, issues: list[Issue]) -> str:
    """A source's label, then how many of ``issues`` it is part of, linked
    to its data issues page (from ``issues/``)."""
    return f"{{sfsrc}}`{source}` [{len(_involving(issues, source))}]({source_page(source)}.md)"


class _Render:
    """Markdown for issues, for pages in ``docs/issues/``."""

    def __init__(self, db: Database, slugs: dict[int, str], focus: Source | None = None) -> None:
        self.db = db
        self.slugs = slugs
        #: On a source's page: its records are in bold.
        self.focus = focus
        self.chips = {f.key: f for f in db.flashes}
        #: The manufacturers with a vendor page.
        self.vendors = {vendor_of(f) for f in db.flashes}

    def chip(self, f: Flash) -> str:
        shown = spaced(f.id_hex)
        if f.family != IdFamily.JEDEC:
            shown = f"{f.family.upper()} {shown}"
        return f"[{shown}](../chips/{self.slugs[id(f)]}.md)"

    @staticmethod
    def names(f: Flash) -> str:
        return more([esc(n) for n in f.names[:2]], list(f.names[2:]))

    def record(self, r: Record) -> str:
        link = self.db.link(r)
        return f"[{esc(r.name)}]({link})" if link else f"{esc(r.name)} ({esc(r.url)})"

    def who(self, records: tuple[Record, ...]) -> str:
        """A label per source giving ``records``, with a count when it has
        several, and an arrow after it linked to its (first) entry upstream:
        the entries themselves are on the chip page."""
        badges = []
        for s in sorted({r.source for r in records}, key=lambda s: s.priority):
            mine = [r for r in records if r.source == s]
            label = str(s) + (f" \u00d7{len(mine)}" if len(mine) > 1 else "")
            link = self.db.link(mine[0])
            role = "sfsrcme" if s == self.focus else "sfsrc"
            badges.append(f"{{{role}}}`{label} <{link}>`" if link else f"{{{role}}}`{label}`")
        return " ".join(badges)

    def entries(self, records: tuple[Record, ...]) -> str:
        """The entries themselves, linked: for one source's own entries."""
        return ", ".join(self.record(r) for r in records)

    def value(self, issue: Issue, v: Any) -> str:
        if isinstance(v, Eraser):
            return esc(eraser_text(v))
        if issue.kind is IssueKind.SHARED_BIT:
            role, bit = v
            return f"{esc(role)}: {esc(str(bit))}"
        if isinstance(v, RegisterBit | NoQuadEnable | QuadEnableRequirement):
            role = (issue.attribute or "").partition(".")[2]
            return esc(f"{role}: {v}" if role else str(v))
        if issue.attribute == "supply_mv":
            # A supply range, or a programmer's supply setting.
            return f"{volt(v[0])}{EM_SPACE}{volt(v[1])}" if isinstance(v, tuple) else volt(v)
        if issue.attribute == "voltage":
            return f"{volt(v[0])}{EM_SPACE}{volt(v[1])}" if v else volt(None)
        if issue.attribute == "ecc.strength_bits":
            return esc(f"{v} bit{'s' if v != 1 else ''}")
        if issue.attribute in COUNTS:
            return esc(str(v))
        if issue.attribute:
            # One unit for all the answers, so their digits line up.
            return size_text(v, common_unit(a.value for a in issue.answers))
        if issue.kind is IssueKind.NAME_IDS:
            return self.chip(self.chips[v])
        if issue.kind is IssueKind.MANUFACTURER and v in self.vendors:
            return vendor_link(v)
        return esc(str(v))

    def answers(
        self,
        issue: Issue,
        extra: Callable[[Answer], str] | None = None,
        *,
        by_entry: bool = False,
    ) -> str:
        """One line per answer: the value, then who gives it, each in a span
        of its own, so a long list of who wraps in its own column."""
        return "\n\n".join(
            f"[**{self.value(issue, a.value)}**{extra(a) if extra else ''}]{{.sf-val}}"
            f"[{self.entries(a.records) if by_entry else self.who(a.records)}]{{.sf-who}}"
            for a in issue.answers
        )

    def section(
        self, kind: IssueKind, issues: list[Issue], level: int, *, targets: bool = False
    ) -> str:
        """The issues of ``kind``: one table, or for issues about a value one
        per value, each under a heading of ``level`` (with a target, if
        ``targets``: only the kind's own page may have them)."""
        if kind not in BY_ATTRIBUTE or not issues:
            return self.table(kind, issues)
        out = []
        for attr, heading in VALUE_TITLES.items():
            mine = [i for i in issues if value_of(i.attribute or "") == attr]
            if mine:
                if targets:
                    out.append(f"({attr_target(kind, attr)})=")
                out.append(f"{'#' * level} {heading} ({len(mine)})\n")
                out.append(self.table(kind, mine))
        return "\n".join(out)

    def table(self, kind: IssueKind, issues: list[Issue]) -> str:
        if not issues:
            return "None found.\n"
        rows: list[list[str]]
        if kind is IssueKind.VALUE:
            volts = issues[0].attribute == "voltage"
            header = ["Chip", "Parts", "Answers: V min, V max" if volts else "Answers"]
            rows = [
                [self.chip(i.flashes[0]), self.names(i.flashes[0]), self.answers(i) + _note(i)]
                for i in issues
            ]
        elif kind is IssueKind.SAME_SOURCE:
            volts = issues[0].attribute == "voltage"
            header = ["Chip", "Parts", "Source", "Entries: V min, V max" if volts else "Entries"]
            rows = [
                [
                    self.chip(i.flashes[0]),
                    self.names(i.flashes[0]),
                    self.who(i.answers[0].records[:1]),
                    self.answers(i, by_entry=True) + _note(i),
                ]
                for i in issues
            ]
        elif kind is IssueKind.SUPPLY:
            header = ["Chip", "Parts", "Ranges: V min, V max", "Supplies outside them"]
            rows = []
            for i in issues:
                given = [a for a in i.answers if isinstance(a.value, tuple)]
                ranges = replace(i, answers=tuple(given))
                settings = replace(i, answers=tuple(a for a in i.answers if a not in given))
                rows.append(
                    [
                        self.chip(i.flashes[0]),
                        self.names(i.flashes[0]),
                        self.answers(ranges),
                        self.answers(settings) + _note(i),
                    ]
                )
        elif kind is IssueKind.SFDP:
            header = ["Chip", "Parts", "Source", "Field", "The entry says", "Its SFDP tables say"]
            rows = [
                [
                    self.chip(i.flashes[0]),
                    self.names(i.flashes[0]),
                    self.who(i.answers[0].records),
                    _attr(i),
                    self.value(i, i.answers[0].value),
                    self.value(i, i.answers[1].value),
                ]
                for i in issues
            ]
        elif kind is IssueKind.SHARED_BIT:
            header = ["Chip", "Parts", "Bit", "Roles on it, and who gives each"]
            rows = [
                [
                    self.chip(i.flashes[0]),
                    self.names(i.flashes[0]),
                    esc(i.attribute or EM_DASH),
                    self.answers(i),
                ]
                for i in issues
            ]
        elif kind is IssueKind.NAME_IDS:
            header = ["Part", "Ids, and who lists each"]
            rows = [[esc(i.subject), self.answers(i, self.shown(i))] for i in issues]
        elif kind is IssueKind.MANUFACTURER:
            header = ["Chip", "Parts", "Manufacturers"]
            rows = [
                [self.chip(i.flashes[0]), self.names(i.flashes[0]), self.answers(i)] for i in issues
            ]
        else:
            header = ["Chip", "Part", "Listed by", "Datasheets"]
            rows = [
                [
                    self.chip(i.flashes[0]),
                    esc(i.part or EM_DASH),
                    self.who(i.answers[0].records),
                    "\n\n".join(f"[{esc(d.title)}](<{d.url}>)" for d in i.datasheets),
                ]
                for i in issues
            ]
        return list_table(header, rows, _TABLE) + "\n"

    def shown(self, issue: Issue) -> Callable[[Answer], str]:
        """For a part's ids: marks the ones its own datasheet gives."""

        def mark(a: Answer) -> str:
            f = self.chips[a.value]
            gives = any(f.confirms(d) and issue.subject in d.parts for d in f.datasheets)
            return " {bdg-success}`datasheet`" if gives else ""

        return mark


def _note(issue: Issue) -> str:
    """Why the sources disagree, where it is known, after the answers."""
    return f"\n\n*{esc(issue.note)}*" if issue.note else ""


def _attr(issue: Issue) -> str:
    about = (issue.attribute or "").replace("_", " ").replace(".", ": ")
    return f"{about}: V min, V max" if issue.attribute == "voltage" else about


def _involving(issues: list[Issue], source: Source) -> list[Issue]:
    return [i for i in issues if source in i.sources]


def _of(issues: list[Issue], kind: IssueKind) -> list[Issue]:
    return [i for i in issues if i.kind is kind]


def _summary_row(title: str, issues: list[Issue]) -> list[str]:
    """A row of the summary: the issues, then those of each source."""
    return [title, count(len(issues)), *(count(len(_involving(issues, s))) for s in Source)]


def index_page(r: _Render, issues: list[Issue]) -> str:
    out = [
        "# Data issues\n",
        (
            "Everything the checks find wrong or "
            "contradictory in the source data: where the upstream tables disagree with "
            "each other, with themselves, or with the datasheets. Every answer links to "
            "the upstream line that gives it. There is a page for "
            "[each kind of issue](#by-kind) and for [each source](#by-source).\n"
        ),
        "## Summary\n",
    ]
    header = ["Kind", "All", *(f"{{sfsrc}}`{s}`" for s in Source)]
    rows = []
    for kind in IssueKind:
        found = _of(issues, kind)
        rows.append(_summary_row(f"[{kind.heading}]({kind_page(kind)}.md)", found))
        if kind in BY_ATTRIBUTE:
            # A sub-row per value.
            for attr, heading in VALUE_TITLES.items():
                mine = [i for i in found if value_of(i.attribute or "") == attr]
                title = heading
                if mine:  # only then has it a section to link to
                    title += f" <{kind_page(kind)}.html#{attr_target(kind, attr)}>"
                rows.append(_summary_row(f"{{sfsub}}`{title}`", mine))
    rows.append(
        [
            "**All**",
            f"**{count(len(issues))}**",
            *(f"[**{count(len(_involving(issues, s)))}**]({source_page(s)}.md)" for s in Source),
        ]
    )
    out.append(list_table(header, rows, "sf-table sf-issue-summary") + "\n")
    out.append(
        "A source's count is the issues it is part of: an issue between two "
        "sources counts for both. Its label links to [its page](../sources/index.md); "
        "its total, to its issues.\n"
    )
    out.append("(by-kind)=\n## By kind\n")
    for kind in IssueKind:
        found = _of(issues, kind)
        out.append(f"### {kind.heading}\n")
        out.append(
            f"{kind.description} {len(found)} found; "
            f"[the {kind.heading.lower()} page]({kind_page(kind)}.md) says more.\n"
        )
        out.append(r.section(kind, found, 4))
    out.append("(by-source)=\n## By source\n")
    out.append(" · ".join(source_link(s, issues) for s in Source) + "\n")
    pages = [kind_page(k) for k in IssueKind] + [source_page(s) for s in Source]
    out.append("```{toctree}\n:hidden:\n\n" + "\n".join(pages) + "\n```\n")
    return "\n".join(out)


def kind_markdown(r: _Render, kind: IssueKind, issues: list[Issue]) -> str:
    found = _of(issues, kind)
    return "\n".join(
        [
            f"# {kind.heading}\n",
            f"{{bdg-primary}}`{len(found)} found`\n",
            f"{kind.description} {KIND_NOTES[kind]}\n",
            "By source: "
            + ", ".join(source_link(s, found) for s in Source)
            + ". All the kinds: [Data issues](index.md).\n",
            "Type in the box to filter.\n",
            r.section(kind, found, 2, targets=True),
        ]
    )


def source_markdown(r: _Render, source: Source, issues: list[Issue]) -> str:
    mine = _involving(issues, source)
    out = [
        f"# Data issues: {{sfsrc}}`{source}`\n",
        f"{{bdg-primary}}`{len(mine)} issues` {{sfsrc}}`{source}`\n",
        (
            f"Every issue {{sfsrc}}`{source}` is part of; its own labels are ringed. "
            "An issue between two sources is on both their pages, and does not say "
            f"which is wrong. What {source.label} is and what it gives: "
            f"[its page](../sources/{source}.md). All the sources: [Data issues](index.md).\n"
        ),
    ]
    for kind in IssueKind:
        found = _of(mine, kind)
        out.append(f"## {kind.heading}\n")
        out.append(f"{kind.description} [More on these]({kind_page(kind)}.md).\n")
        out.append(r.section(kind, found, 3))
    return "\n".join(out)


def chip_issues(db: Database, slugs: dict[int, str], f: Flash, issues: list[Issue]) -> list[str]:
    """The issues about chip ``f``, for its page's "What each source says"."""
    mine = [i for i in issues if any(g is f for g in i.flashes)]
    if not mine:
        return []
    r = _Render(db, slugs)
    rows = []
    for i in mine:
        if i.kind is IssueKind.DATASHEET:
            about = esc(i.part or EM_DASH)
            sheets = ", ".join(f"[{esc(d.title)}](<{d.url}>)" for d in i.datasheets)
            answer = (
                f"Not found in {sheets}\n\nListed under this id by {r.who(i.answers[0].records)}"
            )
        elif i.kind is IssueKind.NAME_IDS:
            about = esc(i.subject)
            answer = r.answers(i, r.shown(i))
        elif i.kind is IssueKind.SAME_SOURCE:
            about = _attr(i)
            answer = r.who(i.answers[0].records[:1]) + "\n\n" + r.answers(i, by_entry=True)
        elif i.kind is IssueKind.MANUFACTURER:
            about = "manufacturer"
            answer = r.answers(i)
        elif i.kind is IssueKind.SFDP:
            about = _attr(i)
            answer = (
                f"{r.value(i, i.answers[0].value)} in the entry, "
                f"{r.value(i, i.answers[1].value)} in its SFDP tables\n\n"
                f"{r.who(i.answers[0].records)}"
            )
        else:
            about = _attr(i)
            answer = r.answers(i)
        rows.append(
            [f"[{i.kind.heading}](../issues/{kind_page(i.kind)}.md)", about, answer + _note(i)]
        )
    return [
        "### Conflicts and errors\n",
        (
            "What the checks behind [Data issues](../issues/index.md) find about this chip. "
            f"Each source's {UP_ARROW} links to its entry upstream.\n"
        ),
        list_table(["Kind", "About", "Answers"], rows, "sf-table sf-issues sf-chip-issues"),
        "",
    ]


def generate_all(
    db: Database, slugs: dict[int, str], issues: list[Issue] | None = None
) -> dict[str, str]:
    """Every page of ``docs/issues/``, by file name."""
    issues = find(db) if issues is None else issues
    everyone = _Render(db, slugs)
    pages = {"index.md": index_page(everyone, issues)}
    for kind in IssueKind:
        pages[f"{kind_page(kind)}.md"] = kind_markdown(everyone, kind, issues)
    for source in Source:
        pages[f"{source_page(source)}.md"] = source_markdown(
            _Render(db, slugs, focus=source), source, issues
        )
    return pages
