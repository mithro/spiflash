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
