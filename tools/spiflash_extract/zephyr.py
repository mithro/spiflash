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
other properties say it can do. A ``sfdp-bfp`` is the chip's own SFDP table,
read from a real part (:mod:`spiflash_extract.sfdp`). ``readoc`` and
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
from typing import TYPE_CHECKING, Any, NoReturn

from spiflash.derive import ERASE_BY_OPCODE

from . import cparse, dts, sfdp
from .ops import Opcodes
from .record import ERASE_FEATURES, Record, make

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
# The reads a BFP table lists: the operation, and the feature.
_SFDP_READ = {
    "1-1-2": ("READ_1_1_2", "dual_read"),
    "1-2-2": ("READ_1_2_2", "dual_read"),
    "1-1-4": ("READ_1_1_4", "quad_read"),
    "1-4-4": ("READ_1_4_4", "quad_read"),
    "2-2-2": (None, None),
    "4-4-4": (None, "qpi"),
}

#: Properties kept in a record's ``flags`` as ``name`` or ``name=value``:
#: what the chip is or needs, not how the board wires or clocks it.
FLAGS = (
    "has-dpd",
    "dpd-wakeup-sequence",
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
    "sfdp-ff05",
    "sfdp-ff84",
    "sector-size",
    "block-size",
    "plane-bytes",
    "erase-block-size",
    "no-chip-erase",
    "no-sector-erase",
)


def extract(root: Path) -> list[Record]:
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
        self.notes = _comments(self.text, self.node)
        erasers = None
        bfp_value = self.props.get("sfdp-bfp")
        if bfp_value:
            bfp = sfdp.decode(dts.bytestring(bfp_value))
            erasers = self.sfdp(bfp)
            if size is None:
                size = bfp.size
            elif size != bfp.size:
                self.notes.append(f"sfdp-bfp gives {bfp.size} bytes, size {size}")
            if page_size is None:
                page_size = bfp.page_size
            elif bfp.page_size and page_size != bfp.page_size:
                self.notes.append(
                    f"sfdp-bfp gives a {bfp.page_size}-byte page, page-size {page_size}"
                )
        if bfp_value or "use-sfdp" in self.props:
            self.features.add("sfdp")
            self.ops.add("RDSFDP", "sfdp-bfp" if bfp_value else "use-sfdp")
        self.modes()
        self.capabilities(binding)
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
            erasers=erasers,
            features=self.features,
            flags=self.flags(bfp=bfp_value is not None),
            opcodes=self.ops.to_json() if binding.type == "nor" else [],
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

    def sfdp(self, bfp: sfdp.Bfp) -> list[dict[str, Any]]:
        """The features and operations a ``sfdp-bfp`` table gives; its erasers."""
        for protocol, opcode in bfp.reads.items():
            op, feature = _SFDP_READ[protocol]
            if feature:
                self.features.add(feature)
            if op:
                self.ops.add(op, f"sfdp-bfp: {protocol}", value=opcode)
        if 4 in bfp.address_bytes:
            self.features.add("4byte_addr")
        erasers = []
        for opcode, block in bfp.erases:
            if block in ERASE_FEATURES:
                self.features.add(ERASE_FEATURES[block])
            erasers.append({"opcode": opcode, "blocks": [[block, bfp.size // block]]})
            if opcode in ERASE_BY_OPCODE:
                self.ops.add(ERASE_BY_OPCODE[opcode], "sfdp-bfp: erase type", value=opcode)
        return erasers

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
        if "use-4b-addr-opcodes" in self.props:
            self.features.update({"4byte_addr", "4byte_opcodes"})
        if {"address-size-32", "use-4byte-addressing"} & set(self.props):
            self.features.add("4byte_addr")
        # JESD216 DWORD 16 bits 31:24; bits 0 and 1 are "issue B7h".
        enter_4b = self.cell("enter-4byte-addr")
        if enter_4b:
            self.features.add("4byte_addr")
            if enter_4b & 3:
                self.ops.add("EN4B", "enter-4byte-addr")
        command = self.cell("enter-4byte-command")
        if command:
            self.features.add("4byte_addr")
            self.ops.add("EN4B", "enter-4byte-command", value=command)
        erase = self.cell("erase-block-size")
        if binding.type == "nor" and erase in ERASE_FEATURES:
            self.features.add(ERASE_FEATURES[erase])

    def flags(self, *, bfp: bool) -> list[str]:
        """The compatibles and the :data:`FLAGS` properties."""
        flags = [c for c in self.compatible if c not in _GENERIC]
        if bfp:
            flags.append("sfdp-bfp")
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
