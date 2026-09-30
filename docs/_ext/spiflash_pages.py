"""Generate the site's vendor and chip pages from the spiflash database.

At ``builder-inited`` this writes MyST pages into ``docs/vendors/`` and
``docs/chips/`` (both git-ignored): one page per vendor, with a table of all
its parts, one page per chip id, and the index pages linking them. A page is
only rewritten when its text changes, so incremental builds stay fast.

It also writes a page per SPI operation into ``docs/opcodes/``
(:mod:`opcode_pages`), with its WaveDrom timing diagram, and the data issues
pages into ``docs/issues/`` (:mod:`issue_pages`), and a page per source
into ``docs/sources/`` (:mod:`source_pages`).

The pages are Markdown, not raw HTML, so Sphinx's search indexes every part
name and id.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from docutils import nodes
from sphinx.builders.html import StandaloneHTMLBuilder
from sphinx.environment.adapters.toctree import global_toctree_for_doc
from sphinx.search import en
from sphinx.util.docutils import SphinxRole
from sphinx.util.nodes import split_explicit_title

import spiflash
from issue_checks import Issue, find
from issue_pages import chip_issues
from issue_pages import generate_all as issue_pages
from jep106_pages import generate_all as jep106_pages
from jep106_pages import jep106_link
from opcode_pages import generate_all as operation_pages
from page_markup import (
    EM_DASH,
    FEATURE_TEXT,
    HIGHLIGHTS,
    JESD216,
    KIND_TITLE,
    TIMES,
    UP_ARROW,
    badge,
    badge_lines,
    chip_slug,
    count,
    esc,
    feature_badges,
    list_table,
    num,
    size_text,
    slug,
    source_badge,
    source_label,
    spaced,
    table_id,
    title_of,
    vendor_link,
    vendor_of,
    volt,
    volts,
)
from source_pages import commit_link, page_name
from source_pages import generate_all as source_pages
from source_pages import sources_table as sources_list
from spiflash.enums import Feature, OperationKind, Source
from spiflash.opcodes import OPERATIONS
from spiflash.units import human_size, human_time

if TYPE_CHECKING:
    from collections.abc import Callable

    from sphinx.application import Sphinx
    from sphinx.config import Config
    from sphinx.writers.html5 import HTML5Translator

    from spiflash import Database, Flash, Record, SfdpDump


def chip_page(
    db: Database, f: Flash, vendor_slug: str, slugs: dict[int, str], issues: list[Issue]
) -> str:
    kind = "SPI NAND" if f.type == "nand" else "SPI NOR"
    # In no toctree: see _chip_nav.
    out = ["---\norphan: true\n---\n", f"# {esc(title_of(f))}\n"]
    out.append(
        " ".join(
            [
                f"{{bdg-link-primary}}`{esc(vendor_of(f))} <../vendors/{vendor_slug}.html>`",
                badge(kind, "info"),
                f"{{sfid}}`{spaced(f.jedec_id if f.family == 'jedec' else f.id_hex)}`",
            ]
        )
        + "\n"
    )
    others = [n for n in f.names if n not in title_of(f).split(" / ")]
    if others:
        out.append(f"Also listed as: {', '.join(esc(n) for n in others)}.\n")
    out += _summary_cards(f)
    out += _datasheets(f)
    out += _identification(db, f, kind)
    out += _extended_ids(db, f)
    out += _capabilities(f)
    out += _sfdp(f)
    out += _opcodes(f)
    out += _erase_layouts(f)
    out += _sources(db, f)
    out += chip_issues(db, slugs, f, issues)
    out.append(f"\n[All {esc(vendor_of(f))} parts](../vendors/{vendor_slug}.md)\n")
    return "\n".join(out)


def _summary_cards(f: Flash) -> list[str]:
    cards = [
        ("Capacity", size_text(f.size)),
        ("Page", size_text(f.page_size)),
        ("Sector", size_text(f.sector_size)),
        ("Supply", volts(f.voltage)),
    ]
    out = ["::::{grid} 2 2 4 4\n:gutter: 2\n:class-container: sf-cards\n"]
    for label, value in cards:
        out.append(f":::{{grid-item-card}} {label}\n:class-card: sf-card\n\n{value}\n:::")
    out.append("::::\n")
    return out


def link_to(text: str, url: str) -> str:
    """A Markdown link; the target in angle brackets, so a URL with
    parentheses or spaces in it stays one link."""
    return f"[{esc(text)}](<{url}>)"


def _datasheets(f: Flash) -> list[str]:
    if not f.datasheets:
        return [
            "## Datasheets\n",
            (
                "No datasheet found for this id yet: if you know where one is, "
                f"[open an issue]({REPO_URL}/issues/new).\n"
            ),
        ]
    rows = []
    for d in f.datasheets:
        where = badge("manufacturer", "success") if d.official else badge("copy", "secondary")
        mirrors = ", ".join(link_to(f"mirror {n}", u) for n, u in enumerate(d.also_at, 1))
        rows.append(
            [
                link_to(d.title, d.url) + (f" ({mirrors})" if mirrors else ""),
                esc(d.revision or EM_DASH),
                d.date.isoformat() if d.date else EM_DASH,
                ", ".join(esc(n) for n in d.parts),
                where,
                "{sfyes}`✓`" if f.key in d.confirmed else " ",
            ]
        )
    return [
        "## Datasheets\n",
        list_table(
            ["Document", "Revision", "Date", "Covers", "From", "Id shown"],
            rows,
            "sf-table sf-datasheets",
        ),
        "",
        (
            "*From* says whether the link is the manufacturer's own site or a copy "
            "elsewhere (a distributor, an archive). *Id shown* marks a document that "
            "gives this id's bytes itself; the others were matched by part number.\n"
        ),
    ]


def _identification(db: Database, f: Flash, kind: str) -> list[str]:
    rows = []
    if f.family == "jedec":
        rows.append(["Read id (0x9f) answers", f"{{sfid}}`{spaced(f.id_hex)}`"])
        if f.bank:
            rows.append(["With JEP106 continuation codes", f"{{sfid}}`{spaced(f.jedec_id)}`"])
    else:
        rows.append([f"Legacy id ({f.family.upper()})", f"{{sfid}}`{spaced(f.id_hex)}`"])
    rows.append(["Manufacturer", vendor_link(vendor_of(f))])
    if db.jep106(f.id[0], f.bank):
        rows.append(
            [
                f"JEP106 name of 0x{f.id[0]:02x} (bank {f.bank + 1})",
                jep106_link(db, (f.bank, f.id[0])),
            ]
        )
    exts = sorted({r.ext_id.hex() for r in f.records if r.ext_id})
    if exts:
        rows.append(
            [
                "Extended ids",
                ", ".join(f"{{sfid}}`{spaced(e)}`" for e in exts)
                + " ([what each means](#extended-ids))",
            ]
        )
    rows.append(["Type", kind])
    return ["## Identification\n", list_table(["", ""], rows, "sf-kv"), ""]


def _one_of(values: set[Any], show: Callable[[Any], str]) -> str:
    """One value, or each of several ("64 KiB / 256 KiB")."""
    if not values:
        return EM_DASH
    return " / ".join(show(v) for v in sorted(values))


def _extended_ids(db: Database, f: Flash) -> list[str]:
    """What each extended id stands for: the bytes a part sends after the
    JEDEC id, which sources use to tell apart variants that share it."""
    if not any(r.ext_id for r in f.records):
        return []
    groups: dict[bytes | None, list[Record]] = defaultdict(list)
    for r in f.records:
        groups[r.ext_id].append(r)
    rows = []
    for ext in sorted(groups, key=lambda e: (e is None, e or b"")):
        recs = groups[ext]
        names = dict.fromkeys(r.name for r in recs)
        notes = dict.fromkeys(n for r in recs for n in r.notes)
        by_source: dict[str, str] = {}
        for r in sorted(recs, key=lambda r: r.source.priority):
            by_source.setdefault(r.source, db.link(r) or "")
        rows.append(
            [
                f"{{sfid}}`{spaced(ext.hex())}`" if ext else "none (any variant)",
                ", ".join(esc(n) for n in names),
                " ".join(
                    f"{{sfsrc}}`{s} <{u}>`" if u else source_badge(s) for s, u in by_source.items()
                ),
                _one_of({r.size for r in recs if r.size}, size_text),
                _one_of({r.page_size for r in recs if r.page_size}, size_text),
                _one_of({r.sector_size for r in recs if r.sector_size}, size_text),
                "; ".join(esc(n) for n in notes) or EM_DASH,
            ]
        )
    return [
        "## Extended ids\n",
        (
            "Some parts send more bytes after the JEDEC id, and sources use them to "
            "tell apart variants that answer the same id. Each extended id, the "
            "entries listed under it, and what they give (an entry with no extended "
            "id covers every variant):\n"
        ),
        list_table(
            ["Ext. id", "Listed as", "By", "Size", "Page", "Sector", "Upstream notes"],
            rows,
            "sf-table sf-ext-ids",
        ),
        "",
    ]


def _source_header(s: str) -> str:
    return f"{{sfsrc}}`{s}`"


def _capabilities(f: Flash) -> list[str]:
    if not f.features:
        return []
    srcs = [s for s in f.sources if any(s in f.feature_sources(x) for x in f.features)]
    rows = [
        [
            badge(*FEATURE_TEXT[feat]),
            esc(Feature(feat).description),
        ]
        + ["{sfyes}`✓`" if s in f.feature_sources(feat) else " " for s in srcs]
        for feat in FEATURE_TEXT
        if feat in f.features
    ]
    return [
        "## Capabilities\n",
        "Each capability some source says this part has, and which sources say so.\n",
        list_table(
            ["Capability", "What it means", *[_source_header(s) for s in srcs]],
            rows,
            "sf-table sf-matrix sf-capabilities",
        ),
        "",
    ]


def _sfdp(f: Flash) -> list[str]:
    if not f.sfdp_dumps:
        return []
    out = [
        "## SFDP\n",
        (
            f"The part's SFDP ([JESD216]({JESD216})) tables, as the sources carry them: "
            "what one part answered, decoded by {py:mod}`spiflash.sfdp`; `spiflash sfdp` "
            "prints every field. SFDP says nothing about the vendor, voltage or protection, "
            "and {sfsrc}`linux` keeps fixups for tables that are wrong, so read it as the "
            "part's own claim.\n"
        ),
    ]
    for d in f.sfdp_dumps:
        out.append(list_table(["Parameter", "Value"], _sfdp_rows(d), "sf-table"))
        out.append("")
    return out


def _sfdp_rows(d: SfdpDump) -> list[list[str]]:
    """One dump's table: whose it is, then what it says. Parts sharing an id
    can carry different dumps, so each names its parts."""
    s = d.tables
    tables = ", ".join(f"{esc(h.name)} {h.revision}" for h in s.headers)
    rows = [
        ["Dump of", f"{source_badge(d.source)} {esc(', '.join(d.parts))}"],
        ["Revision", f"{esc(s.revision_name)}, with {tables}"],
    ]
    geometry = [size_text(s.size)]
    if s.page_size is not None:
        geometry.append(f"{size_text(s.page_size)} pages")
    if s.address_bytes is not None:
        geometry.append(f"{s.address_bytes}-byte addresses")
    rows.append(["Geometry", esc(", ".join(geometry))])
    if s.erase_types:
        erases = []
        for e in s.erase_types:
            text = f"`0x{e.opcode:02x}` {size_text(e.size)}"
            if e.opcode_4b is not None:
                text += f" (`0x{e.opcode_4b:02x}` with a 4-byte address)"
            if e.typical_us is not None:
                text += f", typically {human_time(e.typical_us)}"
            erases.append(text)
        rows.append(["Erase types", "; ".join(erases)])
    if s.reads:
        rows.append(
            [
                "Fast reads",
                "; ".join(
                    f"{esc(r.protocol)} `0x{r.opcode:02x}`, {r.dummy_clocks} dummy clocks"
                    for r in s.reads.values()
                ),
            ]
        )
    bfpt = s.bfpt
    if bfpt is not None:
        if bfpt.quad_enable_description is not None:
            rows.append(["Quad enable", esc(bfpt.quad_enable_description)])
        if bfpt.four_byte_enter:
            rows.append(
                ["Enter 4-byte mode", esc(", ".join(sorted(map(str, bfpt.four_byte_enter))))]
            )
        if bfpt.soft_reset:
            rows.append(["Soft reset", esc("; ".join(bfpt.soft_reset))])
    if s.warnings:
        rows.append(["Warnings", esc("; ".join(s.warnings))])
    return rows


def _opcodes(f: Flash) -> list[str]:
    out = ["## Opcodes\n"]
    if not f.opcodes:
        out.append("No source lists opcodes for this part.\n")
        return out
    srcs = [s for s in f.sources if any(s in o.sources for o in f.opcodes.values())]
    out.append(
        "Each opcode some source says this part has, and which sources say so. "
        "A missing opcode may still be supported: see [](../opcodes.md).\n"
    )
    rows = [
        [
            f"{{sfop}}`0x{o.opcode:02x}`",
            f"[`{o.name}`](../opcodes/{o.name}.md)",
            kind_link(o.operation.kind, "../opcodes.html"),
            esc(o.operation.description),
        ]
        + ["{sfyes}`✓`" if s in o.sources else " " for s in srcs]
        for o in f.opcodes.values()
    ]
    out.append(
        list_table(
            ["Opcode", "Operation", "Type", "Description", *[_source_header(s) for s in srcs]],
            rows,
            "sf-table sf-matrix sf-opcodes",
        )
    )
    out.append("")
    out.append(":::{dropdown} Why each source lists each opcode\n:class-container: sf-why\n")
    for o in f.opcodes.values():
        reasons = "; ".join(f"{source_badge(s)} {esc(via)}" for s, via in o.because)
        out.append(f"- [`{o.name}`](../opcodes/{o.name}.md) (0x{o.opcode:02x}): {reasons}")
    out.append(":::\n")
    return out


def _erase_layouts(f: Flash) -> list[str]:
    rows = []
    for r in f.records:
        for e in r.erasers:
            blocks = ", ".join(num(f"{b.count:,} {TIMES} {human_size(b.size)}") for b in e.blocks)
            op = e.opcode
            opname = next(
                (
                    n
                    for n, o in OPERATIONS.items()
                    if o.opcode == op and o.kind is OperationKind.ERASE
                ),
                None,
            )
            rows.append(
                [
                    source_badge(r.source),
                    esc(r.name),
                    f"{{sfop}}`0x{op:02x}`" if op is not None else esc(e.function or ""),
                    f"[`{opname}`](../opcodes/{opname}.md)" if opname else EM_DASH,
                    blocks,
                ]
            )
    if not rows:
        return []
    return [
        "## Erase layouts\n",
        list_table(["Source", "As", "Opcode", "Operation", "Blocks"], rows, "sf-table"),
        "",
    ]


def _sources(db: Database, f: Flash) -> list[str]:
    rows = []
    for r in f.records:
        link = db.link(r)
        where = f"[{esc(r.url)}]({link})" if link else esc(r.url)
        rows.append(
            [
                source_badge(r.source),
                esc(r.name),
                f"{{sfid}}`{spaced(r.ext_id.hex())}`" if r.ext_id else EM_DASH,
                size_text(r.size),
                size_text(r.page_size),
                size_text(r.sector_size),
                volt(r.voltage[0] if r.voltage else None),
                volt(r.voltage[1] if r.voltage else None),
                esc(r.tested or EM_DASH),
                where,
            ]
        )
    out = [
        "## What each source says\n",
        list_table(
            [
                "Source",
                "Name",
                "Ext. id",
                "Size",
                "Page",
                "Sector",
                "V min",
                "V max",
                "Tested",
                "Where",
            ],
            rows,
            "sf-table sf-sources",
        ),
        "",
    ]
    notes = [(r, n) for r in f.records for n in r.notes]
    if notes:
        out.append(":::{dropdown} Upstream comments\n:class-container: sf-why\n")
        for r, n in notes:
            out.append(f"- {source_badge(r.source)} {esc(r.name)}: {esc(n)}")
        out.append(":::\n")
    return out


#: What the filter box above a table of parts takes (tables.js).
FILTER_SYNTAX = (
    "Every word must be somewhere in the row (an id, a part, a vendor, a "
    "size); a word with `*`, `?` or `[...]` in it is a glob for a whole part "
    "name (`W25Q128*`, `MX25?12835F`); `/.../` is a regular expression "
    "searched for in the part names (`/^MX25[LU]128/`)."
)


def parts_table(
    flashes: list[Flash],
    slugs: dict[int, str],
    prefix: str,
    *,
    with_vendor: bool = False,
    vendor_prefix: str = "../vendors/",
    nearest: bool = False,
) -> str:
    """The table of chips on the vendor and All chips pages; with
    ``nearest``, tables.js puts a nearest-part-name box above it."""
    rows = []
    for f in flashes:
        link = table_id(f, f"{prefix}{slugs[id(f)]}.md")
        row = [link]
        if with_vendor:
            row.append(vendor_link(vendor_of(f), vendor_prefix))
        row += [
            ", ".join(esc(n) for n in f.names),
            link_to("PDF", f.datasheets[0].url) if f.datasheets else " ",
            "NAND" if f.type == "nand" else "NOR",
            size_text(f.size),
            *([] if with_vendor else [size_text(f.page_size)]),
            size_text(f.sector_size),
            volt(f.voltage[0] if f.voltage else None),
            volt(f.voltage[1] if f.voltage else None),
            feature_badges(f.features & HIGHLIGHTS),
            count(len(f.opcodes)),
            count(len(f.sources)),
        ]
        rows.append(row)
    header = (
        ["Id"]
        + (["Vendor"] if with_vendor else [])
        + [
            "Parts",
            "Data\u00adsheet",
            "Type",
            "Size",
            *([] if with_vendor else ["Page"]),
            "Sector",
            "V min",
            "V max",
            "Highlights",
            "Ops",
            "Srcs",
        ]
    )
    classes = "sf-table sf-filterable sf-parts" + (" sf-with-vendor" if with_vendor else "")
    if nearest:
        classes += " sf-nearest"
    return list_table(header, rows, classes)


def vendor_page(db: Database, vendor: str, flashes: list[Flash], slugs: dict[int, str]) -> str:
    nor = sum(1 for f in flashes if f.type == "nor")
    nand = len(flashes) - nor
    ids = Counter((f.bank, f.id[0]) for f in flashes if f.family == "jedec")
    out = [f"# {esc(vendor)}\n"]
    counts = [badge(f"{len(flashes)} chip ids", "primary")]
    if nor:
        counts.append(badge(f"{nor} SPI NOR", "info"))
    if nand:
        counts.append(badge(f"{nand} SPI NAND", "info"))
    out.append(" ".join(counts) + "\n")
    if ids:
        parts = []
        for (bank, m), _ in ids.most_common():
            jep = db.jep106(m, bank)
            text = f"{{sfid}}`{m:02x}`" + (f" (bank {bank + 1})" if bank else "")
            if jep:
                text += f" (JEP106: {jep106_link(db, (bank, m))})"
            parts.append(text)
        noun = "byte" if len(parts) == 1 else "bytes"
        out.append(f"Manufacturer id {noun}: {', '.join(parts)}.\n")
    sizes = sorted({f.size for f in flashes if f.size})
    if sizes:
        out.append(f"Capacities from {human_size(sizes[0])} to {human_size(sizes[-1])}.\n")
    out.append(f"Type in the box to filter. {FILTER_SYNTAX} Click a heading to sort.\n")
    out.append(parts_table(flashes, slugs, "../chips/"))
    return "\n".join(out)


def vendors_index(vendors: dict[str, list[Flash]], vslug: dict[str, str]) -> str:
    out = [
        "# Vendors\n",
        f"{len(vendors)} manufacturers. Each page lists all of a vendor's parts.\n",
        "::::{grid} 1 2 3 3\n:gutter: 3\n",
    ]
    for v in sorted(vendors, key=lambda x: (-len(vendors[x]), x.lower())):
        fl = vendors[v]
        nand = sum(1 for f in fl if f.type == "nand")
        detail = f"{len(fl)} chip ids" + (f", {nand} of them SPI NAND" if nand else "")
        out.append(
            f":::{{grid-item-card}} {esc(v)}\n:link: {vslug[v]}\n:link-type: doc\n"
            f":class-card: sf-vendor-card\n\n{detail}\n:::"
        )
    out.append("::::\n")
    out.append(
        "```{toctree}\n:hidden:\n\n"
        + "\n".join(vslug[v] for v in sorted(vendors, key=str.lower))
        + "\n```\n"
    )
    return "\n".join(out)


def chips_index(flashes: list[Flash], slugs: dict[int, str]) -> str:
    return "\n".join(
        [
            "# All chips\n",
            f"Every one of the {len(flashes)} chip ids in the database.\n",
            (
                "For a part name that is not here (a marking read off a chip, an "
                "order code, a typo), type it in the first box: it lists the chips "
                "with the closest names, the closest first, scored as "
                "`spiflash find --nearest` scores them "
                "([how](../usage.md#searching-part-names)).\n"
            ),
            (
                f"Type in the second box to filter the table. {FILTER_SYNTAX} "
                "Click a heading to sort.\n"
            ),
            parts_table(flashes, slugs, "", with_vendor=True, nearest=True),
        ]
    )


def opcodes_table(flashes: list[Flash]) -> str:
    uses = Counter(name for f in flashes for name in f.opcodes)
    out = []
    for kind in OperationKind:
        rows = [
            [
                f"{{sfop}}`0x{op.opcode:02x}`",
                f"[`{op.name}`](opcodes/{op.name}.md)",
                esc(op.description),
                count(uses.get(op.name, 0)),
            ]
            for op in OPERATIONS.values()
            if op.kind == kind
        ]
        out.append(f"({KIND_TARGET}{kind})=\n### {KIND_TITLE[kind]}\n")
        out.append(list_table(["Opcode", "Operation", "Description", "Chips"], rows, "sf-table"))
        out.append("")
    return "\n".join(out)


#: The opcodes page's target for each kind of operation's table.
KIND_TARGET = "opcodes-"


def kind_link(kind: str, page: str) -> str:
    """The kind of an operation, linked to its table on the opcodes page
    (``page``, relative to the linking page's HTML)."""
    return f"{{sfkind}}`{kind} <{page}#{KIND_TARGET}{kind}>`"


def sources_table(db: Database) -> str:
    rows = []
    for name, s in sorted(db.sources.items()):
        base = s.browse
        label = (
            f"JEP106 ({{sfsrc}}`openocd <{base}>`)"
            if name == "jep106"
            else f"{{sfsrc}}`{name} <{base}>`"
        )
        rows.append([label, commit_link(s), f"{s.date:%Y-%m-%d}", count(s.records), esc(s.license)])
    return list_table(["Source", "Commit", "Date", "Entries", "Licence"], rows, "sf-table")


def files_read_table(db: Database) -> str:
    """Each upstream's files, linked at the pinned commit, with its licence."""
    rows = []
    for name, s in sorted(db.sources.items()):
        if name == "jep106":
            continue  # the same OpenOCD checkout; its file is in OpenOCD's row
        files = ", ".join(f"{{upstream}}`{name}:{path}`" for path in s.paths)
        rows.append([source_badge(name), files, esc(s.license)])
    return list_table(["Source", "Files read", "Licence of those files"], rows, "sf-table")


def stats(db: Database) -> dict[str, int]:
    fl = db.flashes
    return {
        "chips": len(fl),
        "nor": sum(1 for f in fl if f.type == "nor"),
        "nand": sum(1 for f in fl if f.type == "nand"),
        "vendors": len({vendor_of(f) for f in fl}),
        "records": len(db.records),
        "multi": sum(1 for f in fl if len(f.sources) > 1),
        "jep106": len(db.manufacturers),
    }


# --- Sphinx glue -------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def generate(srcdir: Path) -> dict[str, str]:
    """Write the pages; returns each chip page's vendor page, by docname."""
    db = spiflash.database()
    vendors: dict[str, list[Flash]] = defaultdict(list)
    for f in db.flashes:
        vendors[vendor_of(f)].append(f)
    vslug = {v: slug(v) for v in vendors}
    if len(set(vslug.values())) != len(vslug):
        msg = "two vendors share a page name"
        raise ValueError(msg)
    slugs = {id(f): chip_slug(f) for f in db.flashes}
    if len(set(slugs.values())) != len(slugs):
        msg = "two chips share a page name"
        raise ValueError(msg)

    issues = find(db)
    chips_dir, vendors_dir, ops_dir = srcdir / "chips", srcdir / "vendors", srcdir / "opcodes"
    issues_dir = srcdir / "issues"
    jep106_dir = srcdir / "jep106"
    sources_dir = srcdir / "sources"
    wanted: set[Path] = set()

    def page(path: Path, text: str) -> None:
        _write(path, text)
        wanted.add(path)

    for v, fl in vendors.items():
        page(vendors_dir / f"{vslug[v]}.md", vendor_page(db, v, fl, slugs))
        for f in fl:
            page(chips_dir / f"{slugs[id(f)]}.md", chip_page(db, f, vslug[v], slugs, issues))
    page(vendors_dir / "index.md", vendors_index(vendors, vslug))
    page(chips_dir / "index.md", chips_index(list(db.flashes), slugs))
    for name, text in operation_pages(db).items():
        page(ops_dir / name, text)
    for name, text in issue_pages(db, slugs, issues).items():
        page(issues_dir / name, text)
    for name, text in jep106_pages(db, slugs).items():
        page(jep106_dir / name, text)
    for name, text in source_pages(db, slugs, issues).items():
        page(sources_dir / name, text)
    # Fragments the hand-written pages include (docs/_generated is excluded
    # from the build as pages of its own).
    _write(srcdir / "_generated" / "opcodes-table.md", opcodes_table(list(db.flashes)))
    _write(srcdir / "_generated" / "sources-table.md", sources_table(db))
    _write(srcdir / "_generated" / "files-read.md", files_read_table(db))
    _write(srcdir / "_generated" / "sources-list.md", sources_list(db, issues, ""))
    # A chip id that left the database leaves no stale page behind.
    for d in (chips_dir, vendors_dir, ops_dir, issues_dir, jep106_dir, sources_dir):
        for old in d.glob("*.md"):
            if old not in wanted:
                old.unlink()
    return {f"chips/{slugs[id(f)]}": f"vendors/{vslug[vendor_of(f)]}" for f in db.flashes}


#: Each chip page's vendor page, by docname (see :func:`_chip_nav`).
CHIP_VENDOR: dict[str, str] = {}
#: The vendor pages' sidebars, by vendor and the theme's toctree() options.
_VENDOR_NAV: dict[tuple[str, str], str] = {}


def _builder_inited(app: Sphinx) -> None:
    CHIP_VENDOR.clear()
    CHIP_VENDOR.update(generate(Path(app.srcdir)))
    _VENDOR_NAV.clear()


def _chip_nav(
    app: Sphinx, pagename: str, _template: str, context: dict[str, Any], _doctree: Any
) -> None:
    """A chip page's sidebar is its vendor page's.

    The theme puts the whole toctree in every page's sidebar, so with the
    chips in it every page carried a link to every chip: the site grew with
    the square of the chips, and writing it took most of the build. Chip
    pages are in no toctree; each shows its vendor's sidebar instead, with
    the vendor as the current page, made once per vendor.
    """
    vendor = CHIP_VENDOR.get(pagename)
    if vendor is None or "toctree" not in context:
        return
    builder = app.builder
    assert isinstance(builder, StandaloneHTMLBuilder)

    def toctree(*, collapse: bool = True, **kwargs: Any) -> str:
        # As StandaloneHTMLBuilder._get_local_toctree, for the vendor page.
        kwargs.setdefault("includehidden", False)
        if kwargs.get("maxdepth") == "":
            kwargs.pop("maxdepth")
        key = (vendor, repr(sorted({**kwargs, "collapse": collapse}.items())))
        if key not in _VENDOR_NAV:
            tree = global_toctree_for_doc(
                builder.env, vendor, builder, tags=builder.tags, collapse=collapse, **kwargs
            )
            if tree is not None:
                _rebase_links(tree, builder.get_target_uri(vendor))
            _VENDOR_NAV[key] = builder.render_partial(tree)["fragment"]
        return _VENDOR_NAV[key]

    context["toctree"] = toctree


def _rebase_links(tree: nodes.Element, vendor_uri: str) -> None:
    """Makes the links of a vendor page's toctree, relative to that page,
    relative to a chip page: from ``vendors/`` to ``chips/``."""
    for ref in tree.findall(nodes.reference):
        uri = ref.get("refuri", "")
        if "://" in uri:
            continue
        path, hash_, anchor = uri.partition("#")
        target = posixpath.normpath(posixpath.join("vendors", path)) if path else vendor_uri
        ref["refuri"] = posixpath.relpath(target, "chips") + (hash_ + anchor if anchor else "")


class SpanRole(SphinxRole):
    """``{role}`text``` as ``<span class="css">text</span>``; with a link,
    ``{role}`text <url>```, the span is linked."""

    def __init__(self, css: str) -> None:
        super().__init__()
        self.css = css

    def run(self) -> tuple[list[nodes.Node], list[nodes.system_message]]:
        has_link, title, target = split_explicit_title(self.text)
        span = nodes.inline(self.rawtext, title, classes=[self.css])
        if not has_link:
            return [span], []
        return [nodes.reference(self.rawtext, "", span, refuri=target)], []


class SourceBadge(nodes.inline):
    """A source's label from ``{sfsrc}``: one node, where a link and a span
    for the label and for each of its lines made four or more, so the pages'
    thousands of labels read, resolve and write faster. ``node["lines"]`` is
    its text; ``node["sfsource"]``, if set, the source whose page it links
    to (the link is made when the page is written, relative to it)."""


def _in_link(node: nodes.Node) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, nodes.reference):
            return True
        parent = parent.parent
    return False


