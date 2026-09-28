"""Write src/spiflash/data/datasheets.json from a datasheet manifest.

The manifest is the ``datasheets.jsonl`` of a spiflash-pdfs checkout: one line
per downloaded datasheet, with where it came from, what it is, and the chip
ids its parts answer. The PDFs stay there (they are their publishers'
copyrighted documents); only the links and what they cover come here.

    uv run tools/import_datasheets.py [../spiflash-pdfs/datasheets.jsonl]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from spiflash import Database, Record
from update_db import DATA, FORMAT, REPO, json_lines

MANIFEST = REPO.parent / "spiflash-pdfs" / "datasheets.jsonl"


def entry(d: dict[str, Any]) -> dict[str, Any]:
    """A manifest line, as the package ships it: the link and what it covers,
    without the local file's path."""
    confirmed = d.get("id_confirmed") or {}
    return {
        "url": d["url"],
        "title": d["title"],
        "revision": d.get("revision"),
        "date": d.get("document_date"),
        "official": d["official"],
        "also_at": d.get("also_at") or [],
        "parts": d["parts"],
        "ids": d["ids"],
        "confirmed": sorted(i for i, shown in confirmed.items() if shown),
        "sha256": d["sha256"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("manifest", nargs="?", type=Path, default=MANIFEST)
    args = ap.parse_args(argv)

    lines = args.manifest.read_text(encoding="utf-8").splitlines()
    sheets = sorted(
        (entry(json.loads(line)) for line in lines if line.strip()), key=lambda d: d["url"]
    )

    records = json.loads((DATA / "records.json").read_text(encoding="utf-8"))["records"]
    known = {f.key for f in Database(Record.from_json(r) for r in records).flashes}
    unknown = sorted({i for d in sheets for i in d["ids"]} - known)
    if unknown:
        print(f"ids not in the database: {', '.join(unknown)}", file=sys.stderr)
        return 1

    (DATA / "datasheets.json").write_text(
        json_lines({"format": FORMAT}, "datasheets", sheets), encoding="utf-8"
    )
    ids = {i for d in sheets for i in d["ids"]}
    print(f"{len(sheets)} datasheets for {len(ids)} of {len(known)} chip ids", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
