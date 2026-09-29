"""The small C reader the extractors share."""

from __future__ import annotations

import pytest

from spiflash_extract import cparse


def test_strip_comments_keeps_offsets_and_strings() -> None:
    text = 'a /* one\ntwo */ b // three\n"http://x" c'
    out = cparse.strip_comments(text)
    assert len(out) == len(text)
    assert out.count("\n") == text.count("\n")
    assert "one" not in out
    assert "three" not in out
    assert '"http://x"' in out


def test_comments() -> None:
    assert cparse.comments("x /* W25Q01JV */ y // z\n/*\n * multi\n * line\n */") == [
        "W25Q01JV",
        "z",
        "multi line",
    ]


def test_split_top_respects_brackets_and_strings() -> None:
    assert cparse.split_top('a, f(b, c), {d, e}, "x,y",') == ["a", "f(b, c)", "{d, e}", '"x,y"']


def test_matching_and_unbalanced() -> None:
    assert cparse.matching("(a(b)c)", 0) == 6
    with pytest.raises(ValueError, match="unbalanced"):
        cparse.matching("(a", 0)
    assert cparse.matching('(")")', 0) == 4
    with pytest.raises(ValueError, match="unterminated string at 1"):
        cparse.matching('("a', 0)


def test_braced_items_and_array_body() -> None:
    text = "static const struct t x[] = { {1}, {2, {3}} };"
    body = cparse.array_body(text, r"struct\s+t\s+x\s*\[\s*\]")
    assert body is not None
    items = [b.body for b in cparse.braced_items(body.body, body.offset)]
    assert items == ["1", "2, {3}"]
    assert text[body.offset] == " "
    assert cparse.array_body(text, "nothing_here") is None


def test_designated_nested_key() -> None:
    fields = cparse.designated('.name = "x", .id.type = ID_SPI_RDID, .v = {1, 2},')
    assert fields == {"name": '"x"', "id.type": "ID_SPI_RDID", "v": "{1, 2}"}


def test_macro_call() -> None:
    assert cparse.macro_call(".id = SNOR_ID(0xef, 0x40, 0x18),", "SNOR_ID") == [
        "0xef",
        "0x40",
        "0x18",
    ]
    assert cparse.macro_call("nothing", "SNOR_ID") is None


def test_c_string() -> None:
    assert cparse.c_string('"ab" "c\\"d"') == 'abc"d'
    with pytest.raises(ValueError, match="not a string"):
        cparse.c_string("NULL")


def test_defines_join_continuations() -> None:
    text = "#define A (1 << 2)\n#define B (A | \\\n  C)\n#define F(x) x\n"
    assert cparse.defines(text) == {"A": "(1 << 2)", "B": "(A |    C)"}


def test_define_comments() -> None:
    assert cparse.define_comments("#define X_ID 0xEF /* Winbond */\n") == {"X_ID": "Winbond"}


@pytest.mark.parametrize(
    ("expr", "value"),
    [
        ("64 * 1024", 65536),
        ("SZ_16M", 16 << 20),
        ("SZ_512", 512),
        ("SZ_1G", 1 << 30),
        ("BIT(3) | BIT(0)", 9),
        ("0x10UL", 16),
        ("010", 8),
        ("0", 0),
        ("GENMASK(3, 1)", 0b1110),
        ("-1", -1),
        ("~0 & 0xff", 255),
        ("(unsigned)12 / 4", 3),
        ("A\n  | B", 3),
    ],
)
def test_evaluate(expr: str, value: int) -> None:
    assert cparse.evaluate(expr, {"A": 1, "B": "A << 1"}) == value


def test_evaluate_errors() -> None:
    with pytest.raises(cparse.EvalError, match="unknown identifier"):
        cparse.evaluate("NOPE")
    with pytest.raises(cparse.EvalError, match="recursive"):
        cparse.evaluate("A", {"A": "B", "B": "A"})
    with pytest.raises(cparse.EvalError, match="cannot parse"):
        cparse.evaluate("1 +")
    with pytest.raises(cparse.EvalError, match="unsupported"):
        cparse.evaluate("f(1)")


def test_flag_names() -> None:
    assert cparse.flag_names("SECT_4K | (SPI_NOR_DUAL_READ) | 0") == [
        "SECT_4K",
        "SPI_NOR_DUAL_READ",
    ]
    assert cparse.flag_names("0") == []


def test_bit_names_expands_composites_and_masks() -> None:
    symbols: dict[str, str | int] = {
        "F_A": "(1 << 0)",
        "F_B": "(1 << 1)",
        "F_C": "(1 << 2)",
        "F_AB": "(F_A | F_B)",
        "F_ONLY_B": "F_B",  # an alias: the plain definition keeps the bit's name
        "F_ABC": "(F_AB | F_C)",
        "F_MASK": "(3 << 0)",
    }
    assert cparse.bit_names("F_ABC & ~F_B", symbols, "F_") == ["F_A", "F_C"]
    assert cparse.bit_names("F_AB", symbols, "F_") == ["F_A", "F_B"]
    assert cparse.bit_names("0", symbols, "F_") == []


def test_line_of_and_drop_preprocessor() -> None:
    text = "a\n#ifdef X\nb\n#endif\n"
    out = cparse.drop_preprocessor(text)
    assert len(out) == len(text)
    assert "#" not in out
    assert cparse.line_of(text, text.index("b")) == 3
