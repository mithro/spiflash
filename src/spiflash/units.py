"""Sizes and times as people read them."""

from __future__ import annotations


def human_size(n: int | None) -> str:
    """``16 MiB`` for 16777216, ``256 B`` for 256, ``?`` for ``None``."""
    if n is None:
        return "?"
    for unit, scale in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if n >= scale and n % scale == 0:
            return f"{n // scale} {unit}"
    return f"{n} B"


def human_time(us: int) -> str:
    """A duration in microseconds as ``704 us``, ``64 ms`` or ``192 s``."""
    if us >= 1_000_000:
        return f"{us / 1_000_000:g} s"
    if us >= 1_000:
        return f"{us / 1_000:g} ms"
    return f"{us} us"
