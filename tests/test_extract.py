"""Each extractor, on a miniature tree in its upstream's format.

The snippets, in tests/fixtures/<upstream>/<path>, are cut down from the real
files at the commits pinned in tools/sources.toml, keeping the shapes that
needed handling."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from spiflash_extract import (
    dediprog,
    dts,
    flashrom,
    imsprog,
    linux,
    mediatek,
    openfpgaloader,
    openocd,
    qemu,
    record,
    sfdp,
    uboot,
    zephyr,
)
from spiflash_extract.ops import Opcodes

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> str:
    """A cut-down copy of an upstream file, verbatim (tabs and all)."""
    return (FIXTURES / name).read_text()


def write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def by_name(recs: list[record.Record]) -> dict[str, record.Record]:
    return {r["name"]: r for r in recs}


def ops(rec: record.Record) -> dict[str, tuple[int, str]]:
    """A record's opcodes as {op: (opcode, via)}."""
    return {o["op"]: (o["opcode"], o["via"]) for o in rec["opcodes"]}


LINUX_CORE_H = fixture("linux/drivers/mtd/spi-nor/core.h")

SPINOR_H = fixture("linux/include/linux/mtd/spi-nor.h")

LINUX_WINBOND = fixture("linux/drivers/mtd/spi-nor/winbond.c")

LINUX_SPANSION = fixture("linux/drivers/mtd/spi-nor/spansion.c")

LINUX_NAND = fixture("linux/drivers/mtd/nand/spi/winbond.c")

LINUX_ESMT = fixture("linux/drivers/mtd/nand/spi/esmt.c")


@pytest.fixture
def linux_tree(tmp_path: Path) -> Path:
    return write(
        tmp_path,
        {
            "drivers/mtd/spi-nor/core.h": LINUX_CORE_H,
            linux.SPINOR_H: SPINOR_H,
            "drivers/mtd/spi-nor/core.c": "static const struct flash_info x[] = { {} };",
            "drivers/mtd/spi-nor/winbond.c": LINUX_WINBOND,
            "drivers/mtd/spi-nor/spansion.c": LINUX_SPANSION,
            "drivers/mtd/nand/spi/winbond.c": LINUX_NAND,
            "drivers/mtd/nand/spi/core.c": "",
        },
    )


def test_part_case() -> None:
    assert record.part_case("w25q128fv/jv") == "W25Q128FV/JV"
    assert record.part_case("n25q256 1.8v") == "N25Q256 1.8V"
    assert record.part_case("S25FL128S_UL Uniform 128 kB Sectors") == (
        "S25FL128S_UL Uniform 128 kB Sectors"
    )
    assert record.part_case("sst25vf512") == "SST25VF512"
    # What is in parentheses keeps its case.
    assert record.part_case("W25Q128JW(3MHz)") == "W25Q128JW(3MHz)"
    assert record.part_case("EN25B10(Bottom Boot)") == "EN25B10(Bottom Boot)"
    assert record.part_case("mt25tl256b ( for one die)") == "MT25TL256B ( for one die)"
    assert record.part_case("S25FL032(A/P)") == "S25FL032(A/P)"
    # Text straight after the parentheses is the word before them.
    assert record.part_case("S79FS01GS(one die)_es") == "S79FS01GS(one die)_ES"
    assert record.part_case("x(3MHz) board") == "X(3MHz) board"


def test_linux_nor(linux_tree: Path) -> None:
    recs = linux.extract_nor(linux_tree)
    r = by_name(recs)
    w = r["W25Q128"]
    assert w["id"] == "ef4018"
    assert w["ext_id"] is None
    assert w["vendor"] == "winbond"
    assert w["size"] == 16 << 20
    assert w["page_size"] == 256
    assert w["sector_size"] == 65536
    assert w["features"] == ["dual_read", "erase_4k", "erase_64k", "lock", "quad_read"]
    # core.c's defaults, plus what the no_sfdp_flags set up.
    assert ops(w) == {
        "RDID": (0x9F, "JEDEC id match (spi_nor_match_id)"),
        "READ_1_1_1": (0x03, "default (spi_nor_init_default_params)"),
        "READ_1_1_1_FAST": (0x0B, "default (spi_nor_init_default_params)"),
        "READ_1_1_2": (0x3B, "SPI_NOR_DUAL_READ"),
        "READ_1_1_4": (0x6B, "SPI_NOR_QUAD_READ"),
        "PP_1_1_1": (0x02, "default (spi_nor_init_default_params)"),
        "BE_4K": (0x20, "SECT_4K"),
        "SE": (0xD8, "default sector erase (spi_nor_no_sfdp_init_params)"),
        "CHIP_ERASE": (0xC7, "default (spi_nor_erase)"),
    }
    assert [o["op"] for o in w["opcodes"]][:2] == ["RDID", "READ_1_1_1"]  # id, read, ...
    assert "SPI_NOR_HAS_TB" in w["flags"]
    assert w["notes"] == ["Flavors w/ and w/o SFDP."]
    assert w["file"] == "drivers/mtd/spi-nor/winbond.c"

    # No .name: the comment names it; no .size: SFDP gives it.
    j = r["W25Q01JV"]
    assert j["id"] == "ef4021"
    assert j["size"] is None
    assert "sfdp" in j["features"]

    assert "RDSFDP" in ops(j)
    assert "SE" not in ops(j)
    big = r["W25Q512JVQ"]
    assert {"4byte_addr", "4byte_opcodes", "otp"} <= set(big["features"])
    assert ops(big)["READ_1_1_1_4B"] == (0x13, "SPI_NOR_4B_OPCODES")
    assert ops(big)["SE_4B"] == (0xDC, "SPI_NOR_4B_OPCODES")
    assert ops(big)["PP_1_1_1_4B"] == (0x12, "SPI_NOR_4B_OPCODES")

    # Neither a name nor a comment: named by vendor and id.
    assert r["WINBOND-EF60"]["notes"] == ["Linux gives this entry no name"]

    s = r["S25FL128S1"]
    assert s["id"] == "012018"
    assert s["ext_id"] == "4d0180"
    assert "USE_CLSR" in s["flags"]

    n = r["EVERSPIN-NONJEDEC"]
    assert n["id"] is None
    assert n["id_method"] is None
    assert "no_erase" in n["features"]
    assert "erase_64k" not in n["features"]


def test_linux_nand(linux_tree: Path) -> None:
    (n,) = linux.extract_nand(linux_tree)
    assert n["type"] == "nand"
    assert n["name"] == "W25N01GV"
    assert n["vendor"] == "Winbond"
    assert n["id"] == "efaa21"
    assert n["id_method"] == "rdid_opcode_dummy"
    assert n["size"] == 2048 * 64 * 1024
    assert n["page_size"] == 2048
    assert n["sector_size"] == 2048 * 64
    assert n["features"] == ["quad_read"]
    assert n["flags"] == ["SPINAND_HAS_QE_BIT"]
    assert n["notes"][0] == "3.3V"


def test_linux_nand_manufacturer_per_table(tmp_path: Path) -> None:
    # esmt.c has two manufacturers, 0x8c and 0xc8, each with its own table.
    write(tmp_path, {"drivers/mtd/nand/spi/esmt.c": LINUX_ESMT})
    ids = {r["name"]: r["id"] for r in linux.extract_nand(tmp_path)}
    assert ids == {"F50L1G41LC": "8c2c", "F50L1G41LB": "c8017f7f7f"}


def test_linux_extract_is_both(linux_tree: Path) -> None:
    assert {r["type"] for r in linux.extract(linux_tree)} == {"nor", "nand"}


def test_linux_bad_id(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "drivers/mtd/spi-nor/core.h": LINUX_CORE_H,
            linux.SPINOR_H: SPINOR_H,
            "drivers/mtd/spi-nor/x.c": "static const struct flash_info x_parts[] = "
            '{ { .id = SOMETHING_ELSE(1), .name = "x" } };',
        },
    )
    with pytest.raises(ValueError, match=r"unexpected \.id"):
        linux.extract_nor(tmp_path)


def test_split_id() -> None:
    assert linux.split_id([0xEF, 0x40, 0x18]) == ("ef4018", None)
    assert linux.split_id([0x7F, 0x7F, 0x9D, 0x60, 0x19, 0x01]) == ("7f7f9d6019", "01")


UBOOT_FLAGS = fixture("u-boot/drivers/mtd/spi/sf_internal.h")

UBOOT_IDS = fixture("u-boot/drivers/mtd/spi/spi-nor-ids.c")


