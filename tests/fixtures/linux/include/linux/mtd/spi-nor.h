#define SPINOR_OP_RDID		0x9f	/* Read JEDEC ID */
#define SPINOR_OP_RDSFDP	0x5a	/* Read SFDP */
#define SPINOR_OP_READ		0x03	/* Read data bytes (low frequency) */
#define SPINOR_OP_READ_FAST	0x0b	/* Read data bytes (high frequency) */
#define SPINOR_OP_READ_1_1_2	0x3b	/* Read data bytes (Dual Output SPI) */
#define SPINOR_OP_READ_1_1_4	0x6b	/* Read data bytes (Quad Output SPI) */
#define SPINOR_OP_PP		0x02	/* Page program (up to 256 bytes) */
#define SPINOR_OP_PP_1_1_4	0x32	/* Quad page program */
#define SPINOR_OP_BE_4K		0x20	/* Erase 4KiB block */
#define SPINOR_OP_BE_4K_PMC	0xd7	/* Erase 4KiB block on PMC chips */
#define SPINOR_OP_CHIP_ERASE	0xc7	/* Erase whole flash chip */
#define SPINOR_OP_SE		0xd8	/* Sector erase (usually 64KiB) */
#define SPINOR_OP_READ_4B	0x13	/* Read data bytes (low frequency) */
#define SPINOR_OP_READ_FAST_4B	0x0c	/* Read data bytes (high frequency) */
#define SPINOR_OP_READ_1_1_2_4B	0x3c	/* Read data bytes (Dual Output SPI) */
#define SPINOR_OP_READ_1_1_4_4B	0x6c	/* Read data bytes (Quad Output SPI) */
#define SPINOR_OP_PP_4B		0x12	/* Page program (up to 256 bytes) */
#define SPINOR_OP_PP_1_1_4_4B	0x34	/* Quad page program */
#define SPINOR_OP_BE_4K_4B	0x21	/* Erase 4KiB block */
#define SPINOR_OP_SE_4B		0xdc	/* Sector erase (usually 64KiB) */
#define SPINOR_OP_BP		0x02	/* Byte program */
#define SPINOR_OP_AAI_WP	0xad	/* Auto address increment word program */
#define SPINOR_OP_RDFSR		0x70	/* Read flag status register */
#define SPINOR_OP_CLSR		0x30	/* Clear status register 1 */
#define SPINOR_OP_GBULK		0x98    /* Global Block Unlock */
#define SR_BP0			BIT(2)	/* Block protect 0 */
#define SR_BP1			BIT(3)	/* Block protect 1 */
#define SR_BP2			BIT(4)	/* Block protect 2 */
#define SR_BP3			BIT(5)	/* Block protect 3 */
#define SR_TB_BIT5		BIT(5)	/* Top/Bottom protect */
#define SR_BP3_BIT6		BIT(6)	/* Block protect 3 */
#define SR_TB_BIT6		BIT(6)	/* Top/Bottom protect */
#define SR_SRWD			BIT(7)	/* SR write protect */
#define SR2_CMP_BIT6		BIT(6)
