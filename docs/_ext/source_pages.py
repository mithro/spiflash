"""The source pages: a page per upstream the data is read from, and an index
of them.

Each page says what the project is and how it identifies chips (the prose
is hand-written, in ``docs/_source_notes/<source>.md``, which the page
includes), where the data came from (the repository, the commit, the files
read and the parser here that reads them), what was taken (counts, and how
many entries give each field), the source's data issues, and a table of its
entries. Every source label on the site links to its page.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from issue_checks import IssueKind
from page_markup import (
    EM_DASH,
    badge,
    count,
    esc,
    feature_badges,
    list_table,
    size_text,
    source_label,
    spaced,
    table_id,
    vendor_link,
    vendor_of,
    volt,
)
from spiflash.enums import FlashType, IdMethod, Source

if TYPE_CHECKING:
    from issue_checks import Issue
    from spiflash import Database, Flash, Record
    from spiflash.db import SourceInfo

#: What each project is, in a sentence: the lead of its page and its row
#: of the index.
SUMMARY = {
    Source.FLASHROM: (
        "A utility for reading, writing and erasing flash chips through many "
        "programmers. Its chip table has the most detail per part."
    ),
    Source.FLASHPROG: (
        "A fork of flashrom, with its own copy of the chip table, as detailed as flashrom's."
    ),
    Source.LINUX: (
        "The Linux kernel's SPI NOR and SPI NAND drivers, and the parts each recognises "
        "by its id: the most recent parts, and much of the SPI NAND."
    ),
    Source.UBOOT: (
        "The U-Boot boot loader's SPI flash driver, whose table keeps Linux's "
        "pre-6.8 format and parts Linux dropped."
    ),
    Source.DEDIPROG: (
        "The chip database of Dediprog's SF100 and SF600 programmers: the largest "
        "table here, SPI NOR and SPI NAND, kept by the programmer maker."
    ),
    Source.ROCKCHIP: (
        "The rkflash driver of Rockchip's U-Boot: the SPI NOR and SPI NAND its boards "
        "boot from, with each SPI NAND part's geometry and ECC."
    ),
    Source.OPENOCD: (
        "The on-chip debugger's table of SPI flash for its flash drivers, with "
        "opcodes per part, and its copy of JEDEC's JEP106 list."
    ),
    Source.OPENFPGALOADER: (
        "A tool for programming FPGAs and their configuration flash; its table gives "
        "each part's block-protection layout."
    ),
    Source.QEMU: (
        "The emulator's SPI NOR flash model: the parts its boards emulate, and "
        "complete SFDP dumps for thirteen of them."
    ),
    Source.ZEPHYR: (
        "The RTOS's board devicetrees, which describe the flash chip each board carries."
    ),
}

#: The parser, in this repository, reading each source.
PARSERS = {
    Source.FLASHROM: ("tools/spiflash_extract/flashrom.py",),
    Source.FLASHPROG: ("tools/spiflash_extract/flashrom.py",),
    Source.LINUX: ("tools/spiflash_extract/linux.py",),
    Source.UBOOT: ("tools/spiflash_extract/uboot.py",),
    Source.DEDIPROG: ("tools/spiflash_extract/dediprog.py",),
    Source.ROCKCHIP: ("tools/spiflash_extract/rockchip.py",),
    Source.OPENOCD: ("tools/spiflash_extract/openocd.py",),
    Source.OPENFPGALOADER: ("tools/spiflash_extract/openfpgaloader.py",),
    Source.QEMU: ("tools/spiflash_extract/qemu.py", "src/spiflash/sfdp.py"),
    Source.ZEPHYR: (
        "tools/spiflash_extract/zephyr.py",
        "tools/spiflash_extract/dts.py",
        "tools/spiflash_extract/sfdp.py",
    ),
}

#: What every parser shares: the C evaluator, the opcodes, the record
#: schema, the fetching, and the script running them.
SHARED = (
    "tools/spiflash_extract/cparse.py",
    "tools/spiflash_extract/ops.py",
    "tools/spiflash_extract/record.py",
    "tools/spiflash_extract/fetch.py",
    "tools/update_db.py",
)

#: The fields of a record a source may give, and what the page calls them.
FIELDS = {
    "vendor": "Manufacturer name",
    "id": "Chip id",
    "ext_id": "Extended id",
    "size": "Size",
    "page_size": "Page size",
    "sector_size": "Sector size",
    "erasers": "Erase layouts",
    "voltage": "Supply voltage",
    "opcodes": "Opcodes",
    "features": "Capabilities",
    "flags": "Upstream flags",
    "tested": "Test status",
    "notes": "Upstream comments",
    "sfdp": "SFDP dump",
}

#: The operation behind each way of reading an id, for a link to its page.
ID_OPERATION = {
    IdMethod.RDID: "RDID",
    IdMethod.RDID_OPCODE: "RDID",
    IdMethod.RDID_OPCODE_DUMMY: "RDID",
    IdMethod.RDID_OPCODE_ADDR: "RDID",
    IdMethod.REMS: "REMS",
    IdMethod.RES1: "RES",
    IdMethod.RES2: "RES",
    IdMethod.AT25F: "RDID_ATMEL",
    IdMethod.ST95: "RDID_M95",
}


def page_name(source: str) -> str:
    """A source's page, in ``docs/sources/``: its name."""
    return str(source)


