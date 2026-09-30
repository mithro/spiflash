"""A database of SPI flash chips, merged from the tables in flashrom,
flashprog, Linux, U-Boot, Dediprog, Rockchip, OpenOCD, openFPGALoader and
QEMU, and the flash chips Zephyr's boards describe, with the SFDP (JESD216)
tables of the parts QEMU has them for (:mod:`spiflash.sfdp` decodes those,
and any other dump).

>>> import spiflash
>>> [f.manufacturer for f in spiflash.lookup("ef4018")]
['Winbond']
>>> spiflash.lookup("ef4018")[0].size
16777216
>>> "W25Q128JV" in spiflash.lookup("ef4018")[0].names
True
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .db import Database, Manufacturer, NameMatch, SourceInfo, database
from .enums import Feature, FlashType, IdFamily, IdMethod, OperationKind, Source
from .model import Datasheet, Eraser, Flash, Record, SfdpDump, Voltage, parse_id
from .sfdp import Sfdp
from .sfdp import parse as parse_sfdp

if TYPE_CHECKING:
    from collections.abc import Iterable

try:
    from ._version import __version__
except ImportError:  # pragma: no cover - a checkout without a build
    __version__ = "0.0.dev0"

__all__ = [
    "Database",
    "Datasheet",
    "Eraser",
    "Feature",
    "Flash",
    "FlashType",
    "IdFamily",
    "IdMethod",
    "Manufacturer",
    "NameMatch",
    "OperationKind",
    "Record",
    "Sfdp",
    "SfdpDump",
    "Source",
    "SourceInfo",
    "Voltage",
    "__version__",
    "database",
    "find",
    "find_glob",
    "find_nearest",
    "find_regex",
    "flashes",
    "jep106",
    "lookup",
    "parse_id",
    "parse_sfdp",
    "records",
    "sources",
]


def lookup(chip_id: str | bytes | int | Iterable[int], **kwargs: str) -> list[Flash]:
    """The chips answering JEDEC read-id ``chip_id``; see :meth:`Database.lookup`."""
    return database().lookup(chip_id, **kwargs)


def find(name: str) -> list[Flash]:
    """The chips matching part name ``name``; see :meth:`Database.find`."""
    return database().find(name)


def find_regex(pattern: str) -> list[Flash]:
    """The chips with a part name matching regular expression ``pattern``;
    see :meth:`Database.find_regex`."""
    return database().find_regex(pattern)


def find_glob(pattern: str) -> list[Flash]:
    """The chips with a part name matching ``pattern`` (``W25Q128*``); see
    :meth:`Database.find_glob`."""
    return database().find_glob(pattern)


def find_nearest(name: str, count: int = 10) -> list[NameMatch]:
    """The chips with the part names closest to ``name``; see
    :meth:`Database.find_nearest`."""
    return database().find_nearest(name, count)


def flashes() -> tuple[Flash, ...]:
    """Every chip id in the database."""
    return database().flashes


def records() -> tuple[Record, ...]:
    """Every upstream entry, as extracted."""
    return database().records


def jep106(manufacturer_id: int, bank: int = 0) -> str | None:
    """The JEP106 name for a manufacturer id byte."""
    return database().jep106(manufacturer_id, bank)


def sources() -> dict[str, SourceInfo]:
    """Which upstream commits the data came from."""
    return database().sources
