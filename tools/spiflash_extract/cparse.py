"""Just enough C to read a table of struct initialisers.

The flash tables upstream are arrays of brace initialisers, designated
(``.size = SZ_16M,``) or positional (``FLASH_ID("st m25p05", 0x03, ...)``).
This module finds them, splits them at the top level, and evaluates the
integer expressions in them against a symbol table.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')


_STRING_OR_COMMENT = re.compile(f"{_STRING.pattern}|{_COMMENT.pattern}", re.DOTALL)


def _blank_comment(match: re.Match[str]) -> str:
    """A comment's replacement: spaces, keeping its newlines so offsets and
    line numbers survive. A string literal is returned unchanged."""
    tok = match.group(0)
    return tok if tok.startswith('"') else re.sub(r"[^\n]", " ", tok)


def strip_comments(text: str) -> str:
    """``text`` with every comment blanked out, the same length as before.

    String literals are protected first, so a ``//`` inside a URL in a string
    is not taken for a comment."""
    return _STRING_OR_COMMENT.sub(_blank_comment, text)


def comments(text: str) -> list[str]:
    """The text of every comment in ``text``, stripped of its delimiters."""
    found = []
    for m in _COMMENT.finditer(text):
        body = m.group(0)
        body = body[2:-2] if body.startswith("/*") else body[2:]
        body = " ".join(line.strip(" *\t") for line in body.splitlines()).strip()
        if body:
            found.append(body)
    return found


def drop_preprocessor(text: str) -> str:
    """Blank out preprocessor lines (``#ifdef CONFIG_...``) inside a table,
    keeping every entry whatever the configuration."""
    return re.sub(r"(?m)^[ \t]*#[^\n]*", lambda m: " " * len(m.group(0)), text)


def line_of(text: str, offset: int) -> int:
    """The 1-based line number of ``offset`` in ``text``."""
    return text.count("\n", 0, offset) + 1


_OPEN = {"{": "}", "(": ")", "[": "]"}
_CLOSE = {"}", ")", "]"}


def matching(text: str, start: int) -> int:
    """The index of the bracket closing the one at ``text[start]``.

    ``text`` must be free of comments; string literals are skipped."""
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == '"':
            m = _STRING.match(text, i)
            if m is None:
                msg = f"unterminated string at {i}"
                raise ValueError(msg)
            i = m.end()
            continue
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    msg = f"unbalanced bracket at {start}"
    raise ValueError(msg)


def split_top(text: str, sep: str = ",") -> list[str]:
    """Split ``text`` at ``sep`` where it is outside every bracket and string.

    Empty trailing pieces (from a trailing comma) are dropped."""
    parts = []
    depth = 0
    cur = 0
    i = 0
    while i < len(text):
        c = text[i]
        if c == '"':
            m = _STRING.match(text, i)
            i = m.end() if m else i + 1
            continue
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(text[cur:i])
            cur = i + 1
        i += 1
    parts.append(text[cur:])
    return [p.strip() for p in parts if p.strip()]


@dataclass(frozen=True)
class Block:
    """A bracketed piece of source: its contents and where it starts."""

    body: str
    offset: int  # offset of the body's first character in the whole text


def braced_items(text: str, offset: int = 0) -> Iterator[Block]:
    """Every top-level ``{ ... }`` in ``text``, in order."""
    i = 0
    while True:
        i = text.find("{", i)
        if i < 0:
            return
        end = matching(text, i)
        yield Block(text[i + 1 : end], offset + i + 1)
        i = end + 1


def initialisers(text: str, declaration: str) -> Iterator[tuple[re.Match[str], Block]]:
    """Every ``declaration = { ... }`` in ``text``: the match of ``declaration``
    (for its groups) and the initialiser's contents."""
    for m in re.finditer(declaration + r"\s*=\s*\{", text):
        start = m.end() - 1
        yield m, Block(text[start + 1 : matching(text, start)], start + 1)


def array_body(text: str, declaration: str) -> Block | None:
    """The contents of the array initialiser whose declaration matches the
    regular expression ``declaration`` (up to, not including, its ``=``)."""
    return next((body for _, body in initialisers(text, declaration)), None)


def designated(body: str) -> dict[str, str]:
    """The ``.field = value`` pairs at the top level of an initialiser body.

    ``.id.type = X`` (a nested designator) is kept as the key ``id.type``."""
    fields = {}
    for item in split_top(body):
        m = re.match(r"\.([A-Za-z_][\w.]*)\s*=\s*(.*)$", item, re.DOTALL)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def macro_call(text: str, name: str) -> list[str] | None:
    """The arguments of the first call of macro ``name`` in ``text``."""
    m = re.search(r"\b" + re.escape(name) + r"\s*\(", text)
    if m is None:
        return None
    start = m.end() - 1
    return split_top(text[start + 1 : matching(text, start)])


def c_string(expr: str) -> str:
    """The value of a C string literal, adjacent literals concatenated."""
    pieces = _STRING.findall(expr)
    if not pieces:
        msg = f"not a string literal: {expr!r}"
        raise ValueError(msg)
    out = []
    for p in pieces:
        s = p[1:-1]
        s = re.sub(r"\\(.)", r"\1", s)
        out.append(s)
    return "".join(out)


_DEFINE = re.compile(r"(?m)^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)[ \t]+([^\n]*?)[ \t]*$")


def defines(text: str) -> dict[str, str]:
    """Object-like ``#define NAME value`` lines (comments already stripped),
    function-like macros excluded. Backslash-continued lines are joined."""
    text = re.sub(r"\\\n", " ", text)
    return {m.group(1): m.group(2).strip() for m in _DEFINE.finditer(text) if m.group(2).strip()}


def bit_names(expr: str, symbols: Mapping[str, str | int], prefix: str) -> list[str]:
    """The single-bit ``prefix*`` symbols set in the value of ``expr``.

    ``FEATURE_QPI_38 & ~FEATURE_FAST_READ_QOUT`` becomes the list of the
    individual bits it leaves set, whatever composite names it was built from.
    Where two names share a bit, the one defined as a plain number wins."""
    value = evaluate(expr, symbols)
    by_bit: dict[int, str] = {}
    for name in sorted(symbols):
        if not name.startswith(prefix):
            continue
        try:
            v = evaluate(str(symbols[name]), symbols)
        except EvalError:
            continue
        if v <= 0 or v & (v - 1):
            continue  # not a single bit
        numbers_removed = re.sub(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]*\b", "", str(symbols[name]))
        plain = not re.search(r"[A-Za-z_]", numbers_removed)
        if v not in by_bit or plain:
            by_bit[v] = name
    return sorted(name for bit, name in by_bit.items() if value & bit)


def define_comments(text: str) -> dict[str, str]:
    """The trailing comment on each ``#define NAME value /* comment */`` line."""
    out = {}
    for m in re.finditer(
        r"(?m)^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)[ \t]+[^\n]*?/\*(.*?)\*/[ \t]*$", text
    ):
        out[m.group(1)] = " ".join(m.group(2).split())
    return out


class EvalError(ValueError):
    """An expression used something the evaluator does not know."""


_BINOPS: dict[type[ast.operator], Callable[[int, int], int]] = {
    ast.BitOr: operator.or_,
    ast.BitAnd: operator.and_,
    ast.BitXor: operator.xor,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv,
}

_FUNCS: dict[str, Callable[..., int]] = {
    "BIT": lambda n: 1 << n,
    "BIT_ULL": lambda n: 1 << n,
    "GENMASK": lambda h, lo: ((1 << (h - lo + 1)) - 1) << lo,
}


def _sizes() -> dict[str, int]:
    """Linux's ``SZ_*`` constants (include/linux/sizes.h)."""
    out = {}
    for shift in range(41):
        n = 1 << shift
        if n < 1024:
            out[f"SZ_{n}"] = n
        elif n < 1024**2:
            out[f"SZ_{n // 1024}K"] = n
        elif n < 1024**3:
            out[f"SZ_{n // 1024**2}M"] = n
        elif n < 1024**4:
            out[f"SZ_{n // 1024**3}G"] = n
        else:
            out[f"SZ_{n // 1024**4}T"] = n
    return out


SIZES = _sizes()


def evaluate(expr: str, symbols: Mapping[str, str | int] | None = None) -> int:
    """The integer value of the C expression ``expr``.

    Identifiers are looked up in ``symbols`` (whose values may themselves be
    expressions, evaluated recursively) and then in the ``SZ_*`` sizes.
    ``BIT(n)`` and ``GENMASK(h, l)`` are understood. Anything else raises
    :class:`EvalError`."""
    return _Evaluator(symbols or {}).value(expr, ())


class _Evaluator:
    def __init__(self, symbols: Mapping[str, str | int]) -> None:
        self.symbols = symbols

    def value(self, expr: str, stack: tuple[str, ...]) -> int:
        py = expr.strip()
        # C integer suffixes and casts that do not change the value.
        py = re.sub(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]+\b", r"\1", py)
        py = re.sub(r"\(\s*(?:unsigned|u8|u16|u32|u64|uint\d+_t|size_t|int)\s*\)", "", py)
        py = py.replace("/", "//").replace("||", " or ").replace("&&", " and ")
        py = re.sub(r"\b0(\d+)\b", lambda m: "0o" + m.group(1) if m.group(1) != "0" else "0", py)
        try:
            # Parenthesised, so an expression spanning lines still parses.
            tree = ast.parse(f"({py})", mode="eval")
        except SyntaxError as e:
            msg = f"cannot parse {expr!r}"
            raise EvalError(msg) from e
        return self.node(tree.body, stack)

    def node(self, n: ast.expr, stack: tuple[str, ...]) -> int:
        if isinstance(n, ast.Constant) and isinstance(n.value, int):
            return n.value
        if isinstance(n, ast.Name):
            return self.name(n.id, stack)
        if isinstance(n, ast.BinOp) and type(n.op) in _BINOPS:
            return _BINOPS[type(n.op)](self.node(n.left, stack), self.node(n.right, stack))
        if isinstance(n, ast.UnaryOp):
            v = self.node(n.operand, stack)
            if isinstance(n.op, ast.USub):
                return -v
            if isinstance(n.op, ast.UAdd):
                return v
            if isinstance(n.op, ast.Invert):
                return ~v
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in _FUNCS
            and not n.keywords
        ):
            return _FUNCS[n.func.id](*(self.node(a, stack) for a in n.args))
        msg = f"unsupported expression: {ast.unparse(n)}"
        raise EvalError(msg)

    def name(self, ident: str, stack: tuple[str, ...]) -> int:
        if ident in stack:
            msg = f"recursive definition of {ident}"
            raise EvalError(msg)
        if ident in self.symbols:
            v = self.symbols[ident]
            return v if isinstance(v, int) else self.value(v, (*stack, ident))
        if ident in SIZES:
            return SIZES[ident]
        msg = f"unknown identifier {ident}"
        raise EvalError(msg)


def flag_names(expr: str) -> list[str]:
    """The identifiers OR-ed together in a flags expression, in order:
    ``SECT_4K | SPI_NOR_DUAL_READ`` gives ``["SECT_4K", "SPI_NOR_DUAL_READ"]``.
    ``0`` gives ``[]``."""
    names = []
    for part in split_top(expr, "|"):
        name = part.strip().strip("()").strip()
        if name in ("", "0"):
            continue
        names.append(name)
    return names
