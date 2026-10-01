"""The common record every extractor produces.

One record is one entry of one upstream table, as that upstream describes it:
nothing is merged or corrected here. ``spiflash`` groups the records by the
id a chip answers, at load time.

A fact is stored once. What follows from a record's stored fields is not
stored: :mod:`spiflash.derive` works it out at load (the id operation from
``id_method``, an erase operation from each eraser, everything its SFDP
tables say), and :func:`make` drops a stored value that duplicates it.

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
    Bytes; ``None`` where the upstream reads it from SFDP, and where the
    record's own SFDP tables give the same (``sfdp``, ``sfdp_tables``).
``page_size``
    The program page, in bytes; ``None`` where its SFDP tables give the
    same.
``erasers``
    ``[{"opcode": 0x20, "blocks": [[4096, 4096]]}, ...]``, without those its
    SFDP tables give. An upstream that
    gives an erase block size without a layout (Linux's and U-Boot's sector
    size, openFPGALoader's ``sector_erase``, a SPI NAND block) gives the
    eraser it describes (:func:`spiflash.derive.block_eraser`): the record's
    sector size is derived from its erasers
    (:func:`spiflash.derive.sector_size`), never stored.
``features``
    Normalised capability names: :class:`spiflash.enums.Feature` values,
    the ones the entry states (a record's ``feature_claims``) and nothing
    else implies: :func:`spiflash.derive.features` gives those from the
    operations, erasers, size and SFDP tables, and :func:`make` drops a
    claim it gives.
``flags``
    The upstream's raw flag and feature names that no field holds: a token
    a ``via`` holds (the record's, or an operation's) is left out.
``via``
    Which upstream token gave a stored value that has no other provenance,
    by key: ``feature:<feature>`` for a feature claim
    (``{"feature:qpi": "QPIEnable"}``), ``<field>`` for a field set from one
    token, ``<field>.<component>`` for one part of a composite field, and
    ``<field>:<member>`` for one member of a set-valued field (an eraser:
    ``erasers:0x20``); several tokens are joined with ``"; "``, and a token
    is under one key (``erasers`` for one that gives several erasers). Only
    the fields of :data:`VIA_FIELDS` that the record fills have keys; fields
    every record of a source fills from the same place (``size``, ``name``,
    ...) need none, and operations have their own ``via``.
``voltage``
    ``[min_mV, max_mV]``.
``opcodes``
    The operations the entry states (a record's ``opcode_claims``):
    ``[{"op": "READ_1_1_4", "via": "SPI_NOR_QUAD_READ"}, ...]``, ``op`` a name
    in ``spiflash.opcodes.OPERATIONS`` (which gives the opcode) and ``via``
    the upstream flag, field or default behind it. Those
    :func:`spiflash.derive.opcodes` gives are not stored (its SFDP tables'
    among them). A driver default (an operation the upstream's driver
    issues to every part, or every part of a class, whatever the entry
    says) has ``"assumed": true``, and implies no capability. A use may
    give the part's ``"dummy_clocks"`` (none does yet).
``sfdp``
    Hex of the part's SFDP (JESD216) area, where the upstream carries a
    dump of it (QEMU's flash model does); :mod:`spiflash.sfdp` decodes it.
    Its facts (:meth:`spiflash.sfdp.Sfdp.facts`) are derived at load: a
    value the upstream states outside the dump is stored only where it
    differs from the dump's (:meth:`spiflash.model.Record.sfdp_disagreements`).
``sfdp_tables``
    The part's SFDP parameter tables by id, each in hex, where the upstream
    copies them without the area around them (Zephyr's ``sfdp-bfp``,
    ``sfdp-ff05`` and ``sfdp-ff84``): ``{"ff00": "e520...", "ff84": ...}``,
    ``{}`` when it has none. A record has this or ``sfdp``, not both, and
    derives from it as from ``sfdp``.
``tested``
    The upstream's test status, verbatim.
``notes``
    Comments the upstream attached to the entry or its id.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from spiflash import derive
from spiflash.enums import Feature, FlashType, OperationKind, Source
from spiflash.model import Record as Model
from spiflash.opcodes import OPERATIONS, OpcodeUse

if TYPE_CHECKING:
    from collections.abc import Iterable

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
    "erasers",
    "features",
    "flags",
    "via",
    "voltage",
    "opcodes",
    "sfdp",
    "sfdp_tables",
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


#: The fields a ``via`` key may name: those holding a value one upstream
#: token can give. Not the identity every record fills from one place, nor
#: ``features`` (a claim has its own ``feature:<feature>`` key) and
#: ``opcodes`` (an operation has its own ``via``), nor the residue.
VIA_FIELDS = frozenset(KEYS) - {
    "source",
    "file",
    "line",
    "name",
    "features",
    "flags",
    "via",
    "opcodes",
    "notes",
}

#: The components a ``<field>.<component>`` key may name, by field: none yet.
VIA_COMPONENTS: dict[str, frozenset[str]] = {}

# A via key: feature:<feature>, <field>, <field>.<component> or <field>:<member>.
_VIA_KEY = re.compile(r"feature:([a-z0-9_]+)|([a-z_]+)(?:\.([a-z_]+))?(?::([a-z0-9_]+))?")


def _eraser_members(rec: Record) -> set[str]:
    """The ``erasers:<member>`` names of a record's erasers: ``0x20``, ..."""
    return {f"0x{e['opcode']:02x}" for e in rec["erasers"] or () if e["opcode"] is not None}