def visit_source_badge(self: HTML5Translator, node: SourceBadge) -> None:
    # Not in a link already, such as a heading's entry in the sidebar.
    source = None if _in_link(node) else node.get("sfsource")
    if source:
        builder = self.builder
        uri = builder.get_relative_uri(builder.current_docname, f"sources/{page_name(source)}")
        title = self.attval(f"About {source_label(source)}")
        self.body.append(
            f'<a class="sf-src-link reference external" href="{self.attval(uri or "#")}" '
            f'title="{title}">'
        )
    self.body.append(self.starttag(node, "span", ""))
    self.body += [f"<span>{self.encode(line)}</span>" for line in node["lines"]]
    self.body.append("</span></a>" if source else "</span>")
    raise nodes.SkipNode


class SourceRole(SphinxRole):
    """``{sfsrc}`linux``` as a coloured label naming the source, linked to
    its page. A count and a link are optional: ``{sfsrc}`linux <count>
    <https://...>``` adds an arrow after the label, linked there (to the
    entry upstream, say). With ``mine``, the label is ringed: the source a
    page is about."""

    def __init__(self, *, mine: bool = False) -> None:
        super().__init__()
        self.mine = mine

    def run(self) -> tuple[list[nodes.Node], list[nodes.system_message]]:
        has_link, title, target = split_explicit_title(self.text)
        source, _, count = title.partition(" ")
        lines = badge_lines(source, count)
        classes = ["sf-src", f"sf-src-{slug(source)}"] + (["sf-src-mine"] if self.mine else [])
        if len(lines) > 1:
            classes.append("sf-src-split")
        text = [nodes.Text(line) for line in lines]
        badge = SourceBadge(self.rawtext, "", *text, classes=classes, lines=lines)
        if source in set(Source):
            badge["sfsource"] = source
        out: list[nodes.Node] = [badge]
        if has_link:
            up = nodes.reference("", UP_ARROW, refuri=target, classes=["sf-src-up"])
            up["reftitle"] = f"Open in {source_label(source)}"
            out.append(up)
        return out, []


