"""The data issues checks, on small databases, and the pages about them."""

from __future__ import annotations

import spiflash
from issue_checks import IssueKind, find
from issue_pages import VALUE_TITLES, chip_issues, generate_all
from page_markup import EM_SPACE, EN_DASH
from spiflash import Database, Datasheet
from spiflash.enums import Source
from spiflash.model import COMPARED
from spiflash.registers import Register, RegisterBit
from test_db import rec
from test_sfdp import MX25L25635E


def kinds(db: Database) -> list[IssueKind]:
    return [i.kind for i in find(db)]


def test_agreement_is_no_issue() -> None:
    db = Database([rec(), rec(source="flashrom", name="W25Q128.V")])
    assert find(db) == []


def test_sources_disagree_on_a_value() -> None:
    db = Database([rec(), rec(source="openocd", name="w25q128fv/jv", size=8 << 20)])
    (issue,) = find(db)
    assert issue.kind is IssueKind.VALUE
    assert issue.subject == "ef4018"
    assert issue.attribute == "size"
    # One answer each, the most trusted source's first on a tie.
    assert [(a.value, a.sources) for a in issue.answers] == [
        (16 << 20, (Source.LINUX,)),
        (8 << 20, (Source.OPENOCD,)),
    ]
    assert issue.sources == (Source.LINUX, Source.OPENOCD)


SR1 = "sr1"


def bit(n: int, register: str = SR1) -> dict[str, object]:
    return {"register": register, "bit": n}


def test_quad_enable_bits_disagree() -> None:
    db = Database(
        [
            rec(quad_enable=bit(1, "sr2")),
            rec(source="openfpgaloader", name="GD25Q32C", quad_enable=bit(6)),
        ]
    )
    (issue,) = find(db)
    assert (issue.kind, issue.attribute) == (IssueKind.VALUE, "quad_enable")
    assert [str(a.value) for a in issue.answers] == ["SR2 bit 1", "SR1 bit 6"]
    page = generate_all(db, {id(f): f.key for f in db.flashes})["value.md"]
    assert "## Quad enable bit (1)" in page
    assert "**SR2 bit 1**" in page


def test_protection_is_compared_role_by_role() -> None:
    full = {"bp0": bit(2), "bp1": bit(3), "bp2": bit(4), "tb": bit(5), "srp": bit(7)}
    # A source giving only TB agrees with a fuller one with the same TB.
    agree = Database([rec(protection=full), rec(source="qemu", protection={"tb": bit(5)})])
    assert find(agree) == []
    (f,) = agree.flashes
    assert f.protection is not None
    assert f.protection.to_json() == full
    # TB elsewhere: one issue, about that role only.
    db = Database([rec(protection=full), rec(source="qemu", protection={"tb": bit(6)})])
    (issue,) = find(db)
    assert (issue.kind, issue.attribute) == (IssueKind.VALUE, "protection.tb")
    page = generate_all(db, {id(f): f.key for f in db.flashes})["value.md"]
    assert "## Block protection bits (1)" in page
    assert "**tb: SR1 bit 5**" in page


def test_a_source_not_saying_how_a_bit_is_written_does_not_disagree() -> None:
    # Linux's SST25 BP bits are volatile; openFPGALoader says nothing of it.
    volatile = {"register": "sr1", "bit": 2, "writability": "volatile"}
    db = Database(
        [
            rec(protection={"bp0": volatile}),
            rec(source="openfpgaloader", protection={"bp0": bit(2)}),
        ]
    )
    assert find(db) == []
    (f,) = db.flashes
    assert f.protection is not None
    assert f.protection.to_json() == {"bp0": volatile}  # from the one saying it
    assert list(f.values("protection.bp0")) == [RegisterBit(Register.SR1, 2)]