def test_uboot(tmp_path: Path) -> None:
    write(tmp_path, {uboot.IDS: UBOOT_IDS, uboot.FLAGS_H: UBOOT_FLAGS, uboot.SPINOR_H: SPINOR_H})
    r = by_name(uboot.extract(tmp_path))
    assert set(r) == {"W25Q128", "W25Q512", "S25FL128S", "S25SL12800", "MB85RS256TY"}
    w = r["W25Q128"]
    assert w["vendor"] == "winbond"
    assert w["id"] == "ef4018"
    assert w["size"] == 16 << 20
    assert w["features"] == ["dual_read", "erase_4k", "erase_64k", "fast_read"]
    assert set(ops(w)) == {
        "RDID",
        "READ_1_1_1",
        "READ_1_1_1_FAST",
        "READ_1_1_2",
        "PP_1_1_1",
        "BE_4K",
        "SE",
        "CHIP_ERASE",
    }
    # SPI_NOR_QUAD_READ gives U-Boot's PP_1_1_4 too; 4B_OPCODES the 4-byte forms.
    w512 = ops(r["W25Q512"])
    assert w512["PP_1_1_4"] == (0x32, "SPI_NOR_QUAD_READ")
    assert w512["PP_1_1_4_4B"] == (0x34, "SPI_NOR_4B_OPCODES")
    assert "READ_1_1_1_FAST" not in ops(r["S25FL128S"])  # SPI_NOR_NO_FR
    assert {"4byte_addr", "4byte_opcodes", "quad_read"} <= set(r["W25Q512"]["features"])
    s = r["S25FL128S"]
    assert s["vendor"] == "spansion"
    assert s["ext_id"] == "4d0180"
    assert "fast_read" not in s["features"]
    assert r["S25SL12800"]["ext_id"] == "0300"
    assert r["S25SL12800"]["sector_size"] == 256 * 1024
    f = r["MB85RS256TY"]
    assert f["vendor"] == "fujitsu"
    assert f["id"] == "047f25"
    assert f["size"] == 32 * 1024
    assert f["page_size"] == 32 * 1024
    assert "no_erase" in f["features"]
    assert f["notes"] == ["Whole chip can be written at once"]


def test_uboot_no_table(tmp_path: Path) -> None:
    write(tmp_path, {uboot.IDS: "int x;", uboot.FLAGS_H: "", uboot.SPINOR_H: ""})
    with pytest.raises(ValueError, match="no spi_nor_ids"):
        uboot.extract(tmp_path)


FLASHROM_H = fixture("flashrom/include/flashchips.h")

FLASH_H = fixture("flashrom/include/flash.h")

FLASHROM_SPI_H = fixture("flashrom/include/spi.h")

# The headers every flashrom and flashprog tree needs.
FLASHROM_HEADERS = {
    flashrom.HEADER: FLASHROM_H,
    flashrom.FLASH_H: FLASH_H,
    flashrom.SPI_H: FLASHROM_SPI_H,
}

FLASHROM_EON = fixture("flashrom/flashchips/eon.c")

FLASHPROG_C = fixture("flashprog/flashchips.c")


def test_flashrom_per_vendor(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            **FLASHROM_HEADERS,
            "flashchips/eon.c": FLASHROM_EON,
            "flashchips.c": '#include "flashchips/eon.c"',
        },
    )
    r = by_name(flashrom.extract(tmp_path, "flashrom"))
    assert set(r) == {"EN25QH128", "S25FL128S_UL Uniform 128 kB Sectors", "M25P05", "M95320"}
    e = r["EN25QH128"]
    assert e["source"] == "flashrom"
    assert e["file"] == "flashchips/eon.c"
    assert e["id"] == "1c7018"
    assert e["id_method"] == "rdid"
    assert e["size"] == 16 << 20
    assert e["page_size"] == 256
    assert e["sector_size"] == 65536
    # FEATURE_QPI_38 & ~FEATURE_FAST_READ_QOUT: everything but the quad output read.
    assert e["flags"] == [
        "FEATURE_FAST_READ",
        "FEATURE_FAST_READ_DOUT",
        "FEATURE_OTP",
        "FEATURE_QPI_38_FF",
        "FEATURE_WRSR_WREN",
    ]
    assert e["features"] == [
        "dual_read",
        "erase_4k",
        "erase_64k",
        "fast_read",
        "lock",
        "otp",
        "qpi",
        "sfdp",
    ]
    assert e["erasers"][0] == {"opcode": 0x20, "blocks": [[4096, 4096]]}
    assert e["erasers"][3] == {
        "opcode": None,
        "blocks": [[4096, 2], [8192, 1]],
        "function": "spi_block_erase_emulation",
    }
    assert len(e["erasers"]) == 4
    assert e["voltage"] == [2700, 3600]
    assert e["tested"] == "TEST_OK_PREW"
    assert ops(e) == {
        "RDID": (0x9F, "probe (rdid)"),
        "RDSFDP": (0x5A, "comment: supports SFDP"),
        "READ_1_1_1_FAST": (0x0B, "FEATURE_FAST_READ"),
        "READ_1_1_2": (0x3B, "FEATURE_FAST_READ_DOUT"),
        "BE_4K": (0x20, "block_erasers (4096 x 4096)"),
        "SE": (0xD8, "block_erasers (256 x 65536)"),
        "CHIP_ERASE": (0xC7, "block_erasers (1 x 16777216)"),
        "WRSR": (0x01, "FEATURE_WRSR_WREN"),
        "EQPI_38": (0x38, "FEATURE_QPI_38_FF"),
        "RSTQIO_FF": (0xFF, "FEATURE_QPI_38_FF"),
    }
    assert "EON_ID_NOPREFIX: EON, missing 0x7F prefix" in e["notes"]

    s = r["S25FL128S_UL Uniform 128 kB Sectors"]
    assert s["id"] == "012018"
    assert s["ext_id"] == "0080"
    assert s["tested"] == "{ .probe = NA, .read = OK }"
    assert r["M25P05"]["id_method"] == "res1"
    assert r["M25P05"]["id"] == "05"
    assert r["M95320"]["id"] is None
    assert r["M95320"]["id_method"] is None


def test_flashprog_single_file(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            **FLASHROM_HEADERS,
            "flashchips.c": FLASHPROG_C,
        },
    )
    (e,) = flashrom.extract(tmp_path, "flashprog")
    assert e["source"] == "flashprog"
    assert e["id"] == "1c7018"
    assert e["features"] == ["4byte_addr", "4byte_opcodes", "erase_64k"]
    assert e["flags"] == ["FEATURE_4BA_READ", "FEATURE_WRSR_EWSR", "FEATURE_WRSR_WREN"]


def test_flashrom_errors(tmp_path: Path) -> None:
    write(tmp_path, FLASHROM_HEADERS)
    write(tmp_path, {"flashchips.c": "int nothing;"})
    with pytest.raises(ValueError, match=r"no flashchips\[\]"):
        flashrom.extract(tmp_path, "flashprog")
    bad = FLASHPROG_C.replace("ID_SPI_RDID", "ID_SOMETHING_NEW")
    write(tmp_path, {"flashchips.c": bad})
    with pytest.raises(ValueError, match="unknown probe 'SOMETHING_NEW'"):
        flashrom.extract(tmp_path, "flashprog")


def test_flashrom_erase_opcode_without_an_operation(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            **FLASHROM_HEADERS,
            "flashchips.c": FLASHPROG_C.replace("spi_block_erase_d8", "spi_block_erase_99"),
        },
    )
    with pytest.raises(ValueError, match="no operation for erase opcode 0x99"):
        flashrom.extract(tmp_path, "flashprog")


OPENOCD_SPI_C = fixture("openocd/src/flash/nor/spi.c")

OPENOCD_SPI_H = fixture("openocd/src/flash/nor/spi.h")

JEP106_INC = fixture("openocd/src/helper/jep106.inc")


def test_openocd(tmp_path: Path) -> None:
    write(
        tmp_path,
        {openocd.SPI_C: OPENOCD_SPI_C, openocd.SPI_H: OPENOCD_SPI_H, openocd.JEP106: JEP106_INC},
    )
    r = by_name(openocd.extract(tmp_path))
    assert set(r) == {"W25Q128FV/JV", "IS25WP512M", "GD25Q512", "S25FL008", "FM25V02"}
    # A byte OpenOCD holds that is no known operation is noted, not guessed.
    s008 = r["S25FL008"]
    assert "OpenOCD's qread_cmd is 0x08, which is not a known operation" in s008["notes"]
    assert "quad_read" not in s008["features"]
    assert set(ops(r["FM25V02"])) == {"RDID", "READ_1_1_1", "PP_1_1_1"}
    w = r["W25Q128FV/JV"]
    assert w["vendor"] == "win"
    assert w["id"] == "ef4018"
    assert ops(w) == {
        "RDID": (0x9F, "probe (SPIFLASH_READ_ID)"),
        "READ_1_1_1": (0x03, "read_cmd"),
        "READ_1_4_4": (0xEB, "qread_cmd"),
        "PP_1_1_1": (0x02, "pprog_cmd"),
        "SE": (0xD8, "erase_cmd"),
        "CHIP_ERASE": (0xC7, "chip_erase_cmd"),
    }
    assert ops(r["IS25WP512M"])["READ_1_4_4_4B"] == (0xEC, "qread_cmd")
    assert w["erasers"] == [
        {"opcode": 0xD8, "blocks": [[65536, 256]]},
        {"opcode": 0xC7, "blocks": [[16 << 20, 1]]},
    ]
    assert w["features"] == ["erase_64k", "quad_read"]
    assert "4byte_addr" in r["IS25WP512M"]["features"]
    g = r["GD25Q512"]
    assert g["features"] == ["erase_4k"]
    assert "CHIP_ERASE" not in ops(g)
    f = r["FM25V02"]
    assert f["id"] == "7f7f7f7f7f7fc22200"
    assert f["features"] == ["no_erase"]
    assert f["notes"] == ["exists ?", "FRAM"]
    assert f["page_size"] is None


