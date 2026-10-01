#!/usr/bin/env python3
"""Rebuild :repo:`src/spiflash/data/` from the upstream trees in
:repo:`tools/sources.toml`::

    uv run tools/update_db.py            # rebuild from the pinned commits
    uv run tools/update_db.py --latest   # move the pins to upstream HEAD first
    uv run tools/update_db.py --check    # fail if the committed data differs
    uv run tools/update_db.py --latest --check   # has anything upstream changed?

The upstream files are fetched into ``upstream`` (git-ignored) in the
repository root.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Run from a checkout, the package and the extractors are not installed:
# spiflash is in src/, spiflash_extract beside this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from spiflash_extract import (
    dediprog,
    fetch,
    flashrom,
    imsprog,
    linux,
    mediatek,
    openfpgaloader,
    openocd,
    qemu,
    rockchip,
    uboot,
    zephyr,
)

if TYPE_CHECKING:
    from collections import Counter
    from collections.abc import Callable

    from spiflash_extract.record import Record

REPO = Path(__file__).resolve().parent.parent
SOURCES = REPO / "tools" / "sources.toml"
DATA = REPO / "src" / "spiflash" / "data"
UPSTREAM = REPO / "upstream"

FORMAT = 5

EXTRACTORS: dict[str, Callable[[Path], list[Record]]] = {
    "flashrom": lambda root: flashrom.extract(root, "flashrom"),
    "flashprog": lambda root: flashrom.extract(root, "flashprog"),
    "linux": linux.extract,
    "u-boot": uboot.extract,
    "dediprog": dediprog.extract,
    "rockchip": rockchip.extract,
    "mediatek": mediatek.extract,
    "openocd": openocd.extract,
    "openfpgaloader": openfpgaloader.extract,
    "imsprog": imsprog.extract,
    "qemu": qemu.extract,
    "zephyr": zephyr.extract,
}

#: For an extractor that leaves known kinds of entry out: how many, by reason.
SKIPPED: dict[str, Callable[[Path], Counter[str]]] = {
    "dediprog": dediprog.skipped,
    "mediatek": mediatek.skipped,
    "imsprog": imsprog.skipped,
}


def json_lines(header: dict[str, Any], key: str, items: list[Any]) -> str:
    """JSON with one item per line, so a diff of the data reads per chip."""
    head = json.dumps(header, sort_keys=True)[:-1]
    body = ",\n".join(json.dumps(i, sort_keys=True, ensure_ascii=False) for i in items)
    sep = ", " if header else ""
    return f'{head}{sep}"{key}": [\n{body}\n]}}\n'


def build(ups: list[fetch.Upstream]) -> dict[str, str]:
    """The contents of every data file, by file name."""
    records: list[Record] = []
    sources: dict[str, Any] = {}
    manufacturers: list[dict[str, object]] = []
    for up in ups:
        tree = fetch.fetch(up, UPSTREAM)
        recs = EXTRACTORS[up.name](tree)
        print(f"{up.name:15} {up.commit[:12]} {len(recs):5} records", file=sys.stderr)
        if up.name in SKIPPED:
            for reason, n in SKIPPED[up.name](tree).most_common():
                print(f"{'':28} {n:5} left out: {reason}", file=sys.stderr)
        records += recs
        sources[up.name] = {
            "url": up.url,
            "browse": up.browse or up.url,
            "branch": up.branch,
            "commit": up.commit,
            "date": fetch.commit_date(tree),
            "paths": up.paths,
            "license": up.license,
            "records": len(recs),
        }
        if up.name == "openocd":
            manufacturers = openocd.manufacturers(tree)
            sources["jep106"] = {**sources["openocd"], "paths": [openocd.JEP106]}
            sources["jep106"]["records"] = len(manufacturers)
    records.sort(key=lambda r: (r["source"], r["file"], r["line"]))
    return {
        "records.json": json_lines({"format": FORMAT}, "records", records),
        "manufacturers.json": json_lines({"format": FORMAT}, "manufacturers", manufacturers),
        "sources.json": json.dumps({"format": FORMAT, "sources": sources}, indent=1, sort_keys=True)
        + "\n",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--latest", action="store_true", help="move the pins to upstream HEAD first")
    ap.add_argument("--check", action="store_true", help="compare with the committed data")
    args = ap.parse_args(argv)

    ups = fetch.load(SOURCES)
    if args.latest:
        for up in ups:
            head = fetch.remote_head(up)
            if head != up.commit:
                print(f"{up.name}: {up.commit[:12]} -> {head[:12]}", file=sys.stderr)
                up.commit = head
                if not args.check:
                    fetch.set_commit(SOURCES, up.name, head)

    files = build(ups)
    if args.check:
        stale = [n for n, text in files.items() if (DATA / n).read_text() != text]
        if stale:
            print(f"out of date: {', '.join(stale)}", file=sys.stderr)
            print(
                "run `uv run tools/update_db.py" + (" --latest" if args.latest else "") + "`",
                file=sys.stderr,
            )
            return 1
        print("data is up to date", file=sys.stderr)
        return 0
    DATA.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (DATA / name).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
