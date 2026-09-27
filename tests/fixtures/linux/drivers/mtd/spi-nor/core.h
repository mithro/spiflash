#define SPI_NOR_DEFAULT_SECTOR_SIZE SZ_64K
struct flash_info {
	u16 flags;
#define SPI_NOR_HAS_LOCK		BIT(0)
#define SPI_NOR_HAS_TB			BIT(1)
#define SPI_NOR_NO_ERASE		BIT(6)
	u8 no_sfdp_flags;
#define SPI_NOR_SKIP_SFDP		BIT(0)
#define SECT_4K				BIT(1)
#define SPI_NOR_DUAL_READ		BIT(3)
#define SPI_NOR_QUAD_READ		BIT(4)
	u8 fixup_flags;
#define SPI_NOR_4B_OPCODES		BIT(0)
};