def commit_link(info: SourceInfo) -> str:
    """The commit read, shortened and linked where the upstream is browsed
    on GitHub."""
    if info.browse.startswith("https://github.com/"):
        return f"[`{info.commit[:12]}`]({info.browse.rstrip('/')}/commit/{info.commit})"
    return f"`{info.commit[:12]}`"


def _ordinal(n: int) -> str:
    return f"{n}{'st' if n == 1 else 'nd' if n == 2 else 'rd' if n == 3 else 'th'}"


def _given(records: list[Record], field: str) -> int:
    return sum(1 for r in records if getattr(r, field))


def _upstream(db: Database, source: Source) -> list[str]:
    info = db.sources[source]
    rows = [["Repository", f"<{info.url}>"]]
    if info.browse != info.url:
        rows.append(["Browsed at", f"<{info.browse}> (the links here go there)"])
    rows += [
        ["Branch", f"`{info.branch}`"],
        ["Commit read", f"{commit_link(info)}, of {info.date:%Y-%m-%d}"],
        ["Files read", "\n\n".join(f"{{upstream}}`{source}:{p}`" for p in info.paths)],
        ["Licence of those files", esc(info.license)],
        [
            "Read by",
            "\n\n".join(f"{{repo}}`{p}`" for p in PARSERS[source])
            + "\n\nwith the helpers every parser shares: "
            + ", ".join(f"{{repo}}`{p.rsplit('/', 1)[-1]} <{p}>`" for p in SHARED),
        ],
        [
            "Rank",
            (
                f"{_ordinal(source.priority + 1)} of {len(Source)}: where sources give one "
                "chip different values and are otherwise tied, the higher-ranked wins "
                "({py:class}`~spiflash.enums.Source`)"
            ),
        ],
    ]
    return [
        "## Upstream\n",
        (
            "Where the data came from: every file is linked at the commit it was read "
            "from, pinned in {repo}`tools/sources.toml`. All the sources: "
            "[Where the data comes from](../SOURCES.md).\n"
        ),
        list_table(["", ""], rows, "sf-kv"),
        "",
    ]


def _taken(source: Source, records: list[Record], chips: list[Flash]) -> list[str]:
    nor = sum(1 for f in chips if f.type == FlashType.NOR)
    only = sum(1 for f in chips if f.sources == (source,))
    shared = len(chips) - only
    vendors = sorted({vendor_of(f) for f in chips}, key=str.lower)
    methods = Counter(r.id_method for r in records if r.id_method)
    no_id = sum(1 for r in records if not r.id)
    rows = [
        [
            "Entries",
            f"{len(records):,} ({_split(records)})",
        ],
        [
            "Chip ids",
            (
                f"{len(chips):,} ({nor:,} SPI NOR, {len(chips) - nor:,} SPI NAND); "
                f"{only:,} listed by no other source, {shared:,} shared with others"
            ),
        ],
        [
            "Ids read with",
            ", ".join(
                f"[`{m}`](../opcodes/{ID_OPERATION[m]}.md) {n:,}" for m, n in methods.most_common()
            )
            + (f"; {no_id:,} entries have no id, and are on no chip page" if no_id else ""),
        ],
        [
            f"Vendors ({len(vendors)})",
            ", ".join(vendor_link(v) for v in vendors),
        ],
    ]
    features = Counter(feat for r in records for feat in r.features)
    if features:
        rows.append(
            [
                "Capabilities it sets",
                ", ".join(
                    f"{feature_badges({feat})} {n:,}"
                    for feat, n in features.most_common()
                    if feature_badges({feat})
                ),
            ]
        )
    tested = Counter(r.tested for r in records if r.tested)
    if tested:
        rows.append(
            [
                "Test status",
                ", ".join(f"`{t}` {n:,}" for t, n in tested.most_common()),
            ]
        )
    fields = [
        [esc(title), count(_given(records, field)), count(_share(records, field))]
        for field, title in FIELDS.items()
    ]
    missing = [_lower(title) for field, title in FIELDS.items() if not _given(records, field)]
    out = [
        "## What was taken\n",
        list_table(["", ""], rows, "sf-kv"),
        "",
        "How many of its entries give each field:\n",
        list_table(["Field", "Entries", "Share (%)"], fields, "sf-table sf-fields"),
        "",
    ]
    if missing:
        out.append(f"It gives no {_and(missing)}.\n")
    return out


