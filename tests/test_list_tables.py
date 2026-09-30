"""How the site's list-table reads the tables the pages write, and how a chip
page's sidebar links are rebased from its vendor's page."""

from __future__ import annotations

from docutils import nodes

from list_tables import split_rows
from page_markup import list_table
from spiflash_pages import _rebase_links


def test_split_rows_reads_list_table() -> None:
    text = list_table(["A", "B"], [["one", " "], ["two\n\nmore", "{sfnum}`1`"]], "sf-table")
    body = [line for line in text.splitlines()[1:-1] if not line.startswith(":")]
    assert split_rows(body) == [
        [["A"], ["B"]],
        [["one"], [" "]],
        [["two", "", "more"], ["{sfnum}`1`"]],
    ]


def test_split_rows_leaves_other_layouts() -> None:
    assert split_rows(["- a", "- b"]) is None
    assert split_rows(["* - a", "  - b", "* - c"]) is None  # uneven rows
    assert split_rows([]) is None


def test_rebase_links() -> None:
    uris = ["index.html", "", "../opcodes.html#x", "https://pypi.org/project/spiflash/"]
    tree = nodes.bullet_list("", *(nodes.reference("", "x", refuri=u) for u in uris))
    _rebase_links(tree, "vendors/winbond.html")
    assert [r["refuri"] for r in tree.findall(nodes.reference)] == [
        "../vendors/index.html",
        "../vendors/winbond.html",
        "../opcodes.html#x",
        "https://pypi.org/project/spiflash/",
    ]