def test_openocd_part_prefixes() -> None:
    assert openocd.part_name("mac", "25l12845") == "mx25l12845"
    assert openocd.part_name("mac", "mx25l12845") == "mx25l12845"
    assert openocd.part_name("adesto", "xp032") == "atxp032"
    assert openocd.part_name("win", "w25q128fv/jv") == "w25q128fv/jv"


def test_openocd_device_id() -> None:
    assert openocd.device_id_hex(0x001840EF) == "ef4018"
    assert openocd.device_id_hex(0x060822C2) == "7f7f7f7f7f7fc22208"


def test_jep106(tmp_path: Path) -> None:
    write(tmp_path, {openocd.JEP106: JEP106_INC})
    assert openocd.manufacturers(tmp_path) == [
        {"bank": 0, "id": 0x01, "name": "AMD"},
        {"bank": 0, "id": 0xEF, "name": "NEXCOM"},  # 0x6f with its parity bit
        {"bank": 1, "id": 0x1C, "name": "Eon Silicon Devices"},
    ]


OFL_DB = fixture("openfpgaloader/src/spiFlashdb.hpp")


OFL_CPP = fixture("openfpgaloader/src/spiFlash.cpp")


def test_openfpgaloader(tmp_path: Path) -> None:
    write(tmp_path, {openfpgaloader.DB: OFL_DB, openfpgaloader.FLASH_CPP: OFL_CPP})
    r = by_name(openfpgaloader.extract(tmp_path))
    s = r["S25FL256S"]
    assert s["id"] == "010219"
    assert s["vendor"] == "spansion"
    assert s["size"] == 32 << 20
    assert s["features"] == ["4byte_addr", "erase_64k", "lock", "quad_read"]
    assert "quad_register=CONFR" in s["flags"]
    assert s["notes"][0].startswith("https://www.mouser.fr/")
    # 32 MiB: the 4-byte forms too; no subsector_erase, so no BE_4K.
    assert set(ops(s)) == {
        "RDID",
        "READ_1_1_1",
        "READ_1_1_1_4B",
        "PP_1_1_1",
        "PP_1_1_1_4B",
        "SE",
        "SE_4B",
    }
    assert ops(s)["SE"] == (0xD8, "sector_erase = true")
    assert r["W25Q128"]["features"] == ["erase_4k", "erase_64k"]


def test_openfpgaloader_no_map(tmp_path: Path) -> None:
    write(tmp_path, {openfpgaloader.DB: "int x;", openfpgaloader.FLASH_CPP: ""})
    with pytest.raises(ValueError, match="no flash_list"):
        openfpgaloader.extract(tmp_path)


IMSPROG = FIXTURES / "imsprog"

IMSPROG_DAT = (IMSPROG / imsprog.DAT).read_bytes()

# The known errors the fixture has (the rest would raise, as stale).
IMSPROG_WRONG = {
    k: v
    for k, v in imsprog.WRONG.items()
    if k[0] in {"A25L40PT", "ES25P10", "P25Q06H", "DS35Q4GM(1.8V)"}
}


def test_imsprog() -> None:
    recs = imsprog.extract(IMSPROG, IMSPROG_WRONG)
    # Sixteen SPI NOR and NAND entries, four of them known to be wrong; the
    # EEPROMs, FRAM and DataFlash after them are not taken, and the all-zero
    # entry ends the table.
    assert len(recs) == 12
    r = by_name(recs)
    s = r["S25FL256S"]
    assert (s["file"], s["line"]) == (imsprog.DAT, 2)
    assert s["vendor"] == "SPANSION"
    assert s["id"] == "010219"
    assert s["size"] == 32 << 20
    # The 256-byte page and 64 KiB block every NOR entry has are IMSProg's
    # defaults, not the part's (the S25FL256S erases 256 KiB blocks), so
    # they are flags: no page or sector size, no erase layout.
    assert (s["page_size"], s["sector_size"], s["erasers"]) == (None, None, None)
    assert s["features"] == ["4byte_addr"]
    assert s["flags"] == [
        "addr4bit=0x21",
        "algorithmCode=0x00",
        "blockSize=64K",
        "chipVCC=3.3 V",
        "delay=1000",
        "pageSize=256",
    ]
    # Spansion's 4-byte mode is a bank register; Winbond's also clears its
    # extended address register on the way out. IMSProg never sends 0xc7.
    assert set(ops(s)) == {"RDID", "READ_1_1_1", "PP_1_1_1", "SE", "BRWR", "BRRD"}
    assert "WREAR" in ops(r["EN25Q256"])
    assert "EN4B" in ops(r["GD25LB512ME(1.8V)"])
    assert set(ops(r["FL016AIF"])) == {"RDID", "READ_1_1_1", "PP_1_1_1", "SE"}
    assert "chipVCC=1.8 V" in r["XT25Q16D(1.8V)"]["flags"]
    assert "delay=200" in r["EN25F10A"]["flags"]
    assert r["PN25F08"]["vendor"] == "PARAGON"  # "PARAGON " upstream
    # Known wrong entries are left out: the A25L40PT has the A25L20PT's id,
    # the ES25P10 twice its size, the P25Q06H an id no part has.
    assert r["A25L20PT"]["id"] == "372022"
    assert not {"A25L40PT", "ES25P10", "P25Q06H", "DS35Q4GM(1.8V)"} & set(r)
    # SPI NAND: 0x9f and a dummy byte, then three id bytes, of which a
    # two-byte id repeats its first.
    g = r["GD5F1GQ5UEXXG"]
    assert (g["type"], g["id"], g["id_method"]) == ("nand", "c851", "rdid_opcode_dummy")
    assert (g["size"], g["page_size"], g["sector_size"]) == (128 << 20, 2048, 128 << 10)
    assert "ECCsize=128" in g["flags"]
    assert g["opcodes"] == []
    assert r["MX35LF1G24AD-Z41"]["id"] == "c21403"
    assert r["F35SQA002G"]["id"] == "cd7272"
    # GigaDevice's F series answers with no dummy byte, as Linux reads it.
    f = r["GD5F1GQ4UFXXG"]
    assert (f["id"], f["id_method"]) == ("c8b148", "rdid_opcode")


def dat(tmp_path: Path, *entries: bytes) -> Path:
    path = tmp_path / imsprog.DAT
    path.parent.mkdir(parents=True)
    path.write_bytes(b"".join(entries))
    return tmp_path


def nth(n: int) -> bytes:
    """The fixture's nth entry, from 0."""
    return IMSPROG_DAT[n * 0x44 : (n + 1) * 0x44]


# A SPI NAND id from a maker not known for two-byte ids, whose third byte
# repeats its first: a real third byte, or the id wrapping round? It is not
# guessed.
UNKNOWN_WRAP = nth(12)[:0x30] + b"\x77" + nth(12)[0x31:0x32] + b"\x77" + nth(12)[0x33:]


