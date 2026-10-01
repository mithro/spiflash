"""Zephyr: the flash chips its boards describe, in the devicetree of
:upstream:`zephyr:boards/` (``.dts``, ``.dtsi`` and ``.overlay`` files) and of
the SoCs in :upstream:`zephyr:dts/`.

Zephyr has no table of flash parts. A board that carries one describes it in
devicetree, for the driver to check and drive at start-up::

    mx25r64: mx25r6435f@0 {
        compatible = "nordic,qspi-nor";
        writeoc = "pp4io";
        readoc = "read4io";
        jedec-id = [c2 28 17];
        sfdp-bfp = [e5 20 f1 ff  ff ff ff 03  44 eb 08 6b  08 3b 04 bb ...];
        size = <67108864>;
        has-dpd;
    };

Every node with a ``jedec-id`` is a part: its id, its size (in bits for the
JESD216-based bindings, in bytes for SPI NAND), its page size, and what its
other properties say it can do. A ``sfdp-bfp`` is the chip's own SFDP Basic
Flash Parameter table, read from a real part, and ``sfdp-ff05`` and
``sfdp-ff84`` its xSPI profile 1.0 and 4-byte address instruction tables:
the record stores them (``sfdp_tables``) and derives what they say at load
(:func:`spiflash.sfdp.from_tables`), so a size or page size the node also
gives is stored only where it differs from the table's. ``readoc`` and
``writeoc`` (and the MSPI ``*-io-mode``) are the read and program modes the
board uses, so the part has at least those.

Devicetree has no field for the part name. It is taken from the first of
these that looks like a part number (letters, then two digits: ``mx25r6435f``,
``w25q64``) rather than a role (``flash``, ``ext_flash_ctrl``): the node's
name, a comment on its ``jedec-id`` line (``/* MX25LM51245 */``), its labels,
a descriptive ``compatible`` (``"issi,is25lp128", "jedec,spi-nor"``) and the
labels of the ``soc-nv-flash`` node inside it; a label's ``_spi``-style suffix
is dropped. A node none of them names is left out, as every record has a part
name. The maker is named only by a descriptive or one-maker compatible
(``"mxicy,mx25u"``); the id's first byte is not taken to name it.

Many boards carry the same chip. Nodes that say the same thing about a part
(name, id, size and every other value taken) are one record: the first file
in path order gives its ``file`` and ``line``, and a note lists the others.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

from spiflash.derive import ERASE_FEATURE
from spiflash.enums import Bound, TimedEvent
from spiflash.sfdp import BFPT_ID, FOUR_BYTE_ID, PROFILE1_ID
from spiflash.timings import TimingKey

from . import cparse, dts
from .ops import Opcodes
from .record import Record, make, member_via

if TYPE_CHECKING:
    from pathlib import Path

#: Where the boards and SoCs are, and the files read in them.
DIRS = ("boards", "dts")
SUFFIXES = (".dts", ".dtsi", ".overlay")


@dataclass(frozen=True)
class Binding:
    """What a driver's devicetree binding says about a node's values."""

    #: The properties that can give the size, each with whether it is in
    #: bits (else bytes).
    sizes: tuple[tuple[str, bool], ...] = (("size", True),)
    type: str = "nor"
    #: The binding is for one maker's parts ("mxicy,mx25u"), so its vendor
    #: prefix names the chip's maker rather than a controller's.
    chip_vendor: bool = False


_JESD216 = Binding()
_ONE_MAKER = Binding(chip_vendor=True)

#: Every binding with a ``jedec-id`` in Zephyr's boards, from its YAML in
#: :upstream:`zephyr:dts/bindings/`: the JESD216 ones
#: (``jedec,jesd216.yaml``: "flash capacity in bits"), and SPI NAND's
#: ``size-bytes``. A node whose compatibles name none of these (nor a
#: :data:`NOT_FLASH` one) stops the extraction, since its units are unknown.
BINDINGS = {
    "jedec,spi-nor": _JESD216,
    "jedec,nor": _JESD216,
    "nordic,qspi-nor": Binding(sizes=(("size", True), ("size-in-bytes", False))),
    "nxp,imx-flexspi-nor": _JESD216,
    "nxp,imx-flexspi-mx25um51345g": _JESD216,
    "nxp,s32-qspi-nor": _JESD216,
    "st,stm32-qspi-nor": _JESD216,
    "st,stm32-ospi-nor": _JESD216,
    "st,stm32-xspi-nor": _JESD216,
    "adi,max32-spixf-nor": _JESD216,
    "renesas,ra-ospi-b-nor": _JESD216,
    "andestech,qspi-nor": _JESD216,
    "mxicy,mx25u": _ONE_MAKER,
    "infineon,s28hx512t": _ONE_MAKER,
    "st,m95p32": _ONE_MAKER,
    "atmel,at45": _ONE_MAKER,
    "bflb,sf-flash": Binding(sizes=()),  # the SoC's own flash; no size given
    "jedec,spi-nand": Binding(sizes=(("size-bytes", False),), type="nand"),
}

#: Bindings with a ``jedec-id`` that are not SPI flash: HyperRAM, and
#: HyperFlash, whose id is its CFI words rather than a JEDEC read-id.
NOT_FLASH = {"nxp,s32-xspi-hyperram", "nxp,s32-qspi-hyperflash"}

#: Driver-agnostic and helper compatibles, which name no part.
_GENERIC = {"soc-nv-flash", "st,nor"}

# A part number: a few letters, then at least two digits (mx25r6435f,
# w25q64, at25, s28hl512t), not a role with a unit number (flash0, spi1).
_PART = re.compile(r"[a-z]{1,5}\d{2}[a-z0-9]*")

# readoc and writeoc values (nordic,qspi-nor's, nxp,s32-qspi-nor's and the
# STM32 and MAX32 ones): the operation and the feature. Dual program has no
# operation in spiflash's table.
_READOC = {
    "fastread": ("READ_1_1_1_FAST", "fast_read"),
    "read2o": ("READ_1_1_2", "dual_read"),
    "read2io": ("READ_1_2_2", "dual_read"),
    "read4o": ("READ_1_1_4", "quad_read"),
    "read4io": ("READ_1_4_4", "quad_read"),
    "1-1-1": ("READ_1_1_1_FAST", "fast_read"),
    "1-1-2": ("READ_1_1_2", "dual_read"),
    "1-2-2": ("READ_1_2_2", "dual_read"),
    "1-1-4": ("READ_1_1_4", "quad_read"),
    "1-4-4": ("READ_1_4_4", "quad_read"),
}
_WRITEOC = {
    "pp": ("PP_1_1_1", None),
    "pp2o": (None, None),
    "pp4o": ("PP_1_1_4", "quad_pp"),
    "pp4io": ("PP_1_4_4", "quad_pp"),
    "PP_1_1_4": ("PP_1_1_4", "quad_pp"),
    "PP_1_4_4": ("PP_1_4_4", "quad_pp"),
    "1-1-1": ("PP_1_1_1", None),
    "1-1-2": (None, None),
    "1-1-4": ("PP_1_1_4", "quad_pp"),
    "1-4-4": ("PP_1_4_4", "quad_pp"),
}
# MSPI I/O modes (mspi-io-mode, read-io-mode): the read feature.
_IO_MODE = {
    "MSPI_IO_MODE_DUAL": "dual_read",
    "MSPI_IO_MODE_DUAL_1_1_2": "dual_read",
    "MSPI_IO_MODE_DUAL_1_2_2": "dual_read",
    "MSPI_IO_MODE_QUAD": "quad_read",
    "MSPI_IO_MODE_QUAD_1_1_4": "quad_read",
    "MSPI_IO_MODE_QUAD_1_4_4": "quad_read",
    "MSPI_IO_MODE_OCTAL": "octal_read",
    "MSPI_IO_MODE_OCTAL_1_1_8": "octal_read",
    "MSPI_IO_MODE_OCTAL_1_8_8": "octal_read",
}
#: The bindings whose driver takes ``page-size`` as its own setting rather
#: than the part's page, so it is kept as a flag (``page-size=128``) and the
#: part's page is its BFPT's. The binding YAMLs say nothing of this: each
#: inherits jedec,jesd216.yaml's "Number of bytes in a page from JESD216 BFP
#: DW11". The drivers differ:
#:
#: - ``adi,max32-spixf-nor`` (:upstream:`zephyr:drivers/flash/flash_max32_spixf_nor.c`) uses it
#:   only as the flash layout page, ``.layout.pages_size =
#:   DT_INST_PROP(0, page_size)``, and programs by the BFPT's page;
#: - ``jedec,nor`` (:upstream:`zephyr:drivers/flash/flash_mspi_nor.c`) programs in chunks of it,
#:   which must fit the controller: ``FLASH_PAGE_SIZE_INST(inst) <=
#:   PACKET_DATA_LIMIT(inst)``; frdm_mcxe247's node says why it gives 128:
#:   "Single QSPI IP write must fit the 128-byte Tx FIFO."
PAGE_SIZE_IS_THE_DRIVERS = frozenset({"adi,max32-spixf-nor", "jedec,nor"})

#: The bindings whose driver ignores ``quad-enable-requirements``
#: (:upstream:`zephyr:dts/bindings/mtd/jedec,jesd216.yaml`'s JESD216 DW15
#: code): :upstream:`zephyr:drivers/flash/spi_nor.c` takes the requirement
#: from the BFPT, the node's or the part's. On such a node it stays a flag.
#: The QSPI and OSPI controllers' drivers (STM32, nRF, NXP, ...) and the
#: MSPI one (``jedec,nor``) use it.
QER_IGNORED_BY = frozenset({"jedec,spi-nor"})

#: The SFDP parameter tables a node copies, by property: the table id each is.
SFDP_TABLES = {"sfdp-bfp": BFPT_ID, "sfdp-ff05": PROFILE1_ID, "sfdp-ff84": FOUR_BYTE_ID}

#: The ways into 4-byte mode by their bit of BFPT DW16, as
#: ``enter-4byte-addr`` gives DW16[31:24] (bit 29, dedicated 4-byte
#: opcodes, is ``4byte_opcodes``).
ENTER_4B = {
    24: "en4b",
    25: "wren_en4b",
    26: "wrear",
    27: "brwr",
    28: "nv_cr",
    30: "always_4b",
}

#: Where the bindings are, which declare the properties the times are read
#: from (:func:`check_bindings`).
BINDINGS_DIR = "dts/bindings/mtd"

_MAX, _MIN = Bound.MAXIMUM, Bound.MINIMUM

#: The properties giving one of the part's times, each a whole number of
#: some unit: (its binding, the event, the bound, nanoseconds per unit).
#: The binding's words decide the bound: "Duration required to complete
#: the DPD command" (``t-enter-dpd``) and "... the RDPD command"
#: (``t-exit-dpd``) are the part's tDP and tRES1, which datasheets give as
#: maxima; ``t-reset-recovery``'s "Minimum time ... the chip needs to
#: recover after reset" is the host's least wait, so the part's own
#: maximum; ``t-reset-pulse``'s "Minimum duration ... of an active pulse on
#: the RESET line" a minimum; the AT45's "Time, in nanoseconds, needed by
#: the chip to enter (exit) the Deep Power-Down mode" maxima; SPI NAND's
#: ``*-duration-max`` maxima, in microseconds. 0 is "not given": the
#: drivers then wait for nothing. Not ``reset-duration-max``'s default,
#: 2000 µs, which is the binding's, not the part's.
TIMES: dict[str, tuple[str, TimingKey, Bound, int]] = {
    "t-enter-dpd": ("jedec,spi-nor-common.yaml", TimingKey(TimedEvent.DPD_ENTER), _MAX, 1),
    "t-exit-dpd": ("jedec,spi-nor-common.yaml", TimingKey(TimedEvent.DPD_EXIT), _MAX, 1),
    "t-reset-recovery": (
        "jedec,spi-nor-common.yaml",
        TimingKey(TimedEvent.RESET_RECOVERY),
        _MAX,
        1,
    ),
    "t-reset-pulse": ("jedec,nor-mspi.yaml", TimingKey(TimedEvent.RESET_PULSE), _MIN, 1),
    "enter-dpd-delay": ("atmel,at45.yaml", TimingKey(TimedEvent.DPD_ENTER), _MAX, 1),
    "exit-dpd-delay": ("atmel,at45.yaml", TimingKey(TimedEvent.DPD_EXIT), _MAX, 1),
    "block-erase-duration-max": (
        "jedec,spi-nand.yaml",
        TimingKey(TimedEvent.BLOCK_ERASE, 0xD8),
        _MAX,
        1000,
    ),
    "page-program-duration-max": (
        "jedec,spi-nand.yaml",
        TimingKey(TimedEvent.PAGE_PROGRAM),
        _MAX,
        1000,
    ),
    "page-read-duration-max": ("jedec,spi-nand.yaml", TimingKey(TimedEvent.PAGE_READ), _MAX, 1000),
    "reset-duration-max": (
        "jedec,spi-nand.yaml",
        TimingKey(TimedEvent.RESET_RECOVERY),
        _MAX,
        1000,
    ),
}

#: ``dpd-wakeup-sequence``: three times in nanoseconds, "(1) tDPDD (Delay
#: Time for Release from Deep Power-Down Mode) (2) tCDRP (CSn Toggling Time
#: before Release from Deep Power-Down Mode) (3) tRDP (Recovery Time for
#: Release from Deep Power-Down Mode)" (jedec,spi-nor-common.yaml). The
#: binding gives no bound; the MX25R datasheets, the parts it is for, give
#: them as a minimum, a minimum and a maximum. Its presence means the part
#: wakes by the chip select pulse, not by RDPD.
WAKEUP = "dpd-wakeup-sequence"
WAKEUP_TIMES = (
    (TimingKey(TimedEvent.DPD_MIN_TIME), _MIN),
    (TimingKey(TimedEvent.DPD_WAKE_PULSE), _MIN),
    (TimingKey(TimedEvent.DPD_EXIT), _MAX),
)

#: ``has-dpd``: "the device supports the DPD (0xB9) command ... implies
#: that the RDPD (0xAB) Release from Deep Power Down command is also
#: supported" (jedec,spi-nor-common.yaml), but for a part waking by a
#: ``dpd-wakeup-sequence``.
HAS_DPD = "has-dpd"

#: Each property read for the times and deep power-down, the binding
#: declaring it and the type it must have there.
DECLARED = {
    **{prop: (binding, "int") for prop, (binding, *_) in TIMES.items()},
    WAKEUP: ("jedec,spi-nor-common.yaml", "array"),
    HAS_DPD: ("jedec,spi-nor-common.yaml", "boolean"),
}


def check_bindings(root: Path) -> None:
    """Raise unless each property :data:`DECLARED` lists is declared, with
    the type it expects, in its binding under :data:`BINDINGS_DIR`: a
    renamed property then stops the build instead of giving nothing."""
    for prop, (binding, kind) in DECLARED.items():
        path = root / BINDINGS_DIR / binding
        if not path.is_file():
            msg = f"{BINDINGS_DIR}/{binding}: not fetched (tools/sources.toml)"
            raise ValueError(msg)
        found = re.search(rf"^  {re.escape(prop)}:\n    type: (\S+)$", path.read_text(), re.M)
        if found is None or found[1] != kind:
            msg = f"{binding}: no {prop} of type {kind}"
            raise ValueError(msg)


#: Properties kept in a record's ``flags`` as ``name`` or ``name=value``:
#: what the chip is or needs, not how the board wires or clocks it.
FLAGS = (
    "requires-ulbpr",
    "has-lock",
    "use-4b-addr-opcodes",
    "use-4byte-addressing",
    "use-flag-status-register",
    "use-fast-read",
    "use-sfdp",
    "enter-4byte-addr",
    "enter-4byte-command",
    "address-size-32",
    "ppsize-512",
    "quad-enable-requirements",
    "readoc",
    "writeoc",
    "read-io-mode",
    "write-io-mode",
    "mspi-io-mode",
    "mspi-data-rate",
    "protocol-mode",
    "mxicy,mx25r-power-mode",
    "sector-size",
    "block-size",
    "plane-bytes",
    "erase-block-size",
    "no-chip-erase",
    "no-sector-erase",
)


def extract(root: Path) -> list[Record]:
    check_bindings(root)
    parsed = []
    for d in DIRS:
        for path in sorted((root / d).rglob("*")):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            text = path.read_text()
            if "jedec-id" in text:
                parsed.append((path.relative_to(root).as_posix(), text, dts.nodes(text)))
    compatibles = _compatibles_by_label(parsed)
    found: dict[str, Record] = {}
    also: dict[str, list[str]] = {}
    for rel, text, top in parsed:
        directory = rel.rsplit("/", 1)[0]
        for node in (n for t in top for n in t.walk()):
            if "jedec-id" not in node.properties:
                continue
            rec = _Node(rel, text, node, compatibles.get(directory, {})).record()
            if rec is None:
                continue
            same = {k: v for k, v in rec.items() if k not in ("file", "line", "notes")}
            key = json.dumps(same, sort_keys=True)
            if key in found:
                also[key].append(f"{rel}:{rec['line']}")
            else:
                found[key] = rec
                also[key] = []
    for key, rec in found.items():
        if also[key]:
            rec["notes"].append(f"Also in {', '.join(also[key])}")
    return list(found.values())


def _compatibles_by_label(
    parsed: list[tuple[str, str, list[dts.Node]]],
) -> dict[str, dict[str, str]]:
    """Each directory's nodes' compatibles, by label: an overlay that changes
    a board's flash node (``&flexspi { ext_flash_ctrl: flash-controller@0
    {...} }``) need not repeat its ``compatible``."""
    out: dict[str, dict[str, str]] = {}
    for rel, _, top in parsed:
        directory = rel.rsplit("/", 1)[0]
        for node in (n for t in top for n in t.walk()):
            prop = node.properties.get("compatible")
            if prop is None or prop.value is None:
                continue
            for label in node.labels:
                out.setdefault(directory, {}).setdefault(label, prop.value)
    return out


class _Node:
    """One node with a ``jedec-id``, and the record it makes."""

    def __init__(self, rel: str, text: str, node: dts.Node, compatibles: dict[str, str]) -> None:
        self.rel, self.text, self.node = rel, text, node
        self.line = cparse.line_of(text, node.offset)
        self.props = {k: p.value for k, p in node.properties.items()}
        value = self.props.get("compatible") or next(
            (compatibles[lab] for lab in node.labels if lab in compatibles), None
        )
        if value is None:
            self.fail("a node with a jedec-id and no compatible")
        self.compatible = dts.strings(value)
        self.features: set[str] = set()
        self.ops = Opcodes()
        self.notes: list[str] = []
        #: The ways into 4-byte mode, each with the property giving it.
        self.four_byte: dict[str, str] = {}
        #: The ``via`` of feature claims a property's value gives.
        self.claim_via: dict[str, str] = {}

    def fail(self, why: str) -> NoReturn:
        msg = f"{self.rel}:{self.line}: {why}"
        raise ValueError(msg)

    def cell(self, prop: str) -> int | None:
        """The value of a single-cell property, or None if it is not there."""
        value = self.props.get(prop)
        if value is None:
            return None
        try:
            (n,) = dts.cells(value)
        except (ValueError, cparse.EvalError) as e:
            msg = f"{self.rel}:{self.line}: {prop} = {value}: {e}"
            raise ValueError(msg) from e
        return n

    def string(self, prop: str) -> str | None:
        value = self.props.get(prop)
        return dts.strings(value)[0] if value else None

    def record(self) -> Record | None:
        """The record, or ``None`` for a node that is not SPI flash or that
        nothing names."""
        if NOT_FLASH & set(self.compatible):
            return None
        binding_name = next((c for c in self.compatible if c in BINDINGS), None)
        if binding_name is None:
            self.fail(f"unknown flash binding {self.compatible}")
        binding = BINDINGS[binding_name]
        descriptive = [c for c in self.compatible if c not in BINDINGS and c not in _GENERIC]
        name = self.name(descriptive)
        if name is None:
            return None
        vendor = None
        if descriptive:
            vendor = descriptive[0].split(",")[0]
        elif binding.chip_vendor:
            vendor = binding_name.split(",")[0]
        jedec_id = dts.bytestring(self.props["jedec-id"] or "")

        self.ops.add("RDID", "jedec-id")
        size = next(
            (n if not bits else n // 8 for prop, bits in binding.sizes if (n := self.cell(prop))),
            None,
        )
        page_size = 512 if "ppsize-512" in self.props else self.cell("page-size")
        driver_page = None
        if binding_name in PAGE_SIZE_IS_THE_DRIVERS and "page-size" in self.props:
            driver_page, page_size = page_size, None
        self.notes = _comments(self.text, self.node)
        # The tables' facts are derived at load (spiflash.sfdp); make()
        # drops a size or page size they repeat. Zephyr's spi_nor driver
        # refuses a size the BFPT contradicts, and other drivers use the
        # page-size property as their write chunk, so a value that differs
        # is the node's own.
        tables = {
            f"{table_id:04x}": dts.bytestring(self.props[prop] or "").hex()
            for prop, table_id in SFDP_TABLES.items()
            if self.props.get(prop)
        }
        via = {"sfdp_tables": "; ".join(p for p in SFDP_TABLES if self.props.get(p))}
        if not tables:
            via = {}
            if "use-sfdp" in self.props:
                self.ops.add("RDSFDP", "use-sfdp")
        self.modes()
        self.capabilities(binding)
        times, time_via = self.times()
        via |= time_via
        qer = self.string("quad-enable-requirements")
        if qer is not None and binding_name not in QER_IGNORED_BY:
            via["quad_enable_requirement"] = f"quad-enable-requirements={qer}"
        else:
            qer = None
        return make(
            "zephyr",
            self.rel,
            self.line,
            name,
            type=binding.type,
            vendor=vendor,
            id=jedec_id[:3].hex(),
            ext_id=jedec_id[3:].hex() or None,
            size=size,
            page_size=page_size,
            features=self.features,
            flags=self.flags() + ([f"page-size={driver_page}"] if driver_page else []),
            via=via | self.claim_via | member_via("four_byte_modes", self.four_byte),
            quad_enable_requirement=qer,
            four_byte_modes=list(self.four_byte),
            timings=times,
            opcodes=self.ops.to_json() if binding.type == "nor" else [],
            sfdp_tables=tables,
            notes=self.notes,
        )

    def name(self, descriptive: list[str]) -> str | None:
        """The part name, from the first place that gives one (see the
        module's description)."""
        jedec = self.node.properties["jedec-id"]
        eol = self.text.find("\n", jedec.offset)
        candidates = [
            self.node.name.split("@")[0],
            *cparse.comments(self.text[jedec.offset : eol]),
            *self.node.labels,
            self.node.reference or "",
            *(c.split(",", 1)[1] for c in descriptive),
        ]
        for child in self.node.children:
            candidates += child.labels
        for candidate in candidates:
            token = candidate.strip().split("_")[0].lower()
            if _PART.fullmatch(token):
                return token
        return None

    def modes(self) -> None:
        """The read and program modes the board uses: readoc and writeoc,
        use-fast-read, and the MSPI I/O mode and data rate."""
        for prop, table in (("readoc", _READOC), ("writeoc", _WRITEOC)):
            value = self.string(prop)
            if value is None:
                continue
            if value not in table:
                self.fail(f"unknown {prop} {value!r}")
            op, feature = table[value]
            if feature:
                self.features.add(feature)
            if op:
                self.ops.add(op, f"{prop} = {value}")
        if "use-fast-read" in self.props:
            self.features.add("fast_read")
            self.ops.add("READ_1_1_1_FAST", "use-fast-read")
        mode = self.string("mspi-io-mode")
        for value in (mode, self.string("read-io-mode")):
            if value in _IO_MODE:
                self.features.add(_IO_MODE[value])
        if (mode or "").startswith("MSPI_IO_MODE_OCTAL") and (
            self.string("mspi-data-rate") == "MSPI_DATA_RATE_DUAL"
        ):
            self.features.update({"octal_dtr_read", "octal_dtr_pp"})

    def capabilities(self, binding: Binding) -> None:
        """What the other properties say the part has."""
        if "has-lock" in self.props:
            self.features.add("lock")
        if "use-flag-status-register" in self.props:
            self.ops.add("RDFSR", "use-flag-status-register")
        # The binding (jedec,spi-nor-common.yaml) says the part needs ULBPR,
        # 0x98, to unlock its block protection; spi_nor.c sends it.
        if "requires-ulbpr" in self.props:
            self.ops.add("ULBPR", "requires-ulbpr")
        if "use-4b-addr-opcodes" in self.props:
            self.features.update({"4byte_addr", "4byte_opcodes"})
        if {"address-size-32", "use-4byte-addressing"} & set(self.props):
            self.features.add("4byte_addr")
        self.four_byte_modes()
        erase = self.cell("erase-block-size")
        if binding.type == "nor" and erase in ERASE_FEATURE:
            self.features.add(ERASE_FEATURE[erase].value)

    def times(self) -> tuple[dict[str, dict[str, int]], dict[str, str]]:
        """The part's times (:data:`TIMES`, :data:`WAKEUP`), as a record's
        ``timings``, and their ``via``: ``timings.<key>`` for a property
        giving one, ``timings`` for the wake-up sequence, which gives
        three. ``has-dpd`` gives the ``DP`` and ``RDPD`` operations (not
        ``RDPD`` with a wake-up sequence). A value of 0 is not given."""
        out: dict[str, dict[str, int]] = {}
        via: dict[str, list[str]] = {}
        for prop, (_binding, key, bound, unit) in TIMES.items():
            n = self.cell(prop)
            if n and prop.endswith("-dpd-delay") and "use-udpd" in self.props:
                # The AT45's times are then Ultra-Deep Power-Down's.
                self.notes.append(f"{prop}={n} not read: use-udpd, so ultra-deep power-down's")
                continue
            if n:
                out.setdefault(str(key), {})[str(bound)] = n * unit
                token = f"{prop}={_flag_value(self.props[prop] or '')}"
                via.setdefault(f"timings.{key}", []).append(token)
        wakeup = self.props.get(WAKEUP)
        if wakeup is not None:
            cells = [c for part in cparse.split_top(wakeup) for c in dts.cells(part)]
            if len(cells) != len(WAKEUP_TIMES):
                self.fail(f"{WAKEUP} = {wakeup}: not three times")
            for (key, bound), n in zip(WAKEUP_TIMES, cells, strict=True):
                if n:
                    given = out.setdefault(str(key), {})
                    if str(bound) in given and given[str(bound)] != n:
                        self.fail(f"{WAKEUP} and another property give {key} otherwise")
                    given[str(bound)] = n
            via.setdefault("timings", []).append(f"{WAKEUP}={_flag_value(wakeup)}")
        if HAS_DPD in self.props:
            self.ops.add("DP", HAS_DPD)
            if wakeup is None:
                self.ops.add("RDPD", HAS_DPD)
        return out, {key: "; ".join(tokens) for key, tokens in via.items()}

    def four_byte_modes(self) -> None:
        """The ways into 4-byte mode: ``enter-4byte-addr``, BFPT DW16[31:24]
        as a byte (jedec,jesd216.yaml; spi_nor.c's spi_nor_set_address_mode
        and nrf_qspi_nor.c read it so), 0 and 0xff saying nothing; and the
        Renesas OSPI binding's ``enter-4byte-command``, the opcode its
        driver sends with no write enable (flash_renesas_ra_ospi_b.c,
        flash_ospi_b_4byte_enable), of which 0xb7 is the one known. A byte
        with the reserved bit 7 set is not DW16's (p2d.dts gives
        GD25LE255E's ``<0xb7>``, EN4B's opcode): it is not read, and a note
        says so. Bit 5 is dedicated 4-byte opcodes, a ``4byte_opcodes``
        claim, not a way in."""
        byte = self.cell("enter-4byte-addr")
        if byte is not None and byte not in (0, 0xFF):
            token = f"enter-4byte-addr={_flag_value(self.props['enter-4byte-addr'] or '')}"
            if byte & 0x80 or byte > 0xFF:
                what = "EN4B's opcode" if byte == 0xB7 else "not a byte"
                self.notes.append(
                    f"{token} not read: {what}, not a JESD216 DW16[31:24] byte, whose bit 7 "
                    "is reserved"
                )
            else:
                for bit, mode in ENTER_4B.items():
                    if byte >> (bit - 24) & 1:
                        self.four_byte[mode] = token
                if byte & 0x20:
                    self.features.add("4byte_opcodes")
                    if not self.four_byte:
                        self.claim_via["feature:4byte_opcodes"] = token
        command = self.cell("enter-4byte-command")
        if command is not None:
            if command != 0xB7:
                self.fail(f"enter-4byte-command 0x{command:02x}: only 0xb7 is known")
            value = _flag_value(self.props["enter-4byte-command"] or "")
            self.four_byte["en4b"] = f"enter-4byte-command={value}"

    def flags(self) -> list[str]:
        """The compatibles and the :data:`FLAGS` properties."""
        flags = [c for c in self.compatible if c not in _GENERIC]
        for prop in FLAGS:
            if prop in self.props:
                value = self.props[prop]
                flags.append(prop if value is None else f"{prop}={_flag_value(value)}")
        return flags


def _flag_value(value: str) -> str:
    """A property's value as a flag shows it: a string or a single cell
    plainly, anything else as written."""
    if value.startswith('"'):
        return ",".join(dts.strings(value))
    try:
        (n,) = dts.cells(value)
    except (ValueError, cparse.EvalError):
        return " ".join(value.split())
    return f"0x{n:x}" if value.strip("<> ").startswith("0x") else str(n)


def _comments(text: str, node: dts.Node) -> list[str]:
    """The comments in the node's own lines, not those of the nodes in it."""
    body = text[node.offset : node.end]
    for child in node.children:
        a, b = child.offset - node.offset, child.end - node.offset
        body = body[:a] + " " * (b - a) + body[b:]
    return cparse.comments(body)
