"""Each extractor, on a miniature tree in its upstream's format.

The snippets, in tests/fixtures/<upstream>/<path>, are cut down from the real
files at the commits pinned in tools/sources.toml, keeping the shapes that
needed handling."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from spiflash.enums import Feature
from spiflash.model import EraseBlock, Eraser, Record
from spiflash.opcodes import OPERATIONS
from spiflash_extract import (
    cparse,
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
    rockchip,
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
    """A record's opcodes as it loads, {op: (opcode, via)}: those it stores
    and those it derives."""
    return {u.op: (u.opcode, u.via) for u in Record.from_json(rec).opcodes}


def features(rec: record.Record) -> list[str]:
    """A record's capabilities as it loads: those it claims and those its
    other fields imply."""
    return sorted(Record.from_json(rec).features)


def erasers(rec: record.Record) -> list[dict[str, object]]:
    """A record's erasers as it loads: those it stores, and those derived
    (the die and chip erase layouts, its SFDP tables')."""
    return [e.to_json() for e in Record.from_json(rec).erasers]


def sector(rec: record.Record) -> int | None:
    """A record's sector size as it loads, from its erasers."""
    return Record.from_json(rec).sector_size


def stored_ops(rec: record.Record) -> dict[str, str]:
    """The operations a record stores, {op: via}."""
    return {o["op"]: o["via"] for o in rec["opcodes"]}


def assumed(rec: record.Record) -> set[str]:
    """The operations a record stores as its driver's defaults."""
    return {o["op"] for o in rec["opcodes"] if o.get("assumed")}


LINUX_CORE_H = fixture("linux/drivers/mtd/spi-nor/core.h")

SPINOR_H = fixture("linux/include/linux/mtd/spi-nor.h")

LINUX_WINBOND = fixture("linux/drivers/mtd/spi-nor/winbond.c")

LINUX_SPANSION = fixture("linux/drivers/mtd/spi-nor/spansion.c")

LINUX_NAND = fixture("linux/drivers/mtd/nand/spi/winbond.c")

LINUX_ESMT = fixture("linux/drivers/mtd/nand/spi/esmt.c")

LINUX_MICRON = fixture("linux/drivers/mtd/nand/spi/micron.c")

SPINAND_H = fixture("linux/include/linux/mtd/spinand.h")


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
            "drivers/mtd/spi-nor/micron-st.c": fixture("linux/drivers/mtd/spi-nor/micron-st.c"),
            "drivers/mtd/nand/spi/winbond.c": LINUX_NAND,
            "drivers/mtd/nand/spi/micron.c": LINUX_MICRON,
            "drivers/mtd/nand/spi/core.c": "",
            linux.SPINAND_H: SPINAND_H,
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
    # No .page_size: the driver's 256 B default is not the part's.
    assert w["page_size"] is None
    # SECT_4K is an eraser. No .sector_size: the driver's default 64 KiB
    # sector is an eraser too, but an assumed one, which gives no sector
    # size and no erase_64k.
    assert w["erasers"] == [
        {"opcode": 0x20, "blocks": [[4096, 4096]]},
        {"opcode": 0xD8, "blocks": [[65536, 256]], "assumed": True},
    ]
    assert sector(w) is None
    assert features(w) == ["dual_read", "erase_4k", "lock", "quad_read"]
    assert w["features"] == []  # all implied: lock by its protection bits
    # SPI_NOR_HAS_LOCK: BP0-2 and SRWD (swp.c); SPI_NOR_HAS_TB without
    # SPI_NOR_TB_SR_BIT6: TB at bit 5.
    assert w["protection"] == {
        "bp0": {"register": "sr1", "bit": 2},
        "bp1": {"register": "sr1", "bit": 3},
        "bp2": {"register": "sr1", "bit": 4},
        "tb": {"register": "sr1", "bit": 5},
        "srp": {"register": "sr1", "bit": 7},
    }
    # No quad enable bit: winbond.c sets none, and the core's default is
    # every part's.
    assert w["quad_enable"] is None
    # core.c's defaults, plus what the no_sfdp_flags set up.
    assert ops(w) == {
        "RDID": (0x9F, "id read (rdid)"),
        "READ_1_1_1": (0x03, "default (spi_nor_init_default_params)"),
        "READ_1_1_1_FAST": (0x0B, "default (spi_nor_init_default_params), m25p,fast-read"),
        "READ_1_1_2": (0x3B, "SPI_NOR_DUAL_READ"),
        "READ_1_1_4": (0x6B, "SPI_NOR_QUAD_READ"),
        "PP_1_1_1": (0x02, "default (spi_nor_init_default_params)"),
        "BE_4K": (0x20, "eraser: 4096 x 4096"),
        "SE": (0xD8, "eraser: 256 x 65536, a driver default"),
        "CHIP_ERASE": (0xC7, "default (spi_nor_erase)"),
    }
    se = next(u for u in Record.from_json(w).opcodes if u.op == "SE")
    assert (se.implied, se.assumed) == (True, True)
    # An entry's own .sector_size is its claim.
    s0 = r["S25FL128S0"]
    assert s0["erasers"] == [{"opcode": 0xD8, "blocks": [[256 << 10, 64]]}]
    assert sector(s0) == 256 << 10
    # The driver's defaults imply no capability: no fast_read.
    assert assumed(w) == {"READ_1_1_1", "READ_1_1_1_FAST", "PP_1_1_1", "CHIP_ERASE"}
    # The id read is derived, not stored; then read, ...
    assert [o["op"] for o in w["opcodes"]][:2] == ["READ_1_1_1", "READ_1_1_1_FAST"]
    # A token a field holds is in via, not in flags.
    assert w["via"] == {
        "erasers:0x20": "SECT_4K",
        "protection": "SPI_NOR_HAS_LOCK",
        "protection.tb": "SPI_NOR_HAS_TB",
    }
    assert "SPI_NOR_HAS_LOCK" not in w["flags"]
    assert "SPI_NOR_HAS_TB" not in w["flags"]
    assert "SPI_NOR_QUAD_READ" not in w["flags"]  # READ_1_1_4 holds it
    assert w["notes"] == ["Flavors w/ and w/o SFDP."]
    assert w["file"] == "drivers/mtd/spi-nor/winbond.c"

    # No .name: the comment names it; no .size: SFDP gives it.
    j = r["W25Q01JV"]
    assert j["id"] == "ef4021"
    assert j["size"] is None
    assert "sfdp" in features(j)  # its RDSFDP implies it
    # Its erasers come from SFDP at run time: none, so no erase_64k.
    assert (j["erasers"], sector(j)) == (None, None)
    assert "erase_64k" not in features(j)

    assert "RDSFDP" in ops(j)
    assert "SE" not in ops(j)
    big = r["W25Q512JVQ"]
    assert {"4byte_addr", "4byte_opcodes", "otp"} <= set(features(big))
    # SNOR_OTP(len, n_regions, base, offset): three 256-byte regions, read,
    # programmed and erased with winbond_nor_otp_ops' security-register
    # commands; the area implies otp.
    assert big["otp"] == {"size": 768, "regions": 3}
    assert big["via"]["otp"] == "SNOR_OTP(256, 3, 0x1000, 0x1000)"
    assert "otp" not in big["features"]
    assert ops(big)["RSECR"] == (0x48, "SNOR_OTP(256, 3, 0x1000, 0x1000) (winbond_nor_otp_ops)")
    assert {"PSECR", "ESECR"} <= set(ops(big))
    assert ops(big)["READ_1_1_1_4B"] == (0x13, "SPI_NOR_4B_OPCODES")
    assert ops(big)["SE_4B"] == (0xDC, "SPI_NOR_4B_OPCODES")
    assert "SE_4B" in assumed(big)  # the 4-byte form of the default sector erase
    assert ops(big)["PP_1_1_1_4B"] == (0x12, "SPI_NOR_4B_OPCODES")

    # Neither a name nor a comment: named by vendor and id.
    assert r["WINBOND-EF60"]["notes"] == ["Linux gives this entry no name"]

    s = r["S25FL128S1"]
    assert s["id"] == "012018"
    assert s["ext_id"] == "4d0180"
    # spansion_nor_clear_sr() clears its error bits with CLSR.
    assert ops(s)["CLSR"] == (0x30, "USE_CLSR")
    assert "USE_CLSR" not in s["flags"]

    n = r["EVERSPIN-NONJEDEC"]
    assert n["id"] is None
    assert n["id_method"] is None
    assert "no_erase" in n["features"]
    # Never an eraser for a part that needs no erase.
    assert (n["erasers"], sector(n)) == (None, None)
    assert "erase_64k" not in features(n)


def test_linux_nor_dies(linux_tree: Path) -> None:
    r = by_name(linux.extract_nor(linux_tree))
    # micron-st.c's two-die late_init: n_dice 2 and die erase 0xc4.
    mt = r["MT25QU01G"]
    assert (mt["dies"], mt["via"]["dies"]) == (2, "mt25q01_fixups: n_dice = 2")
    die = {o["op"]: o["via"] for o in mt["opcodes"]}["DIE_ERASE"]
    assert die == "mt25q01_fixups: die_erase_opcode = SPINOR_OP_MT_DIE_ERASE"
    # Winbond's n_dice is size / SZ_64M, the SFDP size: 2 for the 1 Gbit
    # W25Q01JV. It has no die erase, but selects each die (0xc2) to poll it.
    w = r["W25Q01JV"]
    assert (w["dies"], w["via"]["dies"]) == (
        2,
        "winbond_nor_multi_die_fixups: n_dice = nor->params->size / SZ_64M",
    )
    ops = {o["op"]: o["via"] for o in w["opcodes"]}
    assert ops["DIE_SELECT"] == "winbond_nor_multi_die_fixups: ready = winbond_nor_multi_die_ready"
    assert "DIE_ERASE" not in ops
    assert r["W25Q128"]["dies"] is None


def test_linux_nor_dies_unknown(linux_tree: Path) -> None:
    micron = linux_tree / "drivers/mtd/spi-nor/micron-st.c"
    micron.write_text(micron.read_text().replace("n_dice = 2", "n_dice = nor->x"))
    with pytest.raises(ValueError, match="sets n_dice to"):
        linux.extract_nor(linux_tree)


def test_linux_nand(linux_tree: Path) -> None:
    r = by_name(linux.extract_nand(linux_tree))
    n = r["W25N01GV"]
    assert n["type"] == "nand"
    assert n["vendor"] == "Winbond"
    assert n["id"] == "efaa21"
    assert n["id_method"] == "rdid_opcode_dummy"
    assert n["size"] == 2048 * 64 * 1024
    assert n["page_size"] == 2048
    assert n["erasers"] == [{"opcode": 0xD8, "blocks": [[2048 * 64, 1024]]}]
    assert sector(n) == 2048 * 64
    # NAND_MEMORG(1, 2048, 64, 64, 1024, 20, 1, 1, 1), NAND_ECCREQ(1, 512).
    assert (n["oob_size"], n["planes"], n["dies"], n["max_bad_blocks"]) == (64, 1, 1, 20)
    assert n["ecc"] == {"strength_bits": 1, "step_bytes": 512}
    assert n["die_select_bit"] is None
    # No SPINAND_HAS_QE_BIT: spinand_init_quad_enable() clears bit 0 of
    # the configuration register on every such part, a driver default that
    # says nothing of the part's bit.
    assert n["quad_enable"] is None
    assert n["features"] == []
    # Its op variants, the core's defaults, its read-id and block erase:
    # SPI NAND's own operations, so no erase_* and no SE.
    assert features(n) == ["dual_read", "fast_read", "quad_pp", "quad_read"]
    assert {op: opcode for op, (opcode, _) in ops(n).items()} == {
        "NAND_RDID_DUMMY": 0x9F,
        "NAND_PAGE_READ": 0x13,
        "NAND_READ_CACHE_1_1_1": 0x03,
        "NAND_READ_CACHE_1_1_1_FAST": 0x0B,
        "NAND_READ_CACHE_1_1_2": 0x3B,
        "NAND_READ_CACHE_1_2_2": 0xBB,
        "NAND_READ_CACHE_1_1_4": 0x6B,
        "NAND_READ_CACHE_1_4_4": 0xEB,
        "NAND_PROGRAM_LOAD_1_1_1": 0x02,
        "NAND_PROGRAM_LOAD_1_1_4": 0x32,
        "NAND_RANDOM_LOAD_1_1_1": 0x84,
        "NAND_RANDOM_LOAD_1_1_4": 0x34,
        "NAND_PROGRAM_EXECUTE": 0x10,
        "NAND_BLOCK_ERASE": 0xD8,
        "NAND_GET_FEATURE": 0x0F,
        "NAND_SET_FEATURE": 0x1F,
    }
    # Dummy clocks are the dummy bytes x 8 over the dummy phase's lines:
    # 1S_4S_4S(0, 2, ...) is 2 bytes on 4 lines, 4 clocks.
    clocks = {o["op"]: o.get("dummy_clocks") for o in n["opcodes"]}
    assert clocks["NAND_READ_CACHE_1_4_4"] == 4
    assert clocks["NAND_READ_CACHE_1_2_2"] == 4
    assert clocks["NAND_READ_CACHE_1_1_1"] == 8
    assert clocks["NAND_PROGRAM_LOAD_1_1_4"] is None  # no dummy phase to give
    assert assumed(n) == {
        "NAND_PAGE_READ",
        "NAND_PROGRAM_EXECUTE",
        "NAND_GET_FEATURE",
        "NAND_SET_FEATURE",
    }
    assert n["via"] == {}
    assert n["flags"] == []
    assert n["notes"] == ["3.3V"]  # no "1 bit(s) per cell, 64 B OOB per page"
    # The double transfer rate variants have no operation; of an
    # operation's several variants, the most dummy clocks (the one with no
    # clock limit: 1S_4S_4S(0, 4, ...), not (0, 2, ..., 104 MHz)).
    hs = r["W25N01JW"]
    # SPINAND_HAS_QE_BIT: bit 0 of the configuration register (0xb0).
    assert hs["quad_enable"] == {"register": "nand-b0", "bit": 0}
    assert hs["via"] == {"quad_enable": "SPINAND_HAS_QE_BIT"}
    clocks = {o["op"]: o.get("dummy_clocks") for o in hs["opcodes"]}
    assert clocks["NAND_READ_CACHE_1_4_4"] == 8
    assert clocks["NAND_READ_CACHE_1_2_2"] == 8
    assert not any("D" in OPERATIONS[o["op"]].protocol for o in hs["opcodes"])


def test_linux_nand_dies(linux_tree: Path) -> None:
    r = by_name(linux.extract_nand(linux_tree))
    # Two targets: Winbond selects one with 0xc2 and the die (winbond.c),
    w = r["W25M02GV"]
    assert w["dies"] == 2
    assert w["die_select_bit"] is None
    (select,) = [o for o in w["opcodes"] if o["op"] == "NAND_DIE_SELECT"]
    assert select["via"] == "SPINAND_SELECT_TARGET(w25m02gv_select_target)"
    # Micron with bit 6 of feature 0xd0 (micron.c).
    m = r["MT29F4G01ADAGD"]
    assert (m["dies"], m["planes"], m["oob_size"], m["max_bad_blocks"]) == (2, 2, 128, 80)
    assert m["die_select_bit"] == {"register": "nand-d0", "bit": 6}
    assert m["via"]["die_select_bit"] == "SPINAND_SELECT_TARGET(micron_select_target)"
    assert "NAND_DIE_SELECT" not in {o["op"] for o in m["opcodes"]}
    # Without SPINAND_HAS_QE_BIT, nothing of a QE bit.
    assert m["quad_enable"] is None


def test_linux_nand_manufacturer_per_table(tmp_path: Path) -> None:
    # esmt.c has two manufacturers, 0x8c and 0xc8, each with its own table.
    write(tmp_path, {"drivers/mtd/nand/spi/esmt.c": LINUX_ESMT, linux.SPINAND_H: SPINAND_H})
    ids = {r["name"]: r["id"] for r in linux.extract_nand(tmp_path)}
    assert ids == {"F50L1G41LC": "8c2c", "F50L1G41LB": "c8017f7f7f"}


def test_linux_nand_op_shapes() -> None:
    shapes = linux.op_shapes(cparse.strip_comments(SPINAND_H))
    assert shapes["SPINAND_PAGE_READ_FROM_CACHE_1S_1D_1D_OP"] is None  # DTR
    quad = shapes["SPINAND_PAGE_READ_FROM_CACHE_1S_4S_4S_OP"]
    assert quad is not None
    assert (quad.opcode, quad.address_lines, quad.dummy, quad.dummy_lines) == (
        "0xeb",
        4,
        "ndummy",
        4,
    )
    load = shapes["SPINAND_PROG_LOAD_1S_1S_4S_OP"]
    assert load is not None
    assert (load.opcode, load.dummy, load.data) == ("reset ? 0x32 : 0x34", None, "out")


LINUX_REGISTERS = """
static int atmel_nor_global_protection_late_init(struct spi_nor *nor)
{
    nor->params->locking_ops = &atmel_nor_global_protection_ops;
    return 0;
}

static const struct spi_nor_fixups atmel_nor_global_protection_fixups = {
    .late_init = atmel_nor_global_protection_late_init,
};

static void mx25l3255e_late_init_fixups(struct spi_nor *nor)
{
    struct spi_nor_flash_parameter *params = nor->params;
    params->quad_enable = spi_nor_sr1_bit6_quad_enable;
}

static const struct spi_nor_fixups mx25l3255e_fixups = {
    .late_init = mx25l3255e_late_init_fixups,
};

static int gd25q256_post_bfpt(struct spi_nor *nor,
                  const struct sfdp_parameter_header *bfpt_header,
                  const struct sfdp_bfpt *bfpt)
{
    if (bfpt_header->major == SFDP_JESD216_MAJOR &&
        bfpt_header->minor == SFDP_JESD216_MINOR)
        nor->params->quad_enable = spi_nor_sr1_bit6_quad_enable;
    return 0;
}

static const struct spi_nor_fixups gd25q256_fixups = {
    .post_bfpt = gd25q256_post_bfpt,
};

static void macronix_nor_default_init(struct spi_nor *nor)
{
    nor->params->quad_enable = spi_nor_sr1_bit6_quad_enable;
}

static const struct spi_nor_fixups macronix_nor_fixups = {
    .default_init = macronix_nor_default_init,
};

static const struct flash_info x_parts[] = {
    {
        .id = SNOR_ID(0x1f, 0x47, 0x01),
        .name = "at25df321a",
        .size = SZ_4M,
        .flags = SPI_NOR_HAS_LOCK | SPI_NOR_SWP_IS_VOLATILE,
        .fixups = &atmel_nor_global_protection_fixups
    }, {
        .id = SNOR_ID(0xbf, 0x25, 0x41),
        .name = "sst25vf016b",
        .size = SZ_2M,
        .flags = SPI_NOR_HAS_LOCK | SPI_NOR_SWP_IS_VOLATILE,
    }, {
        .id = SNOR_ID(0xef, 0x40, 0x20),
        .name = "w25q512jvq",
        .size = SZ_64M,
        .flags = SPI_NOR_HAS_LOCK | SPI_NOR_HAS_TB | SPI_NOR_TB_SR_BIT6 |
             SPI_NOR_4BIT_BP | SPI_NOR_HAS_CMP,
    }, {
        .id = SNOR_ID(0xc2, 0x9e, 0x16),
        .name = "mx25l3255e",
        .size = SZ_4M,
        .fixups = &mx25l3255e_fixups,
    }, {
        .id = SNOR_ID(0xc8, 0x40, 0x19),
        .name = "gd25q256",
        .size = SZ_32M,
        .fixups = &gd25q256_fixups,
    },
};

const struct spi_nor_manufacturer spi_nor_x = {
    .name = "x",
    .parts = x_parts,
    .fixups = &macronix_nor_fixups,
};
"""


def test_linux_nor_registers(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "drivers/mtd/spi-nor/core.h": LINUX_CORE_H,
            linux.SPINOR_H: SPINOR_H,
            "drivers/mtd/spi-nor/x.c": LINUX_REGISTERS,
        },
    )
    r = by_name(linux.extract_nor(tmp_path))
    # Its fixups replace the status register locking: no bits, the claim stays.
    at = r["AT25DF321A"]
    assert at["protection"] is None
    assert at["features"] == ["lock"]
    assert "SPI_NOR_SWP_IS_VOLATILE" in at["flags"]
    # Otherwise SPI_NOR_SWP_IS_VOLATILE makes the BP bits volatile.
    sst = r["SST25VF016B"]["protection"]
    assert sst["bp0"] == {"register": "sr1", "bit": 2, "writability": "volatile"}
    assert sst["srp"] == {"register": "sr1", "bit": 7}
    w = r["W25Q512JVQ"]
    assert {role: (b["register"], b["bit"]) for role, b in w["protection"].items()} == {
        "bp0": ("sr1", 2),
        "bp1": ("sr1", 3),
        "bp2": ("sr1", 4),
        "bp3": ("sr1", 5),
        "tb": ("sr1", 6),
        "cmp": ("sr2", 6),
        "srp": ("sr1", 7),
    }
    assert w["via"]["protection.tb"] == "SPI_NOR_HAS_TB; SPI_NOR_TB_SR_BIT6"
    assert w["via"]["protection.bp3"] == "SPI_NOR_4BIT_BP"
    # The part's own quad enable fixup is its claim; the maker's
    # default_init (macronix_nor_fixups) is no part's.
    mx = r["MX25L3255E"]
    assert mx["quad_enable"] == {"register": "sr1", "bit": 6}
    assert mx["via"]["quad_enable"] == ".fixups = &mx25l3255e_fixups"
    assert "quad_read" in features(mx)
    # GD25Q256: the C's bit, not the D's or E's: none, and a note.
    gd = r["GD25Q256"]
    assert gd["quad_enable"] is None
    assert any("gd25q256_post_bfpt" in n for n in gd["notes"])