class Number(nodes.inline):
    """A number from ``{sfnum}``: one node, where a span for each part made
    up to a dozen (see :class:`SourceBadge`). ``node["parts"]`` is its
    parts, ``(css class, text)``."""


def visit_number(self: HTML5Translator, node: Number) -> None:
    self.body.append(self.starttag(node, "span", ""))
    self.body += [f'<span class="{css}">{self.encode(text)}</span>' for css, text in node["parts"]]
    self.body.append("</span>")
    raise nodes.SkipNode


class NumberRole(SphinxRole):
    """``{sfnum}`16 MiB``` as spans for its parts, so the numbers of a column
    line up. Besides a number and its unit it takes a count of blocks before
    a multiplication sign (an erase layout), a range with an en dash (a
    supply), thousands separators (``1,234``) and an em dash for a missing
    value, which is aligned where the number would be. The CSS gives each
    part a fixed width by unit: whole sizes, volts with a decimal part, and
    plain counts."""

    PARTS = re.compile(
        r"^(?:(?P<count>[\d,]+) \u00d7 )?"
        r"(?P<a>[\d,]+|\u2014)(?:\.(?P<af>\d+))?"
        r"(?:\u2013(?P<b>[\d,]+)(?:\.(?P<bf>\d+))?)?"
        r"(?: (?P<unit>\S+))?$"
    )

    def run(self) -> tuple[list[nodes.Node], list[nodes.system_message]]:
        m = self.PARTS.match(self.text)
        if not m:
            return [nodes.inline(self.rawtext, self.text, classes=["sf-num"])], []
        unit = m["unit"] or ""
        kind = "volt" if unit == "V" else "plain" if not unit else "size"
        parts: list[tuple[str, str]] = []

        def part(text: str, css: str) -> None:
            parts.append((css, text))

        if m["count"]:
            part(m["count"], "sf-n-count")
            part(" \u00d7 ", "sf-n-times")
        for whole, frac, sep in ((m["a"], m["af"], ""), (m["b"], m["bf"], "\u2013")):
            if whole is None:
                continue
            if sep:
                part(sep, "sf-n-sep")
            part(whole, "sf-n-int")
            if kind == "volt":
                part(f".{frac}" if frac else "", "sf-n-frac")
        if unit:
            # A missing value keeps the unit's room but not its text.
            part("" if m["a"] == "\u2014" else f" {unit}", "sf-n-unit")
        text = "".join(t for _, t in parts)
        classes = ["sf-num", f"sf-num-{kind}"]
        return [Number(self.rawtext, text, classes=classes, parts=parts)], []


