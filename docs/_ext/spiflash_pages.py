"""Generate the site's vendor and chip pages from the spiflash database.

At ``builder-inited`` this writes MyST pages into ``docs/vendors/`` and
``docs/chips/`` (both git-ignored): one page per vendor, with a table of all
its parts, one page per chip id, and the index pages linking them. A page is
only rewritten when its text changes, so incremental builds stay fast.

The pages are Markdown, not raw HTML, so Sphinx's search indexes every part
name and id.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from docutils import nodes
from sphinx.util.docutils import SphinxRole

import spiflash
from spiflash.cli import human_size
from spiflash.opcodes import KINDS, OPERATIONS

if TYPE_CHECKING:
    from sphinx.application import Sphinx
    from sphinx.config import Config

    from spiflash import Database, Flash

EN_DASH = "\N{EN DASH}"
EM_DASH = "\N{EM DASH}"
TIMES = "\N{MULTIPLICATION SIGN}"

SOURCE_LABEL = {
    "flashrom": "flashrom",
    "flashprog": "flashprog",
    "linux": "Linux",
    "u-boot": "U-Boot",
    "openocd": "OpenOCD",
    "openfpgaloader": "openFPGALoader",
}

# What each feature means, and the badge colour for its group.
_FEATURE_TEXT = {
    "erase_4k": ("4 KiB erase", "secondary"),
    "erase_32k": ("32 KiB erase", "secondary"),
    "erase_64k": ("64 KiB erase", "secondary"),
    "sfdp": ("SFDP", "success"),
    "fast_read": ("fast read", "info"),
    "dual_read": ("dual read", "info"),
    "quad_read": ("quad read", "info"),
    "quad_pp": ("quad program", "info"),
    "octal_read": ("octal read", "info"),
    "octal_dtr_read": ("octal DTR read", "info"),
    "octal_dtr_pp": ("octal DTR program", "info"),
    "qpi": ("QPI", "info"),
    "4byte_addr": ("4-byte address", "primary"),
    "4byte_opcodes": ("4-byte opcodes", "primary"),
    "otp": ("OTP", "success"),
    "lock": ("block protection", "success"),
    "rww": ("read-while-write", "success"),
    "no_erase": ("no erase (FRAM/MRAM)", "warning"),
}

# The features worth a badge in the parts tables.
_HIGHLIGHTS = frozenset({"dual_read", "quad_read", "octal_read", "qpi", "4byte_addr", "sfdp"})

_KIND_TITLE = {
    "id": "Identification",
    "read": "Read",
    "program": "Program",
    "erase": "Erase",
    "register": "Registers",
    "mode": "Modes",
}


# --- small helpers -----------------------------------------------------------


def esc(text: str) -> str:
    """Text safe to put in MyST: Markdown's inline markup escaped."""
    return re.sub(r"([\\`*_{}\[\]<>|#])", r"\\\1", text)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def chip_slug(f: Flash) -> str:
    base = f.id_hex if f.family == "jedec" else f"{f.family}-{f.id_hex}"
    return f"{base}-nand" if f.type == "nand" else base


def spaced(hex_id: str) -> str:
    return " ".join(hex_id[i : i + 2] for i in range(0, len(hex_id), 2))


def vendor_of(f: Flash) -> str:
    return f.manufacturer or "Unknown"


def title_of(f: Flash) -> str:
    names = [n for n in f.names if "." not in n][:3] or list(f.names[:3])
    return " / ".join(names)


def badge(text: str, colour: str = "secondary") -> str:
    return f"{{bdg-{colour}}}`{esc(text)}`"


def feature_badges(features: frozenset[str] | set[str]) -> str:
    return " ".join(badge(*_FEATURE_TEXT[x]) for x in _FEATURE_TEXT if x in features)


def volts(v: tuple[int, int] | None) -> str:
    return f"{v[0] / 1000:g}{EN_DASH}{v[1] / 1000:g} V" if v else EM_DASH


def size_text(n: int | None) -> str:
    return human_size(n) if n else EM_DASH


def list_table(
    header: list[str], rows: list[list[str]], classes: str = "", widths: str | None = None
) -> str:
    """A MyST list-table."""
    lines = [":::{list-table}", ":header-rows: 1"]
    if classes:
        lines.append(f":class: {classes}")
    if widths:
        lines.append(f":widths: {widths}")
    lines.append("")
    for row in [header, *rows]:
        for i, cell in enumerate(row):
            lines.append(("* - " if i == 0 else "  - ") + (cell if cell.strip() else " "))
    lines.append(":::")
    return "\n".join(lines)