def test_linux_unknown_quad_enable_fixup(tmp_path: Path) -> None:
    text = LINUX_REGISTERS.replace("mx25l3255e_fixups", "new_fixups")
    write(
        tmp_path,
        {
            "drivers/mtd/spi-nor/core.h": LINUX_CORE_H,
            linux.SPINOR_H: SPINOR_H,
            "drivers/mtd/spi-nor/x.c": text,
        },
    )
    with pytest.raises(ValueError, match="new_fixups sets quad_enable"):
        linux.extract_nor(tmp_path)


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
    assert w["erasers"] == [
        {"opcode": 0x20, "blocks": [[4096, 4096]]},
        {"opcode": 0xD8, "blocks": [[65536, 256]]},
    ]
    # Fast read is U-Boot's default, so implies nothing.
    assert features(w) == ["dual_read", "erase_4k", "erase_64k"]
    assert w["features"] == []
    assert assumed(w) == {"READ_1_1_1", "READ_1_1_1_FAST", "PP_1_1_1", "CHIP_ERASE"}
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
    # SPI_NOR_QUAD_READ gives U-Boot's PP_1_1_4 too, for every such part:
    # a default, as is its 4-byte form, so neither implies quad_pp.
    w512 = ops(r["W25Q512"])
    assert w512["PP_1_1_4"] == (0x32, "SPI_NOR_QUAD_READ: default (spi_nor_init_params)")
    assert w512["PP_1_1_4_4B"] == (0x34, "SPI_NOR_4B_OPCODES")
    assert {"PP_1_1_4", "PP_1_1_4_4B", "READ_1_1_1_FAST_4B"} <= assumed(r["W25Q512"])
    assert "READ_1_1_4_4B" not in assumed(r["W25Q512"])
    assert "quad_pp" not in features(r["W25Q512"])
    assert "READ_1_1_1_FAST" not in ops(r["S25FL128S"])  # SPI_NOR_NO_FR
    assert {"4byte_addr", "4byte_opcodes", "quad_read"} <= set(features(r["W25Q512"]))
    s = r["S25FL128S"]
    assert s["vendor"] == "spansion"
    assert s["ext_id"] == "4d0180"
    assert "fast_read" not in features(s)
    assert r["S25SL12800"]["ext_id"] == "0300"
    assert sector(r["S25SL12800"]) == 256 * 1024
    f = r["MB85RS256TY"]
    assert f["vendor"] == "fujitsu"
    assert f["id"] == "047f25"
    assert f["size"] == 32 * 1024
    assert f["page_size"] == 32 * 1024
    assert "no_erase" in f["features"]
    # A sector the size of the chip, but no eraser: it needs no erase.
    assert (f["erasers"], sector(f)) == (None, None)
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
    assert sector(e) == 65536
    # FEATURE_QPI_38 & ~FEATURE_FAST_READ_QOUT: everything but the quad output
    # read. Each bit is an operation's via, or a claim's, so none is left.
    assert e["flags"] == []
    assert e["via"] == {"feature:otp": "FEATURE_OTP"}
    # .reg_bits gives the protection bits, by role: .bp is BP0, BP1, ...
    # flashrom says how each bit is written: RW too.
    assert e["protection"] == {"bp0": {"register": "sr1", "bit": 2, "writability": "rw"}}
    assert features(e) == [
        "dual_read",
        "erase_4k",
        "erase_64k",
        "fast_read",
        "lock",
        "otp",
        "qpi",
        "sfdp",
    ]
    # Its operations, erasers and protection bits imply the rest.
    assert e["features"] == ["otp"]
    # The chip erase's layout is its size: the operation is stored, and the
    # layout derived, as a die erase's is.
    assert e["erasers"] == [
        {"opcode": 0x20, "blocks": [[4096, 4096]]},
        {"opcode": 0xD8, "blocks": [[65536, 256]]},
        {
            "opcode": None,
            "blocks": [[4096, 2], [8192, 1]],
            "function": "spi_block_erase_emulation",
        },
    ]
    assert erasers(e)[3] == {"opcode": 0xC7, "blocks": [[16 << 20, 1]]}
    assert stored_ops(e)["CHIP_ERASE"] == "block_erasers (1 x 16777216)"
    assert e["voltage"] == [2700, 3600]
    assert e["tested"] == "TEST_OK_PREW"
    assert ops(e) == {
        "RDID": (0x9F, "id read (rdid)"),
        "RDSFDP": (0x5A, "comment: supports SFDP"),
        "READ_1_1_1_FAST": (0x0B, "FEATURE_FAST_READ"),
        "READ_1_1_2": (0x3B, "FEATURE_FAST_READ_DOUT"),
        "BE_4K": (0x20, "eraser: 4096 x 4096"),
        "SE": (0xD8, "eraser: 256 x 65536"),
        "CHIP_ERASE": (0xC7, "block_erasers (1 x 16777216)"),
        "WRSR": (0x01, "FEATURE_WRSR_WREN"),
        "EQPI_38": (0x38, "FEATURE_QPI_38_FF"),
        "RSTQIO_FF": (0xFF, "FEATURE_QPI_38_FF"),
    }
    assert "EON_ID_NOPREFIX: EON, missing 0x7F prefix" in e["notes"]
    assert "supports SFDP" not in e["notes"]  # RDSFDP's via holds it
    # The id read and the block erases are derived, so not stored; the chip
    # erase is stored, and its layout derived.
    assert {o["op"] for o in e["opcodes"]}.isdisjoint({"RDID", "BE_4K", "SE"})

    s = r["S25FL128S_UL Uniform 128 kB Sectors"]
    assert s["id"] == "012018"
    assert s["ext_id"] == "4d0080"  # the id length byte, 4d, the probe skips
    assert s["tested"] == "{ .probe = NA, .read = OK }"
    # Its 256-byte page is wrong (ENTRY_WRONG): the record has the
    # datasheet's 512, with a note.
    assert s["page_size"] == 512
    assert any(n.startswith("page 512 B, not the entry's 256 B: ") for n in s["notes"])
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
    assert features(e) == ["4byte_addr", "4byte_opcodes", "erase_64k"]
    assert e["features"] == []  # each is implied
    assert e["flags"] == []  # READ_1_1_1_4B, EWSR and WRSR hold them