def _lower(title: str) -> str:
    """A title in running text: ``Size`` is ``size``, ``SFDP dump`` stays."""
    return title if title[:2].isupper() else title[0].lower() + title[1:]


def _split(records: list[Record]) -> str:
    nor = sum(1 for r in records if r.type == FlashType.NOR)
    return f"{nor:,} SPI NOR, {len(records) - nor:,} SPI NAND"


def _share(records: list[Record], field: str) -> int:
    return round(100 * _given(records, field) / len(records)) if records else 0


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} or {items[-1]}"


def _issues(source: Source, issues: list[Issue]) -> list[str]:
    mine = [i for i in issues if source in i.sources]
    rows = [
        [
            f"[{kind.heading}](../issues/{kind}.md)",
            count(sum(1 for i in mine if i.kind is kind)),
        ]
        for kind in IssueKind
    ]
    rows.append(["**All**", f"**{len(mine):,}**"])
    return [
        "## Data issues\n",
        (
            f"{source_label(source)} is part of {len(mine):,} of the "
            f"[issues](../issues/index.md) the checks find: where sources disagree with "
            "each other, with themselves or with the datasheets. Each is on "
            f"[its data issues page](../issues/source-{source}.md), with the entries "
            "involved.\n"
        ),
        list_table(["Kind", "Issues"], rows, "sf-table sf-source-issues"),
        "",
    ]


#: The columns of the entries table: each shown only for a source giving
#: its field (``None``: always).
_COLUMNS: tuple[tuple[str, str | None], ...] = (
    ("Id", None),
    ("Vendor", None),
    ("Name", None),
    ("Type", None),
    ("Ext. id", "ext_id"),
    ("Size", "size"),
    ("Page", "page_size"),
    ("Sector", "sector_size"),
    ("V min", "voltage"),
    ("V max", "voltage"),
    ("Tested", "tested"),
)


def _entries(db: Database, records: list[Record], slugs: dict[int, str]) -> list[str]:
    chip_of = {id(r): f for f in db.flashes for r in f.records}
    columns = [c for c in _COLUMNS if c[1] is None or _given(records, c[1])]
    rows = []
    for r in records:
        f = chip_of.get(id(r))
        link = db.link(r)
        cells = {
            "Id": table_id(f, f"../chips/{slugs[id(f)]}.md") if f else EM_DASH,
            "Vendor": vendor_link(vendor_of(f)) if f else esc(r.vendor or EM_DASH),
            # The line in the title too: GitHub shows some files (Dediprog's)
            # without line anchors.
            "Name": f'[{esc(r.name)}](<{link}> "{r.file}:{r.line}")' if link else esc(r.name),
            "Type": "NAND" if r.type == FlashType.NAND else "NOR",
            "Ext. id": f"{{sfid}}`{spaced(r.ext_id.hex())}`" if r.ext_id else EM_DASH,
            "Size": size_text(r.size),
            "Page": size_text(r.page_size),
            "Sector": size_text(r.sector_size),
            "V min": volt(r.voltage[0] if r.voltage else None),
            "V max": volt(r.voltage[1] if r.voltage else None),
            "Tested": f"`{r.tested}`" if r.tested else EM_DASH,
        }
        rows.append([cells[title] for title, _ in columns])
    return [
        "## Entries\n",
        (
            "Every entry taken, in the upstream's order, with the values it gives "
            "(the chip pages show what all the sources say). Each name links to its "
            "line upstream. Type in the box to filter; click a heading to sort.\n"
        ),
        list_table([t for t, _ in columns], rows, "sf-table sf-filterable sf-entries"),
        "",
    ]