def source_badge(source: str) -> str:
    return f"{{sfsrc}}`{source}`"


# --- pages -------------------------------------------------------------------


def chip_page(db: Database, f: Flash, vendor_slug: str) -> str:
    out: list[str] = []
    kind = "SPI NAND" if f.type == "nand" else "SPI NOR"
    out.append(f"# {esc(title_of(f))}\n")
    out.append(
        " ".join(
            [
                badge(vendor_of(f), "primary"),
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
    out += _identification(db, f, kind)
    out += _capabilities(f)
    out += _opcodes(f)
    out += _erase_layouts(f)
    out += _disagreements(f)
    out += _sources(db, f)
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


def _identification(db: Database, f: Flash, kind: str) -> list[str]:
    rows = []
    if f.family == "jedec":
        rows.append(["Read id (0x9f) answers", f"{{sfid}}`{spaced(f.id_hex)}`"])
        if f.bank:
            rows.append(["With JEP106 continuation codes", f"{{sfid}}`{spaced(f.jedec_id)}`"])
    else:
        rows.append([f"Legacy id ({f.family.upper()})", f"{{sfid}}`{spaced(f.id_hex)}`"])
    jep = db.jep106(f.id[0], f.bank)
    rows.append(["Manufacturer", esc(vendor_of(f))])
    if jep and jep.lower() != vendor_of(f).lower():
        rows.append([f"JEP106 name of 0x{f.id[0]:02x} (bank {f.bank + 1})", esc(jep)])
    exts = sorted({r.ext_id.hex() for r in f.records if r.ext_id})
    if exts:
        rows.append(
            [
                "Extended ids that tell variants apart",
                ", ".join(f"{{sfid}}`{spaced(e)}`" for e in exts),
            ]
        )
    rows.append(["Type", kind])
    return ["## Identification\n", list_table(["", ""], rows, "sf-kv"), ""]


def _capabilities(f: Flash) -> list[str]:
    if not f.features:
        return []
    rows = [
        [
            _FEATURE_TEXT[feat][0],
            " ".join(source_badge(s) for s in f.feature_sources(feat)),
        ]
        for feat in _FEATURE_TEXT
        if feat in f.features
    ]
    return [
        "## Capabilities\n",
        feature_badges(f.features) + "\n",
        list_table(["Capability", "Listed by"], rows, "sf-table", "40 60"),
        "",
    ]


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
            f"`{o.name}`",
            f"{{sfkind}}`{o.operation.kind}` {esc(o.operation.description)}",
        ]
        + ["{sfyes}`✓`" if s in o.sources else " " for s in srcs]
        for o in f.opcodes.values()
    ]
    out.append(
        list_table(
            ["Opcode", "Operation", "Description", *[SOURCE_LABEL.get(s, s) for s in srcs]],
            rows,
            "sf-table sf-opcodes",
        )
    )
    out.append("")
    out.append(":::{dropdown} Why each source lists each opcode\n:class-container: sf-why\n")
    for o in f.opcodes.values():
        reasons = "; ".join(f"{SOURCE_LABEL.get(s, s)}: {esc(via)}" for s, via in o.because)
        out.append(f"- `{o.name}` (0x{o.opcode:02x}): {reasons}")
    out.append(":::\n")
    return out


def _erase_layouts(f: Flash) -> list[str]:
    rows = []
    for r in f.records:
        for e in r.erasers or ():
            blocks = ", ".join(f"{n} {TIMES} {human_size(s)}" for s, n in e["blocks"])
            op = e["opcode"]
            opname = next(
                (n for n, o in OPERATIONS.items() if o.opcode == op and o.kind == "erase"), None
            )
            rows.append(
                [
                    source_badge(r.source),
                    esc(r.name),
                    f"{{sfop}}`0x{op:02x}`" if op is not None else esc(e.get("function", "")),
                    f"`{opname}`" if opname else EM_DASH,
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


def _conflict_value(attr: str, value: Any) -> str:
    if attr == "voltage":
        return volts(value)
    return size_text(value) if isinstance(value, int) else esc(str(value))


def _disagreements(f: Flash) -> list[str]:
    if not f.conflicts:
        return []
    out = [":::{warning}\nThe sources disagree:\n"]
    for attr, vals in f.conflicts.items():
        said = "; ".join(
            f"{_conflict_value(attr, v)} ({', '.join(SOURCE_LABEL.get(s, s) for s in ss)})"
            for v, ss in vals.items()
        )
        out.append(f"- **{attr.replace('_', ' ')}**: {said}")
    out.append(
        "\nParts sharing an id often differ in these; the values above are what most "
        "sources give.\n:::\n"
    )
    return out


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
                volts(r.voltage),
                esc(r.tested or EM_DASH),
                where,
            ]
        )
    out = [
        "## What each source says\n",
        list_table(
            ["Source", "Name", "Ext. id", "Size", "Page", "Sector", "Supply", "Tested", "Where"],
            rows,
            "sf-table sf-sources",
        ),
        "",
    ]
    notes = [(r, n) for r in f.records for n in r.notes]
    if notes:
        out.append(":::{dropdown} Upstream comments\n:class-container: sf-why\n")
        for r, n in notes:
            out.append(f"- {SOURCE_LABEL.get(r.source, r.source)} ({esc(r.name)}): {esc(n)}")
        out.append(":::\n")
    return out


def parts_table(
    flashes: list[Flash], slugs: dict[int, str], prefix: str, *, with_vendor: bool = False
) -> str:
    rows = []
    for f in flashes:
        link = (
            f"[{spaced(f.jedec_id if f.family == 'jedec' else f.id_hex)}]"
            f"({prefix}{slugs[id(f)]}.md)"
        )
        row = [link]
        if with_vendor:
            row.append(esc(vendor_of(f)))
        row += [
            ", ".join(esc(n) for n in f.names),
            "NAND" if f.type == "nand" else "NOR",
            size_text(f.size),
            *([] if with_vendor else [size_text(f.page_size)]),
            size_text(f.sector_size),
            volts(f.voltage),
            feature_badges(f.features & _HIGHLIGHTS),
            str(len(f.opcodes)),
            str(len(f.sources)),
        ]
        rows.append(row)
    header = (
        ["Id"]
        + (["Vendor"] if with_vendor else [])
        + [
            "Parts",
            "Type",
            "Size",
            *([] if with_vendor else ["Page"]),
            "Sector",
            "Supply",
            "Highlights",
            "Opcodes",
            "Sources",
        ]
    )
    classes = "sf-table sf-filterable sf-parts" + (" sf-with-vendor" if with_vendor else "")
    return list_table(header, rows, classes)


def vendor_page(db: Database, vendor: str, flashes: list[Flash], slugs: dict[int, str]) -> str:
    nor = sum(1 for f in flashes if f.type == "nor")
    nand = len(flashes) - nor
    ids = Counter(f.id[0] for f in flashes if f.family == "jedec")
    out = [f"# {esc(vendor)}\n"]
    counts = [badge(f"{len(flashes)} chip ids", "primary")]
    if nor:
        counts.append(badge(f"{nor} SPI NOR", "info"))
    if nand:
        counts.append(badge(f"{nand} SPI NAND", "info"))
    out.append(" ".join(counts) + "\n")
    if ids:
        parts = []
        for m, _ in ids.most_common():
            jep = db.jep106(m)
            text = f"{{sfid}}`{m:02x}`"
            if jep and jep.lower() != vendor.lower():
                text += f" (JEP106 lists this byte as {esc(jep)})"
            parts.append(text)
        noun = "byte" if len(parts) == 1 else "bytes"
        out.append(f"Manufacturer id {noun}: {', '.join(parts)}.\n")
    sizes = sorted({f.size for f in flashes if f.size})
    if sizes:
        out.append(f"Capacities from {human_size(sizes[0])} to {human_size(sizes[-1])}.\n")
    out.append("Type in the box to filter; click a heading to sort.\n")
    out.append(parts_table(flashes, slugs, "../chips/"))
    out.append(
        "\n```{toctree}\n:hidden:\n\n"
        + "\n".join(f"../chips/{slugs[id(f)]}" for f in flashes)
        + "\n```\n"
    )
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
            (
                f"Every one of the {len(flashes)} chip ids in the database. "
                "Type in the box to filter (by id, part, vendor or size); "
                "click a heading to sort.\n"
            ),
            parts_table(flashes, slugs, "", with_vendor=True),
        ]
    )


