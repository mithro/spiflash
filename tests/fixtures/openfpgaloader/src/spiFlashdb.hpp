static std::map <uint32_t, flash_t> flash_list = {
	{0x010219, {
		/* https://www.mouser.fr/datasheet/2/196/Infineon_S25FL128SS25FL256S.pdf */
		.manufacturer = "spansion",
		.model = "S25FL256S",
		.nr_sector = 512,
		.sector_erase = true,
		.subsector_erase = false,
		.bp_len = 3,
		.quad_register = CONFR,
		.quad_mask = (1 << 1),
	}},
	{0xef4018, {
		.manufacturer = "Winbond",
		.model = "W25Q128",
		.nr_sector = 256,
		.sector_erase = true,
		.subsector_erase = true,
		.bp_len = 0,
		.quad_register = NONER,
	}},
};