def test_two_roles_on_one_bit() -> None:
    # flashrom's tb is the bit openFPGALoader calls BP3: role by role, the
    # sources agree on each, but together they put two roles on SR1 bit 5.
    flashrom = {"bp0": bit(2), "bp1": bit(3), "bp2": bit(4), "tb": bit(5)}
    ofl = {"bp0": bit(2), "bp1": bit(3), "bp2": bit(4), "bp3": bit(5)}
    db = Database(
        [rec(source="flashrom", protection=flashrom), rec(source="openfpgaloader", protection=ofl)]
    )
    (f,) = db.flashes
    assert f.shared_bits() == {"SR1 bit 5": ("bp3", "tb")}
    # The chip's layout is then the best source's own.
    assert f.protection is not None
    assert f.protection.to_json() == flashrom
    (issue,) = find(db)
    assert (issue.kind, issue.attribute) == (IssueKind.SHARED_BIT, "SR1 bit 5")
    assert sorted((a.value[0], a.sources) for a in issue.answers) == [
        ("bp3", (Source.OPENFPGALOADER,)),
        ("tb", (Source.FLASHROM,)),
    ]
    page = generate_all(db, {id(f): f.key for f in db.flashes})["shared-bit.md"]
    assert "**tb: SR1 bit 5**" in page
    # A quad enable bit on a protection role's bit is one too.
    qe = Database([rec(source="flashrom", protection=flashrom), rec(quad_enable=bit(5))])
    assert qe.flashes[0].shared_bits() == {"SR1 bit 5": ("quad_enable", "tb")}


def test_one_source_two_values() -> None:
    db = Database([rec(page_size=256), rec(name="w25q128x", line=2, page_size=512)])
    (issue,) = find(db)
    assert issue.kind is IssueKind.SAME_SOURCE
    assert issue.attribute == "page_size"
    assert issue.sources == (Source.LINUX,)


def test_extended_ids_tell_entries_apart() -> None:
    db = Database(
        [
            rec(ext_id="4d00", sector_size=65536),
            rec(ext_id="4d01", sector_size=262144, line=2),
        ]
    )
    # Still two answers for the id as a whole, but not one entry twice.
    assert IssueKind.SAME_SOURCE not in kinds(db)


def test_a_source_disagrees_with_its_own_sfdp() -> None:
    # The dump says 32 MiB and erases 4 KiB with 0x20; the entry says
    # 16 MiB, and 0x20 erases 64 KiB.
    stated = {"opcode": 0x20, "blocks": [[65536, 256]]}
    db = Database([rec(source="qemu", id="c22019", sfdp=MX25L25635E.hex(), erasers=[stated])])
    found = find(db)
    assert [(i.kind, i.attribute) for i in found] == [
        (IssueKind.SFDP, "size"),
        (IssueKind.SFDP, "erasers"),
    ]
    size, _ = found
    assert [a.value for a in size.answers] == [16 << 20, 32 << 20]
    assert size.sources == (Source.QEMU,)
    slugs = {id(f): f.key for f in db.flashes}
    page = generate_all(db, slugs)["sfdp.md"]
    assert "Its SFDP tables say" in page
    assert "0x20, 256 \N{MULTIPLICATION SIGN} 64 KiB" in page
    assert "0x20, 4,096 \N{MULTIPLICATION SIGN} 4 KiB" in page  # over its own 16 MiB
    (f,) = db.flashes
    text = "\n".join(chip_issues(db, slugs, f, found))
    assert "in its SFDP tables" in text
    # The entry's own size is the record's, and the issue says so; a dump
    # that agrees is no issue.
    assert db.records[0].size == 16 << 20
    assert find(Database([rec(source="qemu", sfdp=MX25L25635E.hex(), size=32 << 20)])) == []


def test_one_part_several_ids() -> None:
    db = Database([rec(), rec(source="u-boot", id="ef7018")])
    (issue,) = find(db)
    assert issue.kind is IssueKind.NAME_IDS
    assert issue.subject == "W25Q128"
    assert {a.value for a in issue.answers} == {"ef4018", "ef7018"}
    assert {f.key for f in issue.flashes} == {"ef4018", "ef7018"}


