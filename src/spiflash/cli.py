"""The ``spiflash`` command.

spiflash id ef4018            which chip answers this JEDEC id?
spiflash find w25q128jv       which ids does this part answer?
spiflash list --manufacturer winbond
spiflash opcodes ef4018       which opcodes does it support? (an id or a part name)
spiflash jep106 c2            the JEP106 manufacturer of an id byte
spiflash sources              where the data came from
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from . import __version__
from .db import Database, database
from .model import Flash, parse_id

if TYPE_CHECKING:
    from collections.abc import Sequence


def human_size(n: int | None) -> str:
    if n is None:
        return "?"
    for unit, scale in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if n >= scale and n % scale == 0:
            return f"{n // scale} {unit}"
    return f"{n} B"


def opcode_table(f: Flash, *, verbose: bool = False) -> list[str]:
    """The operations a chip supports, one per line: opcode, name, what it
    does, and who says so (with -v, why each source says so)."""
    if not f.opcodes:
        return ["    no opcodes known (the sources describe none for this part)"]
    width = max(len(o.name) for o in f.opcodes.values())
    lines = []
    for o in f.opcodes.values():
        head = f"    0x{o.opcode:02x}  {o.name:<{width}}  {o.operation.description}"
        if verbose:
            lines.append(head)
            lines.extend(f"          {src:15} {via}" for src, via in o.because)
        else:
            lines.append(f"{head}  [{', '.join(o.sources)}]")
    return lines


def header(f: Flash) -> str:
    """The first line describing a chip: its id, maker, part names and type."""
    ident = f.jedec_id if f.family == "jedec" else f"{f.family}:{f.id_hex}"
    return f"{ident}  {f.manufacturer or '?'}  {', '.join(f.names)}  ({f.type})"


def describe(f: Flash, *, verbose: bool = False, opcodes: bool = False) -> str:
    lines = [header(f)]
    detail = [f"size {human_size(f.size)}"]
    if f.page_size:
        detail.append(f"page {human_size(f.page_size)}")
    if f.sector_size:
        detail.append(f"sector {human_size(f.sector_size)}")
    if f.voltage:
        detail.append(f"{f.voltage[0] / 1000:g}-{f.voltage[1] / 1000:g} V")
    lines.append("    " + ", ".join(detail))
    if f.features:
        lines.append("    features: " + " ".join(sorted(f.features)))
    for attr, vals in f.conflicts.items():
        # A voltage prints as its (min, max) pair, not its NamedTuple repr.
        said = "; ".join(
            f"{tuple(v) if isinstance(v, tuple) else v} ({', '.join(s)})" for v, s in vals.items()
        )
        lines.append(f"    sources disagree on {attr}: {said}")
    if verbose:
        for r in f.records:
            ext = f" ext {r.ext_id.hex()}" if r.ext_id else ""
            lines.append(f"    {r.source:15} {r.name}{ext}  [{r.url}]")
    else:
        lines.append("    from: " + ", ".join(f.sources))
    if opcodes:
        lines.append("    opcodes:")
        lines.extend(opcode_table(f, verbose=verbose))
    return "\n".join(lines)


def _emit(
    found: Sequence[Flash], *, as_json: bool, verbose: bool = False, opcodes: bool = False
) -> int:
    if as_json:
        json.dump([f.to_json() for f in found], sys.stdout, indent=1)
        sys.stdout.write("\n")
    else:
        print("\n\n".join(describe(f, verbose=verbose, opcodes=opcodes) for f in found))
    return 0 if found else 1


def _resolve(db: Database, query: str) -> list[Flash]:
    """An id if ``query`` reads as one and matches, else a part name."""
    try:
        found = db.lookup(query)
    except ValueError:
        found = []
    return found or db.find(query)


def _parser() -> argparse.ArgumentParser:
    """The command line: one subcommand per question, sharing --json, -v and --opcodes."""
    ap = argparse.ArgumentParser(
        prog="spiflash",
        description="Look up SPI flash chips by JEDEC id or part name.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"spiflash {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print JSON")
    common.add_argument("-v", "--verbose", action="store_true", help="list every upstream entry")
    common.add_argument("--opcodes", action="store_true", help="list the supported opcodes")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("id", parents=[common], help="look up a JEDEC read-id (0x9F) answer")
    p.add_argument("id", help="hex bytes, e.g. ef4018 or 'ef 40 18'")
    p.add_argument("--method", default="jedec", help="legacy id: rems, res1, res2, at25f")
    p.add_argument("--type", choices=["nor", "nand"])

    p = sub.add_parser("find", parents=[common], help="look up a part name")
    p.add_argument("name")

    p = sub.add_parser("list", parents=[common], help="list chips")
    p.add_argument("--manufacturer")
    p.add_argument("--type", choices=["nor", "nand"])

    p = sub.add_parser("opcodes", parents=[common], help="the opcodes a chip supports")
    p.add_argument("query", help="a JEDEC id (ef4018) or a part name (W25Q128JV)")

    p = sub.add_parser("jep106", help="name the manufacturer of an id byte")
    p.add_argument("id", help="the id byte in hex, with any 7f continuation codes before it")

    sub.add_parser("sources", help="where the data came from")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    db = database()
    try:
        if args.command == "id":
            return _emit(
                db.lookup(args.id, flash_type=args.type, method=args.method),
                as_json=args.json,
                verbose=args.verbose,
                opcodes=args.opcodes,
            )
        if args.command == "find":
            return _emit(
                db.find(args.name), as_json=args.json, verbose=args.verbose, opcodes=args.opcodes
            )
        if args.command == "opcodes":
            found = _resolve(db, args.query)
            if args.json:
                return _emit(found, as_json=True)
            for f in found:
                print(header(f))
                print("\n".join(opcode_table(f, verbose=args.verbose)))
                print()
            return 0 if found else 1
        if args.command == "list":
            listed = list(
                db.by_manufacturer(args.manufacturer) if args.manufacturer else db.flashes
            )
            if args.type:
                listed = [f for f in listed if f.type == args.type]
            if args.json or args.verbose:
                return _emit(listed, as_json=args.json, verbose=args.verbose, opcodes=args.opcodes)
            for f in listed:
                print(
                    f"{f.jedec_id:10} {f.type:4} {f.manufacturer or '?':14} "
                    f"{human_size(f.size):>8}  {', '.join(f.names)}"
                )
            return 0
        if args.command == "jep106":
            data = parse_id(args.id)
            bank = len(data) - 1
            name = db.jep106(data[-1], bank)
            print(name or f"no JEP106 manufacturer 0x{data[-1]:02x} in bank {bank + 1}")
            return 0 if name else 1
        # sources
        for name, s in db.sources.items():
            print(
                f"{name:15} {s.commit[:12]} {s.date:%Y-%m-%d} {s.records:5}  {s.url} ({s.license})"
            )
        return 0
    except ValueError as e:
        print(f"spiflash: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
