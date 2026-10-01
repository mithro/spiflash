"""The common record every extractor produces.

One record is one entry of one upstream table, as that upstream describes it:
nothing is merged or corrected here. ``spiflash`` groups the records by the
id a chip answers, at load time.

A fact is stored once. What follows from a record's stored fields is not
stored: :mod:`spiflash.derive` works it out at load (the id operation from
``id_method``, an erase operation from each eraser), and :func:`make` drops
a stored value that duplicates it.

Fields (``None`` / empty when the upstream does not say):

``source``
    Which upstream: ``linux``, ``u-boot``, ``flashrom``, ...
``file``, ``line``
    Where in that upstream's tree the entry is.
``type``
    ``nor`` or ``nand``.
``vendor``
    The upstream's own vendor name for the entry.
``name``
    The upstream's part name, verbatim.
``id``
    Hex of the id bytes the chip answers (to JEDEC read-id, 0x9f, unless
    ``id_method`` says otherwise), continuation codes included.
``ext_id``
    Hex of further id bytes some upstreams match on.
``id_method``
    ``rdid`` (JEDEC 0x9f), or the legacy probe: ``rems``, ``res1``, ``res2``,
    ``at25f``, ``st95``, ...
``size``
    Bytes; ``None`` where the upstream reads it from SFDP.
``page_size``
    The program page, in bytes.
``sector_size``
    The erase unit the upstream uses by default, in bytes.
``erasers``
    ``[{"opcode": 0x20, "blocks": [[4096, 4096]]}, ...]``.
``features``
    Normalised capability names: :class:`spiflash.enums.Feature` values,
    the ones the entry states (a record's ``feature_claims``).
``flags``
    The upstream's raw flag and feature names that no field holds: a token
    a ``via`` holds (the record's, or an operation's) is left out.
``via``
    Which upstream token gave a stored value that has no other provenance,
    by key: ``feature:<feature>`` for a feature claim
    (``{"feature:qpi": "QPIEnable"}``), ``<field>`` for a field set from one
    token, ``<field>.<component>`` for one part of a composite field, and
    ``<field>:<member>`` for one member of a set-valued field; several
    tokens are joined with ``"; "``. Fields every record of a source fills
    from the same place (``size``, ``name``, ...) have none, and nor do
    operations, whose own ``via`` says.
``voltage``
    ``[min_mV, max_mV]``.
``opcodes``
    The operations the entry states (a record's ``opcode_claims``):
    ``[{"op": "READ_1_1_4", "via": "SPI_NOR_QUAD_READ"}, ...]``, ``op`` a name
    in ``spiflash.opcodes.OPERATIONS`` (which gives the opcode) and ``via``
    the upstream flag, field or default behind it. Those
    :func:`spiflash.derive.opcodes` gives are not stored.
``sfdp``
    Hex of the part's SFDP (JESD216) area, where the upstream carries a
    dump of it (QEMU's flash model does); :mod:`spiflash.sfdp` decodes it.
``tested``
    The upstream's test status, verbatim.
``notes``
    Comments the upstream attached to the entry or its id.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from spiflash import derive
from spiflash.enums import Feature, FlashType, Source
from spiflash.model import Record as Model
from spiflash.opcodes import OPERATIONS

if TYPE_CHECKING:
    from collections.abc import Iterable

# The feature a uniform erase block of each size gives.
ERASE_FEATURES = {
    4096: Feature.ERASE_4K.value,
    32 * 1024: Feature.ERASE_32K.value,
    64 * 1024: Feature.ERASE_64K.value,
}

Record = dict[str, Any]

KEYS = (
    "source",
    "file",
    "line",
    "type",
    "vendor",
    "name",
    "id",
    "ext_id",
    "id_method",
    "size",
    "page_size",
    "sector_size",
    "erasers",
    "features",
    "flags",
    "via",
    "voltage",
    "opcodes",
    "sfdp",
    "tested",
    "notes",
)


def part_case(name: str) -> str:
    """An upstream's name with its part numbers in upper case, as datasheets
    write them: ``"w25q128fv/jv"`` is ``"W25Q128FV/JV"``, ``"n25q256 1.8v"``
    is ``"N25Q256 1.8V"``. A word with a digit in it is part of a part number
    (or a voltage); the first word is the part; other words ("Uniform
    128 kB Sectors"), and what is in parentheses ("W25Q128JW(3MHz)",
    "EN25B10(Bottom Boot)"), keep their case; text straight after the
    parentheses is still the word before them ("S79FS01GS(one die)_ES")."""
    out = []
    first = True
    upper = False  # whether the last word outside parentheses was upper-cased
    after_group = False
    for piece in re.split(r"(\([^()]*\))", name):
        if piece.startswith("("):
            out.append(piece)
            after_group = True
            continue
        words = piece.split(" ")
        for i, w in enumerate(words):
            if not w:
                continue
            upper = (upper if i == 0 and after_group else first) or any(c.isdigit() for c in w)
            if upper:
                words[i] = w.upper()
            first = False
        after_group = False
        out.append(" ".join(words))
    return "".join(out)


# A via key: feature:<feature>, <field>, <field>.<component> or <field>:<member>.
_VIA_KEY = re.compile(r"feature:([a-z0-9_]+)|([a-z_]+)(?:\.[a-z_]+)?(?::[a-z0-9_]+)?")


def check_via(via: dict[str, str], features: Iterable[str]) -> None:
    """Raise for a ``via`` key that names no field, nor one of the record's
    ``features``."""
    for key in via:
        m = _VIA_KEY.fullmatch(key)
        if m is None or (m[1] not in features if m[1] else m[2] not in KEYS):
            msg = f"bad via key {key!r}"
            raise ValueError(msg)


def tokens(via: str) -> list[str]:
    """The upstream tokens a ``via`` joins."""
    return via.split("; ")


def feature_via(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """The ``via`` of feature claims, from (feature, upstream token) pairs:
    ``{"feature:qpi": "FEATURE_QPI"}``."""
    out: dict[str, list[str]] = {}
    for feature, token in pairs:
        given = out.setdefault(f"feature:{feature}", [])
        if token not in given:
            given.append(token)
    return {key: "; ".join(given) for key, given in out.items()}


def make(source: str, file: str, line: int, name: str, **fields: Any) -> Record:
    """A record with every key present, in the canonical order.

    ``features`` and ``flags`` may come in any order and with repeats; they
    are stored sorted and unique, without the flags a ``via`` holds. A
    feature claim's ``via`` keeps only the tokens no operation's ``via``
    holds: the operation is the claim's provenance. The name's part
    numbers are upper case (:func:`part_case`). The operations
    :func:`spiflash.derive.opcodes` gives are dropped, as the record
    derives them at load."""
    unknown = set(fields) - set(KEYS)
    if unknown:
        msg = f"unknown record fields: {sorted(unknown)}"
        raise KeyError(msg)
    Source(source)  # raises for a source spiflash does not know
    features = fields.get("features") or []
    bad = set(features) - {f.value for f in Feature}
    if bad:
        msg = f"unknown features {sorted(bad)} for {source} {name}"
        raise ValueError(msg)
    rec: Record = dict.fromkeys(KEYS)
    rec.update(
        source=source,
        file=file,
        line=line,
        name=part_case(name),
        type=FlashType.NOR.value,
        id_method="rdid",
        features=[],
        flags=[],
        notes=[],
        opcodes=[],
        via={},
    )
    rec.update(fields)
    rec["features"] = sorted(set(rec["features"]))
    check_via(rec["via"], rec["features"])
    flags = set(rec["flags"])
    by_ops = {t for o in rec["opcodes"] for t in tokens(o["via"])}
    via: dict[str, list[str]] = {}
    for key, value in rec["via"].items():
        kept = [t for t in tokens(value) if not (key.startswith("feature:") and t in by_ops)]
        if kept:
            via[key] = kept
    rec["flags"] = sorted(flags - by_ops - {t for v in via.values() for t in v})
    _drop_derived(rec, flags, via)
    rec["via"] = {key: "; ".join(via[key]) for key in sorted(via)}
    return rec


def _drop_derived(rec: Record, flags: set[str], via: dict[str, list[str]]) -> None:
    """Drop the operations ``rec`` derives at load. An upstream flag that
    only a dropped operation's via held moves to ``via``, under the field
    the operation derives from: ``id_method``, or ``erasers:0x<opcode>``."""
    model = Model.from_json(rec)
    derived = {u.op for u in derive.opcodes(model)}
    kept = [o for o in rec["opcodes"] if o["op"] not in derived]
    held = {t for o in kept for t in tokens(o["via"])} | {t for v in via.values() for t in v}
    id_op = derive.ID_OPERATION.get(model.id_method) if model.id_method else None
    for o in rec["opcodes"]:
        if o["op"] in derived:
            key = "id_method" if o["op"] == id_op else f"erasers:0x{OPERATIONS[o['op']].opcode:02x}"
            for t in tokens(o["via"]):
                if t in flags and t not in held and t not in via.get(key, []):
                    via.setdefault(key, []).append(t)
    stated = {o["op"] for o in rec["opcodes"]}
    rec["opcodes"] = kept
    lost = stated - {u.op for u in Model.from_json(rec).opcodes}
    if lost:
        msg = f"{rec['source']} {rec['name']}: dropping derived operations lost {sorted(lost)}"
        raise AssertionError(msg)