def test_legacy_ids_and_wildcards_are_not_name_issues() -> None:
    db = Database(
        [
            rec(name="sst25vf512"),
            rec(name="sst25vf512", id="bf48", id_method="rems"),
            rec(source="flashrom", name="W25Q.V", id="ef4017"),
            rec(source="flashrom", name="W25Q.V", id="ef4016"),
        ]
    )
    assert IssueKind.NAME_IDS not in kinds(db)


def test_manufacturers() -> None:
    db = Database([rec(vendor="Spansion", id="012018"), rec(vendor="Cypress", id="012018")])
    (issue,) = find(db)
    assert issue.kind is IssueKind.MANUFACTURER
    assert {a.value for a in issue.answers} == {"Spansion", "Cypress"}


def datasheet(parts: list[str], confirmed: list[str]) -> Datasheet:
    return Datasheet.from_json(
        {
            "url": f"https://example.com/{parts[0]}.pdf",
            "title": parts[0],
            "official": True,
            "parts": parts,
            "ids": ["ef4018"],
            "confirmed": confirmed,
        }
    )


def test_datasheet_not_giving_the_id() -> None:
    records = [rec(name="w25q128jv/fv")]
    assert find(Database(records, datasheets=[datasheet(["W25Q128JV"], ["ef4018"])])) == []
    # Another part's datasheet giving the id doesn't make this one's right.
    db = Database(
        records,
        datasheets=[datasheet(["W25Q128JV"], ["ef4018"]), datasheet(["W25Q128FV"], [])],
    )
    (issue,) = find(db)
    assert issue.kind is IssueKind.DATASHEET
    assert issue.part == "W25Q128FV"
    assert [d.title for d in issue.datasheets] == ["W25Q128FV"]


def test_shipped_data() -> None:
    found = find()
    assert {i.kind for i in found} == set(IssueKind)
    by_kind = {k: [i for i in found if i.kind is k] for k in IssueKind}
    # U-Boot's MT25QL01G id has its bytes swapped; the datasheet gives 20 ba 21.
    (mt,) = [i for i in by_kind[IssueKind.NAME_IDS] if i.subject == "MT25QL01G"]
    assert {a.value: a.sources for a in mt.answers}["21ba20"] == (Source.UBOOT,)
    # Every issue's records are the database's own.
    records = set(map(id, spiflash.records()))
    assert all(id(r) in records for i in found for a in i.answers for r in a.records)


def test_pages() -> None:
    db = spiflash.database()
    slugs = {id(f): f.key.replace(":", "-") for f in db.flashes}
    pages = generate_all(db, slugs)
    assert set(pages) == {
        "index.md",
        *(f"{k}.md" for k in IssueKind),
        *(f"source-{s}.md" for s in Source),
    }
    index = pages["index.md"]
    for k in IssueKind:
        assert f"]({k}.md)" in index
    # Labels link the upstream lines; a source's page rings its own.
    assert "<https://github.com/" in pages["value.md"]
    assert "{sfsrcme}`u-boot" in pages["source-u-boot.md"]
    assert "{sfsrcme}`linux" not in pages["source-u-boot.md"]
    assert "{sfsrcme}" not in pages["value.md"]


def test_chip_page_section() -> None:
    db = spiflash.database()
    slugs = {id(f): f.key for f in db.flashes}
    issues = find(db)
    (mt,) = [f for f in db.flashes if f.key == "20ba21"]
    text = "\n".join(chip_issues(db, slugs, mt, issues))
    assert text.startswith("### Conflicts and errors")
    assert "](../issues/name-ids.md)" in text
    assert "MT25QL01G" in text
    (quiet,) = [f for f in db.flashes if f.key == "ef4018"]
    assert chip_issues(db, slugs, quiet, [i for i in issues if quiet not in i.flashes]) == []


