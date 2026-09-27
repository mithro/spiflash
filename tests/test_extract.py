"""Each extractor, on a miniature tree in its upstream's format.

The snippets are cut down from the real files at the commits pinned in
tools/sources.toml, keeping the shapes that needed handling."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from spiflash_extract import flashrom, linux, openfpgaloader, openocd, record, uboot

if TYPE_CHECKING:
    from pathlib import Path


def write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def by_name(recs: list[record.Record]) -> dict[str, record.Record]:
    return {r["name"]: r for r in recs}


LINUX_CORE_H = """
#define SPI_NOR_DEFAULT_SECTOR_SIZE SZ_64K
struct flash_info {
	u16 flags;
#define SPI_NOR_HAS_LOCK		BIT(0)
#define SPI_NOR_HAS_TB			BIT(1)
#define SPI_NOR_NO_ERASE		BIT(6)
	u8 no_sfdp_flags;
#define SPI_NOR_SKIP_SFDP		BIT(0)
#define SECT_4K				BIT(1)
#define SPI_NOR_DUAL_READ		BIT(3)
#define SPI_NOR_QUAD_READ		BIT(4)
	u8 fixup_flags;
#define SPI_NOR_4B_OPCODES		BIT(0)
};
"""

LINUX_WINBOND = """
static const struct flash_info winbond_nor_parts[] = {
	{
		.id = SNOR_ID(0xef, 0x40, 0x18),
		/* Flavors w/ and w/o SFDP. */
		.name = "w25q128",
		.size = SZ_16M,
		.flags = SPI_NOR_HAS_LOCK | SPI_NOR_HAS_TB,
		.no_sfdp_flags = SECT_4K | SPI_NOR_DUAL_READ | SPI_NOR_QUAD_READ,
		.fixups = &w25q128_fixups,
	}, {
		/* W25Q01JV */
		.id = SNOR_ID(0xef, 0x40, 0x21),
		.fixups = &winbond_nor_multi_die_fixups,
	}, {
		.id = SNOR_ID(0xef, 0x40, 0x20),
		.name = "w25q512jvq",
		.size = SZ_64M,
		.fixup_flags = SPI_NOR_4B_OPCODES,
		.otp = SNOR_OTP(256, 3, 0x1000, 0x1000),
	}, {
		.id = SNOR_ID(0xef, 0x60),
	},
};