def test_flashprog_register_bits(tmp_path: Path) -> None:
    # A .reg_bits as flashprog writes them (W25Q128.V's, with a fixed QE and
    # a Macronix-style OTP TB), and the feature bits naming how SR2 and SR3
    # are written: FEATURE_WRSR_EXT3 is the EXT2 bit and one of its own.
    reg_bits = """.reg_bits	=
		{
			.qe	= {STATUS2, 1, RO}, /* Fixed QE=1 */
			.srp    = {STATUS1, 7, RW},
			.srl    = {STATUS2, 0, RW},
			.bp     = {{STATUS1, 2, RW}, {STATUS1, 3, RW}, {STATUS1, 4, RW}},
			.tb     = {CONFIG, 3, OTP},
			.wps    = {SECURITY, 7, OTP},
			.dc	= {{STATUS3, 0, RW}, {STATUS3, 1, RW}},
		},
		.block_erasers"""
    text = FLASHPROG_C.replace(".block_erasers", reg_bits).replace(
        "FEATURE_WRSR_EITHER | FEATURE_4BA_READ", "FEATURE_WRSR_WREN | FEATURE_WRSR_EXT3"
    )
    write(tmp_path, {**FLASHROM_HEADERS, "flashchips.c": text})
    (e,) = flashrom.extract(tmp_path, "flashprog")
    # flashprog's .qe is the quad enable bit; RO: fixed.
    assert e["quad_enable"] == {"register": "sr2", "bit": 1, "writability": "ro"}
    assert "quad_read" in features(e)
    # CONFIG is read with 0x15, as SR3 is; SECURITY with RDSCUR.
    rw = {"writability": "rw"}
    assert e["protection"] == {
        "bp0": {"register": "sr1", "bit": 2, **rw},
        "bp1": {"register": "sr1", "bit": 3, **rw},
        "bp2": {"register": "sr1", "bit": 4, **rw},
        "tb": {"register": "sr3", "bit": 3, "writability": "otp"},
        "srp": {"register": "sr1", "bit": 7, **rw},
        "srl": {"register": "sr2", "bit": 0, **rw},
        "wps": {"register": "security", "bit": 7, "writability": "otp"},
    }
    # flashprog reads CONFIG with RDCR (0x15) and SECURITY with RDSCUR.
    assert ops(e)["RDSCUR"] == (0x2B, ".reg_bits SECURITY")
    assert "lock" in features(e)
    assert e["flags"] == []
    given = ops(e)
    assert given["WRSR_24"] == (0x01, "FEATURE_WRSR_EXT3")
    assert given["WRSR_16"] == (0x01, "FEATURE_WRSR_EXT3")
    assert given["RDSR2"] == (0x35, "FEATURE_WRSR_EXT3")
    assert given["RDSR3"] == (0x15, "FEATURE_WRSR_EXT3; .reg_bits CONFIG")


@pytest.mark.parametrize("comment", ["the latter supports SFDP", "F model supports SFDP"])
def test_flashrom_sfdp_comment_qualified_to_one_model(tmp_path: Path, comment: str) -> None:
    eon = FLASHROM_EON.replace("/* supports SFDP */", f"/* {comment} */")
    write(tmp_path, {**FLASHROM_HEADERS, "flashchips/eon.c": eon, "flashchips.c": ""})
    e = by_name(flashrom.extract(tmp_path, "flashrom"))["EN25QH128"]
    assert "sfdp" not in features(e)
    assert "RDSFDP" not in ops(e)
    assert comment in e["notes"]


def test_flashrom_errors(tmp_path: Path) -> None:
    write(tmp_path, FLASHROM_HEADERS)
    write(tmp_path, {"flashchips.c": "int nothing;"})
    with pytest.raises(ValueError, match=r"no flashchips\[\]"):
        flashrom.extract(tmp_path, "flashprog")
    bad = FLASHPROG_C.replace("ID_SPI_RDID", "ID_SOMETHING_NEW")
    write(tmp_path, {"flashchips.c": bad})
    with pytest.raises(ValueError, match="unknown probe 'SOMETHING_NEW'"):
        flashrom.extract(tmp_path, "flashprog")


def test_flashrom_wrong_values_are_corrected() -> None:
    # "S25FL256S Large Sectors": half the size, and the S25FS's voltage.
    notes: list[str] = []
    erasers = [
        {"opcode": 0xDC, "blocks": [[256 << 10, 64]]},
        {"opcode": 0x60, "blocks": [[16 << 20, 1]]},
    ]
    got = flashrom.corrected("S25FL256S Large Sectors", 16 << 20, 256, [1700, 2000], erasers, notes)
    assert got == (32 << 20, 512, [2700, 3600])
    assert erasers == [
        {"opcode": 0xDC, "blocks": [[256 << 10, 128]]},
        {"opcode": 0x60, "blocks": [[32 << 20, 1]]},
    ]
    assert [n.split(":")[0] for n in notes] == [
        "size 32 MiB, not the entry's 16 MiB",
        "page 512 B, not the entry's 256 B",
        "voltage 2700-3600 mV, not the entry's 1700-2000 mV",
    ]
    # The uniform-128 KiB S25FL128S_UL erases 256 KiB blocks.
    erasers = [{"opcode": 0xD8, "blocks": [[128 << 10, 128]]}]
    flashrom.corrected("S25FL128S_UL Uniform 128 kB Sectors", 16 << 20, 256, None, erasers, [])
    assert erasers == [{"opcode": 0xD8, "blocks": [[256 << 10, 64]]}]
    # An entry not listed is as it is.
    assert flashrom.corrected("W25Q128.V", 16 << 20, 256, None, [], []) == (16 << 20, 256, None)


def test_flashrom_only_big_spansion_has_an_extended_id() -> None:
    # The id length byte, 4d, belongs to PROBE_SPI_BIG_SPANSION's parts only.
    assert flashrom.id_bytes("rdid", 0x01, 0x20180080, "SPI_BIG_SPANSION") == ("012018", "4d0080")
    assert flashrom.id_bytes("rdid", 0xEF, 0x4018, "SPI_RDID") == ("ef4018", None)
    with pytest.raises(ValueError, match="more than two bytes, 0x20180080, from probe 'SPI_RDID'"):
        flashrom.id_bytes("rdid", 0x01, 0x20180080, "SPI_RDID")


def test_flashrom_continuation_id_has_a_one_byte_model() -> None:
    # rdid_get_ids(): 3 bytes starting 0x7f are 7f, the maker, one model
    # byte (PMC's PM25LD020 is 7f 9d 22); RDID4 reads two (AMIC's), and
    # flashprog's ID_SPI_RDID as many as the table writes.
    assert flashrom.id_bytes("rdid", 0x7F9D, 0x22, "SPI_RDID") == ("7f9d22", None)
    assert flashrom.id_bytes("rdid", 0x7F37, 0x2010, "SPI_RDID4") == ("7f372010", None)
    assert flashrom.id_bytes("rdid", 0x7F37, 0x2020, "SPI_RDID") == ("7f372020", None)
    assert flashrom.id_bytes("rdid", 0x7F37, 0x20, "SPI_RDID4") == ("7f370020", None)


def flashrom_micron(tmp_path: Path) -> record.Record:
    """flashrom's MT25QL01G, from the fixture."""
    write(
        tmp_path,
        {
            **FLASHROM_HEADERS,
            "flashchips/micron.c": fixture("flashrom/flashchips/micron.c"),
            "flashchips.c": '#include "flashchips/micron.c"',
        },
    )
    (m,) = flashrom.extract(tmp_path, "flashrom")
    return m


def test_flashrom_four_byte_modes_and_otp(tmp_path: Path) -> None:
    m = flashrom_micron(tmp_path)
    # FEATURE_4BA_WREN: 0xb7 after a write enable, and the extended address
    # register (FEATURE_4BA_EAR_C5C8), each from its own bit; 0xe9 out is
    # stated, EN4B, WREAR and RDEAR derived.
    assert m["four_byte_modes"] == ["wrear", "wren_en4b"]
    assert m["via"]["four_byte_modes:wren_en4b"] == "FEATURE_4BA_ENTER_WREN"
    assert m["via"]["four_byte_modes:wrear"] == "FEATURE_4BA_EAR_C5C8"
    assert "EX4B" in stored_ops(m)
    assert not {"EN4B", "WREAR", "RDEAR"} & set(stored_ops(m))
    assert ops(m)["EN4B"] == (0xB7, "4-byte mode wren_en4b")
    assert ops(m)["RDEAR"] == (0xC8, "4-byte mode wrear")
    assert not any(f.startswith("FEATURE_4BA") for f in m["flags"])
    # "OTP: 64B total; read 0x4B, write 0x42": the area, and Micron's OTP
    # read and program; the comment leaves the notes for the area's via,
    # with FEATURE_OTP, which the area implies.
    comment = "OTP: 64B total; read 0x4B, write 0x42"
    assert m["otp"] == {"size": 64}
    assert m["via"]["otp"] == f"{comment}; FEATURE_OTP"
    assert comment not in m["notes"]
    assert "otp" not in m["features"]
    assert stored_ops(m)["READ_OTP"] == comment
    assert stored_ops(m)["PSECR"] == comment


@pytest.mark.parametrize(
    ("note", "area", "found"),
    [
        (
            "OTP: 1024B total, 256B reserved; read 0x48; write 0x42, erase 0x44, read ID 0x4B",
            {"size": 768},
            ["RSECR", "PSECR", "ESECR", "RUID"],
        ),
        ("OTP: 3x 512B; read 0x48; write 0x42, erase 0x44", {"size": 1536, "regions": 3}, None),
        ("OTP: 4 x 256 bytes", {"size": 1024, "regions": 4}, []),
        ("4 x 256B Security Region (OTP)", {"size": 1024, "regions": 4}, []),
        ("OTP: 512B total; enter 0xB1, exit 0xC1", {"size": 512}, ["ENSO", "EXSO"]),
        ("OTP: 8KiB total; enter 0xB1, exit 0xC1", {"size": 8192}, None),
        ("OTP: 512B total; enter 0x3A", {"size": 512}, ["ENTER_OTP_3A"]),
        ("OTP: 128B total, 64B pre-programmed; read 0x77; write 0x9B", {"size": 64}, []),
        ("OTP: 64B total; read 0x4B, 0x48; write 0x42", {"size": 64}, None),
        # PMC's: 0x4b is read only beside a 0x42 program.
        ("OTP: 256B total; read 0x4b; write 0xb1", {"size": 256}, []),
        ("OTP: 506B total (2x 8B, 30x 16B, 1x 10B); read 0x4B; write 0x42", {"size": 506}, None),
        # Qualified to one model of the entry, or two revisions: a note.
        ("OTP: 1024B total, 256B reserved; read 0x48; write 0x42 (B version only)", None, None),
        ("OTP: 1024B total, 256B reserved, later 3x 512B; read 0x48", None, None),
        ("OTP: 06E 64B total; enter 0xB1, exit 0xC1", None, None),
        # A command qualified to one model is left out, the rest taken.
        (
            "OTP: 256B total; enter 0x3A, (A version only:) read ID 0x4B",
            {"size": 256},
            ["ENTER_OTP_3A"],
        ),
        ("OTP: MX25L12833F has 1KB total, others have 512B total", None, None),
    ],
)
def test_flashrom_otp_comments(
    note: str, area: dict[str, int] | None, found: list[str] | None
) -> None:
    parsed = flashrom.otp(note)
    if area is None:
        assert parsed is None
        return
    assert parsed is not None
    assert parsed[0] == area
    if found is not None:
        assert parsed[1] == found
    if "read 0x4B, 0x48" in note:
        assert set(parsed[1]) == {"RSECR", "PSECR", "READ_OTP"}


