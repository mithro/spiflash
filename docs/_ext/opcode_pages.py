"""A page per SPI operation: what it does, its timing diagram, where its bus
shape comes from, and every part in the database that has it."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from opcode_timing import diagram
from page_markup import (
    EM_DASH,
    JEP106,
    JESD216,
    JESD251,
    KIND_TITLE,
    badge,
    chip_slug,
    esc,
    list_table,
    size_text,
    source_badge,
    table_id,
    vendor_link,
    vendor_of,
)
from spiflash.enums import DataPhase, FlashType, OperationKind, ShapeSource
from spiflash.opcodes import OPERATIONS

if TYPE_CHECKING:
    from spiflash import Database, Flash
    from spiflash.opcodes import Operation

# Where the bus-shape numbers come from, as the pages cite it.
SHAPE_TEXT = {
    ShapeSource.LINUX_DEFAULT: (
        "{sfsrc}`linux`'s `spi_nor_init_default_params()` in "
        "{upstream}`linux:drivers/mtd/spi-nor/core.c` sets up, for every part, read "
        "(0x03) with no dummy clocks, fast read (0x0b) with 8, and page program (0x02); "
        "and quad page program (0x32) for parts flagged `SPI_NOR_QUAD_PP`."
    ),
    ShapeSource.LINUX_NO_SFDP: (
        "{sfsrc}`linux`'s `spi_nor_no_sfdp_init_params()` in "
        "{upstream}`linux:drivers/mtd/spi-nor/core.c` gives the 1-1-2, 1-1-4 and 1-1-8 "
        "reads 8 dummy clocks, and octal DTR reads 20, for parts that have no SFDP tables."
    ),
    ShapeSource.LINUX_4B: (
        "{sfsrc}`linux`'s `spi_nor_convert_3to4_read()` and its siblings in "
        "{upstream}`linux:drivers/mtd/spi-nor/core.c` map each 3-byte-address operation "
        "to its 4-byte form, which keeps the same dummy clocks."
    ),
    ShapeSource.FLASHPROG_FEATURES: (
        "{sfsrc}`flashprog`'s `FEATURE_*` definitions in {upstream}`flashprog:include/flash.h` "
        "give the dummy clocks: 8 for fast read (0x0b), dual output (0x3b) and quad "
        "output (0x6b), 4 for dual I/O (0xbb), 6 for quad I/O (0xeb)."
    ),
    ShapeSource.FLASHROM_SIZES: (
        "{sfsrc}`flashrom`'s `JEDEC_*_OUTSIZE` and `_INSIZE` definitions in "
        "{upstream}`flashrom:include/spi.h` give the bytes sent and received: the "
        "command, the address, any dummy bytes, and the data."
    ),
    ShapeSource.JESD216: (
        f"[JEDEC JESD216]({JESD216}) (SFDP) specifies a 3-byte address and 8 dummy clocks."
    ),
    ShapeSource.LINUX_SPINAND: (
        "{sfsrc}`linux`'s `SPINAND_*_OP` macros in "
        "{upstream}`linux:include/linux/mtd/spinand.h` give each SPI NAND operation's "
        "address bytes and lines; its dummy bytes are each part's, from the op variants "
        "its entry lists (shown on the chip pages as dummy clocks)."
    ),
    ShapeSource.PART: (
        "The details vary by part: check its datasheet. Where the dummy clocks are "
        "shown as varying, the part sets them in a register or states them in its SFDP "
        "tables."
    ),
}

# What some operations do beyond what their timing says.
NOTES = {
    "RDID": (
        f"The flash answers with its JEDEC id: the manufacturer's [JEP106]({JEP106}) code, "
        "then two device bytes (usually the memory type and the capacity). Many parts "
        "carry on with extended id bytes if the host keeps clocking; {sfsrc}`linux` matches "
        "up to six. "
        "Manufacturers in later JEP106 banks should send `0x7f` continuation codes "
        "first, but many parts leave them out."
    ),
    "RDID_ATMEL": (
        "Older Atmel AT25F parts answer 0x15 (0x1d on some) instead of the JEDEC read-id "
        "([`RDID`](RDID.md)), with a manufacturer byte and one device byte."
    ),
    "RDID_M95": (
        "ST's M95 SPI EEPROMs read an identification page: the address is 2 bytes on "
        "parts up to 64 KiB and 3 on larger ones."
    ),
    "REMS": (
        "With address 0x000000 the flash answers the manufacturer id, then the device "
        "id; with address 0x000001 most parts answer them the other way round."
    ),
    "RES": (
        "After three dummy bytes the flash answers its one-byte electronic signature "
        "({sfsrc}`flashrom`'s RES1; RES2 parts answer two bytes). The same opcode alone is "
        "the release from deep power-down on many parts ([`RDPD`](RDPD.md)), so this "
        "wakes them too. "
        "Older parts use this in place of the JEDEC id ([`RDID`](RDID.md))."
    ),
    "DP": (
        "Puts the flash in deep power-down once chip select goes high (after tDP), where it "
        "ignores everything but its release: [`RDPD`](RDPD.md), or on some parts (Macronix's "
        "MX25R) a pulse of chip select. The chip pages give the times "
        "([](../derived.md#times))."
    ),
    "RDPD": (
        "The release from deep power-down alone: 0xab, then chip select high, and the part is "
        "ready after tRES1. On many parts the same opcode followed by dummy bytes reads a "
        "signature ([`RES`](RES.md)), but not on all (the M25PX parts give none), so the two "
        "are separate operations."
    ),
    "RDSFDP": (
        f"Reads the Serial Flash Discoverable Parameters ([JESD216]({JESD216})) from the given "
        "address: tables describing the part's size, erase types, read modes and "
        "dummy clocks. {sfsrc}`linux` reads them to set up most modern parts."
    ),
    "WRSR": (
        "Writes status register 1, and on many parts status register 2 as a second "
        "byte. The host sends Write Enable (0x06) first, or on some SST parts Enable "
        "Write Status Register ([`EWSR`](EWSR.md), 0x50)."
    ),
    "EWSR": (
        "SST parts need this, rather than Write Enable (0x06), before Write Status "
        "Register ([`WRSR`](WRSR.md))."
    ),
    "WRSR2": "Writes status register 2 on its own (Winbond and compatible parts).",
    "WRSR3": "Writes status register 3 on its own (Winbond and compatible parts).",
    "RDFSR": (
        "Reads Micron's flag status register: whether the part is ready, and whether a "
        "program or erase failed."
    ),
    "CLSR": "Clears the program and erase error bits in the status register (Spansion).",
    "SET_READ_PARAMS": (
        "Sets the dummy clocks and the wrap length of QPI reads (Winbond); sent in QPI mode."
    ),
    "EN4B": (
        "Switches the part to 4-byte addresses, so the 3-byte-address commands take 4 "
        "bytes. Some parts need Write Enable first."
    ),
    "EX4B": "Switches the part back to 3-byte addresses.",
    "WREAR": (
        "Writes the extended address register: the top address byte used by 3-byte-address "
        "commands on parts larger than 16 MiB. On some parts bit 7 switches to 4-byte "
        "addresses."
    ),
    "RDEAR": "Reads the extended address register (written by [`WREAR`](WREAR.md)).",
    "BRWR": (
        "Writes Spansion's bank address register, which does the extended address "
        "register's job ([`WREAR`](WREAR.md)) on its parts."
    ),
    "BRRD": "Reads Spansion's bank address register (written by [`BRWR`](BRWR.md)).",
    "EQPI_38": (
        "Switches Winbond, GigaDevice and compatible parts to QPI, where every command, "
        "address and data byte goes over all four lines."
    ),
    "EQPI_35": "Switches Macronix, ISSI and SST parts to QPI.",
    "RSTQIO_FF": (
        "Leaves QPI mode (entered with [`EQPI_38`](EQPI_38.md)); sent in QPI mode, so on "
        "all four lines."
    ),
    "RSTQIO_F5": "Leaves QPI mode on the parts that enter it with 0x35 ([`EQPI_35`](EQPI_35.md)).",
    "BP": (
        "Programs one byte per command, for parts without page program ([`PP_1_1_1`](PP_1_1_1.md))."
    ),
    "AAI_WP": (
        "SST's auto-address-increment program: the first command carries the address and "
        "two bytes, each following one only the next two bytes, and Write Disable (0x04) "
        "ends the sequence."
    ),
    "BE_4K_PMC": (
        "PMC's opcode for the 4 KiB sector erase other parts do with 0x20 ([`BE_4K`](BE_4K.md))."
    ),
    "BE_ALT1": "A block erase some older parts use this opcode for.",
    "BE_ALT2": "A block or page erase some parts use this opcode for.",
    "BE_40": (
        "Erases one of the small parameter blocks of Intel's S33 and Spansion's S25FL-P parts."
    ),
    "BE_53": "Spansion's 32 KiB block erase with a 4-byte address.",
    "BE_256": "Erases one 256-byte page.",
    "DIE_ERASE": (
        "Erases one die of a stacked part (Micron); the address selects the die. On "
        "single-die parts chip erase ([`CHIP_ERASE`](CHIP_ERASE.md)) does the same."
    ),
    "DIE_ERASE_61": (
        "Infineon's (Cypress's) die erase, on its stacked S25H and S28H parts in "
        "4-byte address mode; the address selects the die."
    ),
    "DIE_SELECT": (
        "Selects which die of a stacked Winbond part the following commands go to; "
        "the data byte is the die, from 0. {sfsrc}`linux` selects each in turn to "
        "poll its busy bit."
    ),
    "NAND_RDID": (
        "The flash answers with its id straight after the opcode: the manufacturer "
        "byte, then its device bytes."
    ),
    "NAND_RDID_DUMMY": "The flash answers with its id after one dummy byte.",
    "NAND_RDID_ADDR": "The flash answers with its id after one address byte (0).",
    "NAND_PAGE_READ": (
        "Reads the page at a 3-byte row (page) address into the part's cache; the "
        "host polls the status feature until the busy bit clears, then reads from "
        "the cache."
    ),
    "NAND_PROGRAM_EXECUTE": (
        "Programs the cache into the page at a 3-byte row (page) address; the host "
        "loads the cache first, and sends Write Enable (0x06) before."
    ),
    "NAND_BLOCK_ERASE": (
        "Erases the block holding a 3-byte row (page) address. The host sends Write "
        "Enable (0x06) first, then polls the status feature until the busy bit clears."
    ),
    "NAND_GET_FEATURE": (
        "Reads the feature (register) at a 1-byte address: 0xa0 block protection, "
        "0xb0 configuration, 0xc0 status, and the vendors' own (Micron's die select "
        "at 0xd0)."
    ),
    "NAND_SET_FEATURE": "Writes the feature (register) at a 1-byte address.",
    "NAND_BBM_SWAP": (
        "Adds a link to the part's bad block lookup table: the logical block, then "
        "the physical block standing in for it."
    ),
    "NAND_READ_BBM_LUT": "Reads the part's bad block lookup table, after 8 dummy clocks.",
    "NAND_DIE_SELECT": (
        "Selects which die of a stacked Winbond SPI NAND part (the W25M02GV) the "
        "following commands go to; the data byte is the die, from 0."
    ),
    "CHIP_ERASE_ALT": "Most parts accept this as well as 0xc7 ([`CHIP_ERASE`](CHIP_ERASE.md)).",
    "CHIP_ERASE_ATMEL": "Atmel AT25F parts' chip erase.",
    "READ_8D_8D_8D": (
        f"Octal DTR (xSPI, [JESD251]({JESD251})) sends two bytes per clock, one on each "
        "edge, over all eight lines. The command is two bytes, the opcode and an "
        "extension byte (the "
        "opcode repeated or inverted, as the part's SFDP says), and the flash drives a "
        "data strobe (DS) with its data."
    ),
    "PP_8D_8D_8D": (
        f"Octal DTR (xSPI, [JESD251]({JESD251})) page program; it keeps the legacy page "
        "program opcode ([`PP_1_1_1`](PP_1_1_1.md))."
    ),
}

_PHASE_NAMES = ("command", "address", "data")


def explain(op: Operation) -> str:
    """What ``op`` does, in a paragraph."""
    cmd, addr, data = op.lines
    width = f"{op.address_bytes}-byte"
    if op.flash_type is FlashType.NAND:
        # A read from cache or a load; the rest are in NOTES.
        cache = op.name.startswith(("NAND_READ_CACHE", "NAND_PROGRAM_LOAD", "NAND_RANDOM_LOAD"))
        text = _explain_nand_cache(op) if cache else ""
    elif op.kind is OperationKind.READ:
        text = (
            f"Reads data from a {width} address. The host sends the command on {_lines(cmd)} "
            f"and the address on {_lines(addr)}"
            + (f", then {_dummy(op)}" if op.dummy_clocks != 0 else "")
            + f"; the flash answers with data on {_lines(data)}, from that address on, for as "
            "long as the host keeps chip select low and the clock running."
        )
    elif op.kind is OperationKind.PROGRAM:
        text = (
            f"Programs data at a {width} address: the command goes on {_lines(cmd)}, the "
            f"address on {_lines(addr)}, and the data on {_lines(data)}. Programming only "
            "turns bits from 1 to 0, so the area is erased first. The host sends Write "
            "Enable (0x06) before it, then polls the status register until the busy bit "
            "clears." + (" The data wraps within its page." if op.data_bytes is None else "")
        )
    elif op.kind is OperationKind.ERASE and op.address_bytes:
        text = (
            f"Erases (sets every bit to 1 in) the block containing a {width} address. The "
            "host sends Write Enable (0x06) first, then polls the status register until "
            "the busy bit clears."
        )
    elif op.kind is OperationKind.ERASE:
        text = (
            "Erases the whole chip. The host sends Write Enable (0x06) first, then polls "
            "the status register until the busy bit clears, which can take minutes on a "
            "large part."
        )
    else:
        text = ""
    note = NOTES.get(op.name, "")
    return " ".join(t for t in (text, note) if t) or esc(op.description) + "."


def _explain_nand_cache(op: Operation) -> str:
    """A SPI NAND read from cache or program load, in a paragraph."""
    cmd, addr, data = op.lines
    width = f"{op.address_bytes}-byte"
    if op.kind is OperationKind.READ:
        return (
            f"Reads from the part's cache, which a page read "
            f"([`NAND_PAGE_READ`](NAND_PAGE_READ.md)) filled, from a {width} column address. "
            f"The host sends the command on {_lines(cmd)} and the address on {_lines(addr)}"
            + (f", then {_dummy(op)}" if op.dummy_clocks != 0 else "")
            + f"; the flash answers with data on {_lines(data)}, for as long as the host "
            "keeps the clock running. A part's own dummy clocks are on its chip page."
        )
    keeps = "keeps the rest of" if op.name.startswith("NAND_RANDOM_LOAD") else "clears"
    return (
        f"Loads data into the part's cache from a {width} column address, and {keeps} "
        f"the cache: the command goes on {_lines(cmd)}, the address on {_lines(addr)}, "
        f"and the data on {_lines(data)}. Program execute "
        "([`NAND_PROGRAM_EXECUTE`](NAND_PROGRAM_EXECUTE.md)) then programs the cache "
        "into a page."
    )


def _lines(n: int) -> str:
    return "one line" if n == 1 else f"{n} lines"


def _dummy(op: Operation) -> str:
    if op.dummy_clocks is None:
        return "a number of dummy clocks that depends on the part"
    return f"{op.dummy_clocks} dummy clocks"


def phases(op: Operation) -> list[list[str]]:
    """The rows of the phase table: phase, lines, clocks, what goes over."""
    cmd, addr, data = op.lines
    per_clock = 2 if op.dtr else 1
    rows = []
    command_bytes = 2 if op.dtr else 1
    rows.append(
        [
            "Command",
            str(cmd),
            str(8 * command_bytes // (cmd * per_clock)),
            f"the opcode, 0x{op.opcode:02x}" + (", and its extension byte" if op.dtr else ""),
        ]
    )
    if op.address_bytes:
        bits = 8 * op.address_bytes
        rows.append(
            [
                "Address",
                str(addr),
                str(bits // (addr * per_clock)),
                f"{bits} bits, most significant first",
            ]
        )
    if op.dummy_clocks != 0:
        clocks = EM_DASH if op.dummy_clocks is None else str(op.dummy_clocks)
        rows.append(["Dummy", EM_DASH, clocks, "nothing: the flash gets ready to answer"])
    if op.data is not None:
        who = "flash to host" if op.data is DataPhase.READ else "host to flash"
        many = "any number of" if op.data_bytes is None else str(op.data_bytes)
        clocks_per_byte = 8 // (data * per_clock)
        rows.append(["Data", str(data), f"{clocks_per_byte} per byte", f"{many} bytes, {who}"])
    return rows


def related(op: Operation) -> list[Operation]:
    """Operations that share ``op``'s opcode, and its 3- and 4-byte-address pair."""
    out = [
        o
        for o in OPERATIONS.values()
        if o.opcode == op.opcode and o is not op and o.flash_type is op.flash_type
    ]
    pair = op.name.removesuffix("_4B") if op.name.endswith("_4B") else op.name + "_4B"
    if pair in OPERATIONS and OPERATIONS[pair] not in out:
        out.append(OPERATIONS[pair])
    return out


