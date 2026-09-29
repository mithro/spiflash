"""Just enough devicetree source to read the properties of its nodes.

Zephyr describes the flash on a board as a node in the board's ``.dts``, a
shared ``.dtsi`` or an ``.overlay``::

    mx25r64: mx25r6435f@0 {
        compatible = "nordic,qspi-nor";
        jedec-id = [c2 28 17];
        size = <67108864>;
        has-dpd;
    };

:func:`nodes` finds every node in a file, with its labels, name, properties
and where each is, without running the C preprocessor: ``#include`` and
``#define`` lines are skipped, and a value using a macro is kept as written
for the caller to evaluate (:func:`cells`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import cparse

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


@dataclass
class Property:
    """One ``name = value;`` (or ``name;``), the value as written."""

    name: str
    value: str | None  # None for a boolean property
    offset: int


@dataclass
class Node:
    """One node: ``labels: name { ... };``, or ``&label { ... };`` for a node
    defined elsewhere."""

    name: str
    labels: list[str]
    offset: int
    end: int
    properties: dict[str, Property] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)

    @property
    def reference(self) -> str | None:
        """The label of the node this one adds to, for ``&label { ... }``."""
        return self.name[1:] if self.name.startswith("&") else None

    def walk(self) -> Iterator[Node]:
        """This node and every node below it."""
        yield self
        for child in self.children:
            yield from child.walk()


_DIRECTIVE = re.compile(r"/(?:dts-v1|plugin|memreserve|delete-node|delete-property|include)/")
_OMIT = re.compile(r"/omit-if-no-ref/\s*")


# A preprocessor line, with its continuations; not a property such as
# ``#address-cells = <1>;``.
_DIRECTIVE_LINE = re.compile(
    r"^[ \t]*#[ \t]*(?:include|define|undef|if|ifdef|ifndef|elif|else|endif|error|pragma)\b"
    r"(?:[^\n]*\\\n)*[^\n]*",
    re.MULTILINE,
)


def clean(text: str) -> str:
    """``text`` without comments and preprocessor lines, the same length."""
    return _DIRECTIVE_LINE.sub(
        lambda m: re.sub(r"[^\n]", " ", m.group(0)), cparse.strip_comments(text)
    )


def nodes(text: str) -> list[Node]:
    """The top-level nodes of ``text``, a whole ``.dts``, ``.dtsi`` or
    ``.overlay`` file; :meth:`Node.walk` goes through the rest."""
    body = clean(text)
    return _parse(body, 0, len(body)).children


def _parse(text: str, start: int, end: int) -> Node:
    """The properties and child nodes in ``text[start:end]``."""
    node = Node("", [], start, end)
    i = start
    while True:
        i = _skip_space(text, i, end)
        if i >= end:
            return node
        m = _OMIT.match(text, i)
        if m:
            i = m.end()
            continue
        stop = _statement_end(text, i, end)
        head = text[i:stop].strip()
        if text[stop] == "{":
            close = cparse.matching(text, stop)
            child = _parse(text, stop + 1, close)
            labels, _, name = head.rpartition(":") if not head.startswith("&") else ("", "", head)
            child.name = name.strip()
            child.labels = [lab.strip() for lab in labels.split(":") if lab.strip()]
            child.offset = i
            child.end = close
            node.children.append(child)
            semi = _skip_space(text, close + 1, end)
            i = semi + 1 if semi < end and text[semi] == ";" else close + 1
            continue
        if not _DIRECTIVE.match(head):
            name, eq, value = head.partition("=")
            name = name.strip()
            node.properties[name] = Property(name, value.strip() if eq else None, i)
        i = stop + 1


def _skip_space(text: str, i: int, end: int) -> int:
    while i < end and text[i].isspace():
        i += 1
    return i


def _statement_end(text: str, i: int, end: int) -> int:
    """The index of the ``;`` ending a property or the ``{`` opening a node
    that starts at ``i``, skipping strings and bracketed values."""
    while i < end:
        c = text[i]
        if c in ";{":
            return i
        if c == '"':
            i = cparse.string_end(text, i)
            continue
        if text.startswith("&{", i):  # a node by path: &{/soc/spi@4000} { ... }
            i = cparse.matching(text, i + 1) + 1
            continue
        i += 1
    msg = f"no ';' or '{{' after offset {i}"
    raise ValueError(msg)


def strings(value: str) -> list[str]:
    """The strings of a ``"a", "b"`` value."""
    return [cparse.c_string(s) for s in cparse.split_top(value)]


def bytestring(value: str) -> bytes:
    """The bytes of a ``[c2 28 17]`` value."""
    m = re.fullmatch(r"\[([0-9a-fA-F\s]*)\]", value.strip())
    if m is None:
        msg = f"not a byte string: {value!r}"
        raise ValueError(msg)
    return bytes.fromhex("".join(m.group(1).split()))


def cells(value: str, symbols: Mapping[str, str | int] | None = None) -> list[int]:
    """The integers of a ``<1 (2 * 3) DT_SIZE_M(4)>`` value."""
    m = re.fullmatch(r"<(.*)>", value.strip(), re.DOTALL)
    if m is None:
        msg = f"not a cell list: {value!r}"
        raise ValueError(msg)
    return [cparse.evaluate(c, symbols) for c in _cell_items(m.group(1))]


def _cell_items(text: str) -> Iterator[str]:
    """The cells of ``<...>``: whitespace separates them outside brackets."""
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        j = i
        while j < len(text) and not text[j].isspace():
            j = cparse.matching(text, j) + 1 if text[j] == "(" else j + 1
        yield text[i:j]
        i = j
