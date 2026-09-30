"""A chip page's sidebar is its vendor page's, with the vendor current and
the links made relative to the chip page; and every chip page, in no
toctree, is linked from its vendor's page."""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from typing import TYPE_CHECKING

import pytest

import spiflash
from page_markup import chip_slug, vendor_of
from spiflash_pages import chip_page, vendor_page

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from spiflash import Flash

CONF = """
import spiflash_pages

extensions = ["myst_parser"]
html_theme = "furo"


def setup(app):
    app.connect(
        "builder-inited", lambda app: spiflash_pages.set_chip_vendors({"chips/c1": "vendors/acme"})
    )
    app.connect("html-page-context", spiflash_pages.chip_nav, priority=400)
"""
TOCTREE = "```{{toctree}}\n:hidden:\n\n{}\n```\n"
FILES = {
    "conf.py": CONF,
    "index.md": "# Home\n\n" + TOCTREE.format("vendors/index"),
    "vendors/index.md": "# Vendors\n\n" + TOCTREE.format("acme\nzeta"),
    "vendors/acme.md": "# Acme\n\n[C1](../chips/c1.md)\n",
    "vendors/zeta.md": "# Zeta\n",
    "chips/c1.md": "---\norphan: true\n---\n# C1\n",
}
SIDEBAR = re.compile(r'<div class="sidebar-tree">.*?</div>', re.DOTALL)
CURRENT = re.compile(r'<li class="[^"]*current-page[^"]*"><a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>')


@pytest.mark.parametrize(
    ("builder", "chip", "vendor", "vendor_href"),
    [
        ("html", "chips/c1.html", "vendors/acme.html", "../vendors/acme.html"),
        ("dirhtml", "chips/c1/index.html", "vendors/acme/index.html", "../../vendors/acme/"),
    ],
)
def test_chip_sidebar(
    build_site: Callable[..., tuple[Path, Path, str]],
    builder: str,
    chip: str,
    vendor: str,
    vendor_href: str,
) -> None:
    out, _, warnings = build_site(FILES, builder)
    assert not warnings
    for page, href in ((chip, vendor_href), (vendor, "#")):
        match = SIDEBAR.search((out / page).read_text(encoding="utf-8"))
        assert match, page
        sidebar = match.group(0)
        assert CURRENT.findall(sidebar) == [(href, "Acme")], page
        # Every link leads to a page.
        for link in re.findall(r'href="([^"#]+)', sidebar):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(page), link))
            path = out / target
            assert (path / "index.html" if path.is_dir() else path).exists(), (page, link)
        assert "Zeta" in sidebar


def test_every_chip_is_on_its_vendors_page() -> None:
    db = spiflash.database()
    slugs = {id(f): chip_slug(f) for f in db.flashes}
    vendors: dict[str, list[Flash]] = defaultdict(list)
    for f in db.flashes:
        vendors[vendor_of(f)].append(f)
    pages = {v: vendor_page(db, v, fl, slugs) for v, fl in vendors.items()}
    for f in db.flashes:
        assert f"](../chips/{slugs[id(f)]}.md)" in pages[vendor_of(f)]
    assert chip_page(db, db.flashes[0], "x", slugs, []).startswith("---\norphan: true\n---\n")
