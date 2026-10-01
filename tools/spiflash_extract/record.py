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
``quad_enable``
    Where the part's quad enable bit is, as the entry states it:
    ``{"register": "sr2", "bit": 1}`` (and ``"writability"`` where it is not
    ``rw``; :class:`spiflash.registers.RegisterBit`), or ``"none"`` where it
    says the part has none. A register is named by the command reading it
    (:class:`spiflash.registers.Register`). Not with ``quad_enable_requirement``,
    which gives the bit, nor where the record's SFDP tables give the same.
``quad_enable_requirement``
    JESD216's quad enable requirement, as the entry states it (Zephyr's
    ``"S2B1v1"``, ...; :class:`spiflash.registers.QuadEnableRequirement`),
    where its SFDP tables do not give the same.
``protection``
    Where its block-protection bits are, by role:
    ``{"bp0": {"register": "sr1", "bit": 2}, ..., "tb": {...}}``
    (:class:`spiflash.registers.Protection`); no two roles, nor a role and
    the quad enable bit, on one bit.
``oob_size``, ``planes``, ``max_bad_blocks``, ``ecc``
    A SPI NAND part's geometry, as the entry states it: the spare
    (out-of-band) bytes of each page, as the part's ONFI parameter page
    gives them (its spare area with the on-die ECC disabled, without an ECC
    parity area of its own; a source giving another view of it, Linux's
    for a few parts, has a note instead), the planes of each die, the most
    blocks of each die that may be bad, and the error correction it needs
    (``{"strength_bits": 8, "step_bytes": 512}``, without ``"step_bytes"``
    where the entry gives none). SPI NAND only. What follows from them
    (blocks, pages per block, the spare area in all) is not stored.
``dies``
    The dies in the package, where the entry states them (SPI NOR and SPI
    NAND), and not where its SFDP tables give the same. A die erase layout
    is never stored: it is the stated die erase (``DIE_ERASE``,
    ``DIE_ERASE_61``) over the dies (:func:`spiflash.derive.die_erasers`).