class MoreRole(SphinxRole):
    """``{sfmore}`a, b, c``` as an ellipsis whose tooltip is the text: what
    a shortened list leaves out."""

    def run(self) -> tuple[list[nodes.Node], list[nodes.system_message]]:
        return [nodes.abbreviation(self.rawtext, "\u2026", explanation=self.text)], []


class SearchEnglish(en.SearchEnglish):
    """English for the search index, each word stemmed once a build: Sphinx
    stems each page's words afresh, some 230,000 words for 7,000 stems."""

    def __init__(self, options: dict[str, str]) -> None:
        super().__init__(options)
        self._stems: dict[str, str] = {}

    def stem(self, word: str) -> str:
        if word not in self._stems:
            self._stems[word] = super().stem(word)
        return self._stems[word]


REPO_URL = "https://github.com/mithro/spiflash"


def _is_dir(path: str) -> bool:
    return path.endswith("/") or "." not in path.rsplit("/", 1)[-1]


def repo_url(path: str) -> str:
    """This repository's page for ``path`` (a file or a directory) on main."""
    kind = "tree" if _is_dir(path) else "blob"
    return f"{REPO_URL}/{kind}/main/{path.rstrip('/')}"


def upstream_url(source: str, path: str) -> str:
    """An upstream's page for ``path`` at the commit the data came from. A
    glob (``drivers/mtd/spi-nor/*.c``, ``boards/**/*.dts``) links to the
    directory above its first wildcard."""
    info = spiflash.sources()[source]
    base = info.browse.rstrip("/")
    if "*" in path:
        parts = path.split("/")
        path = "/".join(parts[: next(i for i, p in enumerate(parts) if "*" in p)]) + "/"
    kind = "tree" if _is_dir(path) else "blob"
    return f"{base}/{kind}/{info.commit}/{path.rstrip('/')}"