def op_link(name: str, prefix: str = "") -> str:
    return f"[`{name}`]({prefix}{name}.md)"


def operation_page(op: Operation, flashes: list[Flash], slugs: dict[int, str]) -> str:
    out = [f"# `{op.name}`: {esc(op.description)}\n"]
    tags = [
        f"{{sfop}}`0x{op.opcode:02x}`",
        badge(op.flash_type.label, "secondary"),
        badge(KIND_TITLE[op.kind], "primary"),
        badge(op.protocol, "info"),
    ]
    if op.address_bytes:
        tags.append(badge(f"{op.address_bytes}-byte address", "secondary"))
    if op.data is not None:
        tags.append(badge("reads data" if op.data is DataPhase.READ else "writes data", "success"))
    out += [" ".join(tags) + "\n", "## What it does\n", explain(op) + "\n"]

    out += ["## Timing\n", "```{wavedrom}", json.dumps(diagram(op), indent=1), "```\n"]
    out.append(
        "- SPI mode 0: the clock idles low, and data changes on its falling edge and is "
        "sampled on its rising edge.\n"
        "- Long phases are shortened at the gaps.\n"
        "- `z` (the middle level) is a line nobody drives; the hatched cells are don't-care.\n"
    )
    out.append(list_table(["Phase", "Lines", "Clocks", "What"], phases(op), "sf-table"))
    out.append("")
    if op.shape_source:
        out.append("### Where the bus shape comes from\n")
        out += [f"- {SHAPE_TEXT[t]}" for t in op.shape_source]
        out.append("")

    others = related(op)
    if others:
        out.append("## Related operations\n")
        out += [f"- {op_link(o.name)} (0x{o.opcode:02x}): {esc(o.description)}" for o in others]
        out.append("")

    out.append("## Parts that support it\n")
    having = [f for f in flashes if op.name in f.opcodes]
    if not having:
        out.append(
            "No part in the database has a source saying it supports this operation. That "
            "is not the same as none supporting it: see [](../opcodes.md).\n"
        )
        return "\n".join(out)
    out.append(
        f"{len(having)} chip ids have a source saying so. Type in the box to filter; "
        "click a heading to sort.\n"
    )
    rows = [
        [
            table_id(f, f"../chips/{slugs[id(f)]}.md"),
            vendor_link(vendor_of(f)),
            ", ".join(esc(n) for n in f.names),
            size_text(f.size),
            " ".join(source_badge(s) for s in f.opcodes[op.name].sources),
        ]
        for f in having
    ]
    out.append(
        list_table(
            ["Id", "Vendor", "Parts", "Size", "Listed by"],
            rows,
            "sf-table sf-filterable sf-parts sf-with-vendor",
        )
    )
    return "\n".join(out)


def generate_all(db: Database) -> dict[str, str]:
    """Every operation's page, by file name."""
    flashes = list(db.flashes)
    slugs = {id(f): chip_slug(f) for f in flashes}
    return {f"{name}.md": operation_page(op, flashes, slugs) for name, op in OPERATIONS.items()}
