"""The JEP106 pages: a page for each manufacturer code a chip in the database
answers with, listing those chips, and a table of every JEP106 name with its
count of chips (a toggle hides the names no chip uses).

JEP106 is JEDEC's list of manufacturer codes: the first byte a chip answers to
a JEDEC read-id, after as many 0x7f continuation codes as its bank number
less one. The names come from OpenOCD's copy of the list
(:attr:`spiflash.Database.manufacturers`).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from page_markup import count, esc, list_table, size_text, table_id, vendor_link, vendor_of

if TYPE_CHECKING:
    from spiflash import Database, Flash

#: A JEP106 code: (bank, the id byte with its parity bit).
Code = tuple[int, int]

JEDEC_JEP106 = "https://www.jedec.org/standards-documents/docs/jep-106ab"


def code_slug(code: Code) -> str:
    """The page for a code: ``bank1-ef`` for Winbond's."""
    bank, byte = code
    return f"bank{bank + 1}-{byte:02x}"


def code_text(code: Code) -> str:
    """How a chip sends the code: ``7f 7f 9d`` (bank 3)."""
    bank, byte = code
    return " ".join(["7f"] * bank + [f"{byte:02x}"])


def chips_by_code(db: Database) -> dict[Code, list[Flash]]:
    """The chips answering each JEP106 code the list names."""
    out: dict[Code, list[Flash]] = defaultdict(list)
    for f in db.flashes:
        if db.jep106(f.id[0], f.bank):
            out[(f.bank, f.id[0])].append(f)
    return out


def jep106_link(db: Database, code: Code, prefix: str = "../jep106/") -> str:
    """The code's JEP106 name, linked to its page (``prefix`` is the way to
    the pages); the name alone if no chip uses the code."""
    name = db.jep106(code[1], code[0]) or "?"
    return f"[{esc(name)}]({prefix}{code_slug(code)}.md)"


def code_page(db: Database, code: Code, chips: list[Flash], slugs: dict[int, str]) -> str:
    bank, byte = code
    name = db.jep106(byte, bank) or "?"
    named = ", ".join(vendor_link(v) for v in sorted({vendor_of(f) for f in chips}))
    rows = [
        [
            table_id(f, f"../chips/{slugs[id(f)]}.md"),
            vendor_link(vendor_of(f)),
            ", ".join(esc(n) for n in f.names),
            "NAND" if f.type == "nand" else "NOR",
            size_text(f.size),
        ]
        for f in chips
    ]
    return "\n".join(
        [
            f"# {esc(name)}\n",
            f"{{bdg-primary}}`{len(chips)} chip ids` {{sfid}}`{code_text(code)}`\n",
            (
                f"JEP106 code 0x{byte:02x} in bank {bank + 1}: a chip from this manufacturer "
                f"answers a JEDEC read-id with {{sfid}}`{code_text(code)}` first. "
                f"The sources name the chips' vendor as {named}. "
                "Every JEP106 name: [JEP106 codes](index.md).\n"
            ),
            "Type in the box to filter; click a heading to sort.\n",
            list_table(
                ["Id", "Vendor", "Parts", "Type", "Size"],
                rows,
                "sf-table sf-filterable sf-parts sf-with-vendor",
            ),
            "",
        ]
    )


def index_page(db: Database, used: dict[Code, list[Flash]]) -> str:
    rows = []
    for m in sorted(db.manufacturers, key=lambda m: (m.bank, m.id & 0x7F)):
        code = (m.bank, m.id)
        chips = used.get(code, [])
        rows.append(
            [
                count(m.bank + 1),
                f"{{sfid}}`{m.id:02x}`",
                jep106_link(db, code, "") if chips else esc(m.name),
                count(len(chips)),
            ]
        )
    return "\n".join(
        [
            "# JEP106 codes\n",
            (
                f"Every name in [JEDEC's JEP106]({JEDEC_JEP106}) list of manufacturer codes "
                f"({len(db.manufacturers):,} in OpenOCD's copy), and how many chip ids in the "
                f"database answer with it; {len(used)} of them are used. Only names with chips "
                "are shown: untick the box to see them all. A name with chips links to "
                "their page.\n"
            ),
            list_table(
                ["Bank", "Code", "Name", "Chips"],
                rows,
                "sf-table sf-filterable sf-hide-zero sf-jep106",
            ),
            "",
            "```{toctree}\n:hidden:\n\n"
            + "\n".join(code_slug(c) for c in sorted(used))
            + "\n```\n",
        ]
    )


def generate_all(db: Database, slugs: dict[int, str]) -> dict[str, str]:
    """Every page of ``docs/jep106/``, by file name."""
    used = chips_by_code(db)
    pages = {"index.md": index_page(db, used)}
    for code, chips in used.items():
        pages[f"{code_slug(code)}.md"] = code_page(db, code, chips, slugs)
    return pages
