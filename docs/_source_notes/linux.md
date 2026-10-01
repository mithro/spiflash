The [Linux](https://www.kernel.org/) kernel's memory technology device
(MTD) layer has a driver for SPI NOR flash, in
{upstream}`linux:drivers/mtd/spi-nor/`, and one for SPI NAND, in
{upstream}`linux:drivers/mtd/nand/spi/`. Each keeps a table per vendor
({upstream}`winbond.c <linux:drivers/mtd/spi-nor/winbond.c>`,
{upstream}`macronix.c <linux:drivers/mtd/spi-nor/macronix.c>`, ...) of the parts it knows, so that a board's
flash works without the board naming it. With {sfsrc}`dediprog`,
{sfsrc}`rockchip`, {sfsrc}`mediatek` and {sfsrc}`imsprog`, it is one of the five
sources here with SPI NAND.

Since Linux 6.8 a SPI NOR entry is a `struct flash_info` designated
initialiser, `.id = SNOR_ID(0xef, 0x40, 0x18)`, with its size, flags and
`no_sfdp_flags`; `.name` is obsolete for new entries, which carry the part
name in a comment instead. A SPI NAND entry is a `SPINAND_INFO`, whose id
says how the part answers [read-id](../opcodes/RDID.md): straight after the
opcode, after a dummy byte, or after an address byte.

The kernel identifies a chip by its read-id answer, and matches up to six
bytes of it: it has the most recent parts, and extended ids (the bytes after
the JEDEC id) that separate variants answering the same id. For chips without
SFDP it carries capability flags (`SECT_4K`, `SPI_NOR_QUAD_READ`, ...). Parts
it reads entirely from SFDP have no size in the table: their records have
`size: null`, no erase layouts, and the [RDSFDP](../opcodes/RDSFDP.md)
operation (so the `sfdp` capability).

A part with a size is erased with 0xd8 over its `.sector_size` blocks, and
with 0x20 over 4 KiB sectors for `SECT_4K` (`spi_nor_no_sfdp_init_params()`):
its records give those as erase layouts, from which its sector size and its
erase capabilities follow. Most entries give no `.sector_size`, and the
kernel takes `SPI_NOR_DEFAULT_SECTOR_SIZE`, 64 KiB, whatever the part: that
0xd8 layout is kept as a driver default (`"assumed": true`), which gives no
sector size and no `erase_64k`, and the same goes for the 256-byte
`SPI_NOR_DEFAULT_PAGE_SIZE`, so an entry without `.page_size` has none. The
default is wrong for some parts: the SST26VF016B and SST26VF064B erase
non-uniform blocks with 0xd8, and the AT45DB081D is a DataFlash. An entry's
own `.sector_size` (`SZ_256K` for the S25FL512S) and `SECT_4K` are its
claims. The read, fast read (a board's devicetree choice, `m25p,fast-read`),
page program, chip erase and default sector erase it sets up for every part
are its driver's defaults, as are their 4-byte forms: they imply no
capability.

`SPI_NOR_HAS_LOCK` and the flags with it give a part's block-protection
bits as {upstream}`swp.c <linux:drivers/mtd/spi-nor/swp.c>` uses them: BP0
to BP2 and SRWD in SR1, BP3 for `SPI_NOR_4BIT_BP` (bit 5, or 6 with
`SPI_NOR_BP3_SR_BIT6`), TB for `SPI_NOR_HAS_TB` (bit 5, or 6 with
`SPI_NOR_TB_SR_BIT6`), CMP in SR2 for `SPI_NOR_HAS_CMP` (read with
[RDSR2](../opcodes/RDSR2.md), 0x35); with
`SPI_NOR_SWP_IS_VOLATILE` the BP bits are volatile. Where an entry's fixups
replace that locking (Atmel's global protection, the AT25FS's own scheme,
the SST26VF's block protection register, unlocked with
[ULBPR](../opcodes/ULBPR.md)) it gives no bits, and its `lock` stays a
claim. Micron's `default_init` sets `HAS_LOCK` for every Micron part, a
driver default that gives none either.

The kernel sets a part's quad enable method in its core for every part (SR2
bit 1) and in some makers' `default_init` for every part of theirs
(Macronix's and ISSI's SR1 bit 6, Micron's none), each overridden by the
part's own SFDP tables: driver defaults, which give no part a quad enable
bit. A per-part fixup does (the MX25L3255E's SR1 bit 6, the MT35XU's none),
but the GD25Q256's sets SR1 bit 6 only for a JESD216 1.0 table (the
GD25Q256C), so that entry has none. A SPI NAND entry's
`SPINAND_HAS_QE_BIT` is bit 0 of its configuration register (feature 0xb0).
Without it, `spinand_init_quad_enable()` clears that bit on every part
(core.c:1794-1802): the core's default, which says nothing of the part's
bit, so the entry gives none. On some it is wrong: XTX's XT26G0xD and
XT26Q0xD have no `SPINAND_HAS_QE_BIT`, though their datasheets' feature
0xb0 bit 0 is a QE bit that must be set for quad reads.

The fixups that set a SPI NOR part's dies (`params->n_dice`) and die erase
(`params->die_erase_opcode`) give the record's `dies` and its die erase:
micron-st.c's MT25Q, N25Q00 and MT35XU parts (4 or 2 dies, Micron's 0xc4
[DIE_ERASE](../opcodes/DIE_ERASE.md); its two-die fixup is wrong for the
MT35XU02G, which has four, the C of MT35XU02GCBA), and spansion.c's 2 Gbit S25H and
S28H parts (2 dies, the 4 their SFDP tables give corrected, with
Infineon's 0x61 [DIE_ERASE_61](../opcodes/DIE_ERASE_61.md); the smaller
parts' come from their SFDP tables alone, which Linux does not carry).
Winbond's W25Q01JV and W25Q02JV have `size / SZ_64M` dies (2 and 4, by
their density) and no die erase; the kernel selects each with 0xc2
([DIE_SELECT](../opcodes/DIE_SELECT.md)) to poll it.

