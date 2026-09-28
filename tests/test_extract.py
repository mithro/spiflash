"""Each extractor, on a miniature tree in its upstream's format.

The snippets, in tests/fixtures/<upstream>/<path>, are cut down from the real
files at the commits pinned in tools/sources.toml, keeping the shapes that
needed handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from spiflash_extract import flashrom, linux, openfpgaloader, openocd, qemu, record, uboot
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


def test_linux_nor(linux_tree: Path) -> None:
    recs = linux.extract_nor(linux_tree)
    r = by_name(recs)
    w = r["w25q128"]
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
    big = r["w25q512jvq"]
    assert {"4byte_addr", "4byte_opcodes", "otp"} <= set(big["features"])
    assert ops(big)["READ_1_1_1_4B"] == (0x13, "SPI_NOR_4B_OPCODES")
    assert ops(big)["SE_4B"] == (0xDC, "SPI_NOR_4B_OPCODES")
    assert ops(big)["PP_1_1_1_4B"] == (0x12, "SPI_NOR_4B_OPCODES")

    # Neither a name nor a comment: named by vendor and id.
    assert r["winbond-ef60"]["notes"] == ["Linux gives this entry no name"]

    s = r["s25fl128s1"]
    assert s["id"] == "012018"
    assert s["ext_id"] == "4d0180"
    assert "USE_CLSR" in s["flags"]

    n = r["everspin-nonjedec"]
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
    assert set(r) == {"w25q128", "w25q512", "s25fl128s", "s25sl12800", "mb85rs256ty"}
    w = r["w25q128"]
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
    w512 = ops(r["w25q512"])
    assert w512["PP_1_1_4"] == (0x32, "SPI_NOR_QUAD_READ")
    assert w512["PP_1_1_4_4B"] == (0x34, "SPI_NOR_4B_OPCODES")
    assert "READ_1_1_1_FAST" not in ops(r["s25fl128s"])  # SPI_NOR_NO_FR
    assert {"4byte_addr", "4byte_opcodes", "quad_read"} <= set(r["w25q512"]["features"])
    s = r["s25fl128s"]
    assert s["vendor"] == "spansion"
    assert s["ext_id"] == "4d0180"
    assert "fast_read" not in s["features"]
    assert r["s25sl12800"]["ext_id"] == "0300"
    assert r["s25sl12800"]["sector_size"] == 256 * 1024
    f = r["mb85rs256ty"]
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
    assert set(r) == {"w25q128fv/jv", "is25wp512m", "gd25q512", "s25fl008", "fm25v02"}
    # A byte OpenOCD holds that is no known operation is noted, not guessed.
    s008 = r["s25fl008"]
    assert "OpenOCD's qread_cmd is 0x08, which is not a known operation" in s008["notes"]
    assert "quad_read" not in s008["features"]
    assert set(ops(r["fm25v02"])) == {"RDID", "READ_1_1_1", "PP_1_1_1"}
    w = r["w25q128fv/jv"]
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
    assert ops(r["is25wp512m"])["READ_1_4_4_4B"] == (0xEC, "qread_cmd")
    assert w["erasers"] == [
        {"opcode": 0xD8, "blocks": [[65536, 256]]},
        {"opcode": 0xC7, "blocks": [[16 << 20, 1]]},
    ]
    assert w["features"] == ["erase_64k", "quad_read"]
    assert "4byte_addr" in r["is25wp512m"]["features"]
    g = r["gd25q512"]
    assert g["features"] == ["erase_4k"]
    assert "CHIP_ERASE" not in ops(g)
    f = r["fm25v02"]
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


QEMU_M25P80 = fixture("qemu/hw/block/m25p80.c")

QEMU_SFDP = fixture("qemu/hw/block/m25p80_sfdp.c")


def test_qemu(tmp_path: Path) -> None:
    write(tmp_path, {qemu.M25P80: QEMU_M25P80, qemu.SFDP_C: QEMU_SFDP})
    recs = qemu.extract(tmp_path)
    r = by_name(recs)
    assert [x["name"] for x in recs] == [
        "at25fs010",
        "at25128a-nonjedec",
        "at25256a-nonjedec",
        "mx25l25635e",
        "n25q256a",
        "mt35xu01g",
        "n25q00",
        "s25sl032p",
        "s25fl016k",
        "w25q512jv",
        "25csm04",
    ]
    assert all(x["source"] == "qemu" and x["file"] == qemu.M25P80 for x in recs)

    a = r["at25fs010"]
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
    }

    # The EEPROMs: no id, a byte-sized "sector", the block comment is not a heading.
    e = r["at25128a-nonjedec"]
    assert e["vendor"] == "Atmel"
    assert e["id"] is None
    assert e["id_method"] is None
    assert e["size"] == 128 << 10
    assert e["sector_size"] is None
    assert e["erasers"] is None
    assert e["features"] == ["fast_read", "no_erase"]
    assert set(ops(e)) == {"READ_1_1_1", "READ_1_1_1_FAST", "PP_1_1_1"}

    # INFO6: a three-byte ext_id; a dump: the SFDP-derived reads and erases.
    m = r["mx25l25635e"]
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
    n = r["n25q256a"]
    assert n["vendor"] == "Micron"
    assert n["flags"] == ["ER_4K", "HAS_SR_BP3_BIT6", "HAS_SR_TB"]
    assert "lock" in n["features"]
    assert n["sfdp"] is not None
    assert r["s25sl032p"]["vendor"] == "Spansion"
    assert r["s25sl032p"]["ext_id"] == "4d00"
    assert r["s25fl016k"]["vendor"] == "Spansion"  # filed there, with a Winbond id
    assert r["s25fl016k"]["id"] == "ef4015"

    # INFO_STACKED: a die count, and only two of the three ext_id bytes.
    t = r["mt35xu01g"]
    assert t["ext_id"] == "4100"
    assert "INFO_STACKED keeps 2 bytes of the ext_id 0x104100" in t["notes"]
    assert "die_cnt=2" in t["flags"]
    assert ops(t)["DIE_ERASE"] == (0xC4, "die_cnt = 2")
    assert ops(t)["BE_32K_4B"] == (0x5C, "SFDP 4BAIT erase type 3: 32768 B, 4-byte address")
    assert t["sector_size"] == 128 << 10
    assert "erase_64k" not in t["features"]
    assert r["n25q00"]["flags"] == ["ER_4K", "die_cnt=4"]
    assert r["n25q00"]["ext_id"] == "1000"
    assert r["n25q00"]["sfdp"] is None

    w = r["w25q512jv"]
    assert w["vendor"] == "Winbond"
    assert {"4byte_opcodes", "qpi", "quad_pp", "sfdp"} <= set(w["features"])
    assert ops(w)["READ_1_4_4_4B"] == (0xEC, "SFDP 4BAIT bit 5: fast read 1-4-4, 4-byte address")
    assert ops(w)["EN4B"][0] == 0xB7
    assert any(note.startswith("SFDP: 0xeb 4-4-4, no named operation") for note in w["notes"])
    assert any(note.startswith("SFDP: 4BAIT claims") for note in w["notes"])

    c = r["25csm04"]
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
    write(
        tmp_path,
        {
            qemu.M25P80: table + "static const FlashPartInfo known_devices[] = {\n"
            '    { INFO("x", 0xef4020, 0, 64 << 10, 1024, ER_4K), .sfdp_read = m25p80_sfdp_x },\n'
            "};\n",
        },
    )
    with pytest.raises(ValueError, match=r"no m25p80_sfdp_x\(\) in"):
        qemu.extract(tmp_path)
    write(
        tmp_path,
        {
            qemu.M25P80: table
            + 'static const FlashPartInfo known_devices[] = {\n    { .part_name = "x" },\n};\n'
        },
    )
    with pytest.raises(ValueError, match="not INFO/INFO6/INFO_STACKED"):
        qemu.extract(tmp_path)
