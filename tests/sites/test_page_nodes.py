"""The source labels and numbers the pages' roles make: one node each, whose
HTML is what the spans they replaced made (as on main, but for a label in a
link, which is no longer a link in a link)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from page_markup import EM_DASH, EN_DASH, TIMES, UP_ARROW

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

CONF = """
from spiflash_pages import (
    Number, NumberRole, SourceBadge, SourceRole, visit_number, visit_source_badge
)

extensions = ["myst_parser"]


def setup(app):
    app.add_role("sfsrc", SourceRole())
    app.add_role("sfsrcme", SourceRole(mine=True))
    app.add_role("sfnum", NumberRole())
    app.add_node(SourceBadge, html=(visit_source_badge, None))
    app.add_node(Number, html=(visit_number, None))
"""


def link(source: str, label: str, classes: str = "", lines: str = "") -> str:
    """A source's label, linked to its page from ``chips/``."""
    inner = lines or f"<span>{label}</span>"
    return (
        f'<a class="sf-src-link reference external" href="../sources/{source}.html" '
        f'title="About {label}"><span class="sf-src sf-src-{source}{classes}">{inner}</span></a>'
    )


def number(kind: str, *parts: tuple[str, str]) -> str:
    spans = "".join(f'<span class="sf-n-{css}">{text}</span>' for css, text in parts)
    return f'<span class="sf-num sf-num-{kind}">{spans}</span>'


CASES = [
    ("{sfsrc}`linux`", link("linux", "Linux")),
    (
        "{sfsrc}`openfpgaloader`",
        link(
            "openfpgaloader",
            "openFPGALoader",
            " sf-src-split",
            "<span>openFPGA</span><span>Loader</span>",
        ),
    ),
    ("{sfsrcme}`qemu`", link("qemu", "QEMU", " sf-src-mine")),
    ("{sfsrc}`other`", '<span class="sf-src sf-src-other"><span>other</span></span>'),
    (
        "{sfsrc}`linux " + TIMES + "3 <https://example.com/x>`",
        link("linux", "Linux", "", "<span>Linux " + TIMES + "3</span>")
        + '<a class="sf-src-up reference external" href="https://example.com/x" '
        + 'title="Open in Linux">'
        + UP_ARROW
        + "</a>",
    ),
    (
        "[{sfsrc}`linux`](https://example.com/)",
        (
            '<a class="reference external" href="https://example.com/">'
            '<span class="sf-src sf-src-linux"><span>Linux</span></span></a>'
        ),
    ),
    ("{sfnum}`16 MiB`", number("size", ("int", "16"), ("unit", " MiB"))),
    (
        "{sfnum}`2.7" + EN_DASH + "3.6 V`",
        number(
            "volt",
            ("int", "2"),
            ("frac", ".7"),
            ("sep", EN_DASH),
            ("int", "3"),
            ("frac", ".6"),
            ("unit", " V"),
        ),
    ),
    ("{sfnum}`3 V`", number("volt", ("int", "3"), ("frac", ""), ("unit", " V"))),
    ("{sfnum}`" + EM_DASH + " V`", number("volt", ("int", EM_DASH), ("frac", ""), ("unit", ""))),
    (
        "{sfnum}`1,024 " + TIMES + " 4 KiB`",
        number("size", ("count", "1,024"), ("times", f" {TIMES} "), ("int", "4"), ("unit", " KiB")),
    ),
    # A duration's fraction is kept, not cut off: 2.24 s, not 2 s.
    ("{sfnum}`2.24 s`", number("size", ("int", "2"), ("frac", ".24"), ("unit", " s"))),
    ("{sfnum}`1,234`", number("plain", ("int", "1,234"))),
    ("{sfnum}`n/a`", '<span class="sf-num">n/a</span>'),
]


@pytest.mark.parametrize(("markup", "html"), CASES)
def test_html(build_site: Callable[..., tuple[Path, Path, str]], markup: str, html: str) -> None:
    page = f"---\norphan: true\n---\n# Page\n\nBefore {markup} after.\n"
    out, _, warnings = build_site({"conf.py": CONF, "index.md": "# Index\n", "chips/x.md": page})
    assert not warnings
    assert f"<p>Before {html} after.</p>" in (out / "chips/x.html").read_text(encoding="utf-8")


def test_label_on_its_own_page(build_site: Callable[..., tuple[Path, Path, str]]) -> None:
    page = "---\norphan: true\n---\n# Page\n\n{sfsrc}`linux`\n"
    out, _, _ = build_site({"conf.py": CONF, "index.md": "# Index\n", "sources/linux.md": page})
    html = (out / "sources/linux.html").read_text(encoding="utf-8")
    assert 'href="#" title="About Linux"' in html