``die_select_bit``
    The register bit that selects the die, where the part selects it with a
    register (Micron's ``{"register": "nand-d0", "bit": 6}``); one that
    selects it with a command has the ``NAND_DIE_SELECT`` or ``DIE_SELECT``
    operation instead, never both.
``opcodes``
    The operations the entry states (a record's ``opcode_claims``):
    ``[{"op": "READ_1_1_4", "via": "SPI_NOR_QUAD_READ"}, ...]``, ``op`` a name
    in ``spiflash.opcodes.OPERATIONS`` (which gives the opcode) and ``via``
    the upstream flag, field or default behind it. Those
    :func:`spiflash.derive.opcodes` gives are not stored (its SFDP tables'
    among them). A driver default (an operation the upstream's driver
    issues to every part, or every part of a class, whatever the entry
    says) has ``"assumed": true``, and implies no capability. A use gives
    the part's ``"dummy_clocks"`` wherever the entry states them, even
    where they are the operation's usual number. Each operation is of the
    record's kind of flash (:attr:`spiflash.opcodes.Operation.flash_type`).
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
from spiflash.enums import Feature, FlashType, IdFamily, IdMethod, OperationKind, Source
from spiflash.model import SFDP_VALUES
from spiflash.model import Record as Model
from spiflash.opcodes import OPERATIONS, OpcodeUse
from spiflash.registers import ROLES

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

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
    "supply_mv",
    "quad_enable",
    "quad_enable_requirement",
    "protection",
    "oob_size",
    "planes",
    "dies",
    "die_select_bit",
    "max_bad_blocks",
    "ecc",
    "four_byte_modes",
    "otp",
    "legacy_ids",
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

#: The components a ``<field>.<component>`` key may name, by field: a role
#: of ``protection`` (``protection.tb``), where the roles come from
#: different tokens; a token giving several is under ``protection``.
VIA_COMPONENTS: dict[str, frozenset[str]] = {"protection": frozenset(ROLES)}

# A via key: feature:<feature>, <field>, <field>.<component> or <field>:<member>.
_VIA_KEY = re.compile(r"feature:([a-z0-9_]+)|([a-z_]+)(?:\.([a-z0-9_]+))?(?::([a-z0-9_]+))?")


def _eraser_members(rec: Record) -> set[str]:
    """The ``erasers:<member>`` names of a record's erasers: ``0x20``, ...;
    those it stores, and those its own SFDP tables give it
    (:attr:`spiflash.model.Record.sfdp_erasers`), which an upstream token can
    state too (QEMU's ``ER_4K``, for an eraser its dump gives)."""
    erasers = [e["opcode"] for e in rec["erasers"] or ()]
    if rec.get("sfdp") or rec.get("sfdp_tables"):
        erasers += [e.opcode for e in Model.from_json(rec).sfdp_erasers]
    return {f"0x{opcode:02x}" for opcode in erasers if opcode is not None}


def _mode_members(rec: Record) -> set[str]:
    """The ``four_byte_modes:<member>`` names of a record's ways into 4-byte
    mode: those it stores, and those its own SFDP tables give it."""
    modes = set(rec["four_byte_modes"] or ())
    if rec.get("sfdp") or rec.get("sfdp_tables"):
        modes |= {str(m) for m in Model.from_json(rec).four_byte_modes}
    return modes


#: The set-valued fields a ``<field>:<member>`` key may name, and the
#: members a record has.
_MEMBERS = {"erasers": _eraser_members, "four_byte_modes": _mode_members}


def check_via(rec: Record) -> None:
    """Raise for a ``via`` key that names no claimed feature, no field
    :data:`VIA_FIELDS` allows, a field the record leaves empty, a component
    the field does not have, or an eraser the record does not have (stored,
    or given by its own SFDP tables)."""
    for key in rec["via"]:
        m = _VIA_KEY.fullmatch(key)
        if m is None:
            ok = False
        elif m[1]:
            ok = m[1] in rec["features"]
        else:
            field, component, member = m[2], m[3], m[4]
            if field not in VIA_FIELDS:
                ok = False
            elif field in _MEMBERS:
                ok = bool(_MEMBERS[field](rec))
            elif field in SFDP_VALUES and rec[field] is None:
                # A value its SFDP tables give, which the entry states too.
                ok = getattr(Model.from_json(rec), field) is not None
            else:
                ok = rec[field] not in (None, [], "", {})
            if component is not None:
                known = component in VIA_COMPONENTS.get(field, ())
                ok = ok and known and component in rec[field]
            if member is not None:
                members = _MEMBERS.get(field)
                ok = ok and members is not None and member in members(rec)
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


def member_via(field: str, given: Mapping[str, str]) -> dict[str, str]:
    """The ``via`` of a set-valued field's members, from each member's
    upstream token: ``four_byte_modes:en4b`` for a token giving one member,
    ``four_byte_modes`` for one giving several (Zephyr's
    ``enter-4byte-addr``, a byte of bits), as a token is under one key."""
    by_token: dict[str, list[str]] = {}
    for member, token in given.items():
        by_token.setdefault(token, []).append(member)
    out: dict[str, list[str]] = {}
    for token, members in by_token.items():
        key = f"{field}:{members[0]}" if len(members) == 1 else field
        out.setdefault(key, []).append(token)
    return {key: "; ".join(tokens) for key, tokens in out.items()}


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
    both a whole SFDP dump and copied tables, nor both a quad enable bit
    and a requirement, nor two roles (of ``protection``, or one and the
    quad enable bit) on one bit."""
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
        four_byte_modes=[],
        legacy_ids=[],
    )
    rec.update(fields)
    if rec["sfdp"] and rec["sfdp_tables"]:
        msg = f"{source} {name}: a whole SFDP dump and copied tables"
        raise ValueError(msg)
    rec["features"] = sorted(set(rec["features"]))
    rec["four_byte_modes"] = sorted(set(rec["four_byte_modes"]))
    _check_legacy_ids(rec)
    check_via(rec)
    _check_kind(rec)
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
    shared = Model.from_json(rec).shared_bits()
    if shared:
        msg = f"{rec['source']} {rec['name']}: two roles on one bit: {shared}"
        raise ValueError(msg)
    lost = claimed - Model.from_json(rec).features
    if lost:
        msg = f"{rec['source']} {rec['name']}: dropping implied claims lost {sorted(lost)}"
        raise AssertionError(msg)
    return rec


def _check_legacy_ids(rec: Record) -> None:
    """Raise for a legacy id that is the record's own id, or one listed
    twice, or read by a JEDEC read-id."""
    seen = set()
    for method, ident in rec["legacy_ids"]:
        if IdMethod(method).family is IdFamily.JEDEC or ident == rec["id"]:
            msg = f"{rec['source']} {rec['name']}: legacy id {method} {ident} is no legacy id"
            raise ValueError(msg)
        if (method, ident) in seen:
            msg = f"{rec['source']} {rec['name']}: legacy id {method} {ident} twice"
            raise ValueError(msg)
        seen.add((method, ident))


#: The fields only a SPI NAND part has.
NAND_ONLY = ("oob_size", "planes", "max_bad_blocks", "ecc")

#: The operations selecting a die by command, which a part selecting it
#: with a register bit (``die_select_bit``) does not have.
DIE_SELECTS = frozenset({"DIE_SELECT", "NAND_DIE_SELECT"})


def _check_kind(rec: Record) -> None:
    """Raise for what a record of its kind of flash cannot hold: SPI NAND
    geometry on a SPI NOR record, another kind's operation, both a die
    select operation and a die select bit, or a die erase layout (which is
    derived from the dies: :func:`spiflash.derive.die_erasers`)."""
    where = f"{rec['source']} {rec['name']}"
    if rec["type"] == FlashType.NOR.value:
        given = [f for f in NAND_ONLY if rec[f] is not None]
        if given:
            msg = f"{where}: {given} on a SPI NOR record"
            raise ValueError(msg)
    other = [o["op"] for o in rec["opcodes"] if OPERATIONS[o["op"]].flash_type != rec["type"]]
    if other:
        msg = f"{where}: {other} are not {rec['type']} operations"
        raise ValueError(msg)
    if rec["die_select_bit"] is not None and DIE_SELECTS & {o["op"] for o in rec["opcodes"]}:
        msg = f"{where}: a die select operation and a die select bit, not one"
        raise ValueError(msg)
    die_erases = {OPERATIONS[op].opcode for op in derive.DIE_ERASES}
    if any(e["opcode"] in die_erases for e in rec["erasers"] or ()):
        msg = f"{where}: a die erase layout is derived: give dies and the die erase"
        raise ValueError(msg)


def _drop_implied(rec: Record) -> None:
    """Drop the capability claims ``rec``'s other fields imply
    (:func:`spiflash.derive.features`), and their ``via`` keys: a token only
    such a key held stays a flag, unless something else holds it."""
    implied = derive.features(Model.from_json(rec))
    rec["features"] = [f for f in rec["features"] if f not in implied]
    dropped = {f"feature:{f}" for f in implied}
    rec["via"] = {k: v for k, v in rec["via"].items() if k not in dropped}


def _drop_sfdp(rec: Record) -> None:
    """Drop the size, page size, quad enable requirement or bit
    (:data:`spiflash.model.SFDP_VALUES`) and erasers ``rec`` states that its
    own SFDP tables give the same (:meth:`spiflash.sfdp.Sfdp.facts`): the record
    derives them from the tables at load. A value that differs stays, as the
    upstream's own (a :meth:`spiflash.model.Record.sfdp_disagreements`). The
    ``via`` token of a dropped value stays, under the value its tables give,
    which the entry also states (Zephyr's ``quad-enable-requirements``, and
    QEMU's ``ER_4K`` under the ``erasers:0x20`` key)."""
    if not (rec["sfdp"] or rec["sfdp_tables"]):
        return
    model = Model.from_json(rec)
    facts = model.sfdp_facts
    if facts is None:
        return
    for name in SFDP_VALUES:
        if model.stored(name) is not None and model.stored(name) == getattr(facts, name):
            rec[name] = None
    given = [e.to_json() for e in Model.from_json(rec).sfdp_erasers]
    kept = [e for e in rec["erasers"] or () if e not in given]
    rec["erasers"] = kept or None
    rec["four_byte_modes"] = [m for m in rec["four_byte_modes"] if m not in facts.four_byte_modes]


def _same_use(stored: dict[str, Any], derived: OpcodeUse) -> bool:
    """Whether a stored use is one ``derived`` gives: the same operation,
    with no dummy clocks of its own or the same ones."""
    clocks = stored.get("dummy_clocks")
    return stored["op"] == derived.op and clocks in (None, derived.dummy_clocks)


def _drop_derived(rec: Record, held: set[str], via: dict[str, list[str]]) -> set[str]:
    """Drop the operations ``rec`` derives at load (a stored use with dummy
    clocks other than the derived one's stays). A flag (of ``held``) that
    only a dropped erase operation's via held moves to ``via``, under its
    eraser (stored, or from its SFDP tables), ``erasers:0x<opcode>``, or
    ``erasers`` for a token that gives several; where the record has no
    such eraser, the flag stays a flag, and is returned."""
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
