"""The common record every extractor produces.

One record is one entry of one upstream table, as that upstream describes it:
nothing is merged or corrected here. ``spiflash`` groups the records by the
id a chip answers, at load time.

Fields (``None`` / empty when the upstream does not say):

``source``       which upstream: ``linux``, ``u-boot``, ``flashrom``, ...
``file``/``line`` where in that upstream's tree the entry is
``type``         ``nor`` or ``nand``
``vendor``       the upstream's own vendor name for the entry
``name``         the upstream's part name, verbatim
``id``           hex of the id bytes the chip answers (``9f`` RDID unless
                 ``id_method`` says otherwise), continuation codes included
``ext_id``       hex of further id bytes some upstreams match on
``id_method``    ``rdid`` (JEDEC 0x9F), or the legacy probe: ``rems``,
                 ``res1``, ``res2``, ``at25f``, ``st95``...
``size``         bytes; ``None`` where the upstream reads it from SFDP
``page_size``    program page, bytes
``sector_size``  the erase unit the upstream uses by default, bytes
``erasers``      ``[{"opcode": 0x20, "blocks": [[4096, 4096]]}, ...]``
``features``     normalised capability names (see :data:`FEATURES`)
``flags``        the upstream's raw flag / feature names, for anything
                 :data:`FEATURES` does not capture
``voltage``      ``[min_mV, max_mV]``
``opcodes``      named opcodes, e.g. ``{"read": 3, "qread": 235}``
``tested``       the upstream's test status, verbatim
``notes``        comments the upstream attached to the entry or its id
"""

from __future__ import annotations

from typing import Any

# The normalised capability vocabulary. Anything an upstream says that does
# not map onto one of these stays in ``flags``.
FEATURES = {
    "erase_4k": "4 KiB sectors can be erased (0x20 or equivalent)",
    "erase_32k": "32 KiB blocks can be erased (0x52)",
    "erase_64k": "64 KiB blocks can be erased (0xd8)",
    "sfdp": "answers SFDP (JESD216) queries",
    "fast_read": "supports fast read (0x0b)",
    "dual_read": "supports dual-output/IO read",
    "quad_read": "supports quad-output/IO read",
    "quad_pp": "supports quad-input page program",
    "octal_read": "supports octal read",
    "octal_dtr_read": "supports octal DTR read",
    "octal_dtr_pp": "supports octal DTR page program",
    "qpi": "supports QPI (4-4-4) mode",
    "4byte_addr": "supports 4-byte addressing",
    "4byte_opcodes": "has dedicated 4-byte-address opcodes",
    "otp": "has one-time-programmable area",
    "lock": "block protection bits in the status register",
    "no_erase": "no erase needed (FRAM/MRAM)",
    "rww": "read-while-write",
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
    "voltage",
    "opcodes",
    "tested",
    "notes",
)


def make(source: str, file: str, line: int, name: str, **fields: Any) -> Record:
    """A record with every key present, in the canonical order."""
    unknown = set(fields) - set(KEYS)
    if unknown:
        raise KeyError(f"unknown record fields: {sorted(unknown)}")
    features = fields.get("features") or []
    bad = set(features) - set(FEATURES)
    if bad:
        raise ValueError(f"unknown features {sorted(bad)} for {source} {name}")
    rec: Record = dict.fromkeys(KEYS)
    rec.update(
        source=source,
        file=file,
        line=line,
        name=name,
        type="nor",
        id_method="rdid",
        features=[],
        flags=[],
        notes=[],
    )
    rec.update(fields)
    rec["features"] = sorted(set(rec["features"]))
    rec["flags"] = sorted(set(rec["flags"]))
    return rec
