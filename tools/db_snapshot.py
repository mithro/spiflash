#!/usr/bin/env python3
"""Write what the database answers for every chip, as sorted JSON, so two
commits can be compared with :repo:`tools/db_diff.py`::

    uv run tools/db_snapshot.py after.json
    uv run tools/db_snapshot.py --src ../base/src before.json
    uv run tools/db_diff.py before.json after.json

Per chip: :meth:`Flash.to_json() <spiflash.model.Flash.to_json>` one key at a
time, every consensus value, who vouches for each operation and why, and
:meth:`~spiflash.model.Flash.feature_sources` for each feature. Only the
public API is used, so it runs on any commit; ``--src`` takes the package
from another checkout's ``src``.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

#: The consensus values a chip has, compared one by one.
VALUES = ("manufacturer", "name", "names", "size", "page_size", "sector_size", "voltage")


def plain(value: Any) -> Any:
    """``value`` as plain JSON: enums as their values, named tuples and
    dataclasses as objects, bytes as hex, sets sorted."""
    if isinstance(value, enum.Enum):
        return plain(value.value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, tuple) and hasattr(value, "_asdict"):
        return {k: plain(v) for k, v in value._asdict().items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(plain(k)): plain(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        return sorted(plain(v) for v in value)
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    return value


def chip(flash: Any) -> dict[str, Any]:
    """Everything the public API answers for one chip, by property."""
    out: dict[str, Any] = {f"json.{k}": plain(v) for k, v in flash.to_json().items()}
    out |= {k: plain(getattr(flash, k)) for k in VALUES}
    out["features"] = sorted(flash.features)
    out["feature_sources"] = {
        str(f): plain(flash.feature_sources(f)) for f in sorted(flash.features)
    }
    out["opcodes"] = {name: plain(o.sources) for name, o in flash.opcodes.items()}
    out["opcode_reasons"] = {name: plain(o.because) for name, o in flash.opcodes.items()}
    out["conflicts"] = plain(flash.conflicts)
    out["by_ext_id"] = {a: plain(flash.by_ext_id(a)) for a in VALUES[3:]}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("out", type=Path, help="where to write the snapshot")
    ap.add_argument("--src", type=Path, default=REPO / "src", help="the spiflash package's root")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(args.src.resolve()))
    import spiflash  # noqa: PLC0415 - from --src, so only after the path is set

    chips: dict[str, Any] = {}
    for flash in spiflash.flashes():
        chips[f"{flash.type}:{flash.key}"] = chip(flash)
    args.out.write_text(json.dumps(chips, indent=1, sort_keys=True) + "\n")
    print(f"{len(chips)} chips from {Path(spiflash.__file__).parent}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
