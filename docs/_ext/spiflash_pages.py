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
from dataclasses import replace
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
from spiflash import derive
from spiflash.derive import ERASE_BY_OPCODE, NAND_ERASE_BY_OPCODE
from spiflash.enums import Bound, Feature, FlashType, OperationKind, Source, TimedEvent
from spiflash.model import compared_value, strip_continuation
from spiflash.opcodes import OPERATIONS, sort_key
from spiflash.registers import ROLES, RegisterBit
from spiflash.sfdp_tools import diff as sfdp_diff
from spiflash.units import human_duration, human_frequency, human_size

if TYPE_CHECKING:
    from collections.abc import Callable

    from sphinx.application import Sphinx
    from sphinx.config import Config
    from sphinx.writers.html5 import HTML5Translator

    from spiflash import Database, Flash, Record, SfdpDump
    from spiflash.model import SupportedOperation
    from spiflash.opcodes import Operation


def chip_page(
    db: Database, f: Flash, vendor_slug: str, slugs: dict[int, str], issues: list[Issue]
) -> str:
    kind = "SPI NAND" if f.type == "nand" else "SPI NOR"
    # In no toctree: see chip_nav.
    out = ["---\norphan: true\n---\n", f"# {esc(title_of(f))}\n"]
    out.append(
        " ".join(
            [
                f"{{bdg-link-primary}}`{esc(vendor_of(f))}"
                + (" (inferred)" if f.manufacturer_inferred else "")
                + f" <../vendors/{vendor_slug}.html>`",
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
    out += _identification(db, f, kind, slugs)
    out += _extended_ids(db, f)
    out += _capabilities(f)
    out += _addressing(f)
    out += _registers(f)
    out += _geometry(f)
    out += _sfdp(f)
    out += _opcodes(f)
    out += _erase_layouts(f)
    out += _timing(f)
    out += _sources(db, f)
    out += chip_issues(db, slugs, f, issues)
    out.append(f"\n[All {esc(vendor_of(f))} parts](../vendors/{vendor_slug}.md)\n")
    return "\n".join(out)


def _summary_cards(f: Flash) -> list[str]:
    cards = [("Capacity", "size"), ("Page", "page_size")]
    if f.type is FlashType.NAND:
        # A SPI NAND part's erase block, and the spare area each page has.
        cards += [("Spare/page", "oob_size"), ("Block", "sector_size")]
    else:
        cards.append(("Sector", "sector_size"))
    out = ["::::{grid} 2 2 4 4\n:gutter: 2\n:class-container: sf-cards\n"]
    for label, attr in [*cards, ("Supply", "voltage")]:
        value = volts(f.voltage) if attr == "voltage" else size_text(getattr(f, attr))
        if attr == "voltage" and f.voltage is None and f.supply_mv is not None:
            # No source gives a range: the voltage the programmers' tables
            # say to power the part at, a setting of theirs.
            setters = {r.source for r in f.records if r.supply_mv == f.supply_mv}
            value = (
                f"{volt(f.supply_mv)}\n\n{_who(tuple(sorted(setters, key=lambda s: s.priority)))} "
                "power it at"
            )
        # Parts an extended id tells apart differ on it: each's is in the
        # Extended ids table.
        if f.by_ext_id(attr):
            value += "\n\n[Differs by part](#extended-ids)"
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
                "{sfyes}`✓`" if f.confirms(d) else " ",
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


def _identification(db: Database, f: Flash, kind: str, slugs: dict[int, str]) -> list[str]:
    rows = []
    if f.family == "jedec":
        rows.append(["Read id (0x9f) answers", f"{{sfid}}`{spaced(f.id_hex)}`"])
        if f.bank:
            rows.append(["With JEP106 continuation codes", f"{{sfid}}`{spaced(f.jedec_id)}`"])
    else:
        rows.append([f"Legacy id ({f.family.upper()})", f"{{sfid}}`{spaced(f.id_hex)}`"])
    if len(f.ids) > 1:
        # A SPI NAND source matching fewer bytes of the id (see Database).
        shorter = ", ".join(f"{{sfid}}`{spaced(i.hex())}`" for i in f.ids[1:])
        rows.append(["Also matched on its first bytes", shorter])
    inferred = " (inferred from the id and part name)" if f.manufacturer_inferred else ""
    rows.append(["Manufacturer", vendor_link(vendor_of(f)) + inferred])
    if db.jep106(f.id[0], f.bank):
        rows.append(
            [
                f"JEP106 name of 0x{f.id[0]:02x} (bank {f.bank + 1})",
                jep106_link(db, (f.bank, f.id[0])),
            ]
        )
    # The legacy ids the sources say the part also answers (no chip of
    # their own), and a legacy chip's JEDEC chips whose records give its id.
    for legacy, giving in f.legacy_ids.items():
        rows.append(
            [
                f"Also answers {legacy.family.upper()}",
                f"{{sfid}}`{spaced(legacy.id.hex())}` {_who(giving)}"
                + "".join(
                    f" ([the {legacy.family.upper()} chip](../chips/{slugs[id(o)]}.md))"
                    for o in db.flashes
                    if o.family is legacy.family and o.id == legacy.id
                ),
            ]
        )
    if f.family != "jedec":
        mine = (f.family, f.id)
        answering = [
            o
            for o in db.flashes
            if o.family == "jedec" and any((i.family, i.id) == mine for i in o.legacy_ids)
        ]
        if answering:
            rows.append(
                [
                    "JEDEC chips whose sources give this id",
                    ", ".join(f"[{esc(o.name)}](../chips/{slugs[id(o)]}.md)" for o in answering),
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
        # Whose part it is: an extended id can tell two makers' parts apart
        # (GigaDevice's GD5F1GQ5REYIG and ESMT's F50L2G41KA at c8 41).
        part = db.narrow(f, ext) if ext else replace(f, records=tuple(recs))
        maker = esc(part.manufacturer or "Unknown") + (
            " (inferred)" if part.manufacturer_inferred else ""
        )
        names = dict.fromkeys(r.name for r in recs)
        notes = dict.fromkeys(n for r in recs for n in r.notes)
        by_source: dict[str, str] = {}
        for r in sorted(recs, key=lambda r: r.source.priority):
            by_source.setdefault(r.source, db.link(r) or "")
        rows.append(
            [
                f"{{sfid}}`{spaced(ext.hex())}`" if ext else "none (any variant)",
                ", ".join(esc(n) for n in names),
                maker,
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
            ["Ext. id", "Listed as", "Maker", "By", "Size", "Page", "Sector", "Upstream notes"],
            rows,
            "sf-table sf-ext-ids",
        ),
        "",
    ]


def _source_header(s: str) -> str:
    return f"{{sfsrc}}`{s}`"


def _mark(role: str, text: str) -> str:
    """A matrix cell's mark, ``{role}`text```: the role draws the mark,
    ``text`` (why) is its tooltip."""
    why = text.replace("`", "'")
    return f"{{{role}}}`{why}`"


def _capabilities(f: Flash) -> list[str]:
    if not f.features:
        return []
    shown = [feat for feat in FEATURE_TEXT if feat in f.features]
    by = {feat: {s.source: s for s in f.feature_sources(feat)} for feat in shown}
    srcs = [s for s in f.sources if any(s in given for given in by.values())]

    def cell(feat: str, source: Source) -> str:
        given = by[feat].get(source)
        if given is None:
            return " "
        return _mark("sfimplied" if given.implied else "sfclaimed", given.because)

    rows = [
        [badge(*FEATURE_TEXT[feat]), esc(Feature(feat).description)] + [cell(feat, s) for s in srcs]
        for feat in shown
    ]
    out = [
        "## Capabilities\n",
        (
            "Each capability some source says this part has, and which sources say so: "
            "{sfyes}`✓` where the source's entry claims it, {sfhollow}`○` where it does "
            "not but implies it, by the operations, erase layouts, size or SFDP tables it "
            "gives ([](../derived.md)). An operation a source's driver sends to every part, "
            "whatever the entry says (a driver default, {sfgrey}`◌` under Opcodes), implies "
            "nothing.\n"
        ),
        list_table(
            ["Capability", "What it means", *[_source_header(s) for s in srcs]],
            rows,
            "sf-table sf-matrix sf-capabilities",
        ),
        "",
        ":::{dropdown} Why each source gives each capability\n:class-container: sf-why\n",
    ]
    for feat in shown:
        reasons = "; ".join(f"{source_badge(s.source)} {esc(s.because)}" for s in by[feat].values())
        out.append(f"- {badge(*FEATURE_TEXT[feat])}: {reasons}")
    out.append(":::\n")
    return out


def _who(sources: tuple[Source, ...]) -> str:
    return " ".join(source_badge(s) for s in sources)


def _addressing(f: Flash) -> list[str]:
    """How many address bytes the part takes, and its ways into 4-byte
    address mode, each with the sources giving it (stated, or from their
    SFDP tables)."""
    if not f.four_byte_modes:
        return []
    rows = [["Address bytes", esc(str(f.address_bytes)), ""]]
    for mode in sorted(f.four_byte_modes):
        given = f.four_byte_mode_sources(mode)
        marks = [(_mark("sfimplied" if s.implied else "sfclaimed", s.because), s) for s in given]
        who = " ".join(f"{mark} {source_badge(s.source)}" for mark, s in marks)
        ops = ", ".join(
            f"[`{op}`](../opcodes/{op}.md)" for op in derive.FOUR_BYTE_MODE_OPERATIONS[mode]
        )
        rows.append([esc(f"Way in: {mode.label}"), ops or EM_DASH, who])
    return [
        "## 4-byte addressing\n",
        (
            "The ways into 4-byte address mode the sources give, stated "
            "({sfyes}`✓`) or from their SFDP tables ({sfhollow}`○`, BFPT DW16), and "
            "the operations each way is ([](../derived.md#4-byte-addressing)).\n"
        ),
        list_table(["", "Operations", "Sources"], rows, "sf-table sf-registers"),
        "",
    ]


def _registers(f: Flash) -> list[str]:
    """The quad enable bit and requirement, and the block-protection bits,
    each value with the sources giving it."""
    qe = f.values("quad_enable")
    qer = f.values("quad_enable_requirement")
    roles = {role: f.values(f"protection.{role}") for role in ROLES}
    otp = {part: f.values(f"otp.{part}") for part in ("size", "regions")}
    if not (qe or qer or any(roles.values()) or any(otp.values())):
        return []
    out = [
        "## Registers\n",
        (
            "Where the part's register bits are, as the sources give them: a register "
            "is named by the command that reads it, so a Macronix configuration register "
            "(read with 0x15) is SR3, and a Spansion CR1 (read with 0x35) SR2 "
            "([](../derived.md#registers)).\n"
        ),
    ]
    rows = []

    def shown(value: Any, chip: Any) -> tuple[str, str]:
        """A value as the sources give it (the chip's with how it is
        written, where a source says), and the chip's mark."""
        if value != compared_value(chip):
            return str(value), ""
        return str(chip), " {bdg-primary}`chip`"

    for value, who in qe.items():
        what, mine = shown(value, f.quad_enable)
        if isinstance(value, RegisterBit):
            what += f" (read with {value.register.read_with})"
        rows.append(["Quad enable bit", esc(what) + mine, _who(who)])
    for value, who in qer.items():
        mine = " {bdg-primary}`chip`" if value == f.quad_enable_requirement else ""
        rows.append(
            ["Quad enable requirement", esc(f"{value}: {value.description}") + mine, _who(who)]
        )
    layout = f.protection.roles() if f.protection else {}
    for role, given in roles.items():
        for value, who in given.items():
            what, mine = shown(value, layout.get(role))
            rows.append([f"Protection: {role}", esc(what) + mine, _who(who)])
    # The OTP area: the bytes the user can program, and in how many regions.
    chip_otp = f.otp
    for part, label in (("size", "OTP area"), ("regions", "OTP regions")):
        for value, who in otp[part].items():
            mine = " {bdg-primary}`chip`" if chip_otp and getattr(chip_otp, part) == value else ""
            text = size_text(value) if part == "size" else esc(str(value))
            rows.append([label, text + mine, _who(who)])
    out.append(list_table(["Field", "Bit", "Sources"], rows, "sf-table sf-registers"))
    out.append("")
    out.append(
        "{bdg-primary}`chip` marks the value the chip is given: the one most sources "
        "give, role by role.\n"
    )
    for bit, shared in f.shared_bits().items():
        out.append(
            f"The sources' answers put {' and '.join(shared)} on one bit, {esc(bit)}, "
            "which no part has: the chip is given the best source's own layout.\n"
        )
    return out


#: The geometry values a chip page lists, and their labels.
GEOMETRY = {
    "oob_size": "Spare area per page",
    "planes": "Planes per die",
    "dies": "Dies",
    "die_select_bit": "Die select bit",
    "max_bad_blocks": "Bad blocks per die (at most)",
    "ecc.strength_bits": "ECC: bits corrected",
    "ecc.step_bytes": "ECC: per step of",
}


def _geometry(f: Flash) -> list[str]:
    """A SPI NAND part's geometry, and a SPI NOR part's dies: each value
    with the sources giving it, then what follows from them (derived)."""
    nand = f.type is FlashType.NAND
    given = {attr: f.values(attr) for attr in GEOMETRY}
    if not any(given.values()):
        return []
    rows = []
    for attr, label in GEOMETRY.items():
        chip = f.value(attr)
        for value, who in given[attr].items():
            mine = " {bdg-primary}`chip`" if value == compared_value(chip) else ""
            if attr in ("oob_size", "ecc.step_bytes"):
                text = size_text(value)
            elif isinstance(value, RegisterBit):
                text = esc(f"{value} (written with SET FEATURE, 0x1f)")
            else:
                text = esc(str(value))
            rows.append([esc(label), text + mine, _who(who)])
    select = [o for o in f.opcodes.values() if o.name in ("DIE_SELECT", "NAND_DIE_SELECT")]
    for o in select:
        op = f"[`{o.name}`](../opcodes/{o.name}.md), {{sfop}}`0x{o.opcode:02x}` and the die"
        rows.append(["Die select operation", op, _who(o.sources)])
    out = [
        "## NAND geometry\n" if nand else "## Dies\n",
        list_table(["Field", "Value", "Sources"], rows, "sf-table sf-registers"),
        "",
        "{bdg-primary}`chip` marks the value the chip is given: the one most sources give.\n",
    ]
    derived = _derived_geometry(f) if nand else []
    if derived:
        out.append("Worked out from those, not stated by any source:\n")
        out.append(list_table(["Field", "Value"], derived, "sf-table sf-kv"))
        out.append("")
    return out


def _derived_geometry(f: Flash) -> list[list[str]]:
    """What a SPI NAND chip's size, page, block, dies and spare area give."""
    rows = []
    page, block, size, dies = f.page_size, f.sector_size, f.size, f.dies
    if page and block:
        rows.append(["Pages per block", num(f"{block // page:,}")])
    if block and size:
        rows.append(["Blocks", num(f"{size // block:,}")])
        if dies:
            rows.append(["Blocks per die", num(f"{size // block // dies:,}")])
    if size and dies:
        rows.append(["Die size", size_text(size // dies)])
    if page and size and f.oob_size:
        rows.append(["Spare area in all", size_text(f.oob_size * (size // page))])
    return rows


def _sfdp(f: Flash) -> list[str]:
    if not f.sfdp_dumps:
        return []
    out = [
        "## SFDP\n",
        (
            f"The part's SFDP ([JESD216]({JESD216})) tables, as the sources carry them: "
            "what one part answered, decoded by {py:mod}`spiflash.sfdp`; `spiflash sfdp` "
            "prints every field. The sources carrying them derive their geometry, erase "
            "layouts, operations and capabilities from them ([](../derived.md)). SFDP says "
            "nothing about the vendor, voltage or protection, and {sfsrc}`linux` keeps "
            "fixups for tables that are wrong, so read it as the part's own claim. Whole "
            "dumps come first, then tables a source copies without the rest.\n"
        ),
    ]
    if len(f.sfdp_dumps) > 1:
        out.append(
            "The sources carry more than one set of tables for this id: "
            f"`spiflash sfdp-diff {f.key} {f.key}#2` compares the first two, field by "
            f"field and dword by dword ([](../usage.md)). {_sfdp_differences(f)}\n"
        )
    for d in f.sfdp_dumps:
        out.append(list_table(["Parameter", "Value"], _sfdp_rows(d), "sf-table"))
        out.append("")
    return out


def _sfdp_differences(f: Flash) -> str:
    """What the chip's first two SFDP dumps differ in, in a sentence: the
    decoded fields (two boards' copies of the MX25R6435F's BFPT differ in
    DW12's erase resume-to-suspend interval alone), else the dwords."""
    d = sfdp_diff(f.sfdp_dumps[0].sfdp, f.sfdp_dumps[1].sfdp)
    if not d:
        return "The first two say the same."
    parts = [f"{x.path}" for x in d.fields if not x.expected]
    parts += [f"{t.name} (only in one)" for t in d.tables if t.a is None or t.b is None]
    if not parts:
        parts = [f"{x.name} DW{x.index}" for x in d.dwords]
    return f"The first two differ in {esc(', '.join(parts))}."


def _sfdp_rows(d: SfdpDump) -> list[list[str]]:
    """One dump's table: whose it is, then what it says. Parts sharing an id
    can carry different dumps, so each names its parts."""
    s = d.sfdp
    if s.partial:
        names = " and ".join(esc(h.name) for h in s.headers)
        props = d.records[0].via.get("sfdp_tables", "")
        copied = f" ({esc(props)})" if props else ""
        rows = [
            [
                "Tables of",
                f"{source_badge(d.source)} {esc(', '.join(d.parts))}, in "
                + ", ".join(f"`{r.url}`" for r in d.records),
            ],
            ["Revision", f"{names} only, copied without the SFDP header{copied}"],
        ]
    else:
        tables = ", ".join(f"{esc(h.name)} {h.revision}" for h in s.headers)
        rows = [
            ["Dump of", f"{source_badge(d.source)} {esc(', '.join(d.parts))}"],
            ["Revision", f"{esc(s.revision_name)}, with {tables}"],
        ]
    geometry = [size_text(s.size)]
    if s.page_size is not None:
        geometry.append(f"{size_text(s.page_size)} pages")
    if s.address_bytes is not None:
        geometry.append(esc(f"{s.address_bytes}-byte addresses"))
    rows.append(["Geometry", ", ".join(geometry)])  # size_text is markup
    if s.erase_types:
        erases = []
        for e in s.erase_types:
            text = f"`0x{e.opcode:02x}` {size_text(e.size)}"
            if e.opcode_4b is not None:
                text += f" (`0x{e.opcode_4b:02x}` with a 4-byte address)"
            if e.typical_ns is not None:
                text += f", typically {human_duration(e.typical_ns)}"
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
            ways = ", ".join(m.label for m in sorted(bfpt.four_byte_enter))
            if not s.four_byte_mode:
                ways += " (not read: the part has no 4-byte mode)"
            rows.append(["Enter 4-byte mode", esc(ways)])
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
        "Each opcode some source says this part has, and which sources say so: "
        "{sfyes}`✓` for the part, {sfgrey}`◌` only as the source's driver default "
        "(sent to every part, whatever the entry says), which implies no capability. "
        "*Dummy* is the part's own dummy clocks for it, where a source gives them "
        "(the operation's page has its usual number). "
        "A missing opcode may still be supported: see [](../opcodes.md).\n"
    )

    def cell(o: SupportedOperation, source: Source) -> str:
        if source not in o.sources:
            return " "
        if source in o.assumed_by:
            why = "; ".join(c.via for c in o.because if c.source == source)
            return _mark("sfdefault", f"driver default: {why}")
        return "{sfyes}`✓`"

    rows = [
        [
            f"{{sfop}}`0x{o.opcode:02x}`",
            f"[`{o.name}`](../opcodes/{o.name}.md)",
            kind_link(o.operation.kind, "../opcodes.html", o.operation.flash_type),
            esc(o.operation.description),
            _dummy(o),
        ]
        + [cell(o, s) for s in srcs]
        for o in f.opcodes.values()
    ]
    head = ["Opcode", "Operation", "Type", "Description", "Dummy"]
    out.append(
        list_table(
            [*head, *[_source_header(s) for s in srcs]],
            rows,
            "sf-table sf-matrix sf-opcodes",
        )
    )
    out.append("")
    out.append(":::{dropdown} Why each source lists each opcode\n:class-container: sf-why\n")
    out.append(
        "*Implied* marks an opcode that follows from what the source's entry "
        "says rather than being listed: its erasers' opcodes, or how it reads "
        "the id. *Driver default* marks one the source's driver sends to every "
        "part, whatever the entry says, which implies no capability.\n"
    )
    for o in f.opcodes.values():
        reasons = "; ".join(
            f"{source_badge(c.source)} {esc(c.via)}"
            + (f", {c.dummy_clocks} dummy clocks" if c.dummy_clocks is not None else "")
            + (" *(implied)*" if c.implied else "")
            + (" *(driver default)*" if c.assumed else "")
            for c in o.because
        )
        out.append(f"- [`{o.name}`](../opcodes/{o.name}.md) (0x{o.opcode:02x}): {reasons}")
    out.append(":::\n")
    return out


def _dummy(o: SupportedOperation) -> str:
    """The part's dummy clocks for an operation, where a source gives them;
    each number and who gives it, where they differ."""
    given = o.dummy_clocks_given()
    if len(given) < 2:
        return num(str(next(iter(given)))) if given else " "
    return "; ".join(f"{num(str(n))} ({_who(who)})" for n, who in sorted(given.items()))


def _erase_layouts(f: Flash) -> list[str]:
    rows = []
    for r in f.records:
        dies = derive.die_erasers(r)
        for e in r.erasers:
            blocks = ", ".join(num(f"{b.count:,} {TIMES} {human_size(b.size)}") for b in e.blocks)
            op = e.opcode
            # A SPI NAND block erase is not the SPI NOR operation of the
            # same opcode.
            by_opcode = ERASE_BY_OPCODE if r.type is FlashType.NOR else NAND_ERASE_BY_OPCODE
            opname = by_opcode.get(op) if op is not None else None
            operation = f"[`{opname}`](../opcodes/{opname}.md)" if opname else EM_DASH
            if e.assumed:
                operation += " *(driver default)*"
            if e in dies and e not in r.eraser_claims:
                operation += f" *(from its {r.dies} dies)*"
            elif e not in r.eraser_claims:
                operation += " *(SFDP)*"
            # The upstream token stating it, where one does (QEMU's ER_4K).
            token = r.via.get(f"erasers:0x{op:02x}") if op is not None else None
            if token:
                operation += f", from `{token.replace('`', '')}`"  # a code span: no escapes
            rows.append(
                [
                    source_badge(r.source),
                    esc(r.name),
                    f"{{sfop}}`0x{op:02x}`" if op is not None else esc(e.function or ""),
                    operation,
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


#: Each time event as a row heading.
_EVENT_TEXT = {
    TimedEvent.BLOCK_ERASE: "Block erase",
    TimedEvent.CHIP_ERASE: "Chip erase",
    TimedEvent.PAGE_PROGRAM: "Page program",
    TimedEvent.BYTE_PROGRAM_FIRST: "Byte program, first byte",
    TimedEvent.BYTE_PROGRAM_ADDITIONAL: "Byte program, each further byte",
    TimedEvent.PAGE_READ: "Page read (array to cache)",
    TimedEvent.ERASE_SUSPEND: "Erase suspend",
    TimedEvent.PROGRAM_SUSPEND: "Program suspend",
    TimedEvent.ERASE_RESUME_TO_SUSPEND: "Erase resume to next suspend",
    TimedEvent.PROGRAM_RESUME_TO_SUSPEND: "Program resume to next suspend",
    TimedEvent.DPD_ENTER: "Enter deep power-down (tDP)",
    TimedEvent.DPD_EXIT: "Leave deep power-down (tRES1, tRDP)",
    TimedEvent.DPD_MIN_TIME: "Least time in deep power-down (tDPDD)",
    TimedEvent.DPD_WAKE_PULSE: "Wake-up chip select pulse (tCRDP)",
    TimedEvent.RESET_PULSE: "Reset pulse",
    TimedEvent.RESET_RECOVERY: "Reset recovery",
}


def _timing(f: Flash) -> list[str]:
    """The part's times: a row per event (a block erase per opcode), a
    column per bound, each time with the sources giving it, stated
    ({sfyes}`✓`) or from their SFDP tables ({sfhollow}`○`); and the
    fastest clock the sources give."""
    clocks = f.values("max_clock_hz")
    if not f.timings and not clocks:
        return []
    out = ["## Timing\n"]
    if f.timings:
        bounds = [b for b in Bound if any(kb[1] is b for kb in f.timings)]
        rows = []
        for key in dict.fromkeys(k for k, _ in f.timings):
            what = _EVENT_TEXT[key.event]
            if key.opcode is not None:
                what += f" ({{sfop}}`0x{key.opcode:02x}`)"
            cells = []
            for bound in bounds:
                given = f.timings.get((key, bound), {})
                cells.append(
                    "\n\n".join(
                        num(human_duration(ns))
                        + " "
                        + " ".join(
                            f"{_mark('sfimplied' if s.implied else 'sfclaimed', s.because)} "
                            f"{source_badge(s.source)}"
                            for s in sources
                        )
                        for ns, sources in given.items()
                    )
                    or EM_DASH
                )
            rows.append([what, *cells])
        out += [
            (
                "How long the part takes, as the sources give it: stated ({sfyes}`✓`), or "
                "from their SFDP tables ({sfhollow}`○`), where a maximum is the typical "
                "time times the table's multiplier ([](../derived.md#times)). Each bound "
                "is its own value; Dediprog's chip erase time does not say which it is "
                "(unspecified).\n"
            ),
            list_table(
                ["", *(esc(str(b).capitalize()) for b in bounds)], rows, "sf-table sf-timing"
            ),
            "",
        ]
    if clocks:
        said = "; ".join(
            f"{num(human_frequency(hz))} {_who(sources)}" for hz, sources in sorted(clocks.items())
        )
        out.append(f"Fastest SPI clock the sources give: {said}.\n")
    return out


def _sfdp_mark(r: Record, attr: str) -> str:
    """`` (SFDP)`` after a value the record has from its SFDP tables rather
    than stating it: its size or page size, or the sector size of an eraser
    the tables give."""
    if r.sfdp_facts is None or getattr(r, attr) is None:
        return ""
    if attr == "sector_size":
        stated = replace(r, sfdp=None, sfdp_tables={})
        return "" if stated.sector_size == r.sector_size else " *(SFDP)*"
    return "" if r.stored(attr) is not None else " *(SFDP)*"


def _quad_enable(r: Record) -> str:
    """A record's quad enable bit, ``SR2 bit 1``, marked where it comes from
    its quad enable requirement (that of its SFDP tables, or one it states)."""
    if r.quad_enable is None:
        return EM_DASH
    if r.stored("quad_enable") is not None:
        return esc(str(r.quad_enable))
    from_tables = r.stored("quad_enable_requirement") is None
    return f"{esc(str(r.quad_enable))} *({'SFDP' if from_tables else r.quad_enable_requirement})*"


def _record_id(r: Record) -> bytes:
    return strip_continuation(r.id or b"")[1]


def _sources(db: Database, f: Flash) -> list[str]:
    # A chip that answers a shorter id too says which id each source gives.
    folded = len(f.ids) > 1
    rows = []
    for r in f.records:
        link = db.link(r)
        where = f"[{esc(r.url)}]({link})" if link else esc(r.url)
        rows.append(
            [
                source_badge(r.source),
                esc(r.name),
                *([f"{{sfid}}`{spaced(_record_id(r).hex())}`"] if folded else []),
                f"{{sfid}}`{spaced(r.ext_id.hex())}`" if r.ext_id else EM_DASH,
                size_text(r.size) + _sfdp_mark(r, "size"),
                size_text(r.page_size) + _sfdp_mark(r, "page_size"),
                size_text(r.sector_size) + _sfdp_mark(r, "sector_size"),
                volt(r.voltage[0] if r.voltage else None),
                volt(r.voltage[1] if r.voltage else None),
                _quad_enable(r),
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
                *(["Id"] if folded else []),
                "Ext. id",
                "Size",
                "Page",
                "Sector",
                "V min",
                "V max",
                "QE",
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
    """The operations, a table per kind of SPI NOR operation, then one of
    SPI NAND's (its own command set), each with how many chips have it."""
    uses = Counter(name for f in flashes for name in f.opcodes)
    out = []

    def row(op: Operation, *more: str) -> list[str]:
        return [
            f"{{sfop}}`0x{op.opcode:02x}`",
            f"[`{op.name}`](opcodes/{op.name}.md)",
            *more,
            esc(op.description),
            count(uses.get(op.name, 0)),
        ]

    nor = [op for op in OPERATIONS.values() if op.flash_type is FlashType.NOR]
    for kind in OperationKind:
        rows = [row(op) for op in nor if op.kind == kind]
        out.append(f"({KIND_TARGET}{kind})=\n### {KIND_TITLE[kind]}\n")
        out.append(list_table(["Opcode", "Operation", "Description", "Chips"], rows, "sf-table"))
        out.append("")
    nand = [op for op in OPERATIONS.values() if op.flash_type is FlashType.NAND]
    rows = [
        row(op, f"{{sfkind}}`{op.kind}`") for op in sorted(nand, key=lambda op: sort_key(op.name))
    ]
    out.append(f"({KIND_TARGET}{FlashType.NAND})=\n### SPI NAND\n")
    out.append(NAND_OPERATIONS_TEXT)
    out.append(
        list_table(["Opcode", "Operation", "Type", "Description", "Chips"], rows, "sf-table")
    )
    out.append("")
    return "\n".join(out)


NAND_OPERATIONS_TEXT = (
    "SPI NAND has its own command set: the host reads a page into the part's "
    "cache ([`NAND_PAGE_READ`](opcodes/NAND_PAGE_READ.md)), then reads from the "
    "cache at a column address; it loads data into the cache, then programs the "
    "cache into a page ([`NAND_PROGRAM_EXECUTE`](opcodes/NAND_PROGRAM_EXECUTE.md)); "
    "and its registers are features, read and written by address. Some of its "
    "opcodes are SPI NOR ones too (0x13, 0x03, 0xd8), so each is an operation "
    "of its own, named `NAND_...`. Only the single transfer rate forms are "
    "here: the double transfer rate ones some Winbond parts take have no "
    "operation yet.\n"
)


#: The opcodes page's target for each kind of operation's table.
KIND_TARGET = "opcodes-"


def kind_link(kind: str, page: str, flash_type: FlashType = FlashType.NOR) -> str:
    """The kind of an operation, linked to its table on the opcodes page
    (``page``, relative to the linking page's HTML): a SPI NAND
    operation's is the SPI NAND table."""
    target = kind if flash_type is FlashType.NOR else flash_type
    return f"{{sfkind}}`{kind} <{page}#{KIND_TARGET}{target}>`"


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


def derived_table(db: Database) -> str:
    """Each capability, what implies it (:mod:`spiflash.derive`), and how
    many entries claim it and how many only imply it."""
    claimed = Counter(f for r in db.records for f in r.feature_claims)
    implied = Counter(f for r in db.records for f in r.features - r.feature_claims)

    def ops(names: tuple[str, ...]) -> str:
        return ", ".join(f"[`{n}`](opcodes/{n}.md)" for n in names)

    erase = {f: size for size, f in derive.ERASE_FEATURE.items()}
    rows = []
    for feat in FEATURE_TEXT:
        why = []
        if feat in derive.FEATURE_IMPLIED_BY:
            why.append(ops(derive.FEATURE_IMPLIED_BY[Feature(feat)]))
        if feat in erase:
            why.append(f"a SPI NOR eraser of uniform {human_size(erase[Feature(feat)])} blocks")
        if feat == Feature.FOUR_BYTE_ADDR:
            why.append(
                "a size over 16 MiB, or "
                + ops(derive.FOUR_BYTE_ADDRESS_OPS[-4:])
                + ", or any `_4B` operation, or SFDP tables saying the part takes "
                "4-byte addresses or has a way into 4-byte mode "
                "({py:func}`~spiflash.derive.address_bytes`)"
            )
        why.extend(
            f"SFDP tables: {what}"
            for what, feats in derive.SFDP_FEATURES.items()
            if Feature(feat) in feats
        )
        if feat == Feature.QUAD_READ:
            why.append("a quad enable bit, stated or from a quad enable requirement")
        if feat == Feature.LOCK:
            why.append("a block-protection bit: BP, or TB, SEC or CMP")
        rows.append(
            [
                badge(*FEATURE_TEXT[feat]),
                "; or ".join(why) or "nothing: only a claim gives it",
                count(claimed[Feature(feat)]),
                count(implied[Feature(feat)]),
            ]
        )
    return list_table(
        ["Capability", "Implied by", "Entries claiming it", "Entries only implying it"],
        rows,
        "sf-table",
    )


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
    _write(srcdir / "_generated" / "derived-table.md", derived_table(db))
    _write(srcdir / "_generated" / "sources-table.md", sources_table(db))
    _write(srcdir / "_generated" / "files-read.md", files_read_table(db))
    _write(srcdir / "_generated" / "sources-list.md", sources_list(db, issues, ""))
    # A chip id that left the database leaves no stale page behind.
    for d in (chips_dir, vendors_dir, ops_dir, issues_dir, jep106_dir, sources_dir):
        for old in d.glob("*.md"):
            if old not in wanted:
                old.unlink()
    return {f"chips/{slugs[id(f)]}": f"vendors/{vslug[vendor_of(f)]}" for f in db.flashes}


#: Each chip page's vendor page, by docname (see :func:`chip_nav`).
CHIP_VENDOR: dict[str, str] = {}
#: The vendor pages' sidebars, by vendor, the directory of the page they
#: are shown on, and the theme's toctree() options.
_VENDOR_NAV: dict[tuple[str, str, str], str] = {}


def set_chip_vendors(vendors: dict[str, str]) -> None:
    """Sets each chip page's vendor page, by docname, for a build."""
    CHIP_VENDOR.clear()
    CHIP_VENDOR.update(vendors)
    _VENDOR_NAV.clear()


def _builder_inited(app: Sphinx) -> None:
    set_chip_vendors(generate(Path(app.srcdir)))


def chip_nav(
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
    page = builder.get_target_uri(pagename)

    def toctree(*, collapse: bool = True, **kwargs: Any) -> str:
        # As StandaloneHTMLBuilder._get_local_toctree, for the vendor page.
        kwargs.setdefault("includehidden", False)
        if kwargs.get("maxdepth") == "":
            kwargs.pop("maxdepth")
        # Made once for each vendor and directory the links are relative to.
        options = repr(sorted({**kwargs, "collapse": collapse}.items()))
        key = (vendor, posixpath.dirname(page), options)
        if key not in _VENDOR_NAV:
            tree = global_toctree_for_doc(
                builder.env, vendor, builder, tags=builder.tags, collapse=collapse, **kwargs
            )
            if tree is not None:
                _rebase_links(tree, builder.get_target_uri(vendor), page)
            _VENDOR_NAV[key] = builder.render_partial(tree)["fragment"]
        return _VENDOR_NAV[key]

    context["toctree"] = toctree


def _rebase_links(tree: nodes.Element, from_uri: str, to_uri: str) -> None:
    """Makes the links of the page at ``from_uri``, relative to it, relative
    to the page at ``to_uri`` (both as the builder names them: a vendor's
    ``vendors/winbond.html`` and a chip's ``chips/ef4018.html``, say, or
    ``vendors/winbond/`` and ``chips/ef4018/``)."""
    here, there = posixpath.dirname(from_uri), posixpath.dirname(to_uri) or "."
    for ref in tree.findall(nodes.reference):
        uri = ref.get("refuri", "")
        if "://" in uri:
            continue
        path, hash_, anchor = uri.partition("#")
        target = posixpath.join(here, path) if path else from_uri
        rebased = posixpath.relpath(posixpath.normpath(target), there)
        if target.endswith("/"):
            rebased += "/"
        ref["refuri"] = rebased + (hash_ + anchor if anchor else "")


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


class Mark(nodes.inline):
    """A matrix cell's mark from ``{sfclaimed}``, ``{sfimplied}`` or
    ``{sfdefault}``: ``node["mark"]`` is drawn, ``node["why"]`` its
    tooltip."""


def visit_mark(self: HTML5Translator, node: Mark) -> None:
    self.body.append(self.starttag(node, "span", "", title=node["why"]))
    self.body.append(self.encode(node["mark"]))
    self.body.append("</span>")
    raise nodes.SkipNode


class MarkRole(SphinxRole):
    """``{role}`why``` as ``mark``, with ``why`` on hover: a capability a
    source claims or implies, or an operation that is only its driver's
    default."""

    def __init__(self, css: str, mark: str) -> None:
        super().__init__()
        self.css, self.mark = css, mark

    def run(self) -> tuple[list[nodes.Node], list[nodes.system_message]]:
        node = Mark(self.rawtext, "", classes=[self.css])
        node["mark"], node["why"] = self.mark, self.text
        return [node], []


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
    app.connect("html-page-context", chip_nav, priority=400)
    app.add_role("sfid", SpanRole("sf-id"))
    app.add_role("sfop", SpanRole("sf-op"))
    app.add_role("sfyes", SpanRole("sf-yes"))
    app.add_role("sfhollow", SpanRole("sf-hollow"))
    app.add_role("sfgrey", SpanRole("sf-default"))
    app.add_role("sfclaimed", MarkRole("sf-yes", "\u2713"))
    app.add_role("sfimplied", MarkRole("sf-hollow", "\u25cb"))
    app.add_role("sfdefault", MarkRole("sf-default", "\u25cc"))
    app.add_node(Mark, html=(visit_mark, None))
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
