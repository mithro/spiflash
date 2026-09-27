	{
		.vendor		= "Eon",
		.name		= "EN25QH128",
		.bustype	= BUS_SPI,
		.manufacture_id	= EON_ID_NOPREFIX,
		.model_id	= EON_EN25QH128,
		.total_size	= 16384,
		.page_size	= 256,
		/* supports SFDP */
		.feature_bits	= FEATURE_WRSR_WREN | FEATURE_OTP | FEATURE_QPI_38 & ~FEATURE_FAST_READ_QOUT,
		.tested		= TEST_OK_PREW,
		.probe		= PROBE_SPI_RDID,
		.block_erasers	=
		{
			{
				.eraseblocks = { {4 * 1024, 4096} },
				.block_erase = SPI_BLOCK_ERASE_20,
			}, {
				.eraseblocks = { {64 * 1024, 256} },
				.block_erase = SPI_BLOCK_ERASE_D8,
			}, {
				.eraseblocks = { {16 * 1024 * 1024, 1} },
				.block_erase = SPI_BLOCK_ERASE_C7,
			}, {
				.eraseblocks = { {4 * 1024, 2}, {8 * 1024, 1} },
				.block_erase = SPI_BLOCK_ERASE_EMULATION,
			}, {
				.eraseblocks = { {0, 0} },
				.block_erase = NULL,
			}
		},
		.voltage	= {2700, 3600},
		.reg_bits	= { .bp = {{STATUS1, 2, RW}} },
	},
	{
		.vendor		= "Spansion",
		.name		= "S25FL128S_UL Uniform 128 kB Sectors",
		.bustype	= BUS_SPI,
		.manufacture_id	= SPANSION_ID,
		.model_id	= SPANSION_S25FL128S_UL,
		.total_size	= 16384,
		.page_size	= 256,
		.tested		= { .probe = NA, .read = OK },
		.probe		= PROBE_SPI_BIG_SPANSION,
	},
	{
		.vendor		= "ST",
		.name		= "M25P05",
		.bustype	= BUS_SPI,
		.manufacture_id	= ST_ID,
		.model_id	= ST_M25P05_RES,
		.total_size	= 64,
		.probe		= PROBE_SPI_RES1,
	},
	{
		.vendor		= "ST",
		.name		= "M95320",
		.bustype	= BUS_SPI,
		.manufacture_id	= ST_ID,
		.model_id	= 0,	/* No RDID */
		.total_size	= 4,
	},
	{
		.vendor		= "Generic",
		.name		= "unknown SPI chip (RDID)",
		.bustype	= BUS_SPI,
		.manufacture_id	= GENERIC_MANUF_ID,
		.model_id	= GENERIC_DEVICE_ID,
		.total_size	= 0,
		.probe		= PROBE_SPI_RDID,
	},
	{
		.vendor		= "ENE",
		.name		= "KB9012 (EDI)",
		.bustype	= BUS_SPI,
		.total_size	= 128,
		.probe		= PROBE_EDI_KB9012,
	},
	{
		.vendor		= "AMD",
		.name		= "Am29F010",
		.bustype	= BUS_PARALLEL,
		.manufacture_id	= AMD_ID,
		.model_id	= AMD_AM29F010,
		.total_size	= 128,
	},
