"""A faster ``list-table``: each distinct cell is parsed once.

The generated pages hold about 260,000 table cells, but only about 21,000
distinct ones per directory: a tick, a size, a source's label, a link to a
chip. Parsing every cell as Markdown was most of reading the site. This
directive reads the list-table layout :func:`page_markup.list_table` writes
(``* -`` starts a row, ``  -`` a cell, four spaces continue one), parses each
cell it has not seen in the page's directory, and copies the nodes of one it
has. A cell's links are relative to its page, so pages in one directory
share cells. The table is built as ``list-table`` builds it; content in
another layout is left to ``list-table`` itself.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING, Any

from docutils import nodes
from docutils.parsers.rst.directives.tables import ListTable
from docutils.statemachine import StringList
from sphinx import addnodes

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sphinx.application import Sphinx

ROW, CELL, MORE = "* - ", "  - ", "    "

#: The parsed cells, by the page's directory and the cell's text.
_CELLS: dict[tuple[str, str], list[nodes.Node]] = {}


def split_rows(lines: list[str]) -> list[list[list[str]]] | None:
    """The rows of a list-table, each cell as its lines; None if ``lines``
    are not laid out as :func:`page_markup.list_table` writes them."""
    rows: list[list[list[str]]] = []
    for line in lines:
        if (line + " ").startswith(ROW):
            rows.append([[line[len(ROW) :]]])
        elif rows and (line + " ").startswith(CELL):
            rows[-1].append([line[len(CELL) :]])
        elif rows and (line.startswith(MORE) or not line.strip()):
            rows[-1][-1].append(line[len(MORE) :])
        elif line.strip():
            return None
    if not rows or any(len(row) != len(rows[0]) for row in rows):
        return None
    return rows


def _reusable(parsed: list[nodes.Node]) -> bool:
    """Whether a cell's nodes can be copied into another page: nothing in
    them is named, targeted or a message about the page they came from."""
    for top in parsed:
        for node in top.findall(nodes.Element):
            if node["ids"] or node["names"] or isinstance(node, nodes.system_message):
                return False
    return True


class CachedListTable(ListTable):
    """``list-table``, parsing each distinct cell once per directory."""

    def run(self) -> Sequence[nodes.table | nodes.system_message]:
        rows = split_rows(list(self.content))
        header_rows = self.options.get("header-rows", 0)
        # A title, given widths or too few rows: as list-table has it.
        if (
            rows is None
            or self.arguments
            or isinstance(self.widths, list)
            or header_rows > len(rows)
        ):
            return super().run()
        env = self.state.document.settings.env
        where = posixpath.dirname(env.docname)
        data = [[self._cell(where, cell) for cell in row] for row in rows]
        table = self.build_table_from_list(
            data,
            [100 // len(rows[0])] * len(rows[0]),
            header_rows,
            self.options.get("stub-columns", 0),
        )
        if "align" in self.options:
            table["align"] = self.options.get("align")
        table["classes"] += self.options.get("class", [])
        self.set_table_width(table)
        self.add_name(table)
        return [table]

    def _cell(self, where: str, lines: list[str]) -> list[nodes.Node]:
        text = "\n".join(lines)
        if not text.strip():
            return []
        cached = _CELLS.get((where, text))
        if cached is None:
            box = nodes.Element()
            self.state.nested_parse(StringList(lines), self.content_offset, box)
            parsed = list(box.children)
            if _reusable(parsed):
                _CELLS[where, text] = [n.deepcopy() for n in parsed]
            return parsed
        copies = [n.deepcopy() for n in cached]
        env = self.state.document.settings.env
        source = self.state.document["source"]
        for top in copies:
            for node in top.findall(nodes.Element):
                node.source, node.line = source, self.lineno
                if isinstance(node, addnodes.pending_xref):
                    node["refdoc"] = env.docname
        return copies


def _clear(_app: Sphinx) -> None:
    _CELLS.clear()


def setup(app: Sphinx) -> dict[str, Any]:
    app.add_directive("list-table", CachedListTable, override=True)
    app.connect("builder-inited", _clear)
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}