def opcodes_table(flashes: list[Flash]) -> str:
    uses = Counter(name for f in flashes for name in f.opcodes)
    out = []
    for kind in KINDS:
        rows = [
            [
                f"{{sfop}}`0x{op.opcode:02x}`",
                f"`{op.name}`",
                esc(op.description),
                str(uses.get(op.name, 0)),
            ]
            for op in OPERATIONS.values()
            if op.kind == kind
        ]
        out.append(f"### {_KIND_TITLE[kind]}\n")
        out.append(
            list_table(
                ["Opcode", "Operation", "Description", "Chips"], rows, "sf-table", "10 25 50 15"
            )
        )
        out.append("")
    return "\n".join(out)


def sources_table(db: Database) -> str:
    rows = []
    for name, s in sorted(db.sources.items()):
        base = s.get("browse") or s["url"]
        commit = (
            f"[`{s['commit'][:12]}`]({base.rstrip('/')}/commit/{s['commit']})"
            if base.startswith("https://github.com/")
            else f"`{s['commit'][:12]}`"
        )
        label = "JEP106 (OpenOCD)" if name == "jep106" else SOURCE_LABEL.get(name, name)
        rows.append(
            [f"[{label}]({base})", commit, s["date"][:10], f"{s['records']:,}", esc(s["license"])]
        )
    return list_table(["Source", "Commit", "Date", "Entries", "Licence"], rows, "sf-table")


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


