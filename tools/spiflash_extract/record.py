"""The common record every extractor produces.

One record is one entry of one upstream table, as that upstream describes it:
nothing is merged or corrected here. ``spiflash`` groups the records by the
id a chip answers, at load time.

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
    Normalised capability names: :class:`spiflash.enums.Feature` values.
``flags``
    The upstream's raw flag and feature names, for anything
    :class:`~spiflash.enums.Feature` does not capture.
``voltage``
    ``[min_mV, max_mV]``.
``opcodes``
    The operations the entry implies:
    ``[{"op": "READ_1_1_4", "opcode": 0x6b, "via": "SPI_NOR_QUAD_READ"}, ...]``,
    ``op`` a name in ``spiflash.opcodes.OPERATIONS`` and ``via`` the upstream
    flag, field or default behind it.
``sfdp``
    Hex of the part's SFDP (JESD216) area, where the upstream carries a
    dump of it (QEMU's flash model does); :mod:`spiflash.sfdp` decodes it.
``tested``
    The upstream's test status, verbatim.
``notes``
    Comments the upstream attached to the entry or its id.
"""

from __future__ import annotations

from typing import Any

from spiflash.enums import Feature, FlashType, Source

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
    "voltage",
    "opcodes",
    "sfdp",
    "tested",
    "notes",
)


def make(source: str, file: str, line: int, name: str, **fields: Any) -> Record:
    """A record with every key present, in the canonical order.

    ``features`` and ``flags`` may come in any order and with repeats; they
    are stored sorted and unique."""
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
        name=name,
        type=FlashType.NOR.value,
        id_method="rdid",
        features=[],
        flags=[],
        notes=[],
        opcodes=[],
    )
    rec.update(fields)
    rec["features"] = sorted(set(rec["features"]))
    rec["flags"] = sorted(set(rec["flags"]))
    return rec
