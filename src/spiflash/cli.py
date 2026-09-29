"""The ``spiflash`` command.

spiflash id ef4018            which chip answers this JEDEC id?
spiflash find w25q128jv       which ids does this part answer?
spiflash find 'W25Q128*'      every part a glob matches (or --regex '^W25Q(64|128)J')
spiflash find --nearest W25Q128JVSIQ   the closest part names (a marking, a typo)
spiflash list --manufacturer winbond
spiflash opcodes ef4018       which opcodes does it support? (an id or a part name)
spiflash sfdp sfdp.bin        decode an SFDP dump (a file, hex, or a chip with a shipped one)
spiflash jep106 c2            the JEP106 manufacturer of an id byte
spiflash sources              where the data came from
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from . import __version__
from .db import Database, NameMatch, database
from .model import Flash, SfdpDump, parse_id
from .sfdp import SIGNATURE, Sfdp
from .sfdp import parse as parse_sfdp
from .units import human_size

if TYPE_CHECKING:
    from collections.abc import Sequence


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
    return f"{f.key}  {f.manufacturer or '?'}  {', '.join(f.names)}  ({f.type})"


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
    lines.extend(
        f"    sfdp: {sfdp_summary(d.tables)}  [{d.source}: {', '.join(d.parts)}]"
        for d in f.sfdp_dumps
    )
    # The best datasheet, or all of them with -v.
    lines.extend(f"    datasheet: {d.url}" for d in f.datasheets[: None if verbose else 1])
    if opcodes:
        lines.append("    opcodes:")
        lines.extend(opcode_table(f, verbose=verbose))
    return "\n".join(lines)


def sfdp_summary(s: Sfdp) -> str:
    """One line on a chip's SFDP dump: its revision and the tables in it."""
    tables = ", ".join(f"{h.name} {h.revision}" for h in s.headers)
    return f"{s.revision_name} ({tables})"


class _SfdpShown(NamedTuple):
    """A dump ``spiflash sfdp`` prints, and the chip it was shipped for
    (``None`` for a dump given directly)."""

    tables: Sfdp
    chip: Flash | None = None
    dump: SfdpDump | None = None

    def to_json(self) -> dict[str, Any]:
        if self.chip is None or self.dump is None:
            return self.tables.to_json()
        return {
            "chip": self.chip.key,
            "source": self.dump.source,
            "parts": list(self.dump.parts),
            **self.tables.to_json(),
        }


def _sfdp_input(db: Database, source: str) -> list[_SfdpShown]:
    """What ``spiflash sfdp`` was given: a file, ``-`` for stdin, hex bytes,
    or a chip (id or part name) whose shipped dumps to show. A part name
    shows that part's dump where parts sharing its id have different ones;
    an id shows them all."""
    if source == "-":
        return [_SfdpShown(parse_sfdp(sys.stdin.buffer.read()))]
    try:
        data = parse_id(source)
    except ValueError:
        data = b""
    if data[:4] == SIGNATURE:
        return [_SfdpShown(parse_sfdp(data))]
    try:
        is_file = Path(source).is_file()
    except OSError:  # a long hex string is not a usable file name
        is_file = False
    if is_file:
        return [_SfdpShown(parse_sfdp(Path(source).read_bytes()))]
    part = source.strip().upper()
    out: list[_SfdpShown] = []
    for f in _resolve(db, source):
        dumps = f.sfdp_dumps
        named = [d for d in dumps if part in d.parts]
        out.extend(_SfdpShown(d.tables, f, d) for d in named or dumps)
    return out


def _emit(
    found: Sequence[Flash], *, as_json: bool, verbose: bool = False, opcodes: bool = False
) -> int:
    if as_json:
        json.dump([f.to_json() for f in found], sys.stdout, indent=1)
        sys.stdout.write("\n")
    else:
        print("\n\n".join(describe(f, verbose=verbose, opcodes=opcodes) for f in found))
    return 0 if found else 1


def nearest_line(m: NameMatch, width: int) -> str:
    """One chip :meth:`Database.find_nearest` found: the score, the part
    name, the id, the maker and why."""
    f = m.flash
    return f"{m.score:3}  {m.name:<{width}}  {f.key:10} {f.manufacturer or '?':14} {m.reason}"


def _find(db: Database, args: argparse.Namespace) -> int:
    """``spiflash find``: by name, glob, regular expression or nearness."""
    if args.nearest:
        near = db.find_nearest(args.name, args.count)
        if args.json:
            json.dump([m.to_json() for m in near], sys.stdout, indent=1)
            sys.stdout.write("\n")
            return 0 if near else 1
        width = max((len(m.name) for m in near), default=0)
        for m in near:
            print(nearest_line(m, width))
            if args.verbose or args.opcodes:
                print(describe(m.flash, verbose=args.verbose, opcodes=args.opcodes) + "\n")
        return 0 if near else 1
    if args.regex:
        found = db.find_regex(args.name)
    elif args.glob or any(c in args.name for c in "*?["):
        found = db.find_glob(args.name)
    else:
        found = db.find(args.name)
        near = [] if found else db.find_nearest(args.name, 3)
        if near:
            close = ", ".join(f"{m.name} ({m.flash.key})" for m in near)
            print(f"spiflash: no part {args.name}; the closest: {close}", file=sys.stderr)
    return _emit(found, as_json=args.json, verbose=args.verbose, opcodes=args.opcodes)


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
    p.add_argument("name", help="a part name; with * ? or [...] in it, a glob (W25Q128*)")
    how = p.add_mutually_exclusive_group()
    how.add_argument(
        "--regex", action="store_true", help="NAME is a regular expression: ^W25Q(64|128)J[VW]$"
    )
    how.add_argument("--glob", action="store_true", help="NAME is a glob: MX25?12835F, S25FL*S")
    how.add_argument(
        "--nearest", action="store_true", help="list the chips with the closest part names"
    )
    p.add_argument("-n", "--count", type=int, default=10, help="how many --nearest lists (10)")

    p = sub.add_parser("list", parents=[common], help="list chips")
    p.add_argument("--manufacturer")
    p.add_argument("--type", choices=["nor", "nand"])

    p = sub.add_parser("opcodes", parents=[common], help="the opcodes a chip supports")
    p.add_argument("query", help="a JEDEC id (ef4018) or a part name (W25Q128JV)")

    p = sub.add_parser("sfdp", help="decode SFDP (JESD216) tables")
    p.add_argument(
        "source",
        help="a dump file (/sys/bus/spi/devices/*/spi-nor/sfdp), - for stdin, hex bytes, "
        "or a chip (a JEDEC id or part name) the database has a dump for",
    )
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument("-v", "--verbose", action="store_true", help="print every table's dwords")

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
            return _find(db, args)
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
        if args.command == "sfdp":
            shown = _sfdp_input(db, args.source)
            if not shown:
                print(f"spiflash: no SFDP dump for {args.source}", file=sys.stderr)
                return 1
            if args.json:
                json.dump([d.to_json() for d in shown], sys.stdout, indent=1)
                sys.stdout.write("\n")
                return 0
            for d in shown:
                if d.chip is not None and d.dump is not None:
                    print(header(d.chip))
                    print(f"    from {d.dump.source}: {', '.join(d.dump.parts)}")
                print(d.tables.describe(verbose=args.verbose))
                print()
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