class _LinkRole(SphinxRole):
    """A role whose text, ``target`` or ``title <target>``, becomes a link
    shown as code (or as ``title``)."""

    def url(self, target: str) -> str:
        raise NotImplementedError

    def display(self, target: str) -> str:
        """The code shown for ``target`` when no title is given."""
        return target

    def run(self) -> tuple[list[nodes.Node], list[nodes.system_message]]:
        has_title, title, target = split_explicit_title(self.text)
        try:
            url = self.url(target)
        except (KeyError, ValueError) as e:
            msg = self.inliner.reporter.error(f"{self.name}: {e}", line=self.lineno)
            return [nodes.problematic(self.rawtext, self.rawtext)], [msg]
        shown = self.display(target)
        text: nodes.Node = nodes.Text(title) if has_title else nodes.literal(shown, shown)
        return [nodes.reference(self.rawtext, "", text, refuri=url)], []


class RepoRole(_LinkRole):
    """``{repo}`tools/sources.toml``` links to the file in this repository."""

    def url(self, target: str) -> str:
        return repo_url(target)


class UpstreamRole(_LinkRole):
    """``{upstream}`linux:drivers/mtd/spi-nor/core.c``` links to the file in
    that upstream, at the pinned commit; shown without the ``linux:``."""

    def url(self, target: str) -> str:
        source, sep, path = target.partition(":")
        if not sep:
            msg = f"expected <source>:<path>, not {target!r}"
            raise ValueError(msg)
        return upstream_url(source, path)

    def display(self, target: str) -> str:
        return target.partition(":")[2]


