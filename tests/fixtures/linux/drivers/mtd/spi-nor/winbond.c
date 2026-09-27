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
