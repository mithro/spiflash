#define SPINAND_SET_FEATURE_1S_1S_1S_OP(reg, valptr)			\
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x1f, 1),				\
		   SPI_MEM_OP_ADDR(1, reg, 1),				\
		   SPI_MEM_OP_NO_DUMMY,					\
		   SPI_MEM_OP_DATA_OUT(1, valptr, 1))

#define SPINAND_GET_FEATURE_1S_1S_1S_OP(reg, valptr)			\
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x0f, 1),				\
		   SPI_MEM_OP_ADDR(1, reg, 1),				\
		   SPI_MEM_OP_NO_DUMMY,					\
		   SPI_MEM_OP_DATA_IN(1, valptr, 1))

#define SPINAND_BLK_ERASE_1S_1S_0_OP(addr)				\
	SPI_MEM_OP(SPI_MEM_OP_CMD(0xd8, 1),				\
		   SPI_MEM_OP_ADDR(3, addr, 1),				\
		   SPI_MEM_OP_NO_DUMMY,					\
		   SPI_MEM_OP_NO_DATA)

#define SPINAND_PAGE_READ_1S_1S_0_OP(addr)				\
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x13, 1),				\
		   SPI_MEM_OP_ADDR(3, addr, 1),				\
		   SPI_MEM_OP_NO_DUMMY,					\
		   SPI_MEM_OP_NO_DATA)

#define SPINAND_PAGE_READ_FROM_CACHE_1S_1S_1S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x03, 1),				\
		   SPI_MEM_OP_ADDR(2, addr, 1),				\
		   SPI_MEM_OP_DUMMY(ndummy, 1),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 1),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_FAST_1S_1S_1S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x0b, 1),				\
		   SPI_MEM_OP_ADDR(2, addr, 1),				\
		   SPI_MEM_OP_DUMMY(ndummy, 1),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 1),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_3A_1S_1S_1S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x03, 1),				\
		   SPI_MEM_OP_ADDR(3, addr, 1),				\
		   SPI_MEM_OP_DUMMY(ndummy, 1),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 1),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_FAST_3A_1S_1S_1S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x0b, 1),				\
		   SPI_MEM_OP_ADDR(3, addr, 1),				\
		   SPI_MEM_OP_DUMMY(ndummy, 1),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 1),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_1S_1D_1D_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x0d, 1),				\
		   SPI_MEM_DTR_OP_ADDR(2, addr, 1),			\
		   SPI_MEM_DTR_OP_DUMMY(ndummy, 1),			\
		   SPI_MEM_DTR_OP_DATA_IN(len, buf, 1),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_1S_1S_2S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x3b, 1),				\
		   SPI_MEM_OP_ADDR(2, addr, 1),				\
		   SPI_MEM_OP_DUMMY(ndummy, 1),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 2),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_3A_1S_1S_2S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x3b, 1),				\
		   SPI_MEM_OP_ADDR(3, addr, 1),				\
		   SPI_MEM_OP_DUMMY(ndummy, 1),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 2),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_1S_1D_2D_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x3d, 1),				\
		   SPI_MEM_DTR_OP_ADDR(2, addr, 1),			\
		   SPI_MEM_DTR_OP_DUMMY(ndummy, 1),			\
		   SPI_MEM_DTR_OP_DATA_IN(len, buf, 2),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_1S_2S_2S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0xbb, 1),				\
		   SPI_MEM_OP_ADDR(2, addr, 2),				\
		   SPI_MEM_OP_DUMMY(ndummy, 2),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 2),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_3A_1S_2S_2S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0xbb, 1),				\
		   SPI_MEM_OP_ADDR(3, addr, 2),				\
		   SPI_MEM_OP_DUMMY(ndummy, 2),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 2),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_1S_2D_2D_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0xbd, 1),				\
		   SPI_MEM_DTR_OP_ADDR(2, addr, 2),			\
		   SPI_MEM_DTR_OP_DUMMY(ndummy, 2),			\
		   SPI_MEM_DTR_OP_DATA_IN(len, buf, 2),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_1S_1S_4S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x6b, 1),				\
		   SPI_MEM_OP_ADDR(2, addr, 1),				\
		   SPI_MEM_OP_DUMMY(ndummy, 1),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 4),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_3A_1S_1S_4S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x6b, 1),				\
		   SPI_MEM_OP_ADDR(3, addr, 1),				\
		   SPI_MEM_OP_DUMMY(ndummy, 1),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 4),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_1S_1D_4D_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x6d, 1),				\
		   SPI_MEM_DTR_OP_ADDR(2, addr, 1),			\
		   SPI_MEM_DTR_OP_DUMMY(ndummy, 1),			\
		   SPI_MEM_DTR_OP_DATA_IN(len, buf, 4),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_1S_4S_4S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0xeb, 1),				\
		   SPI_MEM_OP_ADDR(2, addr, 4),				\
		   SPI_MEM_OP_DUMMY(ndummy, 4),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 4),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_3A_1S_4S_4S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0xeb, 1),				\
		   SPI_MEM_OP_ADDR(3, addr, 4),				\
		   SPI_MEM_OP_DUMMY(ndummy, 4),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 4),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_1S_4D_4D_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0xed, 1),				\
		   SPI_MEM_DTR_OP_ADDR(2, addr, 4),			\
		   SPI_MEM_DTR_OP_DUMMY(ndummy, 4),			\
		   SPI_MEM_DTR_OP_DATA_IN(len, buf, 4),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_1S_1S_8S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x8b, 1),				\
		   SPI_MEM_OP_ADDR(2, addr, 1),				\
		   SPI_MEM_OP_DUMMY(ndummy, 1),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 8),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_1S_8S_8S_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0xcb, 1),				\
		   SPI_MEM_OP_ADDR(2, addr, 8),				\
		   SPI_MEM_OP_DUMMY(ndummy, 8),				\
		   SPI_MEM_OP_DATA_IN(len, buf, 8),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PAGE_READ_FROM_CACHE_1S_1D_8D_OP(addr, ndummy, buf, len, freq) \
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x9d, 1),				\
		   SPI_MEM_DTR_OP_ADDR(2, addr, 1),			\
		   SPI_MEM_DTR_OP_DUMMY(ndummy, 1),			\
		   SPI_MEM_DTR_OP_DATA_IN(len, buf, 8),			\
		   SPI_MEM_OP_MAX_FREQ(freq))

#define SPINAND_PROG_EXEC_1S_1S_0_OP(addr)				\
	SPI_MEM_OP(SPI_MEM_OP_CMD(0x10, 1),				\
		   SPI_MEM_OP_ADDR(3, addr, 1),				\
		   SPI_MEM_OP_NO_DUMMY,					\
		   SPI_MEM_OP_NO_DATA)

#define SPINAND_PROG_LOAD_1S_1S_1S_OP(reset, addr, buf, len)		\
	SPI_MEM_OP(SPI_MEM_OP_CMD(reset ? 0x02 : 0x84, 1),		\
		   SPI_MEM_OP_ADDR(2, addr, 1),				\
		   SPI_MEM_OP_NO_DUMMY,					\
		   SPI_MEM_OP_DATA_OUT(len, buf, 1))

#define SPINAND_PROG_LOAD_1S_1S_4S_OP(reset, addr, buf, len)		\
	SPI_MEM_OP(SPI_MEM_OP_CMD(reset ? 0x32 : 0x34, 1),		\
		   SPI_MEM_OP_ADDR(2, addr, 1),				\
		   SPI_MEM_OP_NO_DUMMY,					\
		   SPI_MEM_OP_DATA_OUT(len, buf, 4))

