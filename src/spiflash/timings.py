"""A part's durations: how long it takes to erase, program, suspend, enter
and leave deep power-down, and recover from a reset.

A duration is a :class:`~spiflash.enums.TimedEvent` (with, for a block
erase, the eraser's opcode: a :class:`TimingKey`), a
:class:`~spiflash.enums.Bound` of the datasheet parameter it is (tDP is a
maximum, tCRDP a minimum), and a whole number of nanoseconds, the one unit
the package uses for time. :class:`Timings` maps (key, bound) to the
nanoseconds, so a record has at most one value for each.

Which bounds an event can have is a closed table (:data:`BOUNDS`), each
entry backed by a source: a value with another bound is refused, and a new
source with one widens the table on purpose.

These are durations of the part's own work. Where a driver waits a fixed
time for every part, or a board's controller is clocked or timed some way,
that is the driver's or the board's choice, and none of it is stored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from .enums import Bound, TimedEvent

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_MIN, _TYP, _MAX, _ANY = Bound.MINIMUM, Bound.TYPICAL, Bound.MAXIMUM, Bound.UNSPECIFIED

#: The bounds each event can have, and who gives them:
#:
#: - erases and programs, typical and maximum: SFDP BFPT DW10 and DW11 (the
#:   maximum through each dword's multiplier); a chip erase also
#:   unspecified, Dediprog's ``ChipEraseTime``;
#: - a SPI NAND page read, maximum: Zephyr's ``page-read-duration-max``;
#: - the suspend latencies, maximum, and the resume-to-suspend intervals,
#:   typical: BFPT DW12, as JESD216B labels them (Macronix MX25U25645G,
#:   Rev. 1.4, pp. 102-103, reproduces the table: "Program Suspend Latency
#:   (Max.)", "Program Resume to Suspend Interval (Typical)");
#: - deep power-down entry and exit, maximum: Zephyr's ``t-enter-dpd`` and
#:   ``t-exit-dpd`` and BFPT DW14 ("Release from Deep Power-down (RDP) Delay
#:   (Max.)"); the dwell before a release and the wake pulse, minimum:
#:   Zephyr's ``dpd-wakeup-sequence``;
#: - the reset pulse, minimum, and the recovery, maximum: Zephyr's
#:   ``t-reset-pulse`` and ``t-reset-recovery``.
BOUNDS: dict[TimedEvent, frozenset[Bound]] = {
    TimedEvent.BLOCK_ERASE: frozenset({_TYP, _MAX}),
    TimedEvent.CHIP_ERASE: frozenset({_TYP, _MAX, _ANY}),
    TimedEvent.PAGE_PROGRAM: frozenset({_TYP, _MAX}),
    TimedEvent.BYTE_PROGRAM_FIRST: frozenset({_TYP, _MAX}),
    TimedEvent.BYTE_PROGRAM_ADDITIONAL: frozenset({_TYP, _MAX}),
    TimedEvent.PAGE_READ: frozenset({_MAX}),
    TimedEvent.ERASE_SUSPEND: frozenset({_MAX}),
    TimedEvent.PROGRAM_SUSPEND: frozenset({_MAX}),
    TimedEvent.ERASE_RESUME_TO_SUSPEND: frozenset({_TYP}),
    TimedEvent.PROGRAM_RESUME_TO_SUSPEND: frozenset({_TYP}),
    TimedEvent.DPD_ENTER: frozenset({_MAX}),
    TimedEvent.DPD_EXIT: frozenset({_MAX}),
    TimedEvent.DPD_MIN_TIME: frozenset({_MIN}),
    TimedEvent.DPD_WAKE_PULSE: frozenset({_MIN}),
    TimedEvent.RESET_PULSE: frozenset({_MIN}),
    TimedEvent.RESET_RECOVERY: frozenset({_MAX}),
}

_EVENT_ORDER = {e: i for i, e in enumerate(TimedEvent)}
_BOUND_ORDER = {b: i for i, b in enumerate(Bound)}

_KEY = re.compile(r"([a-z_]+)(?::0x([0-9a-f]{2}))?")


@dataclass(frozen=True, slots=True)
class TimingKey:
    """What a duration is the time of: an event, and for a block erase the
    3-byte erase opcode of the eraser whose block it is (an SFDP erase
    type; a SPI NAND part's is 0xd8). The 4-byte form of an erase takes its
    3-byte form's time, and has no key of its own."""

    event: TimedEvent
    opcode: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event", TimedEvent(self.event))
        block = self.event is TimedEvent.BLOCK_ERASE
        if block != (self.opcode is not None):
            msg = f"{self.event}: an opcode only, and always, for a block erase"
            raise ValueError(msg)
        if self.opcode is not None and not 0 <= self.opcode <= 0xFF:
            msg = f"{self.event}: opcode {self.opcode} is not a byte"
            raise ValueError(msg)

    def __str__(self) -> str:
        """``chip_erase``, ``block_erase:0x20``."""
        return str(self.event) if self.opcode is None else f"{self.event}:0x{self.opcode:02x}"

    @property
    def order(self) -> tuple[int, int]:
        """The events in :class:`~spiflash.enums.TimedEvent` order, block
        erases by opcode."""
        return _EVENT_ORDER[self.event], self.opcode or 0

    @classmethod
    def parse(cls, text: str) -> TimingKey:
        """The key :meth:`__str__` writes; ``ValueError`` for anything else."""
        m = _KEY.fullmatch(text)
        if m is None or m[1] not in TimedEvent.__members__.values():
            msg = f"not a timing key: {text!r}"
            raise ValueError(msg)
        return cls(TimedEvent(m[1]), int(m[2], 16) if m[2] else None)


def _sort(item: tuple[tuple[TimingKey, Bound], int]) -> tuple[tuple[int, int], int]:
    (key, bound), _ = item
    return key.order, _BOUND_ORDER[bound]


@dataclass(frozen=True)
class Timings:
    """Durations in nanoseconds, by (:class:`TimingKey`, bound): at most one
    of each. A bound an event cannot have (:data:`BOUNDS`), or a value that
    is not a whole number of nanoseconds above 0 (0 is "not given", and the
    extractors leave it out), is refused.

    In JSON, ``{"chip_erase": {"unspecified": 200000000000},
    "block_erase:0x20": {"typical": 48000000, "maximum": 384000000}}``."""

    values: Mapping[tuple[TimingKey, Bound], int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        checked = {}
        for (key, given), ns in self.values.items():
            bound = Bound(given)
            if bound not in BOUNDS[key.event]:
                msg = f"{key}: a {bound} time is none this event has ({sorted(BOUNDS[key.event])})"
                raise ValueError(msg)
            if not isinstance(ns, int) or isinstance(ns, bool) or ns <= 0:
                msg = f"{key}.{bound}: {ns!r} is not a duration in whole nanoseconds"
                raise ValueError(msg)
            checked[key, bound] = ns
        object.__setattr__(
            self, "values", MappingProxyType(dict(sorted(checked.items(), key=_sort)))
        )

    def __hash__(self) -> int:
        return hash(frozenset(self.values.items()))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Timings):
            return NotImplemented
        return dict(self.values) == dict(other.values)

    def __bool__(self) -> bool:
        return bool(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def __iter__(self) -> Iterator[tuple[TimingKey, Bound]]:
        return iter(self.values)

    def __contains__(self, item: object) -> bool:
        return item in self.values

    def __getstate__(self) -> dict[str, Any]:
        # A mapping proxy does not pickle; the dict it wraps does.
        return {"values": dict(self.values)}

    def __setstate__(self, state: dict[str, Any]) -> None:
        object.__setattr__(self, "values", MappingProxyType(state["values"]))

    def items(self) -> Iterator[tuple[tuple[TimingKey, Bound], int]]:
        return iter(self.values.items())

    @property
    def keys(self) -> tuple[TimingKey, ...]:
        """The keys given, each once, in order."""
        return tuple(dict.fromkeys(key for key, _ in self.values))

    def get(
        self, event: TimedEvent | str, bound: Bound | str, opcode: int | None = None
    ) -> int | None:
        """The nanoseconds for ``event`` (and ``opcode``, a block erase's) at
        ``bound``; ``None`` where none is given."""
        return self.values.get((TimingKey(TimedEvent(event), opcode), Bound(bound)))

    def component(self, name: str) -> int | None:
        """A value by its component name (:func:`component_name`):
        ``"chip_erase.maximum"``, ``"block_erase:0x20.typical"``."""
        key, bound = parse_component(name)
        return self.values.get((key, bound))

    def over(self, under: Timings) -> Timings:
        """These values, and ``under``'s where these give none for its
        (key, bound)."""
        return Timings({**under.values, **self.values})

    def without(self, other: Timings) -> Timings:
        """These values, but those ``other`` gives the same."""
        return Timings({k: v for k, v in self.values.items() if other.values.get(k) != v})

    def disorder(self) -> list[tuple[TimingKey, Bound, int, Bound, int]]:
        """Where a key's bounds are out of order (a minimum above a typical
        or a maximum, a typical above a maximum): ``(key, lower bound, its
        value, higher bound, its value)`` for each such pair. An
        unspecified value is not ordered against the others."""
        order = (Bound.MINIMUM, Bound.TYPICAL, Bound.MAXIMUM)
        out: list[tuple[TimingKey, Bound, int, Bound, int]] = []
        for key in self.keys:
            given = [(b, self.values[key, b]) for b in order if (key, b) in self.values]
            for i, (low, a) in enumerate(given):
                out.extend((key, low, a, high, b) for high, b in given[i + 1 :] if a > b)
        return out

    def to_json(self) -> dict[str, dict[str, int]]:
        """``{"chip_erase": {"maximum": 480000000000}, ...}``; ``{}`` when
        empty."""
        out: dict[str, dict[str, int]] = {}
        for (key, bound), ns in self.values.items():
            out.setdefault(str(key), {})[str(bound)] = ns
        return out

    @classmethod
    def from_json(cls, d: Mapping[str, Mapping[str, int]] | None) -> Timings:
        return cls(
            {
                (TimingKey.parse(key), Bound(bound)): ns
                for key, bounds in (d or {}).items()
                for bound, ns in bounds.items()
            }
        )


def component_name(key: TimingKey, bound: Bound) -> str:
    """``"chip_erase.maximum"``: a (key, bound) as a component of the
    ``timings`` value the sources are compared on
    (:data:`spiflash.model.COMPONENTS`)."""
    return f"{key}.{bound}"


def parse_component(name: str) -> tuple[TimingKey, Bound]:
    """The (key, bound) :func:`component_name` writes; ``ValueError`` for
    anything else."""
    key, _, bound = name.rpartition(".")
    if bound not in Bound.__members__.values():
        msg = f"not a timing component: {name!r}"
        raise ValueError(msg)
    return TimingKey.parse(key), Bound(bound)
