"""A faster ``list-table``: each distinct cell is parsed once.

The generated pages hold about 260,000 table cells, but only about 21,000
distinct ones per directory: a tick, a size, a source's label, a link to a
chip. Parsing every cell as Markdown was most of reading the site. In the
directories named by the ``list_tables_cached`` setting (the generated
ones), in Markdown pages, this directive reads the list-table layout
:func:`page_markup.list_table` writes (``* -`` starts a row, ``  -`` a cell,
four spaces continue one), parses the cells it has not seen in the page's
directory, all at once, and copies the nodes of those it has. A cell's
links are relative to its page, so pages in one directory share cells;
a cross-reference in a copy names its own page, and its nodes their own
source and line.

A cell whose meaning can depend on the page is parsed on each page: a
substitution (``{{ ... }}``), a reference-style link, a link definition or
a footnote (a ``]`` not followed by ``(``), and anything with an id or
name. The table is built as ``list-table`` builds it; another layout, a
title or given widths, another directory and reStructuredText are left to
``list-table`` itself. A warning from a cell's Markdown is given on the
first page with that cell, not on each.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING, Any

from docutils import nodes
from docutils.parsers.rst.directives.tables import ListTable
from docutils.statemachine import StringList
from docutils.utils import SystemMessagePropagation
from myst_parser.mocking import MockState
from sphinx import addnodes

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sphinx.application import Sphinx

ROW, CELL, MORE = "* - ", "  - ", "    "
#: The comment between the cells of a table parsed at once.
SEPARATOR = "sf-list-table-cell"
#: What makes a cell's meaning depend on its page (see above).
PAGE_BOUND = re.compile(r"\{\{|(?<!\\)\](?!\()")

#: A fence, which could run on past a cell into the next when the cells are
#: parsed at once.
FENCE = re.compile(r"^\s*(?:`{3,}|~{3,}|:{3,})", re.MULTILINE)

#: A cell: the index of its first line in the directive's content, and its
#: lines.
Cell = tuple[int, list[str]]

#: The parsed cells, by the page's directory and reference context and the
#: cell's text; their lines are relative to the cell's.
_CELLS: dict[tuple[str, str, str], list[nodes.Node]] = {}


def split_rows(lines: list[str]) -> list[list[Cell]] | None:
    """The rows of a list-table; None if ``lines`` are not laid out as
    :func:`page_markup.list_table` writes them."""
    rows: list[list[Cell]] = []
    for i, line in enumerate(lines):
        if (line + " ").startswith(ROW):
            rows.append([(i, [line[len(ROW) :]])])
        elif rows and (line + " ").startswith(CELL):
            rows[-1].append((i, [line[len(CELL) :]]))
        elif rows and (line.startswith(MORE) or not line.strip()):
            rows[-1][-1][1].append(line[len(MORE) :])
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


def _shift(parsed: list[nodes.Node], by: int) -> None:
    for top in parsed:
        for node in top.findall(nodes.Element):
            if node.line is not None:
                node.line += by


class CachedListTable(ListTable):
    """``list-table``, parsing each distinct cell once per directory."""

    def run(self) -> Sequence[nodes.table | nodes.system_message]:
        env = self.state.document.settings.env
        where = posixpath.dirname(env.docname)
        rows = split_rows(list(self.content))
        # Not a generated page, or a title, given widths or no rows: as
        # list-table has it.
        if (
            not isinstance(self.state, MockState)
            or where.split("/")[0] not in env.config.list_tables_cached
            or rows is None
            or self.arguments
            or isinstance(self.widths, list)
        ):
            return super().run()
        header_rows = self.options.get("header-rows", 0)
        stub_columns = self.options.get("stub-columns", 0)
        try:
            self.check_table_dimensions(rows, header_rows, stub_columns)
        except SystemMessagePropagation as error:
            return [error.args[0]]
        key = (where, repr(sorted(env.ref_context.items())))
        parsed = self._cells(key, [cell for row in rows for cell in row])
        width = len(rows[0])
        data = [parsed[i : i + width] for i in range(0, len(parsed), width)]
        table = self.build_table_from_list(data, [100 // width] * width, header_rows, stub_columns)
        if "align" in self.options:
            table["align"] = self.options.get("align")
        table["classes"] += self.options.get("class", [])
        self.set_table_width(table)
        self.add_name(table)
        return [table]

    def _cells(self, key: tuple[str, str], cells: list[Cell]) -> list[list[nodes.Node]]:
        """Each cell's nodes: parsed now, in order (the page-bound ones and
        those not in the cache yet), or copied."""
        texts = ["\n".join(lines) for _, lines in cells]
        shared = [bool(t.strip()) and not PAGE_BOUND.search(t) for t in texts]
        todo = [
            i
            for i, text in enumerate(texts)
            if text.strip() and not (shared[i] and (*key, text) in _CELLS)
        ]
        fresh = dict(zip(todo, self._parse([cells[i] for i in todo]), strict=True))
        out: list[list[nodes.Node]] = []
        for i, text in enumerate(texts):
            if i in fresh:
                out.append(fresh[i])
                if shared[i] and (*key, text) not in _CELLS and _reusable(fresh[i]):
                    self._store((*key, text), fresh[i], cells[i][0])
            elif text.strip():
                out.append(self._copy(_CELLS[*key, text], cells[i][0]))
            else:
                out.append([])
        return out

    def _base(self, start: int) -> int:
        """What a cell's node lines are counted from, for its first line
        at ``start`` in the content."""
        return self.lineno + self.content_offset + start

    def _store(self, key: tuple[str, str, str], parsed: list[nodes.Node], start: int) -> None:
        copies = [n.deepcopy() for n in parsed]
        _shift(copies, -self._base(start))
        _CELLS[key] = copies

    def _copy(self, cached: list[nodes.Node], start: int) -> list[nodes.Node]:
        copies = [n.deepcopy() for n in cached]
        refdoc = self.state.document.settings.env.docname
        source = self.state.document["source"]
        base = self._base(start)
        for top in copies:
            for node in top.findall(nodes.Element):
                node.source = source
                if node.line is not None:
                    node.line += base
                if isinstance(node, addnodes.pending_xref):
                    node["refdoc"] = refdoc
        return copies

    def _parse(self, cells: list[Cell]) -> list[list[nodes.Node]]:
        """Parses ``cells`` at once, a comment line between each two, and
        splits the nodes at the comments; each cell alone if one has a
        fence, or should the split not come out even (a second parse, whose
        ids differ from a single parse's)."""
        if not cells:
            return []
        if any(FENCE.search("\n".join(lines)) for _, lines in cells):
            return [self._parse_one(cell) for cell in cells]
        lines: list[str] = []
        shifts = []
        for start, cell in cells:
            shifts.append(start - len(lines))
            lines += [*cell, "", f"% {SEPARATOR}", ""]
        box = nodes.Element()
        self.state.nested_parse(StringList(lines), self.content_offset, box)
        parts: list[list[nodes.Node]] = [[]]
        for node in box.children:
            if isinstance(node, nodes.comment) and node.astext().strip() == SEPARATOR:
                parts.append([])
            else:
                parts[-1].append(node)
        if len(parts) != len(cells) + 1 or parts[-1]:
            return [self._parse_one(cell) for cell in cells]
        for part, shift in zip(parts, shifts, strict=False):
            _shift(part, shift)
        return parts[:-1]

    def _parse_one(self, cell: Cell) -> list[nodes.Node]:
        start, lines = cell
        box = nodes.Element()
        self.state.nested_parse(StringList(lines), self.content_offset + start, box)
        return list(box.children)


def _clear(_app: Sphinx) -> None:
    _CELLS.clear()


def setup(app: Sphinx) -> dict[str, Any]:
    # The top directories whose Markdown pages' list-tables share cells.
    app.add_config_value("list_tables_cached", (), "env", types=frozenset({list, tuple}))
    app.add_directive("list-table", CachedListTable, override=True)
    app.connect("builder-inited", _clear)
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}
