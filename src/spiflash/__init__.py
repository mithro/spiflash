"""A database of SPI flash chips, merged from the tables in Linux, U-Boot,
flashrom, flashprog, OpenOCD and openFPGALoader.

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

from .db import Database, Manufacturer, SourceInfo, database
from .enums import Feature, FlashType, IdFamily, IdMethod, OperationKind, Source
from .model import Eraser, Flash, Record, Voltage, parse_id

if TYPE_CHECKING:
    from collections.abc import Iterable

try:
    from ._version import __version__
except ImportError:  # pragma: no cover - a checkout without a build
    __version__ = "0.0.dev0"

__all__ = [
    "Database",
    "Eraser",
    "Feature",
    "Flash",
    "FlashType",
    "IdFamily",
    "IdMethod",
    "Manufacturer",
    "OperationKind",
    "Record",
    "Source",
    "SourceInfo",
    "Voltage",
    "__version__",
    "database",
    "find",
    "flashes",
    "jep106",
    "lookup",
    "parse_id",
    "records",
    "sources",
]


def lookup(chip_id: str | bytes | int | Iterable[int], **kwargs: str) -> list[Flash]:
    """The chips answering JEDEC read-id ``chip_id``; see :meth:`Database.lookup`."""
    return database().lookup(chip_id, **kwargs)


def find(name: str) -> list[Flash]:
    """The chips matching part name ``name``; see :meth:`Database.find`."""
    return database().find(name)


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
