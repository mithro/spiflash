#define SPINAND_MFR_MICRON		0x2c
#define MICRON_DIE_SELECT_REG	0xD0
#define MICRON_SELECT_DIE(x)	((x) << 6)

static SPINAND_OP_VARIANTS(quadio_read_cache_variants,
		SPINAND_PAGE_READ_FROM_CACHE_1S_4S_4S_OP(0, 2, NULL, 0, 0),
		SPINAND_PAGE_READ_FROM_CACHE_1S_1S_4S_OP(0, 1, NULL, 0, 0),
		SPINAND_PAGE_READ_FROM_CACHE_1S_2S_2S_OP(0, 1, NULL, 0, 0),
		SPINAND_PAGE_READ_FROM_CACHE_1S_1S_2S_OP(0, 1, NULL, 0, 0),
		SPINAND_PAGE_READ_FROM_CACHE_FAST_1S_1S_1S_OP(0, 1, NULL, 0, 0),
		SPINAND_PAGE_READ_FROM_CACHE_1S_1S_1S_OP(0, 1, NULL, 0, 0));

static SPINAND_OP_VARIANTS(x4_write_cache_variants,
		SPINAND_PROG_LOAD_1S_1S_4S_OP(true, 0, NULL, 0),
		SPINAND_PROG_LOAD_1S_1S_1S_OP(true, 0, NULL, 0));

static SPINAND_OP_VARIANTS(x4_update_cache_variants,
		SPINAND_PROG_LOAD_1S_1S_4S_OP(false, 0, NULL, 0),
		SPINAND_PROG_LOAD_1S_1S_1S_OP(false, 0, NULL, 0));

static int micron_select_target(struct spinand_device *spinand,
				unsigned int target)
{
	struct spi_mem_op op = SPINAND_OP(spinand, set_feature,
					  MICRON_DIE_SELECT_REG, spinand->scratchbuf);

	if (target > 1)
		return -EINVAL;

	*spinand->scratchbuf = MICRON_SELECT_DIE(target);

	return spi_mem_exec_op(spinand->spimem, &op);
}

static const struct spinand_info micron_spinand_table[] = {
	SPINAND_INFO("MT29F4G01ADAGD",
		     SPINAND_ID(SPINAND_READID_METHOD_OPCODE_DUMMY, 0x36),
		     NAND_MEMORG(1, 2048, 128, 64, 2048, 80, 2, 1, 2),
		     NAND_ECCREQ(8, 512),
		     SPINAND_INFO_OP_VARIANTS(&quadio_read_cache_variants,
					      &x4_write_cache_variants,
					      &x4_update_cache_variants),
		     0,
		     SPINAND_ECCINFO(&micron_8_ooblayout,
				     micron_8_ecc_get_status),
		     SPINAND_SELECT_TARGET(micron_select_target)),
};

const struct spinand_manufacturer micron_spinand_manufacturer = {
	.id = SPINAND_MFR_MICRON,
	.name = "Micron",
	.chips = micron_spinand_table,
	.nchips = ARRAY_SIZE(micron_spinand_table),
	.ops = &micron_spinand_manuf_ops,
};
