"""Check that every URL in the built site is a link.

    uv run docs/check_links.py [docs/_build/html]

Walks the HTML Sphinx wrote and reports anything a reader would expect to
click that is not a link: ``http(s)://`` URLs, file names and paths, and
GitHub ``owner/repo`` names, whether in running text or inline code. Code
blocks (``<pre>``) are exempt: a shell command is meant to be copied.
Exits 1 if it finds any.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# What counts as a URL is the page generator's own definition.
sys.path.insert(0, str(Path(__file__).resolve().parent / "_ext"))

from spiflash_pages import URL, trim_url

# Text inside these is exempt: it is already a link, or it is code or markup
# meant to be copied, not followed.
EXEMPT = {"a", "pre", "script", "style", "title", "textarea"}

# File names: something.ext with an extension a file here or upstream has.
_EXT = r"(?:py|c|h|hpp|cpp|inc|toml|md|yml|yaml|json|sh|js|css|txt|html|rst|lock|svg|cfg)"
FILE_NAME = re.compile(rf"^[\w.*-]+\.{_EXT}$")
# Paths in inline code: a/b/c, a/b/ (a directory), .github/...; a plain a/b
# is a path only with an extension, since "w25q128fv/jv" is a part name.
PATH = re.compile(r"^(?:\.?[\w.*-]+/[\w.*-]+/[\w./*-]*|[\w.*-]+/[\w.*-]*/|[\w.*-]+/[\w*-]+\.\w+)$")
# GitHub repositories (owner/repo) in inline code, for the owners these docs
# mention.
REPO = re.compile(
    r"^(?:mithro|litex-hub|torvalds|u-boot|flashrom|SourceArcade|openocd-org|trabucayre)"
    r"/[\w.-]+$"
)
# A file path in running text.
PROSE_PATH = re.compile(rf"(?<![\w/.:-])(?:[\w.-]+/)+[\w.*-]+\.{_EXT}\b")


class _Finder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open: list[str] = []
        self.code: list[str] = []
        self.bare: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"br", "img", "meta", "link", "input", "hr", "wbr", "source"}:
            self.open.append(tag)
        if tag == "code":
            self.code = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "code" and EXEMPT.isdisjoint(self.open):
            literal = "".join(self.code).strip()
            if FILE_NAME.match(literal) or PATH.match(literal) or REPO.match(literal):
                self.bare.append(literal)
        if tag in self.open:
            while self.open and self.open.pop() != tag:
                pass

    def handle_data(self, data: str) -> None:
        if not EXEMPT.isdisjoint(self.open):
            return
        if "code" in self.open:
            self.code.append(data)
            return
        self.bare.extend(trim_url(url) for url in URL.findall(data))
        self.bare.extend(PROSE_PATH.findall(data))


def bare_urls(html: str) -> list[str]:
    """What ``html`` shows that should be a link and is not: URLs, and file
    names, paths and ``owner/repo`` names, in text or inline code."""
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
    print(f"{len(pages)} pages, {found} things that should be links and are not", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
