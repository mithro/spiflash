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
{upstream}`u-boot:drivers/mtd/spi/spi-nor-core.c` would use follow.