An entry's `.otp = SNOR_OTP(len, n_regions, base, offset)` (core.h) is its
OTP area, `n_regions` regions of `len` bytes ([](../derived.md#otp)), the
whole macro its `via` (`base` and `offset`, where the regions are, have no
field). Only Winbond's entries have one, and `winbond_nor_late_init()` reads,
programs and erases it with the security-register commands
(`winbond_nor_otp_ops`: [RSECR](../opcodes/RSECR.md),
[PSECR](../opcodes/PSECR.md), [ESECR](../opcodes/ESECR.md)), which those
records state. Its `set_4byte_addr_mode` functions (Micron's WREN then
0xb7, micron-st.c:647; Winbond's 0xb7, then clearing the extended address
register on the way out, winbond.c:491; the core's bank register for any
other part, core.c:3037) are drivers' defaults for every part of a maker,
or every part, not an entry's, so give no way into 4-byte mode; SFDP
overrides them at run time. A comment naming a part's supply (`/* 3.3V */`, on
the Winbond SPI NAND entries) stays a note: a nominal supply, not a
programmer's setting, so not `supply_mv`.

A SPI NAND entry's `NAND_MEMORG` gives its spare area per page (`oob_size`),
planes, most bad blocks per die and dies (LUNs per target times targets),
and `NAND_ECCREQ` its ECC requirement. Its oobsize is not always the
parameter page's spare area, the record's `oob_size`
([](../derived.md#spi-nand-geometry)): the MX35LF2GE4AD's and
MX35LF4GE4AD's are the spare left with the ECC on, the W25N01KV's the spare
and its ECC parity area; those three are notes instead. Its read-from-cache, write-cache and
update-cache op variants, each file's `SPINAND_OP_VARIANTS` of the
`SPINAND_*_OP` macros in {upstream}`linux:include/linux/mtd/spinand.h`, are
its {ref}`SPI NAND operations <opcodes-nand>`, each read with its
dummy clocks (the dummy bytes the variant passes, times 8, over the dummy
phase's lines; of several variants of one operation, the most, which is the
one without a clock limit). The double transfer rate variants have no
operation here yet, nor do the continuous-read ones. The page read, program
execute and feature commands its core sends every part are driver defaults.
A part of two targets selects one with its `SPINAND_SELECT_TARGET`: the
W25M02GV with 0xc2 and the die
([NAND_DIE_SELECT](../opcodes/NAND_DIE_SELECT.md)), Micron's MT29F4G01ADAGD,
MT29F8G01ADAFD and MT29F8G01ADBFD with bit 6 of feature 0xd0 (the record's
`die_select_bit`). A part of several LUNs and one target (the W25N02JW,
the GD5F4GQ6) has dies, but no select: they are row address bits.
`USE_FSR`, `USE_CLSR` and `USE_CLPEF` are the flag status register and the
error-clearing commands its driver sends.
