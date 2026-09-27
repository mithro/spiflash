"""Check that every URL in the built site is a link.

    uv run docs/check_links.py [docs/_build/html]

Walks the HTML Sphinx wrote and reports any ``http(s)://`` text that sits
outside an ``<a>``: a URL the reader can see but not click. Code (``<pre>``,
``<code>``) is exempt, since a shell command's URL is meant to be copied.
Exits 1 if it finds any.
"""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path

# What counts as a URL is the page generator's own definition.
sys.path.insert(0, str(Path(__file__).resolve().parent / "_ext"))

from spiflash_pages import URL, trim_url

# Text inside these is not prose a reader would expect to click.
EXEMPT = {"a", "pre", "code", "script", "style", "title", "textarea"}


class _Finder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open: list[str] = []
        self.bare: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"br", "img", "meta", "link", "input", "hr", "wbr", "source"}:
            self.open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.open:
            while self.open and self.open.pop() != tag:
                pass

    def handle_data(self, data: str) -> None:
        if EXEMPT.isdisjoint(self.open):
            self.bare.extend(trim_url(url) for url in URL.findall(data))


def bare_urls(html: str) -> list[str]:
    """The URLs in ``html`` that are shown as text but are not links."""
    finder = _Finder()
    finder.feed(html)
    return finder.bare


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "docs/_build/html")
    pages = sorted(root.rglob("*.html"))
    if not pages:
        print(f"no HTML under {root}: build the site first", file=sys.stderr)
        return 2
    found = 0
    for page in pages:
        for url in bare_urls(page.read_text(encoding="utf-8")):
            found += 1
            print(f"{page.relative_to(root)}: {url}")
    print(f"{len(pages)} pages, {found} URLs that are not links", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