def generate(srcdir: Path) -> None:
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

    chips_dir, vendors_dir = srcdir / "chips", srcdir / "vendors"
    wanted: set[Path] = set()

    def page(path: Path, text: str) -> None:
        _write(path, text)
        wanted.add(path)

    for v, fl in vendors.items():
        page(vendors_dir / f"{vslug[v]}.md", vendor_page(db, v, fl, slugs))
        for f in fl:
            page(chips_dir / f"{slugs[id(f)]}.md", chip_page(db, f, vslug[v]))
    page(vendors_dir / "index.md", vendors_index(vendors, vslug))
    page(chips_dir / "index.md", chips_index(list(db.flashes), slugs))
    # Fragments the hand-written pages include (docs/_generated is excluded
    # from the build as pages of its own).
    _write(srcdir / "_generated" / "opcodes-table.md", opcodes_table(list(db.flashes)))
    _write(srcdir / "_generated" / "sources-table.md", sources_table(db))
    # A chip id that left the database leaves no stale page behind.
    for d in (chips_dir, vendors_dir):
        for old in d.glob("*.md"):
            if old not in wanted:
                old.unlink()


class SpanRole(SphinxRole):
    """``{role}`text``` as ``<span class="css">text</span>``."""

    def __init__(self, css: str) -> None:
        super().__init__()
        self.css = css

    def run(self) -> tuple[list[nodes.Node], list[nodes.system_message]]:
        return [nodes.inline(self.rawtext, self.text, classes=[self.css])], []


class SourceRole(SphinxRole):
    """``{sfsrc}`linux``` as a coloured label naming the source."""

    def run(self) -> tuple[list[nodes.Node], list[nodes.system_message]]:
        label = SOURCE_LABEL.get(self.text, self.text)
        classes = ["sf-src", f"sf-src-{slug(self.text)}"]
        return [nodes.inline(self.rawtext, label, classes=classes)], []


def _substitutions(app: Sphinx, config: Config) -> None:
    """The numbers the home page quotes ({{chips}}, {{vendors}}, ...)."""
    numbers = {k: f"{v:,}" for k, v in stats(spiflash.database()).items()}
    config.myst_substitutions = {**numbers, "version": app.config.release}


def setup(app: Sphinx) -> dict[str, Any]:
    app.connect("config-inited", _substitutions)
    app.connect("builder-inited", lambda app: generate(Path(app.srcdir)))
    app.add_role("sfid", SpanRole("sf-id"))
    app.add_role("sfop", SpanRole("sf-op"))
    app.add_role("sfyes", SpanRole("sf-yes"))
    app.add_role("sfkind", SpanRole("sf-kind"))
    app.add_role("sfsrc", SourceRole())
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}