def test_flashrom_die_erase_gives_the_dies(tmp_path: Path) -> None:
    m = flashrom_micron(tmp_path)
    # spi_block_erase_c4 over {64 MiB, 2}: two dies, and the die erase it
    # sends; the layout is the dies', derived, so not stored.
    assert (m["dies"], m["via"]["dies"]) == (2, "spi_block_erase_c4")
    assert 0xC4 not in [e["opcode"] for e in m["erasers"]]
    assert ops(m)["DIE_ERASE"] == (0xC4, "block_erasers (2 x 67108864)")
    die = Eraser(0xC4, (EraseBlock(64 << 20, 2),))
    assert die in Record.from_json(m).erasers
    assert sector(m) == 64 << 10


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
    assert "quad_read" not in features(s008)
    assert set(ops(r["FM25V02"])) == {"RDID", "READ_1_1_1", "PP_1_1_1"}
    w = r["W25Q128FV/JV"]
    assert w["vendor"] == "win"
    assert w["id"] == "ef4018"
    assert ops(w) == {
        "RDID": (0x9F, "id read (rdid)"),
        "READ_1_1_1": (0x03, "read_cmd"),
        "READ_1_4_4": (0xEB, "qread_cmd"),
        "PP_1_1_1": (0x02, "pprog_cmd"),
        "SE": (0xD8, "eraser: 256 x 65536"),
        "CHIP_ERASE": (0xC7, "chip_erase_cmd"),
    }
    assert ops(r["IS25WP512M"])["READ_1_4_4_4B"] == (0xEC, "qread_cmd")
    # The chip erase's layout is derived from the size.
    assert w["erasers"] == [{"opcode": 0xD8, "blocks": [[65536, 256]]}]
    assert erasers(w) == [
        {"opcode": 0xD8, "blocks": [[65536, 256]]},
        {"opcode": 0xC7, "blocks": [[16 << 20, 1]]},
    ]
    assert features(w) == ["erase_64k", "quad_read"]
    assert w["features"] == []
    assert sector(w) == 65536
    assert "4byte_addr" in features(r["IS25WP512M"])
    g = r["GD25Q512"]
    assert features(g) == ["erase_4k"]
    assert sector(g) is None  # a 0x20 eraser: no 0xd8, 0xdc or 0x52 block
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
    # Its quad enable bit and protection bits imply lock and quad_read.
    assert s["features"] == []
    # CONFR (1 << 1): SR2 (read with 0x35) bit 1; TB at CONFR (1 << 5), OTP;
    # BP0 to BP2 from bp_offset.
    assert s["quad_enable"] == {"register": "sr2", "bit": 1}
    assert s["protection"] == {
        "bp0": {"register": "sr1", "bit": 2},
        "bp1": {"register": "sr1", "bit": 3},
        "bp2": {"register": "sr1", "bit": 4},
        "tb": {"register": "sr2", "bit": 5, "writability": "otp"},
    }
    assert s["erasers"] == [{"opcode": 0xD8, "blocks": [[65536, 512]]}]
    assert sector(s) == 65536
    assert s["via"] == {
        "erasers:0xd8": "sector_erase=true",
        "protection": "bp_len=3; bp_offset={(1 << 2), (1 << 3), (1 << 4), 0}",
        "protection.tb": "tb_register=CONFR; tb_offset=(1 << 5); tb_otp=true",
        "quad_enable": "quad_register=CONFR; quad_mask=(1 << 1)",
    }
    assert "quad_register=CONFR" not in s["flags"]
    assert "sector_erase=true" not in s["flags"]
    assert s["notes"][0].startswith("https://www.mouser.fr/")
    # 32 MiB: the 4-byte forms too; no subsector_erase, so no BE_4K;
    # set_quad_bit() reads CONFR with 0x35 and writes it with a 2-byte WRSR.
    assert set(ops(s)) == {
        "RDID",
        "READ_1_1_1",
        "READ_1_1_1_4B",
        "PP_1_1_1",
        "PP_1_1_1_4B",
        "SE",
        "SE_4B",
        "RDSR2",
        "WRSR_16",
    }
    assert ops(s)["SE"] == (0xD8, "eraser: 512 x 65536")
    # Every read and write is its driver's, so implies nothing; the erases
    # the entry allows are the part's, but their 4-byte forms the driver's
    # (any address above 0xffffff, whatever the part).
    assert assumed(s) == {"READ_1_1_1", "READ_1_1_1_4B", "PP_1_1_1", "PP_1_1_1_4B", "SE_4B"}
    assert "4byte_opcodes" not in features(s)
    assert features(s) == ["4byte_addr", "erase_64k", "lock", "quad_read"]
    assert features(r["W25Q128"]) == ["erase_4k", "erase_64k"]
    assert r["W25Q128"]["erasers"] == [
        {"opcode": 0x20, "blocks": [[4096, 4096]]},
        {"opcode": 0xD8, "blocks": [[65536, 256]]},
    ]
    # NONER: not filled in, so no bit; bp_len 0: no block protection.
    assert (r["W25Q128"]["quad_enable"], r["W25Q128"]["protection"]) == (None, None)
    # Locked at power-up: the driver sends ULBPR first.
    sst = r["SST26VF064B"]
    assert ops(sst)["ULBPR"] == (0x98, "global_lock=true")
    assert sst["protection"] is None
    # Macronix's CONFR is its configuration register, read with 0x15: SR3.
    mx = r["MX25L12833"]
    assert mx["protection"]["tb"] == {"register": "sr3", "bit": 3, "writability": "otp"}
    assert mx["quad_enable"] == {"register": "sr1", "bit": 6}
    assert mx["protection"]["bp3"] == {"register": "sr1", "bit": 5}  # bp_len 5, 4 offsets
    # A TB past the first status byte, (1 << 14), is never read: none.
    gd = r["GD25Q32C"]
    assert "tb" not in gd["protection"]
    assert any(n.startswith("tb_offset=(1 << 14) left out") for n in gd["notes"])
    # Its bp_offset bits are its sector protection status and WP pin bits,
    # not block protect ones: no layout, and a lock claim.
    at = r["AT25DF321A"]
    assert at["protection"] is None
    assert any(n.startswith("bp_offset left out") for n in at["notes"])
    assert at["features"] == ["lock"]
    assert "tb_offset=(1 << 3)" in at["flags"]
    # get_tb() reads CONFR: 0x35, and on a Macronix part 0x15.
    assert ops(s)["RDSR2"] == (0x35, "set_quad_bit: CONFR; get_tb: CONFR")
    assert ops(mx)["RDSR3"] == (0x15, "get_tb: CONFR, Macronix")


def test_openfpgaloader_no_map(tmp_path: Path) -> None:
    write(tmp_path, {openfpgaloader.DB: "int x;", openfpgaloader.FLASH_CPP: ""})
    with pytest.raises(ValueError, match="no flash_list"):
        openfpgaloader.extract(tmp_path)


# --- Rockchip ----------------------------------------------------------------

ROCKCHIP = FIXTURES / "rockchip"


def test_rockchip_nor() -> None:
    r = by_name(rockchip.extract_nor(ROCKCHIP))
    gd = r["GD25Q40B"]
    assert (gd["id"], gd["id_method"], gd["line"]) == ("c84013", "rdid", 16)
    # No page size: NOR_PAGE_SIZE is the driver's, for every part.
    assert (gd["size"], gd["page_size"], sector(gd)) == (512 << 10, None, 64 << 10)
    assert gd["erasers"] == [
        {"opcode": 0x20, "blocks": [[4096, 128]]},
        {"opcode": 0xD8, "blocks": [[65536, 8]]},
    ]
    # Feature 0x05: quad read, and the status registers written together;
    # prog_cmd_4 is there, but no FEA_4BIT_PROG to use it.
    assert features(gd) == ["erase_4k", "erase_64k", "quad_read"]
    # QE_bits 9: register 1 (0x35), bit 1. snor_write_status1 sets it by
    # reading SR2 and writing both registers with a 2-byte 0x01.
    assert gd["quad_enable"] == {"register": "sr2", "bit": 1}
    assert gd["flags"] == []
    # READ_1_1_4's via, "read_cmd_4 (FEA_4BIT_READ)", holds the bit.
    assert gd["via"] == {"quad_enable": "QE_bits=9"}
    assert set(ops(gd)) == {
        "RDID",
        "READ_1_1_1",
        "READ_1_1_4",
        "PP_1_1_1",
        "BE_4K",
        "SE",
        "RDSR2",
        "WRSR_16",
    }
    assert ops(gd)["READ_1_1_4"] == (0x6B, "read_cmd_4 (FEA_4BIT_READ)")
    assert ops(gd)["WRSR_16"] == (0x01, "write_status=snor_write_status1")
    # snor_write_status: SR2 with its own 0x31.
    gd64 = r["GD25Q64B/GD25Q64C/GD25Q64E"]
    assert {"RDSR2", "WRSR2"} <= set(ops(gd64))
    # GD25Q256: its bit is the revision's (snor_flash_info_adjust): none.
    gd256 = r["GD25Q256B/GD25Q256C/GD25Q256D/GD25Q256E"]
    assert gd256["quad_enable"] is None
    assert "QE_bits=6" in gd256["flags"]
    assert any(n.startswith("no quad enable bit: snor_flash_info_adjust") for n in gd256["notes"])

    # The names in a comment, each part spelled out.
    assert "GD25Q64B/GD25Q64C/GD25Q64E" in r
    assert "GD25Q127C/GD25Q128C/GD25Q128E" in r
    assert "MX25L25635E/MX25L25635F/MX25L25645G/MX25L25645GMI-08G" in r
    assert "BH25Q128AS/BY25Q128AS" in r

    # 4-byte opcodes; 0x3e is sent on one address line, not being Macronix.
    gd256 = r["GD25Q256B/GD25Q256C/GD25Q256D/GD25Q256E"]
    assert set(ops(gd256)) == {
        "RDID",
        "READ_1_1_1_4B",
        "READ_1_1_4_4B",
        "PP_1_1_1_4B",
        "BE_4K_4B",
        "SE_4B",
    }
    assert {"4byte_addr", "4byte_opcodes", "quad_pp"} <= set(features(gd256))
    assert gd256["notes"][0].startswith("prog_cmd_4 0x3e left out")
    # XM25QH(QU)256B: the QU answers another id, so only the QH is taken.
    xm = r["XM25QH256B"]
    assert xm["notes"][0].startswith("the comment also names the XM25QU256B")
    assert xm["notes"][1].startswith("prog_cmd_4 0x3e left out")
    mx = r["MX25L25635E/MX25L25635F/MX25L25645G/MX25L25645GMI-08G"]
    assert ops(mx)["PP_1_4_4_4B"] == (0x3E, "prog_cmd_4 (FEA_4BIT_PROG)")
    # snor_write_status2: SR1 bit 6, written with the configuration
    # register (read with 0x15) in a 2-byte 0x01.
    assert mx["quad_enable"] == {"register": "sr1", "bit": 6}
    assert ops(mx)["RDSR3"] == (0x15, "write_status=snor_write_status2")
    assert ops(mx)["WRSR_16"] == (0x01, "write_status=snor_write_status2")
    assert "write_status=snor_write_status2" not in mx["flags"]
    assert ops(r["MX25L6433F"])["PP_1_4_4"] == (0x38, "prog_cmd_4 (FEA_4BIT_PROG)")

    # Feature 0x3c: 4-byte addresses, entering 4-byte mode first.
    w = r["W25Q256F/W25Q256J"]
    assert w["four_byte_modes"] == ["en4b"]
    assert w["via"]["four_byte_modes:en4b"] == "FEA_4BYTE_ADDR_MODE"
    assert ops(w)["EN4B"] == (0xB7, "4-byte mode en4b")  # derived from the way in
    assert "FEA_4BYTE_ADDR_MODE" not in w["flags"]
    assert {"READ_1_1_1_4B", "PP_1_1_1", "PP_1_1_4", "BE_4K", "SE"} <= set(ops(w))
    assert "fast_read" in features(r["MX25U51245G"])  # 0x0c


def test_rockchip_nand() -> None:
    recs = rockchip.extract_nand(ROCKCHIP)
    r = by_name(recs)
    assert all(x["type"] == "nand" and x["id_method"] == "rdid_opcode_addr" for x in recs)
    tc = r["TC58CVG0S0HXAIX"]
    assert tc["id"] == "98c2"  # a third byte of 0 is not compared
    assert (tc["size"], tc["page_size"], sector(tc)) == (128 << 20, 2048, 128 << 10)
    assert tc["features"] == []
    assert tc["flags"] == [
        "ecc_status=sfc_nand_get_ecc_status0",
        "has_qe_bits=0",
        "meta={ 0x04, 0x08, 0xFF, 0xFF }",
    ]
    # max_ecc_bits, with no step; plane_per_die. Rockchip states no dies
    # (its FTL's die_num is 1 for every part).
    assert tc["ecc"] == {"strength_bits": 8}
    assert (tc["planes"], tc["dies"], tc["oob_size"]) == (1, None, None)
    # The read and program every part gets, and the core commands, are the
    # driver's defaults.
    assert assumed(tc) == set(rockchip.NAND_DEFAULTS)
    assert r["TC58CVG2S0HRAIJ"]["page_size"] == 4096
    assert sector(r["XT26G04A"]) == 128 * 2048
    # No FEA_4BIT_READ: has_qe_bits=0 says nothing, and stays a flag.
    assert tc["quad_enable"] is None
    assert r["W25N01GV"]["id"] == "efaa21"
    assert "FEA_SOFT_QOP_BIT" in r["W25N01GV"]["flags"]
    # FEA_4BIT_READ and FEA_4BIT_PROG: the quad read and load sfc_nand_init()
    # sets up, which imply quad_read and quad_pp; the bits are their via.
    assert r["W25N01GV"]["features"] == []
    quad = {o["op"]: o["via"] for o in r["W25N01GV"]["opcodes"] if not o.get("assumed")}
    assert quad == {
        "NAND_READ_CACHE_1_1_4": "FEA_4BIT_READ: page_read_cmd = 0x6b",
        "NAND_PROGRAM_LOAD_1_1_4": "FEA_4BIT_PROG: page_prog_cmd = 0x32",
    }
    assert {"quad_read", "quad_pp"} <= set(features(r["W25N01GV"]))
    assert "FEA_4BIT_READ" not in r["W25N01GV"]["flags"]
    # Quad reads with has_qe_bits=0: sfc_nand_init() sets no QE bit first,
    # as NOR's QE_bits=0.
    assert r["W25N01GV"]["quad_enable"] == "none"
    assert r["W25N01GV"]["via"]["quad_enable"] == "has_qe_bits=0"
    # has_qe_bits=1: bit 0 of feature 0xb0, which implies quad_read.
    (mx,) = (x for x in recs if x["id"] == "c226")
    assert mx["quad_enable"] == {"register": "nand-b0", "bit": 0}
    assert "quad_read" not in mx["features"]
    assert "quad_read" in features(mx)
    gd = r["GD5F1GQ5REYIG"]
    # A third byte repeating the manufacturer's, or 0x7f, follows the id.
    assert (gd["id"], gd["ext_id"]) == ("c841", "c8")
    assert (r["F50L2G41KA"]["id"], r["F50L2G41KA"]["ext_id"]) == ("c841", "7f")
    assert r["W25N01GV"]["ext_id"] is None
    assert gd["notes"] == ["Add 3rd code to distingush with F50L2G41KA"]
    assert r["GD5F4GQ6REXXG"]["notes"] == ["1*4096"]
    assert r["GD5F4GQ6REXXG"]["planes"] == 2
    assert r["GD5F4GQ6REXXG"]["size"] == 512 << 20
    assert "MT29F2G01ABA/XT26G02E/F50L2G41XA" in r
    assert "S35ML01G3/ANV1GCP0CLG/HYF1GQ4UTXCAE/YX25G1E/GSS01GSAM0" in r
    # The driver takes the first entry that fits.
    assert r["XT26Q04DWSIGT-B"]["notes"][0] == (
        "never used: the driver matches the entry on line 37 first"
    )
    assert r["UM19A0HISW"]["notes"][0].endswith("on line 46 first")
    assert r["F50L2G41KA"]["notes"] == []  # c8 41 7f is not c8 41 c8
    assert len(rockchip.extract(ROCKCHIP)) == len(recs) + 11


