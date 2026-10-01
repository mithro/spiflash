#!/usr/bin/env python3
"""Compare two snapshots from :repo:`tools/db_snapshot.py`::

    uv run tools/db_diff.py before.json after.json
    uv run tools/db_diff.py --summary before.json after.json
    uv run tools/db_diff.py --only opcodes --only features before.json after.json

Prints, per chip, every property that changed (for a property holding an
object, the keys added, removed and changed in it), then how many chips
each property changed on. Exit 0 when nothing differs, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def changes(a: Any, b: Any) -> list[str]:
    """What differs from ``a`` to ``b``, one line each; for two objects,
    by key."""
    if a == b:
        return []
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return [f"{json.dumps(a)} -> {json.dumps(b)}"]
    out = [f"+{k}: {json.dumps(b[k])}" for k in sorted(b.keys() - a.keys())]
    out += [f"-{k}: {json.dumps(a[k])}" for k in sorted(a.keys() - b.keys())]
    out += [
        f"~{k}: {json.dumps(a[k])} -> {json.dumps(b[k])}"
        for k in sorted(a.keys() & b.keys())
        if a[k] != b[k]
    ]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("a", type=Path, help="the snapshot before")
    ap.add_argument("b", type=Path, help="the snapshot after")
    ap.add_argument("--summary", action="store_true", help="print only the totals")
    ap.add_argument("--only", action="append", default=[], help="compare only this property")
    args = ap.parse_args(argv)
    a, b = (json.loads(p.read_text()) for p in (args.a, args.b))

    totals: Counter[str] = Counter()
    for key in sorted(a.keys() - b.keys()):
        totals["(chip removed)"] += 1
        if not args.summary:
            print(f"{key}: removed")
    for key in sorted(b.keys() - a.keys()):
        totals["(chip added)"] += 1
        if not args.summary:
            print(f"{key}: added")
    for key in sorted(a.keys() & b.keys()):
        props = sorted(a[key].keys() | b[key].keys())
        for prop in (p for p in props if not args.only or p in args.only):
            lines = changes(a[key].get(prop), b[key].get(prop))
            if not lines:
                continue
            totals[prop] += 1
            if not args.summary:
                print(f"{key} {prop}:")
                for line in lines:
                    print(f"    {line}")

    print(f"{len(a)} chips before, {len(b)} after")
    for prop, n in sorted(totals.items()):
        print(f"{n:6} {prop}")
    return 1 if totals else 0


if __name__ == "__main__":
    sys.exit(main())
