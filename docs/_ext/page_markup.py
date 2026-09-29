"""The Markdown (MyST) building blocks the site's generated pages share:
escaping, links, badges, tables, and how a chip, a size or a voltage is
written."""

from __future__ import annotations

import re
from html import escape
from typing import TYPE_CHECKING

from spiflash.enums import Source
from spiflash.units import human_size

if TYPE_CHECKING:
    from spiflash import Flash


EN_DASH = "\N{EN DASH}"
EM_DASH = "\N{EM DASH}"
TIMES = "\N{MULTIPLICATION SIGN}"

#: JEDEC's pages for the standards the pages cite.
JESD216 = "https://www.jedec.org/standards-documents/docs/jesd216b"
JESD251 = "https://www.jedec.org/standards-documents/docs/jesd251"
JEP106 = "https://www.jedec.org/standards-documents/docs/jep-106ab"


def source_label(name: str) -> str:
    """A source's name as its project writes it (``u-boot`` is U-Boot)."""
    return Source(name).label if name in set(Source) else name


#: Labels too long for one line of a badge, and where they break.
SPLIT_LABELS = {"openfpgaloader": ("openFPGA", "Loader")}


def badge_lines(name: str, count: str = "") -> tuple[str, ...]:
    """A source's badge text, one or two lines, with an optional count
    (``"\u00d73"``) after it."""
    lines = SPLIT_LABELS.get(name, (source_label(name),))
    return (*lines[:-1], lines[-1] + (f" {count}" if count else ""))


def source_badge_html(name: str) -> str:
    """A source's badge as raw HTML, for pages Sphinx does not write."""
    lines = badge_lines(name)
    classes = f"sf-src sf-src-{escape(slug(name))}" + (" sf-src-split" if len(lines) > 1 else "")
    inner = "".join(f"<span>{escape(line)}</span>" for line in lines)
    return f'<span class="{classes}">{inner}</span>'


# What each feature means, and the badge colour for its group.
FEATURE_TEXT = {
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
HIGHLIGHTS = frozenset({"dual_read", "quad_read", "octal_read", "qpi", "4byte_addr", "sfdp"})

KIND_TITLE = {
    "id": "Identification",
    "read": "Read",
    "program": "Program",
    "erase": "Erase",
    "register": "Registers",
    "mode": "Modes",
}


# --- small helpers -----------------------------------------------------------


URL = re.compile(r"https?://[^\s<>]+")


def trim_url(url: str) -> str:
    """A URL matched by :data:`URL`, less what follows it in the prose:
    trailing punctuation, and closing brackets it did not open
    (``IS25LP(WP)256D.pdf`` keeps its brackets; ``(see https://x.org).``
    loses ``).``)."""
    url = url.rstrip(".,;:!?'\"")
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1].rstrip(".,;:!?'\"")
    return url


def _escape_markup(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>|#])", r"\\\1", text)


def esc(text: str) -> str:
    """Text safe to put in MyST: Markdown's inline markup escaped, and any
    URL in it made a link (an autolink, ``<https://...>``)."""
    out, pos = [], 0
    for m in URL.finditer(text):
        url = trim_url(m.group(0))
        out += [_escape_markup(text[pos : m.start()]), f"<{url}>"]
        pos = m.start() + len(url)
    out.append(_escape_markup(text[pos:]))
    return "".join(out)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def table_id(f: Flash, href: str) -> str:
    """A chip's id for a table cell, linked to ``href``: the id bytes, and
    for a chip in a later JEP106 bank a small "bank N" label rather than
    its continuation codes (the chip page gives the full id)."""
    link = f"[{spaced(f.id_hex)}]({href})"
    return link + (f" {{sfkind}}`bank {f.bank + 1}`" if f.bank else "")


def vendor_link(name: str, prefix: str = "../vendors/") -> str:
    """``name`` linked to its vendor page (``prefix`` is the way there)."""
    return f"[{esc(name)}]({prefix}{slug(name)}.md)"


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
    return " ".join(badge(*FEATURE_TEXT[x]) for x in FEATURE_TEXT if x in features)


def volts(v: tuple[int, int] | None) -> str:
    return f"{v[0] / 1000:g}{EN_DASH}{v[1] / 1000:g} V" if v else EM_DASH


def size_text(n: int | None) -> str:
    return human_size(n) if n else EM_DASH


def list_table(
    header: list[str], rows: list[list[str]], classes: str = "", widths: str | None = None
) -> str:
    """A MyST list-table. A cell may be several paragraphs (``"a\\n\\nb"``)."""
    lines = [":::{list-table}", ":header-rows: 1"]
    if classes:
        lines.append(f":class: {classes}")
    if widths:
        lines.append(f":widths: {widths}")
    lines.append("")
    for row in [header, *rows]:
        for i, cell in enumerate(row):
            text = cell.replace("\n", "\n    ") if cell.strip() else " "
            lines.append(("* - " if i == 0 else "  - ") + text)
    lines.append(":::")
    return "\n".join(lines)


def source_badge(source: str) -> str:
    return f"{{sfsrc}}`{source}`"


# --- pages -------------------------------------------------------------------