def _fields() -> list[str]:
    """What each field of the "What was taken" table means."""
    rows = [
        ["Manufacturer name", "the vendor, as the upstream spells it"],
        [
            "Chip id",
            "the bytes the part answers to read-id, or to a legacy id command",
        ],
        ["Extended id", "id bytes after the JEDEC id, which tell variants apart"],
        ["Size, Page size, Sector size", "the part's capacity, page and erase sector"],
        ["Erase layouts", "each erase opcode with the blocks it erases"],
        ["Supply voltage", "the minimum and maximum supply"],
        ["Opcodes", "the SPI operations the upstream uses on the part ([](../opcodes.md))"],
        ["Capabilities", "what the part can do, normalised across the sources"],
        ["Upstream flags", "the upstream's own flag and feature names, kept as they are"],
        ["Test status", "how far the upstream has tested the part on hardware"],
        ["Upstream comments", "comments the upstream attached to the entry"],
        ["SFDP dump", "the part's whole SFDP area, as it answers the SFDP command"],
    ]
    return [
        "::::{dropdown} What each field means\n:class-container: sf-why\n",
        list_table(["Field", "Meaning"], rows, "sf-table"),
        "::::\n",
    ]


def source_page(db: Database, source: Source, slugs: dict[int, str], issues: list[Issue]) -> str:
    records = [r for r in db.records if r.source == source]
    chips = [f for f in db.flashes if source in f.sources]
    nor = sum(1 for f in chips if f.type == FlashType.NOR)
    counts = [
        f"{{sfsrcme}}`{source}`",
        badge(f"{len(records):,} entries", "primary"),
        badge(f"{len(chips):,} chip ids", "primary"),
        badge(f"{nor:,} SPI NOR", "info"),
    ]
    if len(chips) > nor:
        counts.append(badge(f"{len(chips) - nor:,} SPI NAND", "info"))
    return "\n".join(
        [
            f"# {esc(source_label(source))}\n",
            " ".join(counts) + "\n",
            f"{esc(SUMMARY[source])}\n",
            "## About\n",
            f"```{{include}} ../_source_notes/{source}.md\n```\n",
            *_upstream(db, source),
            *_taken(source, records, chips),
            *_fields(),
            *_issues(source, issues),
            *_entries(db, sorted(records, key=lambda r: (r.file, r.line)), slugs),
            "All the sources: [Sources](index.md).\n",
        ]
    )


def sources_table(db: Database, issues: list[Issue], root: str = "../") -> str:
    """Every source: its label and page, what it is, and its counts
    (``root`` is the way to the top of the site)."""
    rows = []
    for source in Source:
        records = [r for r in db.records if r.source == source]
        chips = [f for f in db.flashes if source in f.sources]
        nor = sum(1 for f in chips if f.type == FlashType.NOR)
        found = sum(1 for i in issues if source in i.sources)
        rows.append(
            [
                f"{{sfsrc}}`{source}`",
                f"[{esc(source_label(source))}]({root}sources/{page_name(source)}.md): "
                + esc(SUMMARY[source]),
                count(len(records)),
                count(len(chips)),
                count(nor),
                count(len(chips) - nor),
                count(sum(1 for f in chips if f.sources == (source,))),
                f"[{found:,}]({root}issues/source-{source}.md)",
                f"{db.sources[source].date:%Y-%m-%d}",
            ]
        )
    return list_table(
        [
            "Source",
            "What it is",
            "Entries",
            "Chip ids",
            "NOR",
            "NAND",
            "Only here",
            "Issues",
            "Commit date",
        ],
        rows,
        "sf-table sf-source-list",
    )


def index_page(db: Database, issues: list[Issue]) -> str:
    return "\n".join(
        [
            "# Sources\n",
            (
                f"The {len(Source)} open source projects whose flash tables the database "
                "merges, in the order they rank when they disagree. Each page says what "
                "the project is, which of its files were read at which commit, what was "
                "taken from them, and lists every entry. Every source label on the site "
                "links to its page. How the data is taken, and its licences: "
                "[Where the data comes from](../SOURCES.md).\n"
            ),
            sources_table(db, issues),
            "",
            (
                "*Entries* are the upstream's own entries; *chip ids* the chips they "
                "describe (several entries can share an id); *only here* the chip ids "
                "no other source lists; *issues* the [data issues](../issues/index.md) "
                "the source is part of.\n"
            ),
            "```{toctree}\n:hidden:\n\n" + "\n".join(page_name(s) for s in Source) + "\n```\n",
        ]
    )


def generate_all(db: Database, slugs: dict[int, str], issues: list[Issue]) -> dict[str, str]:
    """Every page of ``docs/sources/``, by file name."""
    pages = {"index.md": index_page(db, issues)}
    for source in Source:
        pages[f"{page_name(source)}.md"] = source_page(db, source, slugs, issues)
    return pages