@pytest.mark.parametrize(
    ("entry", "error"),
    [
        (IMSPROG_DAT[:0x43], "not a whole number"),
        (b"SPI_NAND" + IMSPROG_DAT[8:0x44], "not a type, vendor and part"),
        (IMSPROG_DAT[:0x43] + b"\x09", "unknown VCC"),
        (IMSPROG_DAT[:0x3E] + b"\x02" + IMSPROG_DAT[0x3F:0x44], "unknown 4-byte"),
        (UNKNOWN_WRAP, "not known for 2-byte ids"),
    ],
)
def test_imsprog_refuses(tmp_path: Path, entry: bytes, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        imsprog.extract(dat(tmp_path, entry), {})


def test_imsprog_skipped() -> None:
    assert imsprog.skipped(IMSPROG, IMSPROG_WRONG) == {
        "I2C EEPROM or FRAM (24xx)": 1,
        "MicroWire EEPROM (93xx)": 1,
        "SPI EEPROM or FRAM (25xx)": 1,
        "SPI EEPROM (95xx)": 1,
        "AT45 DataFlash": 1,
        "wrong id, per its datasheet": 2,
        "size contradicts its part number and capacity byte": 1,
        "wrong name, per its maker's naming and its own VCC": 1,
    }


def test_imsprog_known_errors() -> None:
    # Each names the part and the id the file gives it, and a wrong size
    # that size, so an entry corrected upstream is taken again; the
    # A25L20PT, whose id the A25L40PT repeats, is not left out with it.
    assert imsprog.WRONG[("A25L40PT", "372022", None)] == "wrong id, per its datasheet"
    assert not any(part == "A25L20PT" for part, _, _ in imsprog.WRONG)
    sizes = {k: v for k, v in imsprog.WRONG.items() if k[2] is not None}
    assert len(sizes) == 11
    assert set(sizes.values()) == {"size contradicts its part number and capacity byte"}
    assert ("F25L008A", "8c2014", 2 << 20) in sizes
    assert ("MT29F4G01ABAFD12", "2c362c", None) in imsprog.WRONG
    assert ("PCT25VF010A", "bf4900", None) in imsprog.WRONG
    # The DS35Q4GM(1.8V) is the 1.8 V DS35M4GM: left out, so that a search
    # for the DS35Q4GM finds only the real one.
    assert imsprog.WRONG[("DS35Q4GM(1.8V)", "e5a4e5", None)].startswith("wrong name")
    assert len(imsprog.WRONG) == 16


def test_imsprog_takes_a_corrected_size(tmp_path: Path) -> None:
    # ES25P10 (the fixture's eighth entry) at 128 KiB, as it should be: no
    # longer the known error, so taken, and the stale key raises.
    es25p10 = nth(7)
    fixed = es25p10[:0x34] + (128 << 10).to_bytes(4, "little") + es25p10[0x38:]
    key = ("ES25P10", "4a2011", 256 << 10)
    wrong = {key: imsprog.WRONG[key]}
    assert imsprog.extract(dat(tmp_path, es25p10), wrong) == []
    with pytest.raises(ValueError, match=r"no entry is \[\('ES25P10', '4a2011', 262144\)\]"):
        imsprog.extract(dat(tmp_path / "fixed", fixed), wrong)
    (r,) = imsprog.extract(dat(tmp_path / "taken", fixed), {})
    assert r["size"] == 128 << 10


def test_imsprog_stale_keys_raise(tmp_path: Path) -> None:
    # A25L40PT under its right id: the key for the wrong one matches
    # nothing, and says so, in extract and in skipped.
    a25l40pt = nth(6)
    fixed = a25l40pt[:0x30] + b"\x13\x20\x37" + a25l40pt[0x33:]
    key = ("A25L40PT", "372022", None)
    root = dat(tmp_path, fixed)
    with pytest.raises(ValueError, match="remove it from WRONG"):
        imsprog.extract(root, {key: imsprog.WRONG[key]})
    with pytest.raises(ValueError, match="A25L40PT"):
        imsprog.skipped(root, {key: imsprog.WRONG[key]})
    (r,) = imsprog.extract(root, {})
    assert (r["name"], r["id"]) == ("A25L40PT", "372013")


def test_imsprog_without_an_end_entry(tmp_path: Path) -> None:
    (r,) = imsprog.extract(dat(tmp_path, IMSPROG_DAT[:0x44]), {})
    assert r["name"] == "FL016AIF"


def test_record_make_validates() -> None:
    with pytest.raises(KeyError, match="unknown record fields"):
        record.make("linux", "f", 1, "n", colour="red")
    with pytest.raises(ValueError, match="unknown features"):
        record.make("linux", "f", 1, "n", features=["telepathy"])
    with pytest.raises(ValueError, match="nowhere"):
        record.make("nowhere", "f", 1, "n")
    r = record.make("linux", "f", 1, "n", features=["otp", "lock", "otp"])
    assert list(r) == list(record.KEYS)
    assert r["features"] == ["lock", "otp"]


def test_opcodes_checks_values_against_the_table() -> None:
    o = Opcodes({"MY_READ": "0x03", "WRONG": "0x04"})
    o.add("READ_1_1_1", "first", "MISSING", "MY_READ")
    o.add("READ_1_1_1", "second")  # a second reason for the same operation
    o.add("READ_1_1_1", "first")  # a repeated reason is kept once
    assert "READ_1_1_1" in o
    assert o.to_json() == [{"op": "READ_1_1_1", "opcode": 3, "via": "first; second"}]
    with pytest.raises(ValueError, match=r"upstream says 0x04, spiflash's table 0x03"):
        o.add("READ_1_1_1", "bad header", "WRONG")
    with pytest.raises(ValueError, match="upstream says 0x99"):
        o.add("SE", "bad value", value=0x99)
    with pytest.raises(KeyError, match="unknown operation"):
        o.add("TELEPORT", "x")
    o.discard("READ_1_1_1")
    o.discard("READ_1_1_1")
    assert o.to_json() == []


# --- Zephyr ------------------------------------------------------------------

ZEPHYR = FIXTURES / "zephyr"


def test_zephyr() -> None:
    r = by_name(zephyr.extract(ZEPHYR))
    assert set(r) == {
        "MX25R6435F",
        "IS25WP064",
        "W25Q128JW",
        "MX25LM51245",
        "IS25LP128",
        "GD25LQ32D",
        "MX25L3233F",
    }
    # nordic,qspi-nor with the chip's own SFDP table: size in bits, page and
    # erase types from the table, readoc/writeoc as the modes used.
    m = r["MX25R6435F"]
    assert (m["file"], m["line"]) == ("boards/nordic/nrf52840dk/nrf52840dk_nrf52840.dts", 20)
    assert (m["id"], m["vendor"], m["size"], m["page_size"]) == ("c22817", None, 8 << 20, 256)
    assert m["erasers"] == [
        {"opcode": 0x20, "blocks": [[4096, 2048]]},
        {"opcode": 0x52, "blocks": [[32768, 256]]},
        {"opcode": 0xD8, "blocks": [[65536, 128]]},
    ]
    assert m["features"] == [
        "dual_read",
        "erase_32k",
        "erase_4k",
        "erase_64k",
        "quad_pp",
        "quad_read",
        "sfdp",
    ]
    assert {"has-dpd", "nordic,qspi-nor", "readoc=read4io", "writeoc=pp4io"} <= set(m["flags"])
    assert ops(m)["READ_1_4_4"] == (0xEB, "sfdp-bfp: 1-4-4; readoc = read4io")
    assert ops(m)["PP_1_4_4"] == (0x38, "writeoc = pp4io")
    assert m["notes"] == [
        "MX25R64 supports only pp and pp4io",
        "MX25R64 supports all readoc options",
    ]
    # flexspi: DT_SIZE_M(8 * 8) bits; the name from the soc-nv-flash inside.
    assert r["IS25WP064"]["size"] == 8 << 20
    # The overlay leaves the compatible to the board's .dts, and deletes and
    # replaces the node inside.
    w = r["W25Q128JW"]
    assert (w["file"], w["line"]) == (
        "boards/nxp/mimxrt1060_evk/mimxrt1060_evk_mimxrt1062_qspi_C.overlay",
        53,
    )
    assert (w["id"], w["size"]) == ("ef6018", 16 << 20)
    # The name from the comment on the jedec-id line; the maker from a
    # one-maker binding; the MSPI mode.
    x = r["MX25LM51245"]
    assert (x["vendor"], x["size"], x["features"]) == ("mxicy", 64 << 20, ["octal_read"])
    assert "mspi-io-mode=MSPI_IO_MODE_OCTAL" in x["flags"]
    # A descriptive compatible names the part and its maker (here with an id
    # that is not ISSI's, kept as the board has it).
    i = r["IS25LP128"]
    assert (i["vendor"], i["id"], i["size"]) == ("issi", "966018", 16 << 20)
    # bflb: no size; use-sfdp says the part answers SFDP.
    g = r["GD25LQ32D"]
    assert (g["size"], g["features"]) == (None, ["erase_4k", "sfdp"])
    assert "erase-block-size=4096" in g["flags"]
    assert set(ops(g)) == {"RDID", "RDSFDP"}
    # Two boards with the same node are one record, which names the other.
    # Its table is JESD216's first: nine DWORDs, no page size.
    f = r["MX25L3233F"]
    assert (f["size"], f["page_size"]) == (4 << 20, None)
    assert f["notes"] == ["Also in boards/particle/boron/dts/mesh_feather.dtsi:21"]


def zephyr_board(tmp_path: Path, nodes: str) -> list[record.Record]:
    """The records of a board file holding ``nodes``."""
    write(tmp_path, {"boards/x/x.dts": f"/dts-v1/;\n&spi0 {{\n{nodes}\n}};\n"})
    return zephyr.extract(tmp_path)


def test_zephyr_node_values(tmp_path: Path) -> None:
    (r,) = zephyr_board(
        tmp_path,
        """flash@0 { // w25q256jv
            compatible = "jedec,spi-nor";
            jedec-id = [ef 40 19 00];  /* w25q256jv */
            size-in-bytes = <0x2000000>;
            page-size = <256>;
            has-lock = <0x1c>;
            use-4b-addr-opcodes;
            use-fast-read;
            use-flag-status-register;
            enter-4byte-addr = <0x01>;
            dpd-wakeup-sequence = <30000>, <20>, <30000>;
        };""",
    )
    assert (r["name"], r["id"], r["ext_id"], r["page_size"]) == ("W25Q256JV", "ef4019", "00", 256)
    assert r["size"] is None  # size-in-bytes is nordic,qspi-nor's alone
    assert r["features"] == ["4byte_addr", "4byte_opcodes", "fast_read", "lock"]
    assert set(ops(r)) == {"RDID", "READ_1_1_1_FAST", "RDFSR", "EN4B"}
    assert "has-lock=0x1c" in r["flags"]
    assert "dpd-wakeup-sequence=<30000>, <20>, <30000>" in r["flags"]


def test_zephyr_modes(tmp_path: Path) -> None:
    n, s, o, q = zephyr_board(
        tmp_path,
        """n25q128a@0 {
            compatible = "nordic,qspi-nor";
            jedec-id = [20 ba 18];
            size-in-bytes = <0x1000000>;
            readoc = "read2o";
            writeoc = "pp";
            address-size-32;
            ppsize-512;
        };
        mx25l6433f@1 {
            compatible = "nxp,s32-qspi-nor";
            jedec-id = [c2 20 17];
            readoc = "1-1-1";
            writeoc = "1-4-4";
        };
        s28hl512t@2 {
            compatible = "infineon,s28hx512t", "jedec,nor";
            jedec-id = [34 5a 1a];
            mspi-io-mode = "MSPI_IO_MODE_OCTAL";
            mspi-data-rate = "MSPI_DATA_RATE_DUAL";
            enter-4byte-command = <0xb7>;
        };
        w25q16jv@3 {
            compatible = "jedec,nor";
            jedec-id = [ef 40 15];
            read-io-mode = "MSPI_IO_MODE_QUAD_1_4_4";
            erase-block-size = <4096>;
        };""",
    )
    assert (n["size"], n["page_size"]) == (16 << 20, 512)
    assert n["features"] == ["4byte_addr", "dual_read"]
    assert set(ops(n)) == {"RDID", "READ_1_1_2", "PP_1_1_1"}
    assert s["features"] == ["fast_read", "quad_pp"]
    assert o["vendor"] == "infineon"
    assert o["features"] == ["4byte_addr", "octal_dtr_pp", "octal_dtr_read", "octal_read"]
    assert ops(o)["EN4B"] == (0xB7, "enter-4byte-command")
    assert q["features"] == ["erase_4k", "quad_read"]


def test_zephyr_sfdp_disagreements(tmp_path: Path) -> None:
    # The nRF52840 DK's MX25R6435F table, under a wrong size and page size.
    (r,) = zephyr_board(
        tmp_path,
        """mx25r6435f@0 {
            compatible = "adi,max32-spixf-nor";
            jedec-id = [c2 28 17];
            sfdp-bfp = [e5 20 f1 ff ff ff ff 03 44 eb 08 6b 08 3b 04 bb
                        ee ff ff ff ff ff 00 ff ff ff 00 ff 0c 20 0f 52
                        10 d8 00 ff 23 72 f5 00 82 ed 04 cc 44 83 68 44
                        30 b0 30 b0 f7 c4 d5 5c 00 be 29 ff f0 d0 ff ff];
            size = <DT_SIZE_M(16)>;
            page-size = <4096>;
        };""",
    )
    assert (r["size"], r["page_size"]) == (2 << 20, 4096)
    assert r["notes"] == [
        "sfdp-bfp gives 8388608 bytes, size 2097152",
        "sfdp-bfp gives a 256-byte page, page-size 4096",
    ]


def test_zephyr_skips(tmp_path: Path) -> None:
    # Not SPI flash, and a node nothing names; a SPI NAND part that is named.
    (nand,) = zephyr_board(
        tmp_path,
        """s26hs512t@0 {
            compatible = "nxp,s32-qspi-hyperflash";
            jedec-id = [00 34 00 7b 00 1a 00 0f 00 90];
        };
        ext_flash_ctrl: flash-controller@1 {
            compatible = "nxp,imx-flexspi-nor";
            jedec-id = [ef 40 17];
        };
        w25n01gv: spi-nand@2 {
            compatible = "jedec,spi-nand";
            jedec-id = [ef aa 21];
            size-bytes = <0x8000000>;
        };""",
    )
    assert (nand["name"], nand["type"], nand["size"], nand["opcodes"]) == (
        "W25N01GV",
        "nand",
        128 << 20,
        [],
    )


def test_zephyr_refuses_what_it_cannot_read(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown flash binding"):
        zephyr_board(tmp_path, 'f@0 { compatible = "acme,nor"; jedec-id = [ef 40 17]; };')
    with pytest.raises(ValueError, match="no compatible"):
        zephyr_board(tmp_path, "w25q64@0 { jedec-id = [ef 40 17]; };")
    with pytest.raises(ValueError, match="unknown readoc 'read8io'"):
        zephyr_board(
            tmp_path,
            'w25q64@0 { compatible = "nordic,qspi-nor"; jedec-id = [ef 40 17];\n'
            'readoc = "read8io"; };',
        )
    with pytest.raises(ValueError, match="DT_FOO"):
        zephyr_board(
            tmp_path,
            'w25q64@0 { compatible = "jedec,spi-nor"; jedec-id = [ef 40 17]; size = <DT_FOO>; };',
        )


def test_dts_nodes() -> None:
    text = """/dts-v1/;
#include <foo.h>
#define TWO(x) \\
    ((x) * 2)
/ {
    /omit-if-no-ref/ a: b: node@1 {
        prop = "x;{y}", "// not a comment";
        flag; /* comment */
        #address-cells = <1>;
        /delete-property/ gone;
        child { }; // done
    };
};
&{/soc/spi@4000} { status = "okay"; };
lbl: &ref { };
"""
    root, path, ref = dts.nodes(text)
    (node,) = root.children
    assert (node.name, node.labels) == ("node@1", ["a", "b"])
    assert dts.strings(node.properties["prop"].value or "") == ["x;{y}", "// not a comment"]
    assert node.properties["flag"].value is None
    assert set(node.properties) == {"prop", "flag", "#address-cells"}
    assert [n.name for n in root.walk()] == ["/", "node@1", "child"]
    assert path.name == "&{/soc/spi@4000}"
    assert (ref.name, ref.reference, ref.labels) == ("&ref", "ref", ["lbl"])
    assert dts.bytestring("[c2 28\n 17]") == b"\xc2\x28\x17"
    assert dts.cells("<1 (2 * 3) DT_SIZE_K(4)>") == [1, 6, 4096]
    with pytest.raises(ValueError, match="not a byte string"):
        dts.bytestring("<1>")
    with pytest.raises(ValueError, match="not a cell list"):
        dts.cells("[01]")
    with pytest.raises(ValueError, match="no ';'"):
        dts.nodes("a = <1>")


def test_sfdp_decode() -> None:
    # JESD216's other density form (2^N bits), 4-byte addresses only, DTR,
    # the 2-2-2 and 4-4-4 reads, and no erase types but DWORD 1's 4 KiB one.
    dw1 = 0xE5 | 0x20 << 8 | 1 << 16 | 2 << 17 | 1 << 19
    dws = [dw1, 1 << 31 | 28, 0, 0x3B00, 0x11, 0xBB << 24, 0xEB << 24, 0, 0]
    b = sfdp.decode(b"".join(d.to_bytes(4, "little") for d in dws))
    assert b.size == 32 << 20
    assert (b.address_bytes, b.dtr) == ((4,), True)
    assert b.reads == {"1-1-2": 0x3B, "2-2-2": 0xBB, "4-4-4": 0xEB}
    assert b.erases == [(0x20, 4096)]
    assert (b.page_size, b.quad_enable) == (None, None)
    with pytest.raises(ValueError, match="at least 9 whole DWORDs"):
        sfdp.decode(bytes(10))


def test_zephyr_size_from_sfdp(tmp_path: Path) -> None:
    # No size property: the table's. 4-byte addresses only.
    dw1 = 0xE5 | 0x20 << 8 | 2 << 17
    dws = [dw1, 1 << 31 | 28, 0, 0, 0, 0, 0, 0, 0]
    table = " ".join(f"{b:02x}" for d in dws for b in d.to_bytes(4, "little"))
    (r,) = zephyr_board(
        tmp_path,
        f'w25q256jv@0 {{ compatible = "jedec,spi-nor"; jedec-id = [ef 40 19]; '
        f"sfdp-bfp = [{table}]; }};",
    )
    assert (r["size"], r["page_size"]) == (32 << 20, None)
    assert r["features"] == ["4byte_addr", "erase_4k", "sfdp"]


# --- QEMU --------------------------------------------------------------------

QEMU_M25P80 = fixture("qemu/hw/block/m25p80.c")

QEMU_SFDP = fixture("qemu/hw/block/m25p80_sfdp.c")


def test_qemu(tmp_path: Path) -> None:
    write(tmp_path, {qemu.M25P80: QEMU_M25P80, qemu.SFDP_C: QEMU_SFDP})
    recs = qemu.extract(tmp_path)
    r = by_name(recs)
    assert [x["name"] for x in recs] == [
        "AT25FS010",
        "AT25128A-NONJEDEC",
        "AT25256A-NONJEDEC",
        "MX25L25635E",
        "N25Q256A",
        "MT35XU01G",
        "N25Q00",
        "S25SL032P",
        "S25FL016K",
        "W25Q512JV",
        "25CSM04",
    ]
    assert all(x["source"] == "qemu" and x["file"] == qemu.M25P80 for x in recs)

    a = r["AT25FS010"]
    assert a["vendor"] == "Atmel"  # the heading, without its "-- some are ..." remark
    assert a["line"] == 119
    assert (a["id"], a["ext_id"], a["size"], a["page_size"], a["sector_size"]) == (
        "1f6601",
        None,
        128 << 10,
        256,
        32 << 10,
    )
    assert a["erasers"] == [
        {"opcode": 0x20, "blocks": [[4096, 32]]},
        {"opcode": 0xD8, "blocks": [[32 << 10, 4]]},
    ]
    assert a["features"] == ["erase_4k", "fast_read"]
    assert a["flags"] == ["ER_4K"]
    assert a["sfdp"] is None
    assert ops(a) == {
        "RDID": (0x9F, "JEDEC_READ: the entry's id bytes"),
        "READ_1_1_1": (0x03, "m25p80 decodes it for every part"),
        "READ_1_1_1_FAST": (0x0B, "m25p80 decodes it for every part"),
        "PP_1_1_1": (0x02, "m25p80 decodes it for every part"),
        "BE_4K": (0x20, "ER_4K"),
        "SE": (0xD8, "ERASE_SECTOR: the entry's sector size"),
        "CHIP_ERASE": (0xC7, "BULK_ERASE"),
        "CHIP_ERASE_ALT": (0x60, "BULK_ERASE_60"),
    }

    # The EEPROMs: no id, a byte-sized "sector", the block comment is not a heading.
    e = r["AT25128A-NONJEDEC"]
    assert e["vendor"] == "Atmel"
    assert e["id"] is None
    assert e["id_method"] is None
    assert e["size"] == 128 << 10
    assert e["sector_size"] is None
    assert e["erasers"] is None
    assert e["features"] == ["fast_read", "no_erase"]
    assert set(ops(e)) == {"READ_1_1_1", "READ_1_1_1_FAST", "PP_1_1_1"}

    # INFO6: a three-byte ext_id; a dump: the SFDP-derived reads and erases.
    m = r["MX25L25635E"]
    assert m["vendor"] == "Macronix"
    assert (m["id"], m["ext_id"]) == ("c22019", "c22019")
    assert m["size"] == 32 << 20
    assert m["sfdp"] is not None
    assert m["sfdp"].startswith("53464450000101ff")
    assert m["features"] == [
        "4byte_addr",
        "dual_read",
        "erase_32k",
        "erase_4k",
        "erase_64k",
        "fast_read",
        "quad_read",
        "sfdp",
    ]
    assert ops(m)["RDSFDP"] == (0x5A, ".sfdp_read = m25p80_sfdp_mx25l25635e")
    assert ops(m)["READ_1_4_4"] == (0xEB, "SFDP BFPT 1-4-4 fast read: 2 mode + 4 wait clocks")
    assert ops(m)["BE_32K"] == (0x52, "ER_32K; SFDP BFPT erase type 2: 32768 B")
    assert m["notes"] == []

    # Flags for the status register layout; the multi-line heading before Spansion.
    n = r["N25Q256A"]
    assert n["vendor"] == "Micron"
    assert n["flags"] == ["ER_4K", "HAS_SR_BP3_BIT6", "HAS_SR_TB"]
    assert "lock" in n["features"]
    assert n["sfdp"] is not None
    assert r["S25SL032P"]["vendor"] == "Spansion"
    assert r["S25SL032P"]["ext_id"] == "4d00"
    assert r["S25FL016K"]["vendor"] == "Spansion"  # filed there, with a Winbond id
    assert r["S25FL016K"]["id"] == "ef4015"

    # INFO_STACKED: a die count, and only two of the three ext_id bytes.
    t = r["MT35XU01G"]
    assert t["ext_id"] == "4100"
    assert "INFO_STACKED keeps 2 bytes of the ext_id 0x104100" in t["notes"]
    assert "die_cnt=2" in t["flags"]
    assert ops(t)["DIE_ERASE"] == (0xC4, "die_cnt = 2")
    assert ops(t)["BE_32K_4B"] == (0x5C, "SFDP 4BAIT erase type 3: 32768 B, 4-byte address")
    assert t["sector_size"] == 128 << 10
    assert "erase_64k" not in t["features"]
    assert r["N25Q00"]["flags"] == ["ER_4K", "die_cnt=4"]
    assert r["N25Q00"]["ext_id"] == "1000"
    assert r["N25Q00"]["sfdp"] is None

    w = r["W25Q512JV"]
    assert w["vendor"] == "Winbond"
    assert {"4byte_opcodes", "qpi", "quad_pp", "sfdp"} <= set(w["features"])
    assert ops(w)["READ_1_4_4_4B"] == (0xEC, "SFDP 4BAIT bit 5: fast read 1-4-4, 4-byte address")
    assert ops(w)["EN4B"][0] == 0xB7
    assert any(note.startswith("SFDP: 0xeb 4-4-4, no named operation") for note in w["notes"])
    assert any(note.startswith("SFDP: 4BAIT claims") for note in w["notes"])

    c = r["25CSM04"]
    assert c["vendor"] == "Microchip"
    assert (c["id"], c["ext_id"]) == ("29cc00", "0100")
    assert c["features"] == ["erase_64k", "fast_read"]


def test_qemu_sfdp_dumps(tmp_path: Path) -> None:
    write(tmp_path, {qemu.SFDP_C: QEMU_SFDP})
    dumps = qemu.sfdp_dumps(tmp_path)
    assert set(dumps) == {
        "m25p80_sfdp_n25q256a",
        "m25p80_sfdp_mt35xu01g",
        "m25p80_sfdp_mx25l25635e",
        "m25p80_sfdp_w25q512jv",
    }
    assert len(dumps["m25p80_sfdp_mx25l25635e"]) == 128
    assert len(dumps["m25p80_sfdp_w25q512jv"]) == 256
    assert dumps["m25p80_sfdp_w25q512jv"][:4] == b"SFDP"


def test_qemu_errors(tmp_path: Path) -> None:
    write(tmp_path, {qemu.M25P80: "int x;", qemu.SFDP_C: ""})
    with pytest.raises(ValueError, match="no known_devices"):
        qemu.extract(tmp_path)
    table = QEMU_M25P80.split("static const FlashPartInfo known_devices[] = {")[0]
    commands = "typedef enum {" + QEMU_M25P80.split("typedef enum {")[1]
    one = (
        "static const FlashPartInfo known_devices[] = {\n"
        '    { INFO("x", 0xef4020, 0, 64 << 10, 1024, ER_4K), .sfdp_read = m25p80_sfdp_x },\n'
        "};\n"
    )
    write(tmp_path, {qemu.M25P80: table + one + commands})
    with pytest.raises(ValueError, match=r"no m25p80_sfdp_x\(\) in"):
        qemu.extract(tmp_path)
    write(
        tmp_path,
        {
            qemu.M25P80: table
            + 'static const FlashPartInfo known_devices[] = {\n    { .part_name = "x" },\n};\n'
            + commands
        },
    )
    with pytest.raises(ValueError, match="not INFO/INFO6/INFO_STACKED"):
        qemu.extract(tmp_path)
    # The opcodes come from the model's FlashCMD enum: it must be there, and
    # every command must have its value spelled out.
    write(tmp_path, {qemu.M25P80: table + one})
    with pytest.raises(ValueError, match="no FlashCMD enum"):
        qemu.extract(tmp_path)
    write(tmp_path, {qemu.M25P80: table + one + "typedef enum { NOP = 0, WRSR, } FlashCMD;\n"})
    with pytest.raises(ValueError, match="FlashCMD WRSR has no explicit value"):
        qemu.extract(tmp_path)


DEDIPROG = (FIXTURES / "dediprog" / dediprog.DB).read_text(encoding="utf-16")


def dediprog_tree(root: Path, text: str = DEDIPROG) -> Path:
    (root / dediprog.DB).write_text(text, encoding="utf-16")
    return root


def dediprog_line(**attrs: str | None) -> str:
    """The W25Q128FV's entry, its attributes changed, added, or dropped (for
    None)."""
    line = next(ln for ln in DEDIPROG.split("\n") if 'TypeName="W25Q128FV"' in ln)
    for key, value in attrs.items():
        line = re.sub(rf' {key}="[^"]*"', "", line)
        if value is not None:
            line = line.replace("/>", f' {key}="{value}"/>')
    return line


def dediprog_chip(root: Path, **attrs: str | None) -> list[record.Record]:
    """The records of a table of one chip (:func:`dediprog_line`)."""
    return dediprog.extract(dediprog_tree(root, f"<x>\n{dediprog_line(**attrs)}\n</x>\n"))


def test_dediprog(tmp_path: Path) -> None:
    recs = dediprog.extract(dediprog_tree(tmp_path))
    r = by_name(recs)
    w = r["W25Q128FV"]
    assert (w["line"], w["vendor"], w["id"], w["id_method"]) == (37, "Winbond", "ef4018", "rdid")
    # 0xd8 has no layout, and gives no sector size: BlockSizeInByte is a
    # template's 64 KiB.
    assert (w["size"], w["page_size"], w["sector_size"]) == (16 << 20, 256, None)
    assert w["erasers"] == [{"opcode": 0xC7, "blocks": [[16 << 20, 1]]}]
    # Only the single-line read and program of the packed words.
    assert set(ops(w)) == {"RDID", "READ_1_1_1_FAST", "PP_1_1_1", "SE", "CHIP_ERASE"}
    assert ops(w)["READ_1_1_1_FAST"] == (0x0B, "ReadCmd=0x006B3B0B")
    assert w["features"] == ["fast_read", "lock", "qpi"]
    assert {"ProgramIOMethod=SPQD_RSWQW", "QPIEnable", "Voltage=3.3V"} <= set(w["flags"])
    assert w["notes"][0].startswith("128 Mbit")
    # Legacy ids: REMS, AT25F, and RES read with its dummy bytes (0xff), or
    # answering the manufacturer too (with its continuation code).
    assert (r["25LF040A"]["id"], r["25LF040A"]["id_method"]) == ("bf44", "rems")
    assert (r["AT25F1024A"]["id"], r["AT25F1024A"]["id_method"]) == ("1f60", "at25f")
    epcs = [x for x in recs if x["name"] == "EPCS16S"]
    assert [(x["id"], x["id_method"]) for x in epcs] == [("202015", "rdid"), ("14", "res1")]
    assert (r["PM25LV512"]["id"], r["PM25LV512"]["id_method"]) == ("9d7b", "res2")
    assert (r["PM25LV512A"]["id"], r["PM25LV512A"]["id_method"]) == ("7f9d7b", "res2")
    # Continuation codes, with one or two device bytes; a fourth byte of a
    # bank 0 id is an extended id.
    assert (r["A25L05PU"]["id"], r["IS25CD010"]["id"]) == ("7f372010", "7f9d21")
    assert (r["NM25L256FV"]["id"], r["NM25L256FV"]["ext_id"]) == ("521019", "52")
    # 0xaf answers the JEDEC id too, but is not RDID.
    mt = r["MT25QL01GB"]
    assert (mt["id"], mt["id_method"]) == ("20ba21", "rdid")
    assert "RDID" not in ops(mt)
    assert "RDIDCommand=0xAF" in mt["flags"]
    assert {"opcode": 0xC4, "blocks": [[64 << 20, 2]]} in mt["erasers"]
    assert "RDID" in ops(r["MT25TL256B ( for one die)"])
    # A 64 KiB block on a 32 KiB part is no layout, and no sector size.
    cd = r["IS25CD025"]
    assert (cd["erasers"], cd["sector_size"]) == (
        [{"opcode": 0xC7, "blocks": [[32 << 10, 1]]}],
        None,
    )
    # 0x20 erases the 4 KiB sectors; 4-byte opcodes above 16 MiB.
    en = r["EN35SXR256A"]
    assert {"opcode": 0x20, "blocks": [[4096, 8192]]} in en["erasers"]
    assert en["sector_size"] == 4096
    assert {"4byte_addr", "4byte_opcodes", "erase_4k"} <= set(en["features"])
    # SPI NAND: the dummy byte, where Dediprog reads one, is not the id.
    n = r["W25N01GVXXIG"]
    assert (n["type"], n["id"], n["id_method"]) == ("nand", "efaa21", "rdid_opcode_dummy")
    assert (n["size"], n["page_size"], n["sector_size"]) == (128 << 20, 2048, 128 << 10)
    assert [o["op"] for o in n["opcodes"]] == ["RDID"]
    assert (n["erasers"], n["features"]) == (None, [])
    assert (r["GD5F1GQ4UC"]["id"], r["GD5F1GQ4UC"]["id_method"]) == ("c8b148", "rdid_opcode")
    assert r["MK60N1GAL"]["id"] == "a791"  # 0xA791, read as three bytes
    assert len(recs) == 29


def test_dediprog_classes(tmp_path: Path) -> None:
    r = by_name(dediprog.extract(dediprog_tree(tmp_path)))
    # DataFlash: its SPI NOR template is not taken, only its id and size.
    at45 = r["AT45DB642D"]
    assert (at45["id"], at45["size"], at45["page_size"], at45["sector_size"]) == (
        "1f2800",
        8 << 20,
        None,
        None,
    )
    assert (at45["erasers"], at45["features"], set(ops(at45))) == (None, [], {"RDID"})
    # SST's parts written a byte or word at a time: byte program; 0x52
    # erases 32 KiB.
    assert "BP" in ops(r["25VF040B"])
    assert "PP_1_1_1" not in ops(r["25VF040B"])
    sst = r["25LF040A"]
    assert {"opcode": 0x52, "blocks": [[32 << 10, 16]]} in sst["erasers"]
    assert (sst["sector_size"], sst["features"]) == (32 << 10, ["erase_32k", "fast_read", "lock"])
    # On an AT25F, 0x52 erases SectorSizeInByte, where that is not the
    # template's 4096 (the AT25F2048's 0x52 erases 64 KiB).
    at25f = r["AT25F1024A"]
    assert {"opcode": 0x52, "blocks": [[32 << 10, 4]]} in at25f["erasers"]
    assert at25f["sector_size"] == 32 << 10
    at25f2048 = r["AT25F2048"]
    assert (at25f2048["erasers"], at25f2048["sector_size"]) == (
        [{"opcode": 0x62, "blocks": [[256 << 10, 1]]}],
        None,
    )
    assert "BE_32K" in ops(at25f2048)
    # Swapped read and program words, read the right way round.
    bg = r["BG25Q80A"]
    assert ops(bg)["READ_1_1_1_FAST"] == (0x0B, "ReadCmd=0x00EBBB0B")
    assert ops(bg)["PP_1_1_1"] == (0x02, "ProgramCmd=0x00000002")
    # SPI NAND sizes that count the spare area; ICType SD_NAND.
    assert r["MX35UF4GE4AD"]["size"] == 512 << 20
    sd = r["W25N02KWXIR"]
    assert (sd["type"], sd["id"], sd["size"]) == ("nand", "efba22", 256 << 20)
    # No ICType, but a SPI NOR class.
    assert (r["FM25Q02B"]["type"], r["FM25Q02B"]["id"]) == ("nor", "a14012")


def test_dediprog_ids_under_the_wrong_command(tmp_path: Path) -> None:
    r = by_name(dediprog.extract(dediprog_tree(tmp_path)))
    # A three-byte REMS id is the JEDEC id.
    wf = r["25WF512"]
    assert (wf["id"], wf["id_method"]) == ("bf2501", "rdid")
    assert ops(wf)["RDID"] == (0x9F, "a JEDEC id under RDIDCommand=0x90")
    # Sanyo's parts answer 0x9f with their two id bytes, repeated.
    assert (r["LE25FU106B"]["id"], r["LE25FU106B"]["id_method"]) == ("621d", "res2")
    assert (r["LE25FU406B"]["id"], r["LE25FU406B"]["id_method"]) == ("621e", "res2")
    # UniqueID gives the id that JedecDeviceID leaves out, or cuts short.
    assert (r["M25P80"]["id"], r["TS25L10P"]["id"]) == ("202014", "202011")


def test_dediprog_skipped(tmp_path: Path) -> None:
    assert dediprog.skipped(dediprog_tree(tmp_path)) == {
        "no id": 3,
        "a legacy id under 0x9f": 1,
        "id with an odd number of hex digits": 1,
    }


def test_dediprog_nand_size(tmp_path: Path) -> None:
    nand = {"ICType": "SPI_NAND", "Class": "GD5F1GQ4xCx", "EraseCmd": "0x000000D8"}
    (r,) = dediprog_chip(
        tmp_path,
        **nand,
        ChipSizeInKByte="270336",  # 2 Gbit and 64 spare bytes per 2 KiB page
        PageSizeInByte="2048",
        BlockSizeInByte="131072",
        SpareSizeInByte="0x00800040",
    )
    assert r["size"] == 256 << 20
    with pytest.raises(ValueError, match="neither blocks of 131072"):
        dediprog_chip(
            tmp_path,
            **nand,
            ChipSizeInKByte="270000",
            PageSizeInByte="2048",
            BlockSizeInByte="131072",
            SpareSizeInByte="0x00800040",
        )


def test_dediprog_refuses_what_it_cannot_read(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"dedicfg:2: W25Q128FV: unknown RDIDCommand"):
        dediprog_chip(tmp_path, RDIDCommand="0x4B")
    with pytest.raises(ValueError, match="cannot read id ef4018 for nor RDIDCommand 0xAB"):
        dediprog_chip(tmp_path, RDIDCommand="0xAB")
    with pytest.raises(ValueError, match="for nand RDIDCommand 0x90"):
        dediprog_chip(tmp_path, ICType="SPI_NAND", RDIDCommand="0x90", JedecDeviceID="0xEF40")
    with pytest.raises(ValueError, match="unknown ICType 'SPI_EEPROM'"):
        dediprog_chip(tmp_path, ICType="SPI_EEPROM")
    with pytest.raises(ValueError, match="unknown opcode 0x6b in slot 0"):
        dediprog_chip(tmp_path, ReadCmd="0x0000006B")
    with pytest.raises(ValueError, match="not a whole number of 4096-byte blocks"):
        dediprog_chip(tmp_path, ChipSizeInKByte="6", EraseCmd="0x000020C7")
    # What cannot be read at all names its line, in extract and in skipped.
    bad = dediprog_tree(tmp_path, f"<x>\n{dediprog_line(JedecDeviceID='0xZZ')}\n</x>\n")
    with pytest.raises(ValueError, match="dedicfg:2: W25Q128FV: invalid literal"):
        dediprog.extract(bad)
    with pytest.raises(ValueError, match="dedicfg:2: W25Q128FV: invalid literal"):
        dediprog.skipped(bad)
    with pytest.raises(ValueError, match="dedicfg:2: W25Q128FV: missing attribute RDIDCommand"):
        dediprog_chip(tmp_path, RDIDCommand=None)
    with pytest.raises(ValueError, match="not one <Chip"):
        dediprog.extract(dediprog_tree(tmp_path, '<x>\n<Chip TypeName="a"\n/>\n</x>\n'))
    with pytest.raises(ValueError, match="dedicfg:1: unclosed token"):
        dediprog.extract(dediprog_tree(tmp_path, '<Chip TypeName="a/>\n'))
    with pytest.raises(ValueError, match="no <Chip> entries"):
        dediprog.extract(dediprog_tree(tmp_path, "<x/>\n"))


MEDIATEK = FIXTURES / "mediatek"

MEDIATEK_IDS = (MEDIATEK / mediatek.IDS).read_text()


def mediatek_tree(tmp_path: Path, ids: str) -> Path:
    return write(
        tmp_path, {mediatek.IDS: ids, mediatek.DEF_H: (MEDIATEK / mediatek.DEF_H).read_text()}
    )


def test_mediatek() -> None:
    recs = mediatek.extract(MEDIATEK)
    # Thirteen entries, two of them known to be wrong.
    assert len(recs) == 11
    r = by_name(recs)
    w = r["W25N01GV"]
    assert (w["file"], w["line"], w["type"], w["vendor"]) == (mediatek.IDS, 56, "nand", None)
    assert (w["id"], w["id_method"]) == ("efaa21", "rdid_opcode_dummy")
    assert (w["size"], w["page_size"], w["sector_size"]) == (128 << 20, 2048, 128 << 10)
    assert w["features"] == ["dual_read", "quad_pp", "quad_read"]
    assert w["flags"] == [
        "cap_pl=snand_cap_program_load_x4",
        "cap_rd=snand_cap_read_from_cache_quad",
        "ndies=1",
        "planes_per_die=1",
        "program_load=1_1_1,1_1_4",
        "read_from_cache=1_1_1,1_1_2,1_2_2,1_1_4,1_4_4",
        "sparesize=64",
    ]
    assert w["notes"] == ["64 B OOB per page; 1 plane(s), 1 die(s) of 1024 blocks"]
    assert w["opcodes"] == []
    # The size is the main area of every die; the spare area is not in it.
    m = r["W25M02GV"]
    assert m["size"] == 256 << 20
    assert {"ndies=2", "select_die=mtk_snand_winbond_select_die"} <= set(m["flags"])
    # Planes are not counted again: a two-plane part's blocks are all its
    # blocks.
    t = r["MT29F2G01AAAED"]
    assert (t["size"], t["sector_size"]) == (256 << 20, 128 << 10)
    assert "planes_per_die=2" in t["flags"]
    # Read from cache on one, two or four lines; program load on one only.
    assert t["features"] == ["dual_read", "quad_read"]
    assert "program_load=1_1_1" in t["flags"]
    d = r["MT29F4G01ADAGD"]
    assert d["size"] == 512 << 20
    assert "select_die=mtk_snand_micron_select_die" in d["flags"]
    # The id method is the one the entry names.
    g = r["GD5F1GQ4UAWXX"]
    assert (g["id"], g["id_method"]) == ("c810", "rdid_opcode_addr")
    # A memory organisation written out: 128 pages per block.
    a = r["EM73C044SNA"]
    assert (a["size"], a["sector_size"]) == (128 << 20, 256 << 10)
    # The driver takes the first entry an id matches.
    assert r["IS37SML01G1"]["notes"][0] == (
        "never used: the driver matches the entry on line 85 first"
    )
    assert len(r["F50L1G41A"]["notes"]) == 1
    # Known wrong entries are left out: the second EM73D044SND, under the
    # EM73C044SND's id, and the EM73E044SNE at 8 Gbit.
    (snd,) = [x for x in recs if x["name"] == "EM73D044SND"]
    assert snd["id"] == "d51e"
    assert "EM73E044SNE" not in r


def test_mediatek_skipped() -> None:
    assert mediatek.skipped(MEDIATEK) == {
        "wrong id: the table gives the part again, with another id": 1,
        "size contradicts its part number's density": 1,
    }
    assert set(mediatek.WRONG) == {
        ("EM73D044SND", "d51d", None),
        ("EM73E044SNE", "d50e", 1 << 30),
    }


def test_mediatek_stale_keys_raise(tmp_path: Path) -> None:
    # The EM73E044SNE at 2 Gbit: not the known error any more, so its key,
    # matching nothing, raises, in extract and in skipped.
    fixed = mediatek_tree(
        tmp_path,
        MEDIATEK_IDS.replace(
            "0xd5, 0x0e),\n\t\t   SNAND_MEMORG_8G_4K_256",
            "0xd5, 0x0e),\n\t\t   SNAND_MEMORG_2G_2K_64",
        ),
    )
    with pytest.raises(ValueError, match=r"no entry is \[\('EM73E044SNE'.*remove it from WRONG"):
        mediatek.extract(fixed)
    with pytest.raises(ValueError, match="EM73E044SNE"):
        mediatek.skipped(fixed)
    wrong = {k: v for k, v in mediatek.WRONG.items() if k[0] != "EM73E044SNE"}
    assert by_name(mediatek.extract(fixed, wrong))["EM73E044SNE"]["size"] == 256 << 20


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        ("SNAND_ID_ADDR, 0xc8, 0x10", "SNAND_ID_DIRECT, 0xc8, 0x10", "cannot read the id"),
        ("SNAND_MEMORG(2048, 64, 128, 512, 1, 1)", "SNAND_MEMORG(2048, 64, 128)", "organisation"),
        ("2048, 128, 64, 2048, 2, 2)", "2048, 128, 64, 2048, 4, 2)", "4 planes"),
        ("mtk_snand_micron_select_die),", "mtk_snand_other_select_die),", "die select"),
        ("&snand_cap_program_load_x1)", "&snand_cap_program_load_x2)", "unknown I/O"),
        (
            "SPI_IO_1_1_1 | SPI_IO_1_1_4,\n\tSNAND_OP(SNAND_IO_1_1_1, SNAND_CMD_PROGRAM",
            "SPI_IO_1_1_4,\n\tSNAND_OP(SNAND_IO_1_1_1, SNAND_CMD_PROGRAM",
            "allows",
        ),
        ("SNAND_OP(SNAND_IO_1_1_1, SNAND_CMD_PROGRAM_LOAD, 0));", "OP(1, 2, 0));", "cannot read"),
        ("&snand_cap_program_load_x1),", "&snand_cap_program_load_x1, 0, 0),", "7 arguments"),
    ],
)
def test_mediatek_refuses(tmp_path: Path, old: str, new: str, error: str) -> None:
    assert MEDIATEK_IDS.count(old) == 1
    tree = mediatek_tree(tmp_path, MEDIATEK_IDS.replace(old, new))
    with pytest.raises(ValueError, match=rf"mtk-snand-ids\.c:\d+: .*{error}"):
        mediatek.extract(tree, {})


def test_mediatek_no_table(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no SNAND_INFO entries"):
        mediatek.extract(mediatek_tree(tmp_path, "static const int x;\n"), {})