def check_via(rec: Record) -> None:
    """Raise for a ``via`` key that names no claimed feature, no field
    :data:`VIA_FIELDS` allows, a field the record leaves empty, a component
    the field does not have, or an eraser the record does not have."""
    for key in rec["via"]:
        m = _VIA_KEY.fullmatch(key)
        if m is None:
            ok = False
        elif m[1]:
            ok = m[1] in rec["features"]
        else:
            field, component, member = m[2], m[3], m[4]
            ok = field in VIA_FIELDS and rec[field] not in (None, [], "", {})
            if component is not None:
                ok = ok and component in VIA_COMPONENTS.get(field, ())
            if member is not None:
                ok = ok and field == "erasers" and member in _eraser_members(rec)
        if not ok:
            msg = f"bad via key {key!r}"
            raise ValueError(msg)


def tokens(via: str) -> list[str]:
    """The upstream tokens a ``via`` joins."""
    return via.split("; ")


def holds(via: str, token: str) -> bool:
    """Whether ``via`` holds upstream ``token``: as one of its tokens, or as
    a word in one (``"read_cmd_4 (FEA_4BIT_READ)"`` holds ``FEA_4BIT_READ``)."""
    word = re.compile(rf"(?<![\w=]){re.escape(token)}(?![\w=])")
    return any(t == token or word.search(t) for t in tokens(via))


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
    are stored sorted and unique, without the flags a ``via`` holds
    (:func:`holds`). A feature claim's ``via`` keeps only the tokens no
    operation's ``via`` holds: the operation is the claim's provenance. Each
    token is under one ``via`` key, and no note repeats one. The name's
    part numbers are upper case (:func:`part_case`). The capability claims
    :func:`spiflash.derive.features` gives, with their ``via`` keys, and the
    operations :func:`spiflash.derive.opcodes` gives are dropped, as the
    record derives them at load; so are a size, page size or eraser its SFDP
    tables give (:func:`_drop_sfdp`). A sector size is refused: give the
    eraser (:func:`spiflash.derive.block_eraser`). A record cannot carry
    both a whole SFDP dump and copied tables."""
    if "sector_size" in fields:
        msg = "sector_size is derived from the erasers: give the eraser instead"
        raise KeyError(msg)
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
        sfdp_tables={},
    )
    rec.update(fields)
    if rec["sfdp"] and rec["sfdp_tables"]:
        msg = f"{source} {name}: a whole SFDP dump and copied tables"
        raise ValueError(msg)
    rec["features"] = sorted(set(rec["features"]))
    check_via(rec)
    _drop_sfdp(rec)
    claimed = set(rec["features"])
    _drop_implied(rec)
    ops = [o["via"] for o in rec["opcodes"]]
    via: dict[str, list[str]] = {}
    for key, value in rec["via"].items():
        by_op = key.startswith("feature:")
        kept = [t for t in tokens(value) if not (by_op and any(holds(v, t) for v in ops))]
        if kept:
            via[key] = kept
    vias = ops + ["; ".join(v) for v in via.values()]
    flags = {f for f in rec["flags"] if not any(holds(v, f) for v in vias)}
    flags |= _drop_derived(rec, set(rec["flags"]) - flags, via)
    rec["flags"] = sorted(flags)
    rec["via"] = {key: "; ".join(via[key]) for key in sorted(via)}
    check_via(rec)
    _check_once(rec)
    lost = claimed - Model.from_json(rec).features
    if lost:
        msg = f"{rec['source']} {rec['name']}: dropping implied claims lost {sorted(lost)}"
        raise AssertionError(msg)
    return rec


def _drop_implied(rec: Record) -> None:
    """Drop the capability claims ``rec``'s other fields imply
    (:func:`spiflash.derive.features`), and their ``via`` keys: a token only
    such a key held stays a flag, unless something else holds it."""
    implied = derive.features(Model.from_json(rec))
    rec["features"] = [f for f in rec["features"] if f not in implied]
    dropped = {f"feature:{f}" for f in implied}
    rec["via"] = {k: v for k, v in rec["via"].items() if k not in dropped}


def _drop_sfdp(rec: Record) -> None:
    """Drop the size, page size and erasers ``rec`` states that its own SFDP
    tables give the same (:meth:`spiflash.sfdp.Sfdp.facts`): the record
    derives them from the tables at load. A value that differs stays, as the
    upstream's own (a :meth:`spiflash.model.Record.sfdp_disagreements`). The
    ``via`` token of a dropped eraser goes back to the flags: no field holds
    it now."""
    if not (rec["sfdp"] or rec["sfdp_tables"]):
        return
    facts = Model.from_json(rec).sfdp_facts
    if facts is None:
        return
    for name in ("size", "page_size"):
        if rec[name] is not None and rec[name] == getattr(facts, name):
            rec[name] = None
    given = [e.to_json() for e in Model.from_json(rec).sfdp_erasers]
    kept = [e for e in rec["erasers"] or () if e not in given]
    rec["erasers"] = kept or None
    members = _eraser_members(rec)
    for key in [k for k in rec["via"] if k.startswith("erasers")]:
        _, _, member = key.partition(":")
        if (member and member not in members) or not kept:
            rec["flags"] = [*rec["flags"], *tokens(rec["via"].pop(key))]


def _same_use(stored: dict[str, Any], derived: OpcodeUse) -> bool:
    """Whether a stored use is one ``derived`` gives: the same operation,
    with no dummy clocks of its own or the same ones."""
    clocks = stored.get("dummy_clocks")
    return stored["op"] == derived.op and clocks in (None, derived.dummy_clocks)


def _drop_derived(rec: Record, held: set[str], via: dict[str, list[str]]) -> set[str]:
    """Drop the operations ``rec`` derives at load (a stored use with dummy
    clocks other than the derived one's stays). A flag (of ``held``) that
    only a dropped erase operation's via held moves to ``via``, under its
    eraser, ``erasers:0x<opcode>``, or ``erasers`` for a token that gives
    several; where the record stores no such eraser (its SFDP tables give
    it), the flag stays a flag, and is returned."""
    model = Model.from_json(rec)
    derived = derive.opcodes(model)
    dropped = [o for o in rec["opcodes"] if any(_same_use(o, u) for u in derived)]
    kept = [o["via"] for o in rec["opcodes"] if o not in dropped]
    kept += ["; ".join(v) for v in via.values()]
    moved: dict[str, list[str]] = {}
    flags: set[str] = set()
    members = _eraser_members(rec)
    for o in dropped:
        for t in held:
            if holds(o["via"], t) and not any(holds(v, t) for v in kept):
                if OPERATIONS[o["op"]].kind is not OperationKind.ERASE:
                    msg = f"{rec['source']} {rec['name']}: {t} would be lost with {o['op']}"
                    raise ValueError(msg)
                member = f"0x{OPERATIONS[o['op']].opcode:02x}"
                if member not in members:
                    flags.add(t)
                elif f"erasers:{member}" not in moved.setdefault(t, []):
                    moved[t].append(f"erasers:{member}")
    for t, keys in moved.items():
        key = keys[0] if len(keys) == 1 else "erasers"
        via.setdefault(key, []).append(t)
    stated = {o["op"] for o in rec["opcodes"]}
    rec["opcodes"] = [o for o in rec["opcodes"] if o not in dropped]
    lost = stated - {u.op for u in Model.from_json(rec).opcodes}
    if lost:
        msg = f"{rec['source']} {rec['name']}: dropping derived operations lost {sorted(lost)}"
        raise AssertionError(msg)
    return flags


def _check_once(rec: Record) -> None:
    """Raise for a token under two ``via`` keys, or a note that is one."""
    seen: dict[str, str] = {}
    for key, value in rec["via"].items():
        for t in tokens(value):
            if seen.setdefault(t, key) != key:
                msg = f"{rec['source']} {rec['name']}: {t!r} under {seen[t]} and {key}"
                raise ValueError(msg)
    repeated = set(rec["notes"]) & set(seen)
    if repeated:
        msg = f"{rec['source']} {rec['name']}: notes repeat via {sorted(repeated)}"
        raise ValueError(msg)
