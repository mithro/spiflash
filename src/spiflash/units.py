"""Sizes, times and frequencies as people read them."""

from __future__ import annotations


def human_size(n: int | None) -> str:
    """``16 MiB`` for 16777216, ``256 B`` for 256, ``?`` for ``None``."""
    if n is None:
        return "?"
    for unit, scale in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if n >= scale and n % scale == 0:
            return f"{n // scale} {unit}"
    return f"{n} B"


def human_duration(ns: int) -> str:
    """A duration in nanoseconds, the package's one unit of time, as
    ``20 ns``, ``35 µs``, ``0.704 ms`` or ``192 s``."""
    for unit, scale in (("s", 10**9), ("ms", 10**6), ("µs", 10**3)):
        if ns >= scale:
            return f"{ns / scale:g} {unit}"
    return f"{ns} ns"


def human_frequency(hz: int) -> str:
    """A frequency in hertz as ``104 MHz``, ``33.3 MHz`` or ``500 kHz``."""
    for unit, scale in (("GHz", 10**9), ("MHz", 10**6), ("kHz", 10**3)):
        if hz >= scale:
            return f"{hz / scale:g} {unit}"
    return f"{hz} Hz"