def rockchip_tree(tmp_path: Path, nor: str = "", nand: str = "") -> Path:
    """A tree of the fixture's headers and one-table sources."""
    tree = {
        name: fixture(f"rockchip/{name}")
        for name in (rockchip.NOR_H, rockchip.NAND_H, rockchip.SFC_H)
    }
    tree[rockchip.NOR] = f"static struct flash_info spi_flash_tbl[] = {{\n{nor}}};\n"
    tree[rockchip.NAND] = f"static struct nand_info spi_nand_tbl[] = {{\n{nand}}};\n"
    return write(tmp_path, tree)


NOR_ENTRY = "{ 0xc84013, 128, 8, 0x03, 0x02, 0x6B, 0x32, 0x20, 0xD8, 0x05, 10, 9, 0 },\n"

NAND_ENTRY = (
    "{ 0xEF, 0xAA, 0x21, 4, 0x40, 1, 1024, 0x4C, 18, 0x1, 0, "
    "{ 0x04, 0x14, 0x24, 0xFF }, &sfc_nand_get_ecc_status1 },\n"
)


def test_rockchip_nor_no_quad_enable_bit(tmp_path: Path) -> None:
    # QE_bits 0 with quad reads (feature 0x05): the driver sets no bit.
    root = rockchip_tree(tmp_path, nor=f"/* A1 */\n{NOR_ENTRY.replace(', 9, 0 }', ', 0, 0 }')}")
    (a,) = rockchip.extract_nor(root)
    assert a["quad_enable"] == "none"
    assert a["via"] == {"quad_enable": "QE_bits=0"}
    # Nothing is written, so the write function stays a flag.
    assert {"RDSR2", "WRSR_16"}.isdisjoint(ops(a))
    assert a["flags"] == ["write_status=snor_write_status1"]


def test_rockchip_nor_duplicate_id(tmp_path: Path) -> None:
    root = rockchip_tree(tmp_path, nor=f"/* A1 */\n{NOR_ENTRY}/* B1 */\n{NOR_ENTRY}")
    a, b = rockchip.extract_nor(root)
    assert a["notes"] == []
    assert b["notes"] == ["never used: the driver matches the entry on line 3 first"]


