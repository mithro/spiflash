"""Load the database and answer questions of it."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Any

from .model import Flash, Record, name_matches, parse_id, strip_continuation

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class Manufacturer:
    """A JEP106 manufacturer: ``bank`` counts the 0x7f continuation codes
    before ``id`` (0 for the first bank); ``id`` includes the parity bit, as
    a chip sends it."""

    bank: int
    id: int
    name: str


def _read(name: str) -> dict[str, Any]:
    text = resources.files("spiflash").joinpath("data", name).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    if data.get("format") != 1:
        raise ValueError(f"{name}: unsupported format {data.get('format')!r}")
    return data


def _family(rec: Record) -> str:
    """Records are grouped by how their id is read: every JEDEC read-id
    variant (the NAND ones send a dummy or address byte first, but answer
    the same bytes) together, each legacy command on its own."""
    return "jedec" if rec.is_jedec else (rec.id_method or "none")


class Database:
    """The records, grouped into one :class:`Flash` per chip id."""

    def __init__(
        self,
        records: Iterable[Record],
        manufacturers: Iterable[Manufacturer] = (),
        sources: dict[str, Any] | None = None,
    ) -> None:
        self.records: tuple[Record, ...] = tuple(records)
        self.manufacturers: tuple[Manufacturer, ...] = tuple(manufacturers)
        self.sources: dict[str, Any] = dict(sources or {})
        self._jep106 = {(m.bank, m.id): m.name for m in self.manufacturers}

        groups: dict[tuple[str, str, bytes], list[Record]] = defaultdict(list)
        banks: dict[tuple[str, str, bytes], Counter[int]] = defaultdict(Counter)
        for r in self.records:
            if r.id is None:
                continue
            bank, core = strip_continuation(r.id)
            key = (r.type, _family(r), core)
            groups[key].append(r)
            banks[key][bank] += 1
        self.flashes: tuple[Flash, ...] = tuple(
            Flash(core, typ, tuple(recs), banks[(typ, fam, core)].most_common(1)[0][0], fam)
            for (typ, fam, core), recs in sorted(groups.items(), key=lambda kv: (kv[0][2], kv[0]))
        )

    @classmethod
    def load(cls) -> Database:
        """The database shipped in this package."""
        recs = _read("records.json")["records"]
        mfrs = _read("manufacturers.json")["manufacturers"]
        return cls(
            (Record.from_json(r) for r in recs),
            (Manufacturer(m["bank"], m["id"], m["name"]) for m in mfrs),
            _read("sources.json")["sources"],
        )

    def lookup(
        self,
        chip_id: str | bytes | int | Iterable[int],
        *,
        type: str | None = None,
        method: str = "jedec",
    ) -> list[Flash]:
        """The chips answering ``chip_id``.

        Give the bytes a chip sent to JEDEC read-id (0x9F): ``"ef4018"``,
        ``b"\\xef\\x40\\x18"``, ``0xef4018``. Leading 0x7f continuation codes
        are ignored, since many chips leave them out. Bytes past the id narrow
        the answer to the variants whose extended id agrees (an S25FL128S
        answers ``01 20 18 4d 01 80``). ``method`` selects a legacy id
        instead (``"rems"``, ``"res1"``, ``"res2"``, ``"at25f"``).

        More than one :class:`Flash` comes back only when a NOR and a NAND
        part share the bytes, or ids of different lengths both fit."""
        _bank, core = strip_continuation(parse_id(chip_id))
        found = []
        for f in self.flashes:
            if f.family != method or (type is not None and f.type != type):
                continue
            if core[: len(f.id)] == f.id:
                ext = core[len(f.id) :]
                found.append(f.with_ext_id(ext) if ext else f)
        return sorted(found, key=lambda f: -len(f.id))

    def find(self, name: str) -> list[Flash]:
        """The chips whose part names match ``name``, best first.

        Exact names (and flashrom wildcards: ``W25Q128.V`` matches
        ``W25Q128JV``) come first, then parts whose name starts with ``name``
        (``w25q128`` finds the FV and the JV), then parts named by a prefix of
        ``name`` (Linux's generic ``w25q128`` for ``W25Q128JVSIQ``)."""
        q = name.strip().upper()
        if not q:
            return []
        ranked: list[tuple[int, int, Flash]] = []
        for i, f in enumerate(self.flashes):
            best = None
            for part in f.names:
                if name_matches(part, q):
                    rank = 0
                elif part.startswith(q):
                    rank = 1
                elif len(part) >= 4 and name_matches(part, q, prefix=True):
                    rank = 2
                else:
                    continue
                best = rank if best is None else min(best, rank)
            if best is not None:
                ranked.append((best, i, f))
        ranked.sort(key=lambda t: (t[0], t[1]))
        return [f for _, _, f in ranked]

    def jep106(self, manufacturer_id: int, bank: int = 0) -> str | None:
        """The JEP106 name of a manufacturer id byte (with its parity bit)."""
        return self._jep106.get((bank, manufacturer_id))

    def by_manufacturer(self, name: str) -> list[Flash]:
        """Every chip whose manufacturer is ``name`` (any upstream spelling)."""
        from .vendors import canonical

        want = (canonical(name) or name).lower()
        return [f for f in self.flashes if (f.manufacturer or "").lower() == want]


@cache
def database() -> Database:
    """The shipped database, loaded once."""
    return Database.load()