const struct spi_nor_manufacturer spi_nor_winbond = {
	.name = "winbond",
	.parts = winbond_nor_parts,
	.nparts = ARRAY_SIZE(winbond_nor_parts),
};
"""

LINUX_SPANSION = """
#define USE_CLSR	BIT(0)
static const struct flash_info spansion_nor_parts[] = {
	{
		.id = SNOR_ID(0x01, 0x20, 0x18, 0x4d, 0x01, 0x80),
		.name = "s25fl128s1",
		.size = SZ_16M,
		.no_sfdp_flags = SPI_NOR_DUAL_READ | SPI_NOR_QUAD_READ,
		.mfr_flags = USE_CLSR,
	}, {
		.name = "everspin-nonjedec",
		.size = SZ_128K,
		.sector_size = SZ_128K,
		.flags = SPI_NOR_NO_ERASE,
	},
};
const struct spi_nor_manufacturer spi_nor_spansion = {
	.name = "spansion",
	.parts = spansion_nor_parts,
};
"""

LINUX_NAND = """
#define SPINAND_MFR_WINBOND		0xEF
static const struct spinand_info winbond_spinand_table[] = {
	SPINAND_INFO("W25N01GV", /* 3.3V */
		     SPINAND_ID(SPINAND_READID_METHOD_OPCODE_DUMMY, 0xaa, 0x21),
		     NAND_MEMORG(1, 2048, 64, 64, 1024, 20, 1, 1, 1),
		     NAND_ECCREQ(1, 512),
		     SPINAND_INFO_OP_VARIANTS(&read_cache_variants,
					      &write_cache_variants,
					      &update_cache_variants),
		     SPINAND_HAS_QE_BIT,
		     SPINAND_ECCINFO(&w25m02gv_ooblayout, NULL)),
};
const struct spinand_manufacturer winbond_spinand_manufacturer = {
	.id = SPINAND_MFR_WINBOND,
	.name = "Winbond",
	.chips = winbond_spinand_table,
};
"""


@pytest.fixture
def linux_tree(tmp_path: Path) -> Path:
    return write(
        tmp_path,
        {
            "drivers/mtd/spi-nor/core.h": LINUX_CORE_H,
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
    assert w["id"] == "ef4018" and w["ext_id"] is None
    assert w["vendor"] == "winbond"
    assert w["size"] == 16 << 20 and w["page_size"] == 256 and w["sector_size"] == 65536
    assert w["features"] == ["dual_read", "erase_4k", "erase_64k", "lock", "quad_read"]
    assert "SPI_NOR_HAS_TB" in w["flags"]
    assert w["notes"] == ["Flavors w/ and w/o SFDP."]
    assert w["file"] == "drivers/mtd/spi-nor/winbond.c"

    # No .name: the comment names it; no .size: SFDP gives it.
    j = r["W25Q01JV"]
    assert j["id"] == "ef4021" and j["size"] is None and "sfdp" in j["features"]

    big = r["w25q512jvq"]
    assert {"4byte_addr", "4byte_opcodes", "otp"} <= set(big["features"])

    # Neither a name nor a comment: named by vendor and id.
    assert r["winbond-ef60"]["notes"] == ["Linux gives this entry no name"]

    s = r["s25fl128s1"]
    assert s["id"] == "012018" and s["ext_id"] == "4d0180" and "USE_CLSR" in s["flags"]

    n = r["everspin-nonjedec"]
    assert n["id"] is None and n["id_method"] is None
    assert "no_erase" in n["features"] and "erase_64k" not in n["features"]


def test_linux_nand(linux_tree: Path) -> None:
    (n,) = linux.extract_nand(linux_tree)
    assert n["type"] == "nand" and n["name"] == "W25N01GV" and n["vendor"] == "Winbond"
    assert n["id"] == "efaa21" and n["id_method"] == "rdid_opcode_dummy"
    assert n["size"] == 2048 * 64 * 1024 and n["page_size"] == 2048
    assert n["sector_size"] == 2048 * 64
    assert n["features"] == ["quad_read"] and n["flags"] == ["SPINAND_HAS_QE_BIT"]
    assert n["notes"][0] == "3.3V"


def test_linux_extract_is_both(linux_tree: Path) -> None:
    assert {r["type"] for r in linux.extract(linux_tree)} == {"nor", "nand"}


def test_linux_bad_id(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "drivers/mtd/spi-nor/core.h": LINUX_CORE_H,
            "drivers/mtd/spi-nor/x.c": "static const struct flash_info x_parts[] = "
            "{ { .id = SOMETHING_ELSE(1), .name = \"x\" } };",
        },
    )
    with pytest.raises(ValueError, match=r"unexpected \.id"):
        linux.extract_nor(tmp_path)


def test_split_id() -> None:
    assert linux.split_id([0xEF, 0x40, 0x18]) == ("ef4018", None)
    assert linux.split_id([0x7F, 0x7F, 0x9D, 0x60, 0x19, 0x01]) == ("7f7f9d6019", "01")


UBOOT_FLAGS = """
#define SECT_4K			BIT(0)	/* SPINOR_OP_BE_4K works uniformly */
#define SPI_NOR_NO_ERASE	BIT(1)
#define SPI_NOR_NO_FR		BIT(3)
#define SPI_NOR_DUAL_READ	BIT(5)
#define SPI_NOR_QUAD_READ	BIT(6)
#define SPI_NOR_HAS_LOCK	BIT(8)
#define SPI_NOR_4B_OPCODES	BIT(11)
"""

UBOOT_IDS = """
const struct flash_info spi_nor_ids[] = {
#ifdef CONFIG_SPI_FLASH_WINBOND		/* WINBOND */
	{ INFO("w25q128", 0xef4018, 0, 64 * 1024, 256, SECT_4K | SPI_NOR_DUAL_READ) },
	{
		INFO("w25q512", 0xef4020, 0, 64 * 1024, 1024,
		     SPI_NOR_QUAD_READ | SPI_NOR_4B_OPCODES)
	},
#endif
#ifdef CONFIG_SPI_FLASH_SPANSION
	{ INFO6("s25fl128s", 0x012018, 0x4d0180, 64 * 1024, 256, SPI_NOR_NO_FR) },
	{ INFO("s25sl12800", 0x012018, 0x0300, 256 * 1024, 64, 0) },
#endif
#ifdef CONFIG_SPI_FRAM_FUJITSU
	/* Fujitsu MB85RS256TY */
	{
		INFO_NAME("mb85rs256ty")
		.id = {0x04, 0x7f, 0x25, 0x00, 0x00},
		.id_len = 3,
		.sector_size = 32 * 1024,
		.n_sectors = 1,
		.page_size = 32 * 1024, /* Whole chip can be written at once */
		.flags = SPI_NOR_NO_ERASE,
	},
#endif
	{ },
};
"""


def test_uboot(tmp_path: Path) -> None:
    write(tmp_path, {uboot.IDS: UBOOT_IDS, uboot.FLAGS_H: UBOOT_FLAGS})
    r = by_name(uboot.extract(tmp_path))
    assert set(r) == {"w25q128", "w25q512", "s25fl128s", "s25sl12800", "mb85rs256ty"}
    w = r["w25q128"]
    assert w["vendor"] == "winbond" and w["id"] == "ef4018" and w["size"] == 16 << 20
    assert w["features"] == ["dual_read", "erase_4k", "erase_64k", "fast_read"]
    assert {"4byte_addr", "4byte_opcodes", "quad_read"} <= set(r["w25q512"]["features"])
    s = r["s25fl128s"]
    assert s["vendor"] == "spansion" and s["ext_id"] == "4d0180"
    assert "fast_read" not in s["features"]
    assert r["s25sl12800"]["ext_id"] == "0300" and r["s25sl12800"]["sector_size"] == 256 * 1024
    f = r["mb85rs256ty"]
    assert f["vendor"] == "fujitsu" and f["id"] == "047f25" and f["size"] == 32 * 1024
    assert f["page_size"] == 32 * 1024 and "no_erase" in f["features"]
    assert f["notes"] == ["Whole chip can be written at once"]


def test_uboot_no_table(tmp_path: Path) -> None:
    write(tmp_path, {uboot.IDS: "int x;", uboot.FLAGS_H: ""})
    with pytest.raises(ValueError, match="no spi_nor_ids"):
        uboot.extract(tmp_path)


FLASHROM_H = """
#define GENERIC_MANUF_ID	0xFFFF	/* Check if there is a vendor ID */
#define GENERIC_DEVICE_ID	0xFFFF	/* Only match the vendor ID */
#define EON_ID			0x7F1C	/* EON Silicon Devices */
#define EON_ID_NOPREFIX		0x1C	/* EON, missing 0x7F prefix */
#define EON_EN25QH128		0x7018
#define SPANSION_ID		0x01	/* Spansion, same ID as AMD */
#define SPANSION_S25FL128S_UL	0x20180080  /* Uniform Large (128kB) sectors */
#define ST_ID			0x20
#define ST_M25P05_RES		0x05
#define AMD_ID			0x01
#define AMD_AM29F010		0x20
"""

FLASH_H = """
#define FEATURE_WRSR_EWSR	(1 << 6)
#define FEATURE_WRSR_WREN	(1 << 7)
#define FEATURE_WRSR_EITHER	(FEATURE_WRSR_EWSR | FEATURE_WRSR_WREN)
#define FEATURE_OTP		(1 << 8)
#define FEATURE_FAST_READ	(1 << 9)
#define FEATURE_4BA_READ	(1 << 15)
#define FEATURE_FAST_READ_DOUT	(1 << 24)
#define FEATURE_FAST_READ_QOUT	(1 << 26)
#define FEATURE_QPI_38_FF	(1 << 30)
#define FEATURE_DIO		(FEATURE_FAST_READ | FEATURE_FAST_READ_DOUT)
#define FEATURE_QIO		(FEATURE_DIO | FEATURE_FAST_READ_QOUT)
#define FEATURE_QPI_38		(FEATURE_QIO | FEATURE_QPI_38_FF)
"""

FLASHROM_EON = """
	{
		.vendor		= "Eon",
		.name		= "EN25QH128",
		.bustype	= BUS_SPI,
		.manufacture_id	= EON_ID_NOPREFIX,
		.model_id	= EON_EN25QH128,
		.total_size	= 16384,
		.page_size	= 256,
		/* supports SFDP */
		.feature_bits	= FEATURE_WRSR_WREN | FEATURE_OTP | FEATURE_QPI_38 & ~FEATURE_FAST_READ_QOUT,
		.tested		= TEST_OK_PREW,
		.probe		= PROBE_SPI_RDID,
		.block_erasers	=
		{
			{
				.eraseblocks = { {4 * 1024, 4096} },
				.block_erase = SPI_BLOCK_ERASE_20,
			}, {
				.eraseblocks = { {64 * 1024, 256} },
				.block_erase = SPI_BLOCK_ERASE_D8,
			}, {
				.eraseblocks = { {16 * 1024 * 1024, 1} },
				.block_erase = SPI_BLOCK_ERASE_C7,
			}, {
				.eraseblocks = { {4 * 1024, 2}, {8 * 1024, 1} },
				.block_erase = SPI_BLOCK_ERASE_EMULATION,
			}, {
				.eraseblocks = { {0, 0} },
				.block_erase = NULL,
			}
		},
		.voltage	= {2700, 3600},
		.reg_bits	= { .bp = {{STATUS1, 2, RW}} },
	},
	{
		.vendor		= "Spansion",
		.name		= "S25FL128S_UL Uniform 128 kB Sectors",
		.bustype	= BUS_SPI,
		.manufacture_id	= SPANSION_ID,
		.model_id	= SPANSION_S25FL128S_UL,
		.total_size	= 16384,
		.page_size	= 256,
		.tested		= { .probe = NA, .read = OK },
		.probe		= PROBE_SPI_BIG_SPANSION,
	},
	{
		.vendor		= "ST",
		.name		= "M25P05",
		.bustype	= BUS_SPI,
		.manufacture_id	= ST_ID,
		.model_id	= ST_M25P05_RES,
		.total_size	= 64,
		.probe		= PROBE_SPI_RES1,
	},
	{
		.vendor		= "ST",
		.name		= "M95320",
		.bustype	= BUS_SPI,
		.manufacture_id	= ST_ID,
		.model_id	= 0,	/* No RDID */
		.total_size	= 4,
	},
	{
		.vendor		= "Generic",
		.name		= "unknown SPI chip (RDID)",
		.bustype	= BUS_SPI,
		.manufacture_id	= GENERIC_MANUF_ID,
		.model_id	= GENERIC_DEVICE_ID,
		.total_size	= 0,
		.probe		= PROBE_SPI_RDID,
	},
	{
		.vendor		= "ENE",
		.name		= "KB9012 (EDI)",
		.bustype	= BUS_SPI,
		.total_size	= 128,
		.probe		= PROBE_EDI_KB9012,
	},
	{
		.vendor		= "AMD",
		.name		= "Am29F010",
		.bustype	= BUS_PARALLEL,
		.manufacture_id	= AMD_ID,
		.model_id	= AMD_AM29F010,
		.total_size	= 128,
	},
