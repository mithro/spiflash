"""Generate the site's vendor and chip pages from the spiflash database.

At ``builder-inited`` this writes MyST pages into ``docs/vendors/`` and
``docs/chips/`` (both git-ignored): one page per vendor, with a table of all
its parts, one page per chip id, and the index pages linking them. A page is
only rewritten when its text changes, so incremental builds stay fast.

It also writes a page per SPI operation into ``docs/opcodes/``
(:mod:`opcode_pages`), with its WaveDrom timing diagram.

The pages are Markdown, not raw HTML, so Sphinx's search indexes every part
name and id.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from docutils import nodes
from sphinx.util.docutils import SphinxRole
from sphinx.util.nodes import split_explicit_title

import spiflash
from opcode_pages import generate_all as operation_pages
from page_markup import (
    EM_DASH,
    FEATURE_TEXT,
    HIGHLIGHTS,
    KIND_TITLE,
    TIMES,
    badge,
    chip_slug,
    esc,
    feature_badges,
    list_table,
    size_text,
    slug,
    source_badge,
    source_label,
    spaced,
    title_of,
    vendor_of,
    volts,
)
from spiflash.cli import human_size
from spiflash.enums import OperationKind
from spiflash.opcodes import OPERATIONS

if TYPE_CHECKING:
    from sphinx.application import Sphinx
    from sphinx.config import Config

    from spiflash import Database, Flash


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
            FEATURE_TEXT[feat][0],
            " ".join(source_badge(s) for s in f.feature_sources(feat)),
        ]
        for feat in FEATURE_TEXT
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
            f"[`{o.name}`](../opcodes/{o.name}.md)",
            f"{{sfkind}}`{o.operation.kind}` {esc(o.operation.description)}",
        ]
        + ["{sfyes}`✓`" if s in o.sources else " " for s in srcs]
        for o in f.opcodes.values()
    ]
    out.append(
        list_table(
            ["Opcode", "Operation", "Description", *[source_label(s) for s in srcs]],
            rows,
            "sf-table sf-opcodes",
        )
    )
    out.append("")
    out.append(":::{dropdown} Why each source lists each opcode\n:class-container: sf-why\n")
    for o in f.opcodes.values():
        reasons = "; ".join(f"{source_label(s)}: {esc(via)}" for s, via in o.because)
        out.append(f"- [`{o.name}`](../opcodes/{o.name}.md) (0x{o.opcode:02x}): {reasons}")
    out.append(":::\n")
    return out


def _erase_layouts(f: Flash) -> list[str]:
    rows = []
    for r in f.records:
        for e in r.erasers:
            blocks = ", ".join(f"{b.count} {TIMES} {human_size(b.size)}" for b in e.blocks)
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
            f"{_conflict_value(attr, v)} ({', '.join(source_label(s) for s in ss)})"
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
            out.append(f"- {source_label(r.source)} ({esc(r.name)}): {esc(n)}")
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
            feature_badges(f.features & HIGHLIGHTS),
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
    for kind in OperationKind:
        rows = [
            [
                f"{{sfop}}`0x{op.opcode:02x}`",
                f"[`{op.name}`](opcodes/{op.name}.md)",
                esc(op.description),
                str(uses.get(op.name, 0)),
            ]
            for op in OPERATIONS.values()
            if op.kind == kind
        ]
        out.append(f"### {KIND_TITLE[kind]}\n")
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
        base = s.browse
        commit = (
            f"[`{s.commit[:12]}`]({base.rstrip('/')}/commit/{s.commit})"
            if base.startswith("https://github.com/")
            else f"`{s.commit[:12]}`"
        )
        label = "JEP106 (OpenOCD)" if name == "jep106" else source_label(name)
        rows.append(
            [f"[{label}]({base})", commit, f"{s.date:%Y-%m-%d}", f"{s.records:,}", esc(s.license)]
        )
    return list_table(["Source", "Commit", "Date", "Entries", "Licence"], rows, "sf-table")


def files_read_table(db: Database) -> str:
    """Each upstream's files, linked at the pinned commit, with its licence."""
    rows = []
    for name, s in sorted(db.sources.items()):
        if name == "jep106":
            continue  # the same OpenOCD checkout; its file is in OpenOCD's row
        files = ", ".join(f"{{upstream}}`{name}:{path}`" for path in s.paths)
        rows.append([source_label(name), files, esc(s.license)])
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

    chips_dir, vendors_dir, ops_dir = srcdir / "chips", srcdir / "vendors", srcdir / "opcodes"
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
    for name, text in operation_pages(db).items():
        page(ops_dir / name, text)
    # Fragments the hand-written pages include (docs/_generated is excluded
    # from the build as pages of its own).
    _write(srcdir / "_generated" / "opcodes-table.md", opcodes_table(list(db.flashes)))
    _write(srcdir / "_generated" / "sources-table.md", sources_table(db))
    _write(srcdir / "_generated" / "files-read.md", files_read_table(db))
    # A chip id that left the database leaves no stale page behind.
    for d in (chips_dir, vendors_dir, ops_dir):
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
        label = source_label(self.text)
        classes = ["sf-src", f"sf-src-{slug(self.text)}"]
        return [nodes.inline(self.rawtext, label, classes=classes)], []


REPO_URL = "https://github.com/mithro/spiflash"


def _is_dir(path: str) -> bool:
    return path.endswith("/") or "." not in path.rsplit("/", 1)[-1]


def repo_url(path: str) -> str:
    """This repository's page for ``path`` (a file or a directory) on main."""
    kind = "tree" if _is_dir(path) else "blob"
    return f"{REPO_URL}/{kind}/main/{path.rstrip('/')}"


def upstream_url(source: str, path: str) -> str:
    """An upstream's page for ``path`` at the commit the data came from. A
    glob (``drivers/mtd/spi-nor/*.c``) links to its directory."""
    info = spiflash.sources()[source]
    base = info.browse.rstrip("/")
    if "*" in path:
        path = path.rsplit("/", 1)[0] + "/"
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
    app.connect("builder-inited", lambda app: generate(Path(app.srcdir)))
    app.add_role("sfid", SpanRole("sf-id"))
    app.add_role("sfop", SpanRole("sf-op"))
    app.add_role("sfyes", SpanRole("sf-yes"))
    app.add_role("sfkind", SpanRole("sf-kind"))
    app.add_role("sfsrc", SourceRole())
    app.add_role("repo", RepoRole())
    app.add_role("upstream", UpstreamRole())
    app.add_role("github", GithubRole())
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}
