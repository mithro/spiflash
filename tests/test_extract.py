"""Each extractor, on a miniature tree in its upstream's format.

The snippets, in tests/fixtures/<upstream>/<path>, are cut down from the real
files at the commits pinned in tools/sources.toml, keeping the shapes that
needed handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from spiflash_extract import (
    dts,
    flashrom,
    linux,
    openfpgaloader,
    openocd,
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


# --- Zephyr ------------------------------------------------------------------

ZEPHYR = FIXTURES / "zephyr"


def test_zephyr() -> None:
    r = by_name(zephyr.extract(ZEPHYR))
    assert set(r) == {
        "mx25r6435f",
        "is25wp064",
        "w25q128jw",
        "mx25lm51245",
        "is25lp128",
        "gd25lq32d",
        "mx25l3233f",
    }
    # nordic,qspi-nor with the chip's own SFDP table: size in bits, page and
    # erase types from the table, readoc/writeoc as the modes used.
    m = r["mx25r6435f"]
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
    assert r["is25wp064"]["size"] == 8 << 20
    # The overlay leaves the compatible to the board's .dts, and deletes and
    # replaces the node inside.
    w = r["w25q128jw"]
    assert (w["file"], w["line"]) == (
        "boards/nxp/mimxrt1060_evk/mimxrt1060_evk_mimxrt1062_qspi_C.overlay",
        53,
    )
    assert (w["id"], w["size"]) == ("ef6018", 16 << 20)
    # The name from the comment on the jedec-id line; the maker from a
    # one-maker binding; the MSPI mode.
    x = r["mx25lm51245"]
    assert (x["vendor"], x["size"], x["features"]) == ("mxicy", 64 << 20, ["octal_read"])
    assert "mspi-io-mode=MSPI_IO_MODE_OCTAL" in x["flags"]
    # A descriptive compatible names the part and its maker (here with an id
    # that is not ISSI's, kept as the board has it).
    i = r["is25lp128"]
    assert (i["vendor"], i["id"], i["size"]) == ("issi", "966018", 16 << 20)
    # bflb: no size; use-sfdp says the part answers SFDP.
    g = r["gd25lq32d"]
    assert (g["size"], g["features"]) == (None, ["erase_4k", "sfdp"])
    assert "erase-block-size=4096" in g["flags"]
    assert set(ops(g)) == {"RDID", "RDSFDP"}
    # Two boards with the same node are one record, which names the other.
    # Its table is JESD216's first: nine DWORDs, no page size.
    f = r["mx25l3233f"]
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
    assert (r["name"], r["id"], r["ext_id"], r["page_size"]) == ("w25q256jv", "ef4019", "00", 256)
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
        "w25n01gv",
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
