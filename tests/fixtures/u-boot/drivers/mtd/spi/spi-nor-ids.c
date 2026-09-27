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
