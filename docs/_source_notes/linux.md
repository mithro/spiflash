The [Linux](https://www.kernel.org/) kernel's memory technology device
(MTD) layer has a driver for SPI NOR flash, in
{upstream}`linux:drivers/mtd/spi-nor/`, and one for SPI NAND, in
{upstream}`linux:drivers/mtd/nand/spi/`. Each keeps a table per vendor
({upstream}`winbond.c <linux:drivers/mtd/spi-nor/winbond.c>`,
{upstream}`macronix.c <linux:drivers/mtd/spi-nor/macronix.c>`, ...) of the parts it knows, so that a board's
flash works without the board naming it. With {sfsrc}`dediprog`,
{sfsrc}`rockchip` and {sfsrc}`imsprog`, it is one of the four sources here with
SPI NAND.

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
`size: null` and the `sfdp` feature.