"""

FLASHPROG_C = """
const struct flashchip flashchips[] = {
	{
		.vendor		= "Eon",
		.name		= "EN25QH128",
		.bustype	= BUS_SPI,
		.id.type	= ID_SPI_RDID,
		.id.manufacture	= EON_ID_NOPREFIX,
		.id.model	= EON_EN25QH128,
		.total_size	= 16384,
		.page_size	= 256,
		.feature_bits	= FEATURE_WRSR_EITHER | FEATURE_4BA_READ,
		.block_erasers	=
		{
			{
				.eraseblocks = { {64 * 1024, 256} },
				.block_erase = spi_block_erase_d8,
			},
		},
	},
	{0}
};
"""


def test_flashrom_per_vendor(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            flashrom.HEADER: FLASHROM_H,
            flashrom.FLASH_H: FLASH_H,
            "flashchips/eon.c": FLASHROM_EON,
            "flashchips.c": '#include "flashchips/eon.c"',
        },
    )
    r = by_name(flashrom.extract(tmp_path, "flashrom"))
    assert set(r) == {"EN25QH128", "S25FL128S_UL Uniform 128 kB Sectors", "M25P05", "M95320"}
    e = r["EN25QH128"]
    assert e["source"] == "flashrom" and e["file"] == "flashchips/eon.c"
    assert e["id"] == "1c7018" and e["id_method"] == "rdid"
    assert e["size"] == 16 << 20 and e["page_size"] == 256 and e["sector_size"] == 65536
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
    assert e["voltage"] == [2700, 3600] and e["tested"] == "TEST_OK_PREW"
    assert "EON_ID_NOPREFIX: EON, missing 0x7F prefix" in e["notes"]

    s = r["S25FL128S_UL Uniform 128 kB Sectors"]
    assert s["id"] == "012018" and s["ext_id"] == "0080"
    assert s["tested"] == "{ .probe = NA, .read = OK }"
    assert r["M25P05"]["id_method"] == "res1" and r["M25P05"]["id"] == "05"
    assert r["M95320"]["id"] is None and r["M95320"]["id_method"] is None


def test_flashprog_single_file(tmp_path: Path) -> None:
    write(
        tmp_path,
        {flashrom.HEADER: FLASHROM_H, flashrom.FLASH_H: FLASH_H, "flashchips.c": FLASHPROG_C},
    )
    (e,) = flashrom.extract(tmp_path, "flashprog")
    assert e["source"] == "flashprog" and e["id"] == "1c7018"
    assert e["features"] == ["4byte_addr", "4byte_opcodes", "erase_64k"]
    assert e["flags"] == ["FEATURE_4BA_READ", "FEATURE_WRSR_EWSR", "FEATURE_WRSR_WREN"]


def test_flashrom_errors(tmp_path: Path) -> None:
    write(tmp_path, {flashrom.HEADER: FLASHROM_H, flashrom.FLASH_H: FLASH_H})
    write(tmp_path, {"flashchips.c": "int nothing;"})
    with pytest.raises(ValueError, match=r"no flashchips\[\]"):
        flashrom.extract(tmp_path, "flashprog")
    bad = FLASHPROG_C.replace("ID_SPI_RDID", "ID_SOMETHING_NEW")
    write(tmp_path, {"flashchips.c": bad})
    with pytest.raises(ValueError, match="unknown probe 'SOMETHING_NEW'"):
        flashrom.extract(tmp_path, "flashprog")


OPENOCD_SPI_C = """
const struct flash_device flash_devices[] = {
	/*        name                  read qread  page  erase chip  device_id   page   erase   flash
	 */
	FLASH_ID("win w25q128fv/jv",    0x03, 0xeb, 0x02, 0xd8, 0xc7, 0x001840ef, 0x100, 0x10000, 0x1000000),
	FLASH_ID("issi is25wp512m",     0x13, 0xec, 0x12, 0xdc, 0xc7, 0x001a709d, 0x100, 0x10000, 0x4000000),
	FLASH_ID("gd gd25q512",         0x03, 0x00, 0x02, 0x20, 0x00, 0x001040c8, 0x100, 0x1000,  0x10000),
	FRAM_ID("cyp fm25v02",          0x03, 0,    0x02, 0x060022c2, 0x8000), /* exists ? */
	FLASH_ID(NULL,                  0,    0,    0,    0,    0,    0,          0,     0,       0)
};
"""

JEP106_INC = """
[0][0x01 - 1] = "AMD",
[0][0x6f - 1] = "NEXCOM",
[1][0x1c - 1] = "Eon Silicon Devices",
"""


def test_openocd(tmp_path: Path) -> None:
    write(tmp_path, {openocd.SPI_C: OPENOCD_SPI_C, openocd.JEP106: JEP106_INC})
    r = by_name(openocd.extract(tmp_path))
    assert set(r) == {"w25q128fv/jv", "is25wp512m", "gd25q512", "fm25v02"}
    w = r["w25q128fv/jv"]
    assert w["vendor"] == "win" and w["id"] == "ef4018"
    assert w["opcodes"] == {"read": 3, "qread": 0xEB, "pp": 2, "erase": 0xD8, "chip_erase": 0xC7}
    assert w["erasers"] == [
        {"opcode": 0xD8, "blocks": [[65536, 256]]},
        {"opcode": 0xC7, "blocks": [[16 << 20, 1]]},
    ]
    assert w["features"] == ["erase_64k", "quad_read"]
    assert "4byte_addr" in r["is25wp512m"]["features"]
    g = r["gd25q512"]
    assert g["features"] == ["erase_4k"] and "chip_erase" not in g["opcodes"]
    f = r["fm25v02"]
    assert f["id"] == "7f7f7f7f7f7fc22200" and f["features"] == ["no_erase"]
    assert f["notes"] == ["exists ?", "FRAM"] and f["page_size"] is None


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


OFL_DB = """
static std::map <uint32_t, flash_t> flash_list = {
	{0x010219, {
		/* https://www.mouser.fr/datasheet/2/196/Infineon_S25FL128SS25FL256S.pdf */
		.manufacturer = "spansion",
		.model = "S25FL256S",
		.nr_sector = 512,
		.sector_erase = true,
		.subsector_erase = false,
		.bp_len = 3,
		.quad_register = CONFR,
		.quad_mask = (1 << 1),
	}},
	{0xef4018, {
		.manufacturer = "Winbond",
		.model = "W25Q128",
		.nr_sector = 256,
		.sector_erase = true,
		.subsector_erase = true,
		.bp_len = 0,
		.quad_register = NONER,
	}},
};
"""


def test_openfpgaloader(tmp_path: Path) -> None:
    write(tmp_path, {openfpgaloader.DB: OFL_DB})
    r = by_name(openfpgaloader.extract(tmp_path))
    s = r["S25FL256S"]
    assert s["id"] == "010219" and s["vendor"] == "spansion" and s["size"] == 32 << 20
    assert s["features"] == ["4byte_addr", "erase_64k", "lock", "quad_read"]
    assert "quad_register=CONFR" in s["flags"]
    assert s["notes"][0].startswith("https://www.mouser.fr/")
    assert r["W25Q128"]["features"] == ["erase_4k", "erase_64k"]


def test_openfpgaloader_no_map(tmp_path: Path) -> None:
    write(tmp_path, {openfpgaloader.DB: "int x;"})
    with pytest.raises(ValueError, match="no flash_list"):
        openfpgaloader.extract(tmp_path)


def test_record_make_validates() -> None:
    with pytest.raises(KeyError, match="unknown record fields"):
        record.make("x", "f", 1, "n", colour="red")
    with pytest.raises(ValueError, match="unknown features"):
        record.make("x", "f", 1, "n", features=["telepathy"])
    r = record.make("x", "f", 1, "n", features=["otp", "lock", "otp"])
    assert list(r) == list(record.KEYS) and r["features"] == ["lock", "otp"]