@pytest.mark.parametrize(
    ("entry", "error"),
    [
        (NOR_ENTRY, r"sfc_nor.c:2: 0 comments before the entry, not 1"),
        (f"/* A1 */ /* B1 */ {NOR_ENTRY}", r"sfc_nor.c:2: 2 comments"),
        (f"/* no part here */\n{NOR_ENTRY}", r"sfc_nor.c:3: no part name in 'no part here'"),
        ("/* A1 */ { 0xc84013, 128 },", r"sfc_nor.c:2: 2 fields, not 13"),
        (f"/* A1 */ {NOR_ENTRY.replace('128, 8,', '128, 16,')}", r"sector_size 16"),
        (f"/* A1 */ {NOR_ENTRY.replace(' 0 }', ' 1 }')}", "reserved2 1"),
        (f"/* A1 */ {NOR_ENTRY.replace('0x05', '0x45')}", r"unknown feature bits 0x40"),
        (f"/* A1 */ {NOR_ENTRY.replace('0x05', '0x07')}", r"no status register write"),
        (f"/* A1 */ {NOR_ENTRY.replace('0x03', '0x99')}", r"unknown read_cmd 0x99"),
        (f"/* A1 */ {NOR_ENTRY.replace('0x32', '0x99')}", r"unknown prog_cmd_4 0x99"),
        (f"/* A1 */ {NOR_ENTRY.replace('0x20', '0x52')}", r"unknown sector_erase_cmd 0x52"),
        (f"/* A1 */ {NOR_ENTRY.replace('0x6B', 'X')}", r"sfc_nor.c:2: unknown identifier X"),
    ],
)
def test_rockchip_nor_errors(tmp_path: Path, entry: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        rockchip.extract_nor(rockchip_tree(tmp_path, nor=entry))


@pytest.mark.parametrize(
    ("entry", "error"),
    [
        ("/* A1 */ { 0xEF, 0xAA },", r"sfc_nand.c:2: 2 fields, not 13"),
        (f"/* A1 */ {NAND_ENTRY.replace('status1', 'mode')}", r"unknown ECC status decoder"),
        (f"/* A1 */ {NAND_ENTRY.replace('4, 0x40', '16, 0x40')}", r"16 sectors per page"),
        (f"/* A1 */ {NAND_ENTRY.replace('1, 1024', '4, 1024')}", r"4 planes"),
        (f"/* A1 */ {NAND_ENTRY.replace('18,', '19,')}", r"density 19, but the geometry"),
        (f"/* A1 */ {NAND_ENTRY.replace('0x4C', '0x5C')}", r"unknown feature bits 0x10"),
    ],
)
def test_rockchip_nand_errors(tmp_path: Path, entry: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        rockchip.extract_nand(rockchip_tree(tmp_path, nand=entry))


def test_rockchip_no_table(tmp_path: Path) -> None:
    root = rockchip_tree(tmp_path)
    (root / rockchip.NAND).write_text("int x;")
    with pytest.raises(ValueError, match=r"sfc_nand.c: no spi_nand_tbl\[\]"):
        rockchip.extract_nand(root)


# --- IMSProg -----------------------------------------------------------------

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
    # defaults, not the part's (the S25FL256S erases 256 KiB blocks): no
    # page or sector size, no erase layout, and no flag.
    assert (s["page_size"], sector(s), s["erasers"]) == (None, None, None)
    assert features(s) == ["4byte_addr"]
    # Spansion's 4-byte mode is a bank register (addr4bit 0x21): its way in,
    # which gives BRWR and BRRD, holds the token.
    assert s["four_byte_modes"] == ["brwr"]
    assert s["via"] == {"four_byte_modes:brwr": "addr4bit=0x21"}
    assert s["flags"] == ["algorithmCode=0x00", "delay=1000"]
    assert s["supply_mv"] == 3300
    # IMSProg never sends 0xc7.
    assert set(ops(s)) == {"RDID", "READ_1_1_1", "PP_1_1_1", "SE", "BRWR", "BRRD"}
    assert set(stored_ops(s)) == {"READ_1_1_1", "PP_1_1_1", "SE"}  # RDID is derived
    # Winbond's (0x11): 0xb7 in, 0xe9 out, then the extended address
    # register cleared with 0xc5.
    en = r["EN25Q256"]
    assert en["four_byte_modes"] == ["en4b"]
    assert {"EX4B", "WREAR"} <= set(stored_ops(en))
    assert "EN4B" in ops(r["GD25LB512ME(1.8V)"])
    assert set(ops(r["FL016AIF"])) == {"RDID", "READ_1_1_1", "PP_1_1_1", "SE"}
    assert r["XT25Q16D(1.8V)"]["supply_mv"] == 1800
    assert not any(f.startswith("chipVCC") for f in r["XT25Q16D(1.8V)"]["flags"])
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
    assert (g["size"], g["page_size"], sector(g)) == (128 << 20, 2048, 128 << 10)
    # ECCsize is how much spare its raw mode reads, a setting in 64-byte
    # steps, not the part's spare area: a flag.
    assert g["oob_size"] is None
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


def test_record_make_keeps_each_kind_to_its_own() -> None:
    nand = {"type": "nand", "id_method": "rdid_opcode_dummy"}
    # SPI NAND geometry is SPI NAND's; dies are both kinds'.
    for field in record.NAND_ONLY:
        value = {"strength_bits": 8} if field == "ecc" else 2
        with pytest.raises(ValueError, match="on a SPI NOR record"):
            record.make("linux", "f", 1, "n", **{field: value})
        assert record.make("linux", "f", 1, "n", **nand, **{field: value})[field] == value
    assert record.make("linux", "f", 1, "n", dies=2)["dies"] == 2
    # Each operation is of the record's kind of flash.
    with pytest.raises(ValueError, match="are not nand operations"):
        record.make("linux", "f", 1, "n", **nand, opcodes=[{"op": "READ_1_1_4", "via": "v"}])
    with pytest.raises(ValueError, match="are not nor operations"):
        record.make("linux", "f", 1, "n", opcodes=[{"op": "NAND_PAGE_READ", "via": "v"}])
    # A die is selected with a command or a register bit, not both.
    bit = {"register": "nand-d0", "bit": 6}
    select = [{"op": "NAND_DIE_SELECT", "via": "v"}]
    with pytest.raises(ValueError, match="a die select operation and a die select bit"):
        record.make("linux", "f", 1, "n", **nand, dies=2, die_select_bit=bit, opcodes=select)
    # The die erase layout is the dies', never stored.
    die = [{"opcode": 0xC4, "blocks": [[64 << 20, 2]]}]
    with pytest.raises(ValueError, match="a die erase layout is derived"):
        record.make("linux", "f", 1, "n", size=128 << 20, erasers=die)


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
    erasers = [{"opcode": 0x20, "blocks": [[4096, 4096]]}]
    bad_keys = [
        "colour",  # no field
        "Size",
        "feature:qpi",  # a feature the record does not claim
        "via",  # residue and provenance fields hold no value of their own
        "notes",
        "flags",
        "opcodes",
        "features",
        "size.bogus",  # size has no components
        "size.x.y",
        "erasers:0x99",  # not one of its erasers
        "page_size",  # a field the record leaves empty
        "size:0x20",  # only erasers have members
    ]
    for key in bad_keys:
        with pytest.raises(ValueError, match="bad via key"):
            record.make("linux", "f", 1, "n", size=16 << 20, erasers=erasers, via={key: "x"})
    with pytest.raises(ValueError, match="bad via key"):
        record.make("linux", "f", 1, "n", id_method=None, via={"id_method": "x"})
    ok = {"size": "a", "erasers:0x20": "b", "erasers": "c", "id_method": "d"}
    size = 16 << 20
    assert record.make("linux", "f", 1, "n", size=size, erasers=erasers, via=ok)["via"] == ok
    # A token is stored once: not under two keys, nor in a note too.
    twice = {"size": "t", "erasers": "t"}
    with pytest.raises(ValueError, match="under erasers and size"):
        record.make("linux", "f", 1, "n", size=size, erasers=erasers, via=twice)
    # A layout is over the size.
    with pytest.raises(ValueError, match="an eraser over 16777216 bytes of 1"):
        record.make("linux", "f", 1, "n", size=1, erasers=erasers)
    with pytest.raises(ValueError, match="notes repeat via"):
        record.make("linux", "f", 1, "n", size=1, via={"size": "t"}, notes=["t"])


def test_record_make_drops_the_claims_the_record_implies() -> None:
    r = record.make(
        "linux",
        "f",
        1,
        "n",
        size=32 << 20,
        erasers=[{"opcode": 0x20, "blocks": [[4096, 8192]]}],
        features=["quad_read", "lock", "4byte_addr", "erase_4k", "fast_read"],
        flags=["Q", "L", "X", "F"],
        via=record.feature_via([("quad_read", "Q"), ("lock", "L"), ("4byte_addr", "X")]),
        opcodes=[
            {"op": "READ_1_1_4", "via": "Q"},
            {"op": "READ_1_1_1_FAST", "via": "F", "assumed": True},
        ],
    )
    # quad_read (READ_1_1_4), 4byte_addr (the size) and erase_4k (the
    # eraser) are implied; a driver default implies nothing, so fast_read
    # stays a claim.
    assert r["features"] == ["fast_read", "lock"]
    assert r["via"] == {"feature:lock": "L"}
    # X explained only the dropped claim: nothing holds it, so it is a flag.
    assert r["flags"] == ["X"]
    assert {"quad_read", "lock", "4byte_addr", "erase_4k", "fast_read"} == set(features(r))


def test_record_make_refuses_a_sector_size() -> None:
    with pytest.raises(KeyError, match="derived from the erasers"):
        record.make("linux", "f", 1, "n", sector_size=65536)


def test_record_make_will_not_lose_a_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    # A rule that implies quad_read while make() drops the claim, and not
    # after: the record would lose it, which make() refuses.
    quad = frozenset({Feature.QUAD_READ})
    calls = iter([quad, quad])
    monkeypatch.setattr(record.derive, "features", lambda _: next(calls, frozenset()))
    with pytest.raises(AssertionError, match=r"lost \['quad_read'\]"):
        record.make("linux", "f", 1, "n", features=["quad_read"])


def test_record_make_keeps_each_token_once() -> None:
    r = record.make(
        "flashrom",
        "f",
        1,
        "n",
        id="ef4018",
        erasers=[{"opcode": 0x20, "blocks": [[4096, 4096]]}],
        features=["fast_read", "otp"],
        flags=["FEATURE_FAST_READ", "FEATURE_OTP", "ER_4K", "OTHER"],
        via=record.feature_via([("fast_read", "FEATURE_FAST_READ"), ("otp", "FEATURE_OTP")]),
        opcodes=[
            {"op": "RDID", "via": "probe (rdid)"},
            {"op": "READ_1_1_1_FAST", "via": "FEATURE_FAST_READ"},
            {"op": "BE_4K", "via": "ER_4K; block_erasers"},
        ],
    )
    # The id read and the erase are derived, so not stored.
    assert r["opcodes"] == [{"op": "READ_1_1_1_FAST", "via": "FEATURE_FAST_READ"}]
    # An operation is fast_read's provenance; ER_4K moves to its eraser's via.
    assert r["via"] == {"erasers:0x20": "ER_4K", "feature:otp": "FEATURE_OTP"}
    assert r["flags"] == ["OTHER"]


def test_record_make_finds_a_token_in_an_operations_words() -> None:
    # "read_cmd_4 (FEA_4BIT_READ)" holds FEA_4BIT_READ, but not FEA_4BIT.
    r = record.make(
        "rockchip",
        "f",
        1,
        "n",
        features=["quad_read", "4byte_addr"],
        flags=["FEA_4BIT_READ", "FEA_4BIT"],
        via=record.feature_via([("quad_read", "FEA_4BIT_READ"), ("4byte_addr", "FEA_4BIT")]),
        opcodes=[{"op": "READ_1_1_4", "via": "read_cmd_4 (FEA_4BIT_READ)"}],
    )
    assert r["via"] == {"feature:4byte_addr": "FEA_4BIT"}
    assert r["flags"] == []


def test_record_make_puts_a_token_of_several_erasers_under_one_key() -> None:
    erasers = [
        {"opcode": 0x52, "blocks": [[32768, 16]]},
        {"opcode": 0x60, "blocks": [[524288, 1]]},
    ]
    token = "EraseCmd=0x00005260"
    r = record.make(
        "dediprog",
        "f",
        1,
        "n",
        id="bf8d",
        erasers=erasers,
        flags=[token],
        opcodes=[{"op": "BE_32K", "via": token}, {"op": "CHIP_ERASE_ALT", "via": token}],
    )
    assert (r["opcodes"], r["flags"], r["via"]) == ([], [], {"erasers": token})


def test_record_make_will_not_lose_an_id_command() -> None:
    # A flag only a derived id read holds has nowhere to go: the extractor
    # must name the command (via["id_method"]), which keeps the operation.
    rdid = [{"op": "RDID", "via": "CMD=0x9F"}]
    with pytest.raises(ValueError, match="CMD=0x9F would be lost with RDID"):
        record.make("dediprog", "f", 1, "n", id="ef4018", flags=["CMD=0x9F"], opcodes=rdid)
    r = record.make(
        "dediprog",
        "f",
        1,
        "n",
        id="ef4018",
        flags=["CMD=0x9F"],
        via={"id_method": "CMD=0x9F"},
        opcodes=[{"op": "RDID", "via": "CMD=0x9F"}],
    )
    assert r["opcodes"] == [{"op": "RDID", "via": "CMD=0x9F"}]


def test_opcodes_checks_values_against_the_table() -> None:
    o = Opcodes({"MY_READ": "0x03", "WRONG": "0x04"})
    o.add("READ_1_1_1", "first", "MISSING", "MY_READ")
    o.add("READ_1_1_1", "second")  # a second reason for the same operation
    o.add("READ_1_1_1", "first")  # a repeated reason is kept once
    assert "READ_1_1_1" in o
    assert o.to_json() == [{"op": "READ_1_1_1", "via": "first; second"}]
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
    # nordic,qspi-nor with the chip's own SFDP table, which the record
    # stores: its size (the node's, in bits, is the same), page and erase
    # types are derived from the table; readoc/writeoc are the modes used.
    m = r["MX25R6435F"]
    assert (m["file"], m["line"]) == ("boards/nordic/nrf52840dk/nrf52840dk_nrf52840.dts", 20)
    assert (m["id"], m["vendor"], m["size"], m["page_size"]) == ("c22817", None, None, None)
    assert list(m["sfdp_tables"]) == ["ff00"]
    assert m["sfdp_tables"]["ff00"].startswith("e520f1ff")
    assert m["via"] == {
        "sfdp_tables": "sfdp-bfp",
        "timings.dpd_enter": "t-enter-dpd=10000",
        "timings.dpd_exit": "t-exit-dpd=35000",
    }
    assert m["erasers"] is None
    loaded = Record.from_json(m)
    assert (loaded.size, loaded.page_size) == (8 << 20, 256)
    assert [e.to_json() for e in loaded.erasers] == [
        {"opcode": 0x20, "blocks": [[4096, 2048]]},
        {"opcode": 0x52, "blocks": [[32768, 256]]},
        {"opcode": 0xD8, "blocks": [[65536, 128]]},
    ]
    assert loaded.sfdp_disagreements() == ()
    assert features(m) == [
        "dual_read",
        "erase_32k",
        "erase_4k",
        "erase_64k",
        "quad_pp",
        "quad_read",
        "sfdp",
    ]
    assert {"nordic,qspi-nor", "readoc=read4io", "writeoc=pp4io"} <= set(m["flags"])
    # has-dpd: DP and RDPD, which its BFPT's DW14 gives too (so derived),
    # with the exit delay, 40 µs. The node's t-exit-dpd, 35 µs, is more
    # precise: stored, and no disagreement (35 µs is 40 µs on DW14's grid).
    assert loaded.sfdp_facts is not None
    assert loaded.sfdp_facts.timings.get("dpd_exit", "maximum") == 40_000
    assert m["timings"] == {"dpd_enter": {"maximum": 10000}, "dpd_exit": {"maximum": 35000}}
    assert {"DP", "RDPD"} <= {u.op for u in loaded.opcodes}
    assert not [o for o in m["opcodes"] if o["op"] in ("DP", "RDPD")]
    assert not [f for f in m["flags"] if f.startswith("sfdp-")]
    # The table gives the 1-4-4 read readoc names, with its dummy clocks.
    assert ops(m)["READ_1_4_4"] == (0xEB, "SFDP BFPT 1-4-4 fast read: 2 mode + 4 wait clocks")
    assert [o["op"] for o in m["opcodes"]] == ["PP_1_4_4"]
    (read,) = (u for u in loaded.opcodes if u.op == "READ_1_4_4")
    assert (read.implied, read.dummy_clocks) == (True, 6)
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
    assert (g["size"], features(g)) == (None, ["erase_4k", "sfdp"])
    assert "erase-block-size=4096" in g["flags"]
    assert set(ops(g)) == {"RDID", "RDSFDP"}
    # Two boards with the same node are one record, which names the other.
    # Its table is JESD216's first: nine DWORDs, no page size.
    f = r["MX25L3233F"]
    assert (f["size"], f["page_size"]) == (None, None)
    assert (Record.from_json(f).size, Record.from_json(f).page_size) == (4 << 20, None)
    assert f["notes"] == ["Also in boards/particle/boron/dts/mesh_feather.dtsi:21"]


def zephyr_board(tmp_path: Path, nodes: str) -> list[record.Record]:
    """The records of a board file holding ``nodes``."""
    bindings = ZEPHYR / zephyr.BINDINGS_DIR
    write(
        tmp_path,
        {
            "boards/x/x.dts": f"/dts-v1/;\n&spi0 {{\n{nodes}\n}};\n",
            **{f"{zephyr.BINDINGS_DIR}/{p.name}": p.read_text() for p in bindings.iterdir()},
        },
    )
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
    assert features(r) == ["4byte_addr", "4byte_opcodes", "fast_read", "lock"]
    assert set(ops(r)) == {"RDID", "READ_1_1_1_FAST", "RDFSR", "EN4B"}
    # enter-4byte-addr is BFPT DW16[31:24]: 0x01 is 0xb7 alone.
    assert r["four_byte_modes"] == ["en4b"]
    assert r["via"]["four_byte_modes:en4b"] == "enter-4byte-addr=0x1"
    assert not any(f.startswith("enter-4byte-addr") for f in r["flags"])
    assert "has-lock=0x1c" in r["flags"]
    # The wake-up sequence's three times, from one token; no flag.
    assert r["timings"] == {
        "dpd_exit": {"maximum": 30000},
        "dpd_min_time": {"minimum": 30000},
        "dpd_wake_pulse": {"minimum": 20},
    }
    assert r["via"]["timings"] == "dpd-wakeup-sequence=<30000>, <20>, <30000>"
    assert not [f for f in r["flags"] if "dpd" in f]


def test_zephyr_times(tmp_path: Path) -> None:
    recs = by_name(
        zephyr_board(
            tmp_path,
            """flash@0 {
                compatible = "jedec,spi-nor";
                jedec-id = [c2 20 16];  /* mx25l3233f */
                has-dpd;
                t-enter-dpd = <10000>;
                t-exit-dpd = <100000>;
                t-reset-recovery = <0>;
            };
            flash@1 {
                compatible = "jedec,spi-nor";
                jedec-id = [c2 28 17];  /* mx25r6435f */
                has-dpd;
                t-enter-dpd = <0>;
                dpd-wakeup-sequence = <30000 20 35000>;
            };
            flash@2 {
                compatible = "jedec,nor";
                jedec-id = [c2 84 37];  /* mx25uw6345g */
                t-reset-pulse = <10000>;
                t-reset-recovery = <35000>;
            };""",
        )
    )
    # has-dpd: DP and RDPD; the properties' times, by the binding's words.
    m = recs["MX25L3233F"]
    given = {o["op"]: o["via"] for o in m["opcodes"]}
    assert (given["DP"], given["RDPD"]) == ("has-dpd", "has-dpd")
    assert m["timings"] == {"dpd_enter": {"maximum": 10000}, "dpd_exit": {"maximum": 100000}}
    assert m["via"]["timings.dpd_exit"] == "t-exit-dpd=100000"
    assert "timings.reset_recovery" not in m["via"]  # 0 is not given
    # A wake-up sequence: no RDPD, and the release is its tRDP.
    r = recs["MX25R6435F"]
    assert {o["op"] for o in r["opcodes"]} >= {"DP"}
    assert "RDPD" not in {o["op"] for o in r["opcodes"]}
    assert "dpd_enter" not in r["timings"]
    assert r["timings"]["dpd_exit"] == {"maximum": 35000}
    u = recs["MX25UW6345G"]
    assert u["timings"] == {"reset_pulse": {"minimum": 10000}, "reset_recovery": {"maximum": 35000}}


def test_zephyr_board_margins_are_not_the_parts(tmp_path: Path) -> None:
    # The b_m2mem shield's reset line powers the module: its reset times
    # are the rail's, not read, and a note says why.
    bindings = ZEPHYR / zephyr.BINDINGS_DIR
    node = """/dts-v1/;
&spi0 { flash@0 {
    compatible = "jedec,nor";
    jedec-id = [c2 85 3a];  /* mx25lm51245 */
    t-reset-pulse = <5000000>;
    t-reset-recovery = <10000000>;
}; };
"""
    write(
        tmp_path,
        {
            "boards/shields/st_b_m2mem_pack1/x.overlay": node,
            **{f"{zephyr.BINDINGS_DIR}/{p.name}": p.read_text() for p in bindings.iterdir()},
        },
    )
    (r,) = zephyr.extract(tmp_path)
    assert r["timings"] == {}
    assert any(n.startswith("t-reset-pulse=5000000 not read: ") for n in r["notes"])


def test_zephyr_reads_the_bindings(tmp_path: Path) -> None:
    # A property the extractor maps that its binding no longer declares
    # stops the build.
    binding = ZEPHYR / zephyr.BINDINGS_DIR / "jedec,spi-nor-common.yaml"
    write(
        tmp_path,
        {
            f"{zephyr.BINDINGS_DIR}/{p.name}": p.read_text()
            for p in (ZEPHYR / zephyr.BINDINGS_DIR).iterdir()
        },
    )
    renamed = binding.read_text().replace("  t-exit-dpd:", "  t-exit-dpd-ns:")
    write(tmp_path, {f"{zephyr.BINDINGS_DIR}/{binding.name}": renamed})
    with pytest.raises(ValueError, match="no t-exit-dpd of type int"):
        zephyr.extract(tmp_path)


@pytest.mark.parametrize(
    ("value", "modes", "claims", "noted"),
    [
        # Every way in JESD216 gives, and bit 5: dedicated 4-byte opcodes, a
        # claim, not a way in.
        ("<0x7f>", ["always_4b", "brwr", "en4b", "nv_cr", "wrear", "wren_en4b"], True, False),
        ("<0x02>", ["wren_en4b"], False, False),
        # 0 and 0xff say nothing (spi_nor_set_address_mode).
        ("<0x00>", [], False, False),
        ("<0xff>", [], False, False),
        # p2d.dts gives the GD25LE255E EN4B's opcode: bit 7 is reserved.
        ("<0xb7>", [], False, True),
    ],
)
def test_zephyr_enter_4byte_addr(
    tmp_path: Path, *, value: str, modes: list[str], claims: bool, noted: bool
) -> None:
    (r,) = zephyr_board(
        tmp_path,
        f"""gd25le255e: memory@0 {{
            compatible = "nordic,qspi-nor";
            jedec-id = [c8 60 19];
            enter-4byte-addr = {value};
        }};""",
    )
    assert r["four_byte_modes"] == modes
    assert ("4byte_opcodes" in r["features"]) is claims
    # A byte giving several ways in is their via as a whole.
    if len(modes) > 1:
        assert r["via"]["four_byte_modes"] == f"enter-4byte-addr={value.strip('<>')}"
    assert any("not read" in n for n in r["notes"]) is noted


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
    assert features(n) == ["4byte_addr", "dual_read"]
    assert set(ops(n)) == {"RDID", "READ_1_1_2", "PP_1_1_1"}
    assert features(s) == ["fast_read", "quad_pp"]
    assert o["vendor"] == "infineon"
    assert features(o) == ["4byte_addr", "octal_dtr_pp", "octal_dtr_read", "octal_read"]
    # The Renesas OSPI driver sends the command with no write enable.
    assert o["four_byte_modes"] == ["en4b"]
    assert o["via"]["four_byte_modes:en4b"] == "enter-4byte-command=0xb7"
    assert ops(o)["EN4B"] == (0xB7, "4-byte mode en4b")
    assert features(q) == ["erase_4k", "quad_read"]


def test_zephyr_sfdp_disagreements(tmp_path: Path) -> None:
    # The nRF52840 DK's MX25R6435F table (8 MiB, 256-byte pages), under a
    # wrong size and page size: once where spi_nor's page-size is the
    # part's page, once where the driver's own setting (the MAX32 SPIXF
    # driver's flash layout page).
    table = """sfdp-bfp = [e5 20 f1 ff ff ff ff 03 44 eb 08 6b 08 3b 04 bb
                        ee ff ff ff ff ff 00 ff ff ff 00 ff 0c 20 0f 52
                        10 d8 00 ff 23 72 f5 00 82 ed 04 cc 44 83 68 44
                        30 b0 30 b0 f7 c4 d5 5c 00 be 29 ff f0 d0 ff ff];"""
    r, m = zephyr_board(
        tmp_path,
        f"""mx25r6435f@0 {{
            compatible = "jedec,spi-nor";
            jedec-id = [c2 28 17];
            {table}
            size = <DT_SIZE_M(16)>;
            page-size = <4096>;
        }};
        mx25r6435f@1 {{
            compatible = "adi,max32-spixf-nor";
            jedec-id = [c2 28 17];
            {table}
            page-size = <4096>;
        }};""",
    )
    # What the node states and the table does not is stored, and is the
    # record's value; the two are disagreements, not notes.
    assert (r["size"], r["page_size"]) == (2 << 20, 4096)
    assert r["notes"] == []
    loaded = Record.from_json(r)
    assert (loaded.size, loaded.page_size) == (2 << 20, 4096)
    assert loaded.sfdp_disagreements() == (
        ("size", 2 << 20, 8 << 20),
        ("page_size", 4096, 256),
    )
    # The table's erase types are laid over the record's own size.
    assert [e.to_json() for e in loaded.erasers] == [
        {"opcode": 0x20, "blocks": [[4096, 512]]},
        {"opcode": 0x52, "blocks": [[32768, 64]]},
        {"opcode": 0xD8, "blocks": [[65536, 32]]},
    ]
    assert loaded.sector_size == 65536
    # The driver's page-size is a flag, and the part's page is the table's.
    assert (m["page_size"], "page-size=4096" in m["flags"]) == (None, True)
    assert Record.from_json(m).page_size == 256
    assert Record.from_json(m).sfdp_disagreements() == ()


def test_zephyr_quad_enable_requirement(tmp_path: Path) -> None:
    table = """sfdp-bfp = [e5 20 f1 ff ff ff ff 03 44 eb 08 6b 08 3b 04 bb
                        ee ff ff ff ff ff 00 ff ff ff 00 ff 0c 20 0f 52
                        10 d8 00 ff 23 72 f5 00 82 ed 04 cc 44 83 68 44
                        30 b0 30 b0 f7 c4 d5 5c 00 be 29 ff f0 d0 ff ff];"""
    a, b, c, d = zephyr_board(
        tmp_path,
        f"""gd25q16@0 {{
            compatible = "nordic,qspi-nor";
            jedec-id = [c8 40 15];
            quad-enable-requirements = "S2B1v1";
        }};
        sst26vf064b@1 {{
            compatible = "jedec,spi-nor";
            jedec-id = [bf 26 43];
            quad-enable-requirements = "S1B6";
            requires-ulbpr;
        }};
        mx25r6435f@2 {{
            compatible = "nordic,qspi-nor";
            jedec-id = [c2 28 17];
            {table}
            quad-enable-requirements = "S1B6";
        }};
        mx25r6435f@3 {{
            compatible = "nordic,qspi-nor";
            jedec-id = [c2 28 17];
            {table}
            quad-enable-requirements = "S2B1v5";
        }};""",
    )
    # The QSPI driver's requirement: stored; the bit and the register
    # operations it says are derived.
    assert a["quad_enable_requirement"] == "S2B1v1"
    assert a["via"] == {"quad_enable_requirement": "quad-enable-requirements=S2B1v1"}
    loaded = Record.from_json(a)
    assert str(loaded.quad_enable) == "SR2 bit 1"
    assert "quad_read" in features(a)
    assert ops(a)["WRSR_16"] == (0x01, "quad enable requirement S2B1v1")
    # spi_nor.c (jedec,spi-nor) ignores it: a flag. requires-ulbpr: ULBPR.
    assert b["quad_enable_requirement"] is None
    assert "quad-enable-requirements=S1B6" in b["flags"]
    assert ops(b)["ULBPR"] == (0x98, "requires-ulbpr")
    # The table's own requirement (S1B6) is derived, not stored again; one
    # that differs is the node's, and a disagreement with its table.
    assert c["quad_enable_requirement"] is None
    assert c["via"]["quad_enable_requirement"] == "quad-enable-requirements=S1B6"
    assert "quad-enable-requirements=S1B6" not in c["flags"]
    assert Record.from_json(c).quad_enable_requirement == "S1B6"
    assert d["quad_enable_requirement"] == "S2B1v5"
    assert Record.from_json(d).sfdp_disagreements()[:1] == (
        ("quad_enable_requirement", "S2B1v5", "S1B6"),
    )


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
    assert (r["size"], r["page_size"]) == (None, None)
    assert Record.from_json(r).size == 32 << 20
    assert features(r) == ["4byte_addr", "erase_4k", "sfdp"]


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
    # No page size: INFO()'s 256 is the model's, for every part.
    assert (a["id"], a["ext_id"], a["size"], a["page_size"], sector(a)) == (
        "1f6601",
        None,
        128 << 10,
        None,
        32 << 10,
    )
    assert a["erasers"] == [
        {"opcode": 0x20, "blocks": [[4096, 32]]},
        {"opcode": 0xD8, "blocks": [[32 << 10, 4]]},
    ]
    # Its 0xd8 sectors are 32 KiB; the fast read is the model's for every
    # part, so implies nothing.
    assert features(a) == ["erase_32k", "erase_4k"]
    assert {"READ_1_1_1", "READ_1_1_1_FAST", "PP_1_1_1"} <= assumed(a)
    # The 4 KiB eraser gives BE_4K, so ER_4K, its token, is the eraser's via.
    assert a["flags"] == []
    assert a["via"] == {"erasers:0x20": "ER_4K"}
    assert a["sfdp"] is None
    assert ops(a) == {
        "RDID": (0x9F, "id read (rdid)"),
        "READ_1_1_1": (0x03, "m25p80 decodes it for every part"),
        "READ_1_1_1_FAST": (0x0B, "m25p80 decodes it for every part"),
        "PP_1_1_1": (0x02, "m25p80 decodes it for every part"),
        "BE_4K": (0x20, "eraser: 32 x 4096"),
        "SE": (0xD8, "eraser: 4 x 32768"),
        "CHIP_ERASE": (0xC7, "BULK_ERASE: m25p80 decodes it for every part"),
        "CHIP_ERASE_ALT": (0x60, "BULK_ERASE_60: m25p80 decodes it for every part"),
    }

    # The EEPROMs: no id, a byte-sized "sector", the block comment is not a heading.
    e = r["AT25128A-NONJEDEC"]
    assert e["vendor"] == "Atmel"
    assert e["id"] is None
    assert e["id_method"] is None
    assert e["size"] == 128 << 10
    assert sector(e) is None
    assert e["erasers"] is None
    assert features(e) == ["no_erase"]  # fast read is the model's, for every part
    assert set(ops(e)) == {"READ_1_1_1", "READ_1_1_1_FAST", "PP_1_1_1"}

    # INFO6: a three-byte ext_id; a dump, which the record stores and
    # derives its reads, erasers and size from: INFO's size and erasers are
    # the dump's, so are not stored; its tokens are the via of the erasers
    # the dump gives, which the entry states too.
    m = r["MX25L25635E"]
    assert m["vendor"] == "Macronix"
    assert (m["id"], m["ext_id"]) == ("c22019", "c22019")
    assert (m["size"], m["erasers"]) == (None, None)
    # INFO's 256 is the model's default; the JESD216 (1.0) table has none.
    assert m["page_size"] is None
    assert Record.from_json(m).size == 32 << 20
    assert m["flags"] == []
    assert m["via"] == {
        "erasers:0x20": "ER_4K",
        "erasers:0x52": "ER_32K",
        "sfdp": ".sfdp_read = m25p80_sfdp_mx25l25635e",
    }
    assert m["sfdp"] is not None
    assert m["sfdp"].startswith("53464450000101ff")
    # The dump implies every one: none is stored.
    assert features(m) == [
        "4byte_addr",
        "dual_read",
        "erase_32k",
        "erase_4k",
        "erase_64k",
        "quad_read",
        "sfdp",
    ]
    assert m["features"] == []
    # SFDP gives no sign of fast read: the model's, for every part, is all.
    fast = next(o for o in m["opcodes"] if o["op"] == "READ_1_1_1_FAST")
    assert fast == {
        "op": "READ_1_1_1_FAST",
        "via": "m25p80 decodes it for every part",
        "assumed": True,
    }
    # Read 0x03 a part with a BFPT has: its own, from the dump.
    assert "READ_1_1_1" not in [o["op"] for o in m["opcodes"]]
    assert ops(m)["READ_1_1_1"] == (0x03, "SFDP implied: a part with a BFPT supports read 0x03")
    assert ops(m)["RDSFDP"] == (0x5A, "SFDP the table itself")
    assert ops(m)["READ_1_4_4"] == (0xEB, "SFDP BFPT 1-4-4 fast read: 2 mode + 4 wait clocks")
    assert ops(m)["BE_32K"] == (0x52, "SFDP BFPT erase type 2: 32768 B")
    # No SFDP operation is stored: only the model's every-part defaults.
    assert {o["op"] for o in m["opcodes"]} == assumed(m)
    assert m["notes"] == []

    # Flags for the status register layout; the multi-line heading before Spansion.
    n = r["N25Q256A"]
    assert n["vendor"] == "Micron"
    assert n["via"] == {
        "erasers:0x20": "ER_4K",
        "protection.bp3": "HAS_SR_BP3_BIT6",
        "sfdp": ".sfdp_read = m25p80_sfdp_n25q256a",
    }
    # The model's BP0-2 are every part's, and its TB bit 5 every HAS_SR_TB
    # part's: only BP3 at bit 6 is the part's own, which implies lock.
    assert n["protection"] == {"bp3": {"register": "sr1", "bit": 6}}
    assert n["flags"] == ["HAS_SR_TB"]
    assert n["features"] == []
    assert "lock" in features(n)
    assert n["sfdp"] is not None
    assert n["quad_enable"] is None
    assert r["S25SL032P"]["vendor"] == "Spansion"
    assert r["S25SL032P"]["ext_id"] == "4d00"
    assert r["S25FL016K"]["vendor"] == "Spansion"  # filed there, with a Winbond id
    assert r["S25FL016K"]["id"] == "ef4015"

    # INFO_STACKED: a die count, and only two of the three ext_id bytes.
    t = r["MT35XU01G"]
    assert t["ext_id"] == "4100"
    assert "INFO_STACKED keeps 2 bytes of the ext_id 0x104100" in t["notes"]
    # The die count is the dies; the die erase layout is theirs, derived.
    assert (t["dies"], t["via"]["dies"]) == (2, "die_cnt=2")
    assert not [f for f in t["flags"] if f.startswith("die_cnt")]
    assert ops(t)["DIE_ERASE"] == (0xC4, "DIE_ERASE: INFO_STACKED parts")
    loaded = Record.from_json(t)
    assert loaded.size is not None  # from its SFDP tables
    die = [e for e in loaded.erasers if e.opcode == 0xC4]
    assert [(b.size, b.count) for e in die for b in e.blocks] == [(loaded.size // 2, 2)]
    assert ops(t)["BE_32K_4B"] == (0x5C, "SFDP 4BAIT erase type 3: 32768 B, 4-byte address")
    assert sector(t) == 128 << 10
    assert "erase_64k" not in features(t)
    assert r["N25Q00"]["dies"] == 4
    assert r["N25Q00"]["ext_id"] == "1000"
    assert r["N25Q00"]["sfdp"] is None

    w = r["W25Q512JV"]
    assert w["vendor"] == "Winbond"
    assert {"4byte_opcodes", "qpi", "quad_pp", "sfdp"} <= set(features(w))
    assert ops(w)["READ_1_4_4_4B"] == (0xEC, "SFDP 4BAIT bit 5: fast read 1-4-4, 4-byte address")
    assert ops(w)["EN4B"][0] == 0xB7
    assert ops(w)["READ_4_4_4"] == (0xEB, "SFDP BFPT 4-4-4 fast read: 2 mode + 0 wait clocks")
    assert not [note for note in w["notes"] if note.startswith("SFDP")]

    c = r["25CSM04"]
    assert c["vendor"] == "Microchip"
    assert (c["id"], c["ext_id"]) == ("29cc00", "0100")
    # No fast read: the model decodes it for every part.
    assert features(c) == ["erase_64k"]


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


@pytest.mark.parametrize(
    ("mask", "bit"),
    [
        ("0x40", {"register": "sr1", "bit": 6}),
        ("0x00000400", {"register": "sr2", "bit": 2}),
        ("0x00000200", None),  # the template's
        ("0", None),
        ("0x80", None),  # SR1 bit 7 is the status register protect bit
        ("0x20", None),  # SR1 bit 5, a block-protect bit (EN25QH256's)
    ],
)
def test_dediprog_quad_enable(tmp_path: Path, mask: str, bit: dict[str, object] | None) -> None:
    (w,) = dediprog_chip(tmp_path, QEbitAddr=mask)
    assert w["quad_enable"] == bit
    assert ("quad_enable" in w["via"]) is (bit is not None)
    # An SR1 bit other than 6 is left out with a note.
    left_out = mask in ("0x80", "0x20")
    assert any(n.startswith(f"QEbitAddr={mask} left out") for n in w["notes"]) is left_out


@pytest.mark.parametrize(
    ("alternative", "jedec", "legacy", "noted"),
    [
        ("0x17", None, [["res1", "17"]], False),  # one byte: RES's signature
        ("0xEF17", None, [["rems", "ef17"]], False),  # two: REMS's maker and part
        ("0x18", None, [["res1", "18"]], False),  # the id's last byte, still RES
        ("0xEF4018", None, [], False),  # a copy of the id
        ("0x4018", None, [], False),  # its last two bytes
        ("0x", None, [], False),
        ("0x8E4018", None, [], True),  # three bytes: no legacy id
        ("0x8C", "0x8C2016", [], True),  # ESMT's maker byte, a template
        ("0x15", "0x898912", [], True),  # Intel's S33 template
        ("0x07", "0x621600", [], True),  # Sanyo's RES answers two bytes
    ],
)
def test_dediprog_legacy_ids(
    tmp_path: Path, *, alternative: str, jedec: str | None, legacy: list[list[str]], noted: bool
) -> None:
    attrs = {"AlternativeID": alternative}
    if jedec is not None:
        attrs |= {"JedecDeviceID": jedec, "UniqueID": jedec}
    (w,) = dediprog_chip(tmp_path, **attrs)
    assert w["legacy_ids"] == legacy
    assert any(n.startswith(f"AlternativeID={alternative} left out") for n in w["notes"]) is noted
    assert not any(f.startswith("AlternativeID") for f in w["flags"])


def test_dediprog_wrong_legacy_ids_are_left_out(tmp_path: Path) -> None:
    # The XM25QH128A answers REMS with 20 17 (its datasheet), not 0x2016.
    (w,) = dediprog_chip(tmp_path, TypeName="XM25QH128A", AlternativeID="0x2016")
    assert w["legacy_ids"] == []
    assert any("answers REMS with 20 17" in n for n in w["notes"])
    # The M25PX parts give no RES signature.
    (m,) = dediprog_chip(tmp_path, TypeName="M25PX80", AlternativeID="0x13")
    assert m["legacy_ids"] == []
    assert any("no signature" in n for n in m["notes"])


def test_dediprog_unique_id_in_rems_form_is_a_legacy_id(tmp_path: Path) -> None:
    # Eon's EN25P20 gives UniqueID 0x1C11, its REMS answer (datasheet Table 5):
    # a legacy id, not a flag; a three-byte UniqueID that differs from the id
    # stays a flag.
    (e,) = dediprog_chip(tmp_path, AlternativeID=None, UniqueID="0x1C11")
    assert e["legacy_ids"] == [["rems", "1c11"]]
    assert not any(f.startswith("UniqueID") for f in e["flags"])
    (o,) = dediprog_chip(tmp_path, AlternativeID=None, UniqueID="0xEF4017")
    assert o["legacy_ids"] == []
    assert "UniqueID=0xEF4017" in o["flags"]
    # The same id twice (AlternativeID and UniqueID) is one.
    (t,) = dediprog_chip(tmp_path, AlternativeID="0x1C11", UniqueID="0x1C11")
    assert t["legacy_ids"] == [["rems", "1c11"]]


@pytest.mark.parametrize(("voltage", "mv"), [("1.2V", 1200), ("2.5V", 2500), ("1.8V", 1800)])
def test_dediprog_supply(tmp_path: Path, voltage: str, mv: int) -> None:
    (w,) = dediprog_chip(tmp_path, Voltage=voltage)
    assert (w["supply_mv"], w["voltage"]) == (mv, None)


def test_dediprog_chip_erase_time_of_zero_is_not_given(tmp_path: Path) -> None:
    (w,) = dediprog_chip(tmp_path, ChipEraseTime="0")
    assert w["timings"] == {}
    assert "timings.chip_erase" not in w["via"]


@pytest.mark.parametrize(
    ("attrs", "hz", "note"),
    [
        ({"Clock": "133 MHz"}, 133_000_000, None),
        ({"Clock": None, "clock": "104Mhz"}, 104_000_000, None),
        ({"Clock": "33/100MHz"}, None, "Clock=33/100MHz not read: two clocks"),
        ({"Clock": "166Mbit"}, None, "Clock=166Mbit not read: not a clock in MHz"),
        ({"Clock": "166"}, None, "Clock=166 not read: not a clock in MHz"),
        ({"Clock": "416MHz"}, None, "Clock=416MHz not read: the 104 MHz quad read's"),
    ],
)
def test_dediprog_clock(
    tmp_path: Path, attrs: dict[str, str | None], hz: int | None, note: str | None
) -> None:
    (w,) = dediprog_chip(tmp_path, **attrs)
    assert w["listed_clock_hz"] == hz
    if note is None:
        assert not [n for n in w["notes"] if "not read" in n]
    else:
        assert any(n.startswith(note) for n in w["notes"])
        assert "listed_clock_hz" not in w["via"]


def test_dediprog_protect_mask_is_no_layout(tmp_path: Path) -> None:
    # ProtectBlockMask is the bits the programmer clears, not where each
    # role is: lock stays a claim, and there is no layout.
    (w,) = dediprog_chip(tmp_path)
    assert w["protection"] is None
    assert w["features"] == ["lock", "qpi"]


def test_dediprog(tmp_path: Path) -> None:
    recs = dediprog.extract(dediprog_tree(tmp_path))
    r = by_name(recs)
    w = r["W25Q128FV"]
    assert (w["line"], w["vendor"], w["id"], w["id_method"]) == (37, "Winbond", "ef4018", "rdid")
    # 0xd8 has no layout, and gives no sector size: BlockSizeInByte is a
    # template's 64 KiB. Its 256-byte PageSizeInByte is the template's too.
    assert (w["size"], w["page_size"], sector(w)) == (16 << 20, None, None)
    # The chip erase's layout is derived from the size.
    assert (w["erasers"], erasers(w)) == (None, [{"opcode": 0xC7, "blocks": [[16 << 20, 1]]}])
    # Only the single-line read and program of the packed words.
    assert set(ops(w)) == {"RDID", "READ_1_1_1_FAST", "PP_1_1_1", "SE", "CHIP_ERASE"}
    assert ops(w)["READ_1_1_1_FAST"] == (0x0B, "ReadCmd=0x006B3B0B")
    assert features(w) == ["fast_read", "lock", "qpi"]
    assert "ProgramIOMethod=SPQD_RSWQW" in w["flags"]
    # Voltage is the supply dpcmd powers the part at: a field, not a flag.
    assert w["supply_mv"] == 3300
    assert not any(f.startswith(("Voltage", "AlternativeID")) for f in w["flags"])
    assert w["via"] == {
        "feature:lock": "ProtectBlockMask=0x9C",
        "feature:qpi": "QPIEnable",
        "listed_clock_hz": "Clock=75MHz",
        "timings.chip_erase": "ChipEraseTime=200",
    }
    # ChipEraseTime is seconds, its bound not said; Clock one clock.
    assert w["timings"] == {"chip_erase": {"unspecified": 200 * 10**9}}
    assert w["listed_clock_hz"] == 75_000_000
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
    # The entry names its id command, 0xaf, which is no operation here: it
    # is the id method's provenance, and no RDID is derived for it.
    assert "RDID" not in ops(mt)
    assert mt["via"]["id_method"] == "RDIDCommand=0xAF"
    assert "RDIDCommand=0xAF" not in mt["flags"]
    # DieSizeInKByte, half the chip: two dies, whose die erase layout is
    # derived from them, not stored.
    assert (mt["dies"], mt["via"]["dies"]) == (2, "DieSizeInKByte=65536")
    assert mt["erasers"] is None
    assert ops(mt)["DIE_ERASE"] == (0xC4, "EraseCmd=0x00C4D800")
    die = Eraser(0xC4, (EraseBlock(64 << 20, 2),))
    assert die in Record.from_json(mt).erasers
    assert "RDID" in ops(r["MT25TL256B ( for one die)"])
    # A 64 KiB block on a 32 KiB part is no layout, and no sector size.
    cd = r["IS25CD025"]
    assert (erasers(cd), sector(cd)) == (
        [{"opcode": 0xC7, "blocks": [[32 << 10, 1]]}],
        None,
    )
    # 0x20 erases the 4 KiB sectors; 4-byte opcodes above 16 MiB.
    en = r["EN35SXR256A"]
    assert {"opcode": 0x20, "blocks": [[4096, 8192]]} in en["erasers"]
    assert sector(en) is None  # a 4 KiB eraser is no sector
    assert {"4byte_addr", "4byte_opcodes", "erase_4k"} <= set(features(en))
    # SPI NAND: the dummy byte, where Dediprog reads one, is not the id.
    n = r["W25N01GVXXIG"]
    assert (n["type"], n["id"], n["id_method"]) == ("nand", "efaa21", "rdid_opcode_dummy")
    assert (n["size"], n["page_size"], sector(n)) == (128 << 20, 2048, 128 << 10)
    # Its read-id is SPI NAND's, from its id method: none is stored.
    assert n["opcodes"] == []
    assert "NAND_RDID_DUMMY" in ops(n)
    # SpareSizeInByte's high half, the whole spare area of a page.
    assert n["oob_size"] == 64
    # Its block erase is over BlockSizeInByte, which gives its sector size.
    assert (n["erasers"], n["features"]) == ([{"opcode": 0xD8, "blocks": [[128 << 10, 1024]]}], [])
    assert (r["GD5F1GQ4UC"]["id"], r["GD5F1GQ4UC"]["id_method"]) == ("c8b148", "rdid_opcode")
    assert r["MK60N1GAL"]["id"] == "a791"  # 0xA791, read as three bytes
    # SupportLUT: the bad block lookup table's swap and read.
    lut = r["W25N01JWXXIG"]
    assert {o["op"]: o["via"] for o in lut["opcodes"]} == {
        "NAND_BBM_SWAP": "SupportLUT=true",
        "NAND_READ_BBM_LUT": "SupportLUT=true",
    }
    assert ops(lut)["NAND_BBM_SWAP"][0] == 0xA1
    assert len(recs) == 30


def test_dediprog_classes(tmp_path: Path) -> None:
    r = by_name(dediprog.extract(dediprog_tree(tmp_path)))
    # DataFlash: its SPI NOR template is not taken, only its id and size.
    at45 = r["AT45DB642D"]
    assert (at45["id"], at45["size"], at45["page_size"], sector(at45)) == (
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
    assert (sector(sst), features(sst)) == (32 << 10, ["erase_32k", "fast_read", "lock"])
    # On an AT25F, 0x52 erases SectorSizeInByte, where that is not the
    # template's 4096 (the AT25F2048's 0x52 erases 64 KiB).
    at25f = r["AT25F1024A"]
    assert {"opcode": 0x52, "blocks": [[32 << 10, 4]]} in at25f["erasers"]
    assert sector(at25f) == 32 << 10
    at25f2048 = r["AT25F2048"]
    assert (erasers(at25f2048), sector(at25f2048)) == (
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
    # The id command the entry names stays the stated operation.
    assert ops(wf)["RDID"] == (0x9F, "a JEDEC id under RDIDCommand=0x90")
    assert wf["via"]["id_method"] == "RDIDCommand=0x90"
    # Sanyo's parts answer 0x9f with their two id bytes, repeated.
    assert (r["LE25FU106B"]["id"], r["LE25FU106B"]["id_method"]) == ("621d", "res2")
    assert (r["LE25FU406B"]["id"], r["LE25FU406B"]["id_method"]) == ("621e", "res2")
    # Dediprog sends them 0x9f, which they store; RES is not derived for them.
    assert set(ops(r["LE25FU106B"])) & {"RDID", "RES"} == {"RDID"}
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
    assert (w["size"], w["page_size"], sector(w)) == (128 << 20, 2048, 128 << 10)
    # SNAND_MEMORG_1G_2K_64: the spare area, planes and dies are fields.
    assert (w["oob_size"], w["planes"], w["dies"]) == (64, 1, 1)
    # Each I/O mode of its read-from-cache and program-load caps is an
    # operation, with the dummy clocks its SNAND_OP gives; they imply the
    # capabilities, so none is claimed, and the caps are their via.
    assert w["features"] == []
    # The one-line read (0x0b) and load (0x02) every table has are the
    # driver's defaults, so imply no fast_read.
    assert features(w) == ["dual_read", "quad_pp", "quad_read"]
    assert w["flags"] == []
    assert w["notes"] == []
    stated = {o["op"]: (o["via"], o["dummy_clocks"]) for o in w["opcodes"] if "assumed" not in o}
    rd, pl = "cap_rd=snand_cap_read_from_cache_quad", "cap_pl=snand_cap_program_load_x4"
    assert stated == {
        "NAND_READ_CACHE_1_1_2": (rd, 8),
        "NAND_READ_CACHE_1_2_2": (rd, 4),
        "NAND_READ_CACHE_1_1_4": (rd, 8),
        "NAND_READ_CACHE_1_4_4": (rd, 4),
        "NAND_PROGRAM_LOAD_1_1_4": (pl, 0),
    }
    defaults = {"NAND_READ_CACHE_1_1_1_FAST", "NAND_PROGRAM_LOAD_1_1_1"}
    assert assumed(w) == set(mediatek.DEFAULTS) | defaults
    # The size is the main area of every die; the spare area is not in it.
    m = r["W25M02GV"]
    assert m["size"] == 256 << 20
    # Two dies, selected with Winbond's 0xc2 and the die.
    assert m["dies"] == 2
    (select,) = [o for o in m["opcodes"] if o["op"] == "NAND_DIE_SELECT"]
    assert select["via"] == "select_die=mtk_snand_winbond_select_die"
    assert m["die_select_bit"] is None
    assert m["flags"] == []
    # Planes are not counted again: a two-plane part's blocks are all its
    # blocks.
    t = r["MT29F2G01AAAED"]
    assert (t["size"], sector(t)) == (256 << 20, 128 << 10)
    assert t["planes"] == 2
    # Read from cache on one, two or four lines; program load on one only.
    assert features(t) == ["dual_read", "quad_read"]
    assert "NAND_PROGRAM_LOAD_1_1_4" not in ops(t)
    d = r["MT29F4G01ADAGD"]
    assert d["size"] == 512 << 20
    # Micron's die select: bit 6 of feature 0xd0, which the driver sets
    # whatever the die (an upstream bug, noted).
    assert (d["dies"], d["planes"]) == (2, 2)
    assert d["die_select_bit"] == {"register": "nand-d0", "bit": 6}
    assert d["via"]["die_select_bit"] == "select_die=mtk_snand_micron_select_die"
    assert d["notes"] == [mediatek.MICRON_SELECT_BUG]
    assert "NAND_DIE_SELECT" not in ops(d)
    # quad_q2d: its 1-4-4 read takes 2 dummy clocks.
    q2d = {o["op"]: o["dummy_clocks"] for o in r["EM73C044SNA"]["opcodes"] if "assumed" not in o}
    assert q2d["NAND_READ_CACHE_1_4_4"] == 2
    # The id method is the one the entry names.
    g = r["GD5F1GQ4UAWXX"]
    assert (g["id"], g["id_method"]) == ("c810", "rdid_opcode_addr")
    # A memory organisation written out: 128 pages per block.
    a = r["EM73C044SNA"]
    assert (a["size"], sector(a)) == (128 << 20, 256 << 10)
    # The driver takes the first entry an id matches.
    assert r["IS37SML01G1"]["notes"][0] == (
        "never used: the driver matches the entry on line 85 first"
    )
    assert r["F50L1G41A"]["notes"] == []
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
