"""The data issues pages: every conflict or error :mod:`issue_checks`
finds in the source data, all on one page, by kind, and by source.

Each issue is shown with every answer the sources give and the records
giving it, each linked to its upstream line, so a reader can check it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from issue_checks import ATTRIBUTES, IssueKind, find
from page_markup import (
    EM_DASH,
    count,
    esc,
    list_table,
    more,
    size_text,
    spaced,
    vendor_link,
    vendor_of,
    volts,
)
from spiflash.enums import IdFamily, Source

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

#: The values sources are compared on, as headings.
VALUE_TITLES = {
    "size": "Size",
    "page_size": "Page size",
    "sector_size": "Sector size",
    "voltage": "Supply voltage",
}


def kind_page(kind: IssueKind) -> str:
    return str(kind)


def source_page(source: Source) -> str:
    return f"source-{source}"


def source_link(source: Source) -> str:
    """A source's label, linked to its data issues page (from ``issues/``)."""
    return f"{{sfsrc}}`{source} <{source_page(source)}.html>`"


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
        """A label per source giving ``records``, linked to its (first) entry
        upstream, with a count when it has several: the entries themselves
        are on the chip page."""
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
        if issue.attribute == "voltage":
            return volts(v)
        if issue.attribute:
            return size_text(v)
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
        """One line per answer: the value, then who gives it."""
        return "\n\n".join(
            f"**{self.value(issue, a.value)}**"
            + (extra(a) if extra else "")
            + f"\u2003{self.entries(a.records) if by_entry else self.who(a.records)}"
            for a in issue.answers
        )

    def section(self, kind: IssueKind, issues: list[Issue], level: int) -> str:
        """The issues of ``kind``: one table, or for disagreeing values one
        per value, each under a heading of ``level``."""
        if kind is not IssueKind.VALUE or not issues:
            return self.table(kind, issues)
        out = []
        for attr in ATTRIBUTES:
            mine = [i for i in issues if i.attribute == attr]
            if mine:
                out.append(f"{'#' * level} {VALUE_TITLES[attr]} ({len(mine)})\n")
                out.append(self.table(kind, mine))
        return "\n".join(out)

    def table(self, kind: IssueKind, issues: list[Issue]) -> str:
        if not issues:
            return "None found.\n"
        rows: list[list[str]]
        if kind is IssueKind.VALUE:
            header = ["Chip", "Parts", "Answers"]
            rows = [
                [self.chip(i.flashes[0]), self.names(i.flashes[0]), self.answers(i)] for i in issues
            ]
        elif kind is IssueKind.SAME_SOURCE:
            header = ["Chip", "Parts", "Source", "Value", "Entries"]
            rows = [
                [
                    self.chip(i.flashes[0]),
                    self.names(i.flashes[0]),
                    self.who(i.answers[0].records[:1]),
                    _attr(i),
                    self.answers(i, by_entry=True),
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
            gives = any(f.key in d.confirmed and issue.subject in d.parts for d in f.datasheets)
            return " {bdg-success}`datasheet`" if gives else ""

        return mark


def _attr(issue: Issue) -> str:
    return (issue.attribute or "").replace("_", " ")


def _involving(issues: list[Issue], source: Source) -> list[Issue]:
    return [i for i in issues if source in i.sources]


def _of(issues: list[Issue], kind: IssueKind) -> list[Issue]:
    return [i for i in issues if i.kind is kind]


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
    header = ["Kind", "All", *(source_link(s) for s in Source)]
    rows = [
        [
            f"[{kind.heading}]({kind_page(kind)}.md)",
            count(len(_of(issues, kind))),
            *(count(len(_involving(_of(issues, kind), s))) for s in Source),
        ]
        for kind in IssueKind
    ]
    rows.append(
        ["**All**", f"**{len(issues)}**", *(f"**{len(_involving(issues, s))}**" for s in Source)]
    )
    out.append(list_table(header, rows, "sf-table sf-issue-summary") + "\n")
    out.append(
        "A source's count is the issues it is part of: an issue between two "
        "sources counts for both.\n"
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
    out.append(" · ".join(f"{source_link(s)} {len(_involving(issues, s))}" for s in Source) + "\n")
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
            + ", ".join(f"{source_link(s)} {len(_involving(found, s))}" for s in Source)
            + ". All the kinds: [Data issues](index.md).\n",
            "Type in the box to filter.\n",
            r.section(kind, found, 2),
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
            "which is wrong. All the sources: [Data issues](index.md).\n"
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
        else:
            about = _attr(i)
            answer = r.answers(i)
        rows.append([f"[{i.kind.heading}](../issues/{kind_page(i.kind)}.md)", about, answer])
    return [
        "### Conflicts and errors\n",
        (
            "What the checks behind [Data issues](../issues/index.md) find about this chip. "
            "Each source label links to its entry upstream.\n"
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
