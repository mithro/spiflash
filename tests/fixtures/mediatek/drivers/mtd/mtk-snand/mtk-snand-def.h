/* SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause */
/*
 * Copyright (C) 2020 MediaTek Inc. All Rights Reserved.
 *
 * Author: Weijie Gao <weijie.gao@mediatek.com>
 */


enum snand_flash_io {
	SNAND_IO_1_1_1,
	SNAND_IO_1_1_2,
	SNAND_IO_1_2_2,
	SNAND_IO_1_1_4,
	SNAND_IO_1_4_4,

	__SNAND_IO_MAX
};

#define SPI_IO_1_1_1			BIT(SNAND_IO_1_1_1)
#define SPI_IO_1_1_2			BIT(SNAND_IO_1_1_2)
#define SPI_IO_1_2_2			BIT(SNAND_IO_1_2_2)
#define SPI_IO_1_1_4			BIT(SNAND_IO_1_1_4)
#define SPI_IO_1_4_4			BIT(SNAND_IO_1_4_4)

struct snand_opcode {
	uint8_t opcode;
	uint8_t dummy;
};

struct snand_io_cap {
	uint8_t caps;
	struct snand_opcode opcodes[__SNAND_IO_MAX];
};

#define SNAND_OP(_io, _opcode, _dummy) [_io] = { .opcode = (_opcode), \
						 .dummy = (_dummy) }

#define SNAND_IO_CAP(_name, _caps, ...) \
	struct snand_io_cap _name = { .caps = (_caps), \
				      .opcodes = { __VA_ARGS__ } }

#define SNAND_MAX_ID_LEN		4

enum snand_id_type {
	SNAND_ID_DYMMY,
	SNAND_ID_ADDR = SNAND_ID_DYMMY,

/* SPI-NAND opcodes */
#define SNAND_CMD_RESET			0xff
#define SNAND_CMD_BLOCK_ERASE		0xd8
#define SNAND_CMD_READ_FROM_CACHE_QUAD	0xeb
#define SNAND_CMD_WINBOND_SELECT_DIE	0xc2
#define SNAND_CMD_READ_FROM_CACHE_DUAL	0xbb
#define SNAND_CMD_READID		0x9f
#define SNAND_CMD_READ_FROM_CACHE_X4	0x6b
#define SNAND_CMD_READ_FROM_CACHE_X2	0x3b
#define SNAND_CMD_PROGRAM_LOAD_X4	0x32
#define SNAND_CMD_SET_FEATURE		0x1f
#define SNAND_CMD_READ_TO_CACHE		0x13
#define SNAND_CMD_PROGRAM_EXECUTE	0x10
#define SNAND_CMD_GET_FEATURE		0x0f
#define SNAND_CMD_READ_FROM_CACHE	0x0b
#define SNAND_CMD_WRITE_ENABLE		0x06
#define SNAND_CMD_PROGRAM_LOAD		0x02

