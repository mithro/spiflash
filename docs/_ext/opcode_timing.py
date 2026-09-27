"""Timing diagrams for SPI flash operations, as WaveDrom documents.

:func:`diagram` turns an :class:`~spiflash.opcodes.Operation` into the
WaveDrom JSON for its transaction: chip select, the clock, and each data line,
through the command, address, dummy and data phases, with a row naming the
phase. The command's bits are the real opcode's, as logic levels; address and
data bits are labelled cells (``A23``, ``D7``). Long phases are cut short with
a gap (``|``), so a 24-bit address on one line takes four clocks, not 24.

Data lines follow the usual SPI flash wiring: single-line phases use IO0 (SI)
from the host and IO1 (SO) from the flash; wider phases use IO0 upwards, the
most significant bit on the highest line; lines nobody drives are
high-impedance (``z``). SPI mode 0 is drawn: the clock idles low, data changes on
its falling edge (the start of each cell) and is sampled on its rising edge
(the middle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from spiflash.enums import DataPhase

if TYPE_CHECKING:
    from collections.abc import Callable

    from spiflash.opcodes import Operation

# A cell: a WaveDrom wave character, and a label for "=" cells.
Cell = tuple[str, str | None]

# Address and dummy phases longer than this many clocks are drawn as their
# first two clocks, a gap, and their last two. The command and the first data
# byte are always drawn in full.
MAX_CLOCKS = 6


@dataclass
class _Column:
    """One clock (or a gap, or the idle ends) across every signal."""

    phase: str | None  # a phase starts here, named this
    cs: str
    clock: str
    lines: dict[int, Cell] = field(default_factory=dict)


def _line_names(width: int) -> list[str]:
    if width <= 2:
        return ["IO0 (SI)", "IO1 (SO)"]
    if width == 4:
        return ["IO0 (SI)", "IO1 (SO)", "IO2 (WP#)", "IO3 (HOLD#)"]
    return [f"IO{k}" for k in range(width)]


def _bits(total: int, lines: int, clock: int) -> list[int]:
    """The bit numbers (MSB first) each line carries on ``clock`` of a
    ``total``-bit field sent over ``lines`` lines, line 0 first."""
    top = total - 1 - clock * lines
    return [top - (lines - 1 - k) for k in range(lines)]


def _fill(width: int, cell: Cell) -> dict[int, Cell]:
    """The same cell on every one of ``width`` lines."""
    return dict.fromkeys(range(width), cell)


def _shown(clocks: int, *, shorten: bool = True) -> list[int | None]:
    """The clocks of a phase to draw, ``None`` for the gap."""
    if clocks <= MAX_CLOCKS or not shorten:
        return list(range(clocks))
    return [0, 1, None, clocks - 2, clocks - 1]


class _Builder:
    def __init__(self, width: int) -> None:
        self.width = width
        self.columns: list[_Column] = []

    def idle(self) -> None:
        self.columns.append(_Column(None, "1", "l", _fill(self.width, ("z", None))))

    def phase(
        self,
        name: str,
        clocks: int,
        cell: Callable[[int], dict[int, Cell]],
        *,
        shorten: bool = True,
    ) -> None:
        """Add ``clocks`` clocks of a phase; ``cell(clock)`` gives each line's
        cell for one clock (a dict, line → cell)."""
        for i, clock in enumerate(_shown(clocks, shorten=shorten)):
            if clock is None:
                lines = _fill(self.width, ("|", None))
                self.columns.append(_Column(None, "|", "|", lines))
                continue
            lines = _fill(self.width, ("z", None))
            lines.update(cell(clock))
            self.columns.append(_Column(name if i == 0 else None, "0", "n", lines))

    def gap(self) -> None:
        """A gap after the last phase: the transfer carries on."""
        lines = _fill(self.width, ("|", None))
        self.columns.append(_Column(None, "|", "|", lines))

    def document(self, head: str, names: list[str], *, wide: bool = False) -> dict[str, Any]:
        signals: list[dict[str, Any]] = [
            self._phase_row(),
            self._row("CS#", [c.cs for c in self.columns]),
        ]
        signals.append(self._row("SCLK", [c.clock for c in self.columns]))
        for k, name in enumerate(names):
            cells = [c.lines[k] for c in self.columns]
            signals.append(self._row(name, [w for w, _ in cells], [lab for _, lab in cells]))
        # A phase of one or two clocks (a QPI command, a DTR address) needs
        # wider cells for its label to fit.
        return {"signal": signals, "head": {"text": head}, "config": {"hscale": 2 if wide else 1}}

    def _phase_row(self) -> dict[str, Any]:
        waves: list[str] = []
        labels: list[str | None] = []
        for c in self.columns:
            if c.phase:  # a phase starts: a new labelled cell
                waves.append("=")
            elif c.cs == "|":  # a gap
                waves.append("|")
            elif c.cs == "1":  # idle, before or after the transaction
                waves.append("x")
            else:  # the phase carries on
                waves.append(".")
            labels.append(c.phase)
        return self._row("phase", waves, labels)

    @staticmethod
    def _row(name: str, waves: list[str], labels: list[str | None] | None = None) -> dict[str, Any]:
        """A signal: repeats of a level become ``.`` (across a gap too, so
        the level carries on through it); labelled cells stay."""
        out = []
        last = None
        for i, w in enumerate(waves):
            if w == "|":
                out.append(w)
                continue
            repeat = w == last and w != "=" and (labels is None or not labels[i])
            out.append("." if repeat else w)
            last = w
        row: dict[str, Any] = {"name": name, "wave": "".join(out)}
        data = [lab for lab in (labels or []) if lab]
        if data:
            row["data"] = data
        return row


def diagram(op: Operation) -> dict[str, Any]:
    """The WaveDrom document for ``op``'s transaction."""
    if op.dtr:
        return _dtr_diagram(op)
    cmd, addr, data = op.lines
    width = max(2, cmd, addr, data)
    b = _Builder(width)
    b.idle()

    def command(clock: int) -> dict[int, Cell]:
        bits = _bits(8, cmd, clock)
        return {k: (str((op.opcode >> bit) & 1), None) for k, bit in enumerate(bits)}

    b.phase(f"command 0x{op.opcode:02x}", 8 // cmd, command, shorten=False)
    if op.address_bytes:
        total = 8 * op.address_bytes

        def address(clock: int) -> dict[int, Cell]:
            return {k: ("=", f"A{bit}") for k, bit in enumerate(_bits(total, addr, clock))}

        b.phase(f"address ({total} bits)", total // addr, address)
    if op.dummy_clocks != 0:
        clocks = op.dummy_clocks if op.dummy_clocks is not None else MAX_CLOCKS + 1
        label = f"dummy ({op.dummy_clocks})" if op.dummy_clocks is not None else "dummy (varies)"
        # The host stops driving; nothing is on the lines until the data.
        b.phase(label, clocks, lambda _clock: {0: ("x", None)} if width == 2 else {})
    if op.data is not None:
        # One line: the flash answers on SO (IO1), the host writes on SI (IO0).
        first = 1 if data == 1 and op.data is DataPhase.READ else 0

        def byte(clock: int) -> dict[int, Cell]:
            bits = _bits(8, data, clock)
            return {first + k: ("=", f"D{bit}") for k, bit in enumerate(bits)}

        name = "read data" if op.data is DataPhase.READ else "write data"
        b.phase(name, 8 // data, byte, shorten=False)
        if op.data_bytes != 1:
            b.gap()
    b.idle()
    head = f"{op.name} (0x{op.opcode:02x}): {op.protocol}"
    return b.document(head, _line_names(width), wide=cmd > 1)


def _dtr_diagram(op: Operation) -> dict[str, Any]:
    """Octal DTR (xSPI): a byte on all eight lines on each clock edge, so two
    per clock; the command is the opcode and an extension byte, and the flash
    sends a data strobe (DS) with its data."""
    halves: list[tuple[str | None, str, str, Cell, Cell]] = []  # phase, cs, clk, bus, ds

    def clocks(name: str, count: int, bus: Any, ds: str = "z") -> None:
        for i, clock in enumerate(_shown(count)):
            if clock is None:
                halves.append((None, "|", "|", ("|", None), ("|", None)))
                continue
            for half in (0, 1):
                phase = name if i == 0 and half == 0 else None
                strobe = ("h" if half == 0 else "l") if ds == "p" else ds
                halves.append(
                    (phase, "0", "h" if half == 0 else "l", bus(clock, half), (strobe, None))
                )

    halves.append((None, "1", "l", ("z", None), ("z", None)))
    clocks(
        f"command 0x{op.opcode:02x}",
        1,
        lambda _c, h: ("=", f"0x{op.opcode:02x}" if h == 0 else "ext"),
    )
    clocks(
        "address (32 bits)", 2, lambda c, h: ("=", ["A31:24", "A23:16", "A15:8", "A7:0"][2 * c + h])
    )
    if op.dummy_clocks:
        clocks(f"dummy ({op.dummy_clocks})", op.dummy_clocks, lambda _c, _h: ("z", None))
    reading = op.data is DataPhase.READ
    clocks(
        "read data" if reading else "write data",
        2,
        lambda c, h: ("=", f"byte {2 * c + h}"),
        ds="p" if reading else "z",
    )
    halves.append((None, "|", "|", ("|", None), ("|", None)))
    halves.append((None, "1", "l", ("z", None), ("z", None)))

    b = _Builder(0)
    b.columns = [_Column(p, cs, clk, {0: bus, 1: ds}) for p, cs, clk, bus, ds in halves]
    b.width = 2
    head = f"{op.name} (0x{op.opcode:02x}): {op.protocol}"
    return b.document(head, ["IO[7:0]", "DS"], wide=True)