def test_values_grouped_by_what_disagrees() -> None:
    db = spiflash.database()
    slugs = {id(f): f.key for f in db.flashes}
    page = generate_all(db, slugs)["value.md"]
    headings = [line for line in page.splitlines() if line.startswith("## ")]
    assert [h.split(" (")[0] for h in headings] == [
        "## Size",
        "## Page size",
        "## Sector size",
        "## Supply voltage",
        "## Quad enable bit",
        "## Block protection bits",
    ]
    # A heading for every value compared, a layout's roles under one.
    assert set(VALUE_TITLES) == set(COMPARED)
    # Each has a target, an HTML id as it is: no underscores.
    assert "(value-page-size)=" in page
    counts = sum(int(h.split("(")[1].rstrip(")")) for h in headings)
    assert counts == len([i for i in find(db) if i.kind is IssueKind.VALUE])


def test_summary_has_a_row_per_value() -> None:
    db = Database([rec(voltage=[2700, 3600]), rec(source="u-boot", voltage=[1700, 2000])])
    slugs = {id(f): f.key for f in db.flashes}
    pages = generate_all(db, slugs)
    rows = [line for line in pages["index.md"].splitlines() if line.startswith("* - ")]
    # Voltage has an issue, so a section to link to; size has none.
    assert "* - {sfsub}`Supply voltage <value.html#value-voltage>`" in rows
    assert "* - {sfsub}`Size`" in rows
    # Only the value page has the targets.
    assert "(value-voltage)=" in pages["value.md"]
    assert "(value-voltage)=" not in pages["index.md"]
    # The totals line up with the counts above them.
    assert "**{sfnum}`1`**" in pages["index.md"]
    assert "[**{sfnum}`1`**](source-linux.md)" in pages["index.md"]


def test_supply_ends_are_numbers_of_their_own() -> None:
    db = Database([rec(voltage=[2700, 3600]), rec(source="u-boot", voltage=[1700, 2000])])
    slugs = {id(f): f.key for f in db.flashes}
    page = generate_all(db, slugs)["value.md"]
    assert "Answers: V min, V max" in page
    assert f"**{{sfnum}}`2.7 V`{EM_SPACE}{{sfnum}}`3.6 V`**" in page
    # The value, then who gives it: a span each, so who wraps in its own column.
    assert f"[**{{sfnum}}`2.7 V`{EM_SPACE}{{sfnum}}`3.6 V`**]{{.sf-val}}[{{sfsrc}}`linux" in page
    assert "`]{.sf-who}" in page
    assert EN_DASH not in page


def test_one_source_grouped_by_value() -> None:
    db = spiflash.database()
    slugs = {id(f): f.key for f in db.flashes}
    pages = generate_all(db, slugs)
    page = pages["same-source.md"]
    headings = [line for line in page.splitlines() if line.startswith("## ")]
    assert headings
    assert all(h.split(" (")[0].removeprefix("## ") in VALUE_TITLES.values() for h in headings)
    counts = sum(int(h.split("(")[1].rstrip(")")) for h in headings)
    assert counts == len([i for i in find(db) if i.kind is IssueKind.SAME_SOURCE])
    assert "(same-source-page-size)=" in page
    # The summary links each value's section.
    assert "<same-source.html#same-source-page-size>`" in pages["index.md"]


def test_parts_an_extended_id_tells_apart_are_not_compared() -> None:
    found = {(i.subject, i.attribute) for i in find() if i.kind is IssueKind.VALUE}
    # flashrom's S25FL128S_UL (1.7-2.0 V) against its and flashprog's 3 V
    # S25FL128S: one part, a real disagreement.
    assert ("012018", "voltage") in found
    # The S25FS512S (1.8 V) and the S25FL512S (3 V): two parts.
    assert ("010220", "voltage") not in found
    # The GD5F1GQ5RE (1 Gbit) and the F50L2G41KA (2 Gbit) at c8 41.
    assert ("c841", "size") not in found
