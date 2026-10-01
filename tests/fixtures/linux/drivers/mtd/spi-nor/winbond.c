#define WINBOND_NOR_OP_SELDIE	0xc2	/* Select active die */

static int winbond_nor_select_die(struct spi_nor *nor, u8 die)
{
	int ret;

	nor->bouncebuf[0] = die;

	if (nor->spimem) {
		struct spi_mem_op op = WINBOND_NOR_SELDIE_OP(nor->bouncebuf);

		spi_nor_spimem_setup_op(nor, &op, nor->reg_proto);

		ret = spi_mem_exec_op(nor->spimem, &op);
	} else {
		ret = spi_nor_controller_ops_write_reg(nor,
						       WINBOND_NOR_OP_SELDIE,
						       nor->bouncebuf, 1);
	}

	if (ret)
		dev_dbg(nor->dev, "error %d selecting die %d\n", ret, die);

	return ret;
}

static int winbond_nor_multi_die_ready(struct spi_nor *nor)
{
	int ret, i;

	for (i = 0; i < nor->params->n_dice; i++) {
		ret = winbond_nor_select_die(nor, i);
		if (ret)
			return ret;

		ret = spi_nor_sr_ready(nor);
		if (ret <= 0)
			return ret;
	}

	return 1;
}

static int
winbond_nor_multi_die_post_sfdp_fixups(struct spi_nor *nor)
{
	/*
	 * SFDP supports dice numbers, but this information is only available in
	 * optional additional tables which are not provided by these chips.
	 * Dice number has an impact though, because these devices need extra
	 * care when reading the busy bit.
	 */
	nor->params->n_dice = nor->params->size / SZ_64M;
	nor->params->ready = winbond_nor_multi_die_ready;

	return 0;
}

static const struct spi_nor_fixups winbond_nor_multi_die_fixups = {
	.post_sfdp = winbond_nor_multi_die_post_sfdp_fixups,
};

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
