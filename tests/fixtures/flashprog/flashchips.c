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
