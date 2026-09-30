"""The site's list-table, which parses each distinct cell once: it reads the
tables the pages write, and builds what list-table builds. Each test site
has the same pages in ``gen/``, where cells are shared, and in ``plain/``,
where list-table is left to itself, and compares the two."""

from __future__ import annotations

import pickle
import re
from typing import TYPE_CHECKING

from docutils import nodes
from sphinx import addnodes

from list_tables import split_rows
from page_markup import list_table

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    Build = Callable[..., tuple[Path, Path, str]]

CONF = """
extensions = ["myst_parser", "list_tables"]
list_tables_cached = ["gen"]
myst_enable_extensions = ["colon_fence", "substitution", "attrs_inline"]
source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
"""
ORPHAN = "---\norphan: true\n---\n"
ARTICLE = re.compile(r'<div class="body" role="main">.*?<div class="sphinxsidebar"', re.DOTALL)


def test_split_rows_reads_list_table() -> None:
    text = list_table(["A", "B"], [["one", " "], ["two\n\nmore", "{sfnum}`1`"]], "sf-table")
    body = text.splitlines()[4:-1]  # the directive's content, less its options
    assert split_rows(body) == [
        [(0, ["A"]), (1, ["B"])],
        [(2, ["one"]), (3, [" "])],
        [(4, ["two", "", "more"]), (7, ["{sfnum}`1`"])],
    ]


def test_split_rows_leaves_other_layouts() -> None:
    assert split_rows(["- a", "- b"]) is None
    assert split_rows(["* - a", "  - b", "* - c"]) is None  # uneven rows
    assert split_rows([]) is None


def site(pages: dict[str, str]) -> dict[str, str]:
    """``pages`` in both directories, and the configuration."""
    files = {"conf.py": CONF, "index.md": "# Index\n"}
    for name, text in pages.items():
        files[f"gen/{name}"] = files[f"plain/{name}"] = text
    return files


def article(out: Path, page: str) -> str:
    match = ARTICLE.search((out / page).read_text(encoding="utf-8"))
    assert match
    return match.group(0)


def tables(doctrees: Path, docname: str) -> list[nodes.table]:
    doc = pickle.loads((doctrees / f"{docname}.doctree").read_bytes())
    return list(doc.findall(nodes.table))


def shape(table: nodes.table) -> list[tuple[str, int | None, str]]:
    """Each node of a table, with its line and text."""
    return [(n.tagname, n.line, n.astext()) for n in table.findall(nodes.Element)]


CELLS = [
    ["**same**", "[B](b.md)", " "],
    ["`code`", "[example](https://example.com)", "one\n\ntwo"],
    ["**same**", "[B](b.md)", "{index}`widget`"],
    ["{index}`widget`", "x", "y"],
]
#: A fence, which the cells are parsed alone for, and an HTML block that runs
#: past its cell, which they are parsed again alone for.
FENCE = [["```", "open", "fence"]]
HTML = [["<pre>", "runs", "on"]]


def test_same_as_list_table(build_site: Build) -> None:
    """The cells shared, copied into another page and repeated in a page,
    ids, a fence, a cell that ends the one-pass parse early: all as
    list-table has them, lines included."""
    page = (
        ORPHAN
        + "# Page\n\n"
        + "\n\n".join(list_table(["A", "B", "C"], rows) for rows in (CELLS, FENCE, HTML))
        + "\n"
    )
    out, doctrees, warnings = build_site(site({"a.md": page, "b.md": page}))
    assert not warnings
    for name in ("a", "b"):
        assert article(out, f"gen/{name}.html") == article(out, f"plain/{name}.html")
        gen, plain = tables(doctrees, f"gen/{name}"), tables(doctrees, f"plain/{name}")
        assert [shape(t) for t in gen] == [shape(t) for t in plain]
    # A copied link is from its own page.
    doc = pickle.loads((doctrees / "gen/b.doctree").read_bytes())
    assert {x["refdoc"] for x in doc.findall(addnodes.pending_xref)} == {"gen/b"}
    assert (doctrees / "gen/b.doctree").exists()
    assert article(out, "gen/b.html").count('id="index-') == 2


def test_substitutions_are_the_pages_own(build_site: Build) -> None:
    def page(who: str) -> str:
        front = f"---\norphan: true\nmyst:\n  substitutions:\n    who: {who}\n---\n"
        return front + "# Page\n\n" + list_table(["Who"], [["{{ who }}"]]) + "\n"

    out, _, warnings = build_site(site({"a.md": page("Alice"), "b.md": page("Bob")}))
    assert not warnings
    assert "Bob" in article(out, "gen/b.html")
    assert "Alice" not in article(out, "gen/b.html")


def test_link_definitions_are_the_pages_own(build_site: Build) -> None:
    a = list_table(["A"], [["[x]"], ["[x]: https://example.com/leak"]])
    b = list_table(["B"], [["[x]"]])
    pages = {"a.md": ORPHAN + "# A\n\n" + a + "\n", "b.md": ORPHAN + "# B\n\n" + b + "\n"}
    out, _, _ = build_site(site(pages))
    for name in ("a", "b"):
        assert article(out, f"gen/{name}.html") == article(out, f"plain/{name}.html")
    assert "example.com/leak" not in article(out, "gen/b.html")


def test_restructuredtext_is_left_to_list_table(build_site: Build) -> None:
    table = ".. list-table::\n   :header-rows: 1\n\n   * - A\n   * - `code`\n"
    rst = ":orphan:\n\nPage\n====\n\n" + table
    out, _, warnings = build_site(site({"c.rst": rst}))
    assert not warnings
    assert "<cite>code</cite>" in article(out, "gen/c.html")


def test_header_only_table_is_an_error(build_site: Build) -> None:
    page = ORPHAN + "# Page\n\n" + list_table(["A", "B"], []) + "\n"
    _, _, warnings = build_site(site({"a.md": page}))
    assert warnings.count("no data remaining for table body") == 2  # gen/ and plain/
