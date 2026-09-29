"""A page for every part name: ``chips/S25FL016A.html`` sends the reader to
the chip that part answers as, ``chips/010214.html``.

Chip pages are named by id, which is what a probe reads, but people look a
chip up by its part number. For each name in :attr:`spiflash.Flash.names`
(upper case) this writes ``chips/<NAME>.html``:

- a redirect, when the name is on one chip's page only;
- a disambiguation page, when it is on several: flashrom and Linux can list
  the same part under different ids (a second JEDEC id, or a legacy id the
  part also answers), and sending the reader to one of them would hide the
  others. It lists each chip, its id, manufacturer, and which sources list
  the name under it, and links to each. The data issues pages explain the
  disagreement ("One part, several ids").

Names are skipped, never mangled into something else:

- flashrom's wildcards (``W25Q128.V``): the ``.`` stands for any character,
  so the name is a family, not a part, and would read as a file extension;
- anything else outside ``A-Z 0-9 _ -`` (a ``/``, a space, ``+``...): it
  would need escaping in a URL, and a slugified name is not what anyone
  types; :func:`spiflash.model.part_names` already splits ``/`` shorthand
  into its parts, so none of these occur today;
- a name that is already a page in ``chips/`` (a chip id, ``index``),
  compared case-insensitively so a case-insensitive file system cannot
  overwrite a real page either.

The pages are raw HTML written into the output directory at
``build-finished``, not Markdown sources, because they are pointers, not
content: the chip pages already carry every name and are what the search
should find, so an alias page in the toctree and the search index would only
be noise, and it would need a place in :mod:`spiflash_pages`'s generated
tree. Written after the build, they leave its pages and its page-name checks
as they are, and every real page in ``chips/`` wins over a part name. A redirect
is a ``<meta http-equiv="refresh">``, a ``location.replace`` (which keeps
any ``#anchor``) and a plain link, all relative, so it works on Read the
Docs under ``/en/latest/``, from ``file://`` and from a local server, with
no server configuration; ``<link rel="canonical">`` and ``noindex`` keep
search engines on the chip page.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sphinx.builders.html import StandaloneHTMLBuilder
from sphinx.util import logging

import spiflash
from page_markup import chip_slug, source_label, spaced, title_of, vendor_of

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sphinx.application import Sphinx

    from spiflash import Flash

logger = logging.getLogger(__name__)

# What a part name may be to become a file name as it is.
SAFE_NAME = re.compile(r"[A-Z0-9][A-Z0-9_-]*")
# Marks the pages this module writes, so a later build removes only those.
GENERATOR = "spiflash alias_pages"


@dataclass(frozen=True)
class Aliases:
    """Which chips each usable part name is on, and why the others were
    left out."""

    #: Part name to the chips listing it, in database order.
    names: dict[str, tuple[Flash, ...]]
    #: Part name to why it has no page: ``"wildcard"``, ``"unsafe"`` or
    #: ``"taken"`` (a real page has that name).
    skipped: dict[str, str]


def aliases(flashes: Iterable[Flash], taken: Iterable[str]) -> Aliases:
    """The part names of ``flashes`` that can have a page next to the pages
    named ``taken`` (chip slugs, ``index``)."""
    reserved = {t.casefold() for t in taken}
    chips: dict[str, list[Flash]] = defaultdict(list)
    for f in flashes:
        for name in f.names:
            chips[name].append(f)
    names: dict[str, tuple[Flash, ...]] = {}
    skipped: dict[str, str] = {}
    for name in sorted(chips):
        if "." in name:
            skipped[name] = "wildcard"
        elif not SAFE_NAME.fullmatch(name):
            skipped[name] = "unsafe"
        elif name.casefold() in reserved:
            skipped[name] = "taken"
        else:
            names[name] = tuple(chips[name])
    return Aliases(names, skipped)


def _head(title: str) -> list[str]:
    return [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f'<meta name="generator" content="{GENERATOR}">',
        '<meta name="robots" content="noindex">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        '<link rel="icon" href="../_static/logo.svg">',
        f"<title>{escape(title)} - spiflash</title>",
    ]


def redirect_page(name: str, target: str) -> str:
    """A page that sends the reader from part ``name`` to ``target``, a URL
    relative to the page."""
    url = escape(target)
    return "\n".join(
        [
            *_head(name),
            f'<link rel="canonical" href="{url}">',
            f'<meta http-equiv="refresh" content="0; url={url}">',
            f"<script>location.replace({json.dumps(target)} + location.hash);</script>",
            "</head>",
            "<body>",
            f'<p>{escape(name)} is described on <a href="{url}">its chip page</a>.</p>',
            "</body>",
            "</html>",
            "",
        ]
    )


def _id_text(f: Flash) -> str:
    if f.family == "jedec":
        return spaced(f.jedec_id)
    return f"{spaced(f.id_hex)} ({f.family.upper()})"


def _listed_by(f: Flash, name: str) -> str:
    sources = {r.source for r in f.records if name in r.part_names}
    return ", ".join(source_label(s) for s in f.sources if s in sources)


# The disambiguation page stands alone, outside the theme.
STYLE = (
    "body { font-family: system-ui, sans-serif; max-width: 48rem; margin: 2rem auto;"
    " padding: 0 1rem; line-height: 1.5; }"
    " table { border-collapse: collapse; }"
    " th, td { text-align: left; padding: 0.3rem 0.8rem 0.3rem 0;"
    " border-bottom: 1px solid #8884; }"
)
CHOICE_TEXT = (
    "under more than one id: a second JEDEC id, or a legacy id older parts answer."
    " Its datasheet, or the id read from the chip, says which applies."
)
CHOICE_HEADER = ("Id", "Chip", "Manufacturer", "Type", "Listed as this part by")


def choice_page(name: str, chips: Iterable[Flash], suffix: str = ".html") -> str:
    """A page for part ``name`` listed under several ``chips``, linking each."""
    rows = []
    for f in chips:
        url = escape(chip_slug(f) + suffix)
        kind = "SPI NAND" if f.type == "nand" else "SPI NOR"
        rows.append(
            "<tr>"
            f'<td><a href="{url}"><code>{escape(_id_text(f))}</code></a></td>'
            f'<td><a href="{url}">{escape(title_of(f))}</a></td>'
            f"<td>{escape(vendor_of(f))}</td>"
            f"<td>{kind}</td>"
            f"<td>{escape(_listed_by(f, name))}</td>"
            "</tr>"
        )
    return "\n".join(
        [
            *_head(name),
            f"<style>{STYLE}</style>",
            "</head>",
            "<body>",
            f"<h1>{escape(name)}</h1>",
            f"<p>The sources list {escape(name)} {CHOICE_TEXT}</p>",
            "<table>",
            f"<thead><tr>{''.join(f'<th>{h}</th>' for h in CHOICE_HEADER)}</tr></thead>",
            "<tbody>",
            *rows,
            "</tbody>",
            "</table>",
            f'<p><a href="index{suffix}">All chips</a></p>',
            "</body>",
            "</html>",
            "",
        ]
    )


def generate_all(found: Aliases, suffix: str = ".html") -> dict[str, str]:
    """The page for each name in ``found``, by file name in ``chips/``."""
    pages = {}
    for name, chips in found.names.items():
        if len(chips) == 1:
            pages[name + suffix] = redirect_page(name, chip_slug(chips[0]) + suffix)
        else:
            pages[name + suffix] = choice_page(name, chips, suffix)
    return pages


def write(chips_dir: Path, pages: dict[str, str]) -> None:
    """Write ``pages`` into ``chips_dir``, and remove the alias pages an
    earlier build wrote that are no longer wanted. A file this module did
    not write is never touched."""
    marker = f'content="{GENERATOR}"'
    for old in chips_dir.glob("*.html"):
        if old.name not in pages and marker in old.read_text(encoding="utf-8"):
            old.unlink()
    for file, text in pages.items():
        path = chips_dir / file
        if path.exists() and marker not in path.read_text(encoding="utf-8"):
            msg = f"{path} is a real page, not an alias"
            raise FileExistsError(msg)
        path.write_text(text, encoding="utf-8")


def _build_finished(app: Sphinx, exception: Exception | None) -> None:
    # Only the plain HTML builder names pages chips/<slug>.html (dirhtml,
    # its subclass, writes chips/<slug>/index.html).
    builder = app.builder
    if exception or not isinstance(builder, StandaloneHTMLBuilder) or builder.name != "html":
        return
    db = spiflash.database()
    taken = {d.removeprefix("chips/") for d in app.env.found_docs if d.startswith("chips/")}
    taken |= {chip_slug(f) for f in db.flashes}
    found = aliases(db.flashes, taken)
    pages = generate_all(found, builder.out_suffix)
    write(Path(app.outdir) / "chips", pages)
    choices = sum(len(chips) > 1 for chips in found.names.values())
    why = Counter(found.skipped.values())
    logger.info(
        "alias pages: %d redirects, %d disambiguation pages, %d names skipped (%s)",
        len(pages) - choices,
        choices,
        len(found.skipped),
        ", ".join(f"{n} {r}" for r, n in sorted(why.items())) or "none",
    )


def setup(app: Sphinx) -> dict[str, Any]:
    app.connect("build-finished", _build_finished)
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}
