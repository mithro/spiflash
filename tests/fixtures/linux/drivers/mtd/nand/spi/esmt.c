/* ESMT uses GigaDevice 0xc8 JECDEC ID on some SPI NANDs */
#define SPINAND_MFR_ESMT_C8			0xc8
#define SPINAND_MFR_ESMT_8C			0x8c

static SPINAND_OP_VARIANTS(read_cache_variants,
			   SPINAND_PAGE_READ_FROM_CACHE_1S_1S_4S_OP(0, 1, NULL, 0, 0),
			   SPINAND_PAGE_READ_FROM_CACHE_1S_1S_2S_OP(0, 1, NULL, 0, 0),
			   SPINAND_PAGE_READ_FROM_CACHE_FAST_1S_1S_1S_OP(0, 1, NULL, 0, 0),
			   SPINAND_PAGE_READ_FROM_CACHE_1S_1S_1S_OP(0, 1, NULL, 0, 0));

static SPINAND_OP_VARIANTS(write_cache_variants,
			   SPINAND_PROG_LOAD_1S_1S_4S_OP(true, 0, NULL, 0),
			   SPINAND_PROG_LOAD_1S_1S_1S_OP(true, 0, NULL, 0));

static SPINAND_OP_VARIANTS(update_cache_variants,
			   SPINAND_PROG_LOAD_1S_1S_4S_OP(false, 0, NULL, 0),
			   SPINAND_PROG_LOAD_1S_1S_1S_OP(false, 0, NULL, 0));


static const struct spinand_info esmt_8c_spinand_table[] = {
	SPINAND_INFO("F50L1G41LC",
		     SPINAND_ID(SPINAND_READID_METHOD_OPCODE_ADDR, 0x2C),
		     NAND_MEMORG(1, 2048, 64, 64, 1024, 20, 1, 1, 1),
		     NAND_ECCREQ(1, 512),
		     SPINAND_INFO_OP_VARIANTS(&read_cache_variants,
					      &write_cache_variants,
					      &update_cache_variants),
		     0,
		     SPINAND_ECCINFO(&f50l1g41lb_ooblayout, NULL),
		     SPINAND_USER_OTP_INFO(28, 2, &f50l1g41lb_user_otp_ops),
		     SPINAND_FACT_OTP_INFO(2, 0, &f50l1g41lb_fact_otp_ops)),
};

static const struct spinand_info esmt_c8_spinand_table[] = {
	SPINAND_INFO("F50L1G41LB",
		     SPINAND_ID(SPINAND_READID_METHOD_OPCODE_ADDR, 0x01, 0x7f,
				0x7f, 0x7f),
		     NAND_MEMORG(1, 2048, 64, 64, 1024, 20, 1, 1, 1),
		     NAND_ECCREQ(1, 512),
		     SPINAND_INFO_OP_VARIANTS(&read_cache_variants,
					      &write_cache_variants,
					      &update_cache_variants),
		     0,
		     SPINAND_ECCINFO(&f50l1g41lb_ooblayout, NULL),
		     SPINAND_USER_OTP_INFO(28, 2, &f50l1g41lb_user_otp_ops),
		     SPINAND_FACT_OTP_INFO(2, 0, &f50l1g41lb_fact_otp_ops)),
};

const struct spinand_manufacturer esmt_8c_spinand_manufacturer = {
	.id = SPINAND_MFR_ESMT_8C,
	.name = "ESMT",
	.chips = esmt_8c_spinand_table,
	.nchips = ARRAY_SIZE(esmt_8c_spinand_table),
	.ops = &esmt_spinand_manuf_ops,
};

const struct spinand_manufacturer esmt_c8_spinand_manufacturer = {
	.id = SPINAND_MFR_ESMT_C8,
	.name = "ESMT",
	.chips = esmt_c8_spinand_table,
	.nchips = ARRAY_SIZE(esmt_c8_spinand_table),
	.ops = &esmt_spinand_manuf_ops,
};
