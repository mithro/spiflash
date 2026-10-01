[U-Boot](https://u-boot.org/) is the boot loader of many embedded boards,
which often boot from SPI flash. Its SPI flash driver
identifies the chip by its JEDEC [read-id](../opcodes/RDID.md) answer (and
up to three more bytes of extended id) against the table in
{upstream}`u-boot:drivers/mtd/spi/spi-nor-ids.c`. It is SPI NOR only.

U-Boot kept {sfsrc}`linux`'s pre-6.8 table format,
`INFO(name, jedec_id, ext_id, sector_size, n_sectors, flags)` (with a
two-byte extended id; `INFO6` with a three-byte one, and `INFO_NAME` with
designated fields for the odd parts, FRAM), grouped under
`CONFIG_SPI_FLASH_<VENDOR>`. It has parts {sfsrc}`linux` dropped or never
had, and like Linux it gives each part's size, page and sector size and
capability flags, from which the opcodes its
{upstream}`u-boot:drivers/mtd/spi/spi-nor-core.c` would use follow. Its
sector size is an erase layout, 0xd8 over the `INFO` sectors (and 0x20 or
0xd7 over 4 KiB ones for `SECT_4K` or `SECT_4K_PMC`). The read, fast read
(unless `SPI_NOR_NO_FR`), page program and chip erase it sets up for every
part, and the quad page program it adds for every `SPI_NOR_QUAD_READ` part,
are its driver's defaults, as are their 4-byte forms: they imply no
capability.

`SPI_NOR_HAS_LOCK` gives the block-protection bits its
{upstream}`spi-nor.h <u-boot:include/linux/mtd/spi-nor.h>` defines (BP0 to
BP2 and SRWD), as {sfsrc}`linux`'s flags do. `SPI_NOR_HAS_TB` gives no TB:
U-Boot has only `SR_TB`, bit 5, for every part, and no flag for the bit 6
of the W25Q256 and W25Q512 families, so the bit is its driver's, and the
flag stays a flag. An
`SPI_NOR_HAS_SST26LOCK` part locks with a block protection register instead:
no bits, a `lock` claim, and [ULBPR](../opcodes/ULBPR.md) to unlock it.