class GithubRole(_LinkRole):
    """``{github}`mithro/apt-repo-action``` links to a GitHub repository."""

    def url(self, target: str) -> str:
        return f"https://github.com/{target}"


def _substitutions(app: Sphinx, config: Config) -> None:
    """The numbers the home page quotes ({{chips}}, {{vendors}}, ...)."""
    numbers = {k: f"{v:,}" for k, v in stats(spiflash.database()).items()}
    config.myst_substitutions = {**numbers, "version": app.config.release}


def setup(app: Sphinx) -> dict[str, Any]:
    app.connect("config-inited", _substitutions)
    app.connect("builder-inited", _builder_inited)
    # Before the theme's handler (500), which renders the sidebar.
    app.connect("html-page-context", _chip_nav, priority=400)
    app.add_role("sfid", SpanRole("sf-id"))
    app.add_role("sfop", SpanRole("sf-op"))
    app.add_role("sfyes", SpanRole("sf-yes"))
    app.add_role("sfkind", SpanRole("sf-kind"))
    app.add_role("sfsub", SpanRole("sf-sub"))
    app.add_role("sfsrc", SourceRole())
    app.add_role("sfsrcme", SourceRole(mine=True))
    app.add_node(SourceBadge, html=(visit_source_badge, None))
    app.add_role("sfnum", NumberRole())
    app.add_node(Number, html=(visit_number, None))
    app.add_role("sfmore", MoreRole())
    app.add_role("repo", RepoRole())
    app.add_role("upstream", UpstreamRole())
    app.add_role("github", GithubRole())
    app.add_search_language(SearchEnglish)
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}
