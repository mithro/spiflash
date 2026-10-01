#define SPINOR_OP_MT_DIE_ERASE	0xc4	/* Chip (die) erase opcode */

static int micron_st_nor_two_die_late_init(struct spi_nor *nor)
{
	struct spi_nor_flash_parameter *params = nor->params;

	params->die_erase_opcode = SPINOR_OP_MT_DIE_ERASE;
	params->n_dice = 2;

	/*
	 * Unfortunately the die erase opcode does not have a 4-byte opcode
	 * correspondent for these flashes. The SFDP 4BAIT table fails to
	 * consider the die erase too. We're forced to enter in the 4 byte
	 * address mode in order to benefit of the die erase.
	 */
	return spi_nor_set_4byte_addr_mode(nor, true);
}

static const struct spi_nor_fixups mt25q01_fixups = {
	.late_init = micron_st_nor_two_die_late_init,
};

static const struct flash_info st_nor_parts[] = {
	{
		.id = SNOR_ID(0x20, 0xbb, 0x21, 0x10, 0x44, 0x00),
		.name = "mt25qu01g",
		.mfr_flags = USE_FSR,
		.fixups = &mt25q01_fixups,
	},
};

const struct spi_nor_manufacturer spi_nor_st = {
	.name = "st",
	.parts = st_nor_parts,
	.nparts = ARRAY_SIZE(st_nor_parts),
	.fixups = &micron_st_nor_fixups,
};
