"""The site's search boxes: the pages ask for them, and tables.js scores and
matches part names as the Python code does (with node; skipped without it)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import spiflash
from spiflash.db import _glob, _reason
from spiflash.model import name_distance, squash_name
from spiflash_pages import chips_index, vendor_page

TABLES_JS = Path(__file__).parent.parent / "docs" / "_static" / "tables.js"
NODE = shutil.which("node")

# Reads {"pairs": [[query, name], ...], "globs": [[pattern, name], ...],
# "filters": [text, ...]} and answers each with what tables.js makes of it.
SCRIPT = """
const t = require(process.argv[1]);
let input = "";
process.stdin.on("data", (d) => { input += d; });
process.stdin.on("end", () => {
  const q = JSON.parse(input);
  const out = {
    pairs: q.pairs.map(([a, b]) => {
      const qa = t.squash(a, false), nb = t.squash(b, true);
      const [cost, common] = t.distance(qa, nb);
      return [cost, common, t.reason(qa, nb, common)];
    }),
    globs: q.globs.map(([p, n]) => t.globRegExp(p.replace(/\\s/g, "")).test(n)),
    filters: q.filters.map((f) => {
      const r = t.parseFilter(f);
      return r.error ? r.error : r.tests.map((x) => (x.word ? "word" : "pattern"));
    }),
  };
  process.stdout.write(JSON.stringify(out));
});
"""

needs_node = pytest.mark.skipif(NODE is None, reason="needs node")


def run_js(query: dict[str, Any]) -> dict[str, Any]:
    assert NODE is not None
    done = subprocess.run(
        [NODE, "-e", SCRIPT, str(TABLES_JS)],
        input=json.dumps(query),
        capture_output=True,
        text=True,
        check=True,
    )
    out: dict[str, Any] = json.loads(done.stdout)
    return out


QUERIES = [
    "W25Q128JVSIQ",
    "W25Q182JV",
    "w25q128jv-im",
    "S25FL128SAGMFI001",
    "25Q64",
    "MX25L6406E",
    "GD25Q6",
    "N25Q128A13ESE40",
    "",
]

GLOBS = ["W25Q128*", "w25q128?v", "MX25?12835F", "S25FL*S", "W25Q[!0-9]*", "[", "W25Q 128 JV"]


@needs_node
def test_nearest_scores_as_python_does() -> None:
    names = sorted({n for f in spiflash.flashes() for n in f.names})
    pairs = [[q, n] for q in QUERIES for n in names]
    got = run_js({"pairs": pairs, "globs": [], "filters": []})["pairs"]
    for (q, n), (cost, common, why) in zip(pairs, got, strict=True):
        want = name_distance(q, n)
        assert (cost, common) == want, (q, n)
        assert why == _reason(q, n, common), (q, n)
        assert squash_name(q) or cost == len(squash_name(n, wildcards=True))


@needs_node
def test_globs_match_as_python_does() -> None:
    names = sorted({n for f in spiflash.flashes() for n in f.names})
    globs = [[p, n] for p in GLOBS for n in names]
    got = run_js({"pairs": [], "globs": globs, "filters": []})["globs"]
    for (p, n), js in zip(globs, got, strict=True):
        assert js == bool(_glob(p).fullmatch(n)), (p, n)


@needs_node
def test_filter_syntax() -> None:
    filters = ["winbond 16 MiB", "W25Q128* winbond", "/^MX25[LU]/", "/(/", "//"]
    got = run_js({"pairs": [], "globs": [], "filters": filters})["filters"]
    assert got == [
        ["word", "word", "word"],
        ["pattern", "word"],
        ["pattern"],
        "not a regular expression",
        ["word"],
    ]


def test_the_all_chips_page_has_a_nearest_box() -> None:
    db = spiflash.database()
    slugs = {id(f): f.key for f in db.flashes}
    index = chips_index(list(db.flashes), slugs)
    assert ":class: sf-table sf-filterable sf-parts sf-with-vendor sf-nearest" in index
    assert "a glob for a whole part name (`W25Q128*`" in index
    winbond = vendor_page(db, "Winbond", db.by_manufacturer("Winbond"), slugs)
    assert "Type in the box to filter. Every word" in winbond
    assert "sf-nearest" not in winbond
