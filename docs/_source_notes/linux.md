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
kernel takes `SPI_NOR_DEFAULT_SECTOR_SIZE`, 64 KiB: that is taken as the
entry's own, as it erases every such part with it, an entry for a part with
other blocks gives its own (`SZ_256K` for the S25FL512S), and
{sfsrc}`u-boot`'s `INFO()` table, which Linux's was, states the 64 KiB in each
entry. The read, fast read (a board's devicetree choice, `m25p,fast-read`),
page program and chip erase it sets up for every part are its driver's
defaults: they imply no capability.
