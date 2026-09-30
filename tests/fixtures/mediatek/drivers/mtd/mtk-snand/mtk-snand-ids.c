// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
/*
 * Copyright (C) 2020 MediaTek Inc. All Rights Reserved.
 *
 * Author: Weijie Gao <weijie.gao@mediatek.com>
 */

#include "mtk-snand-def.h"

static int mtk_snand_winbond_select_die(struct mtk_snand *snf, uint32_t dieidx);
static int mtk_snand_micron_select_die(struct mtk_snand *snf, uint32_t dieidx);

#define SNAND_MEMORG_512M_2K_64		SNAND_MEMORG(2048, 64, 64, 512, 1, 1)
#define SNAND_MEMORG_1G_2K_64		SNAND_MEMORG(2048, 64, 64, 1024, 1, 1)
#define SNAND_MEMORG_2G_2K_64		SNAND_MEMORG(2048, 64, 64, 2048, 1, 1)
#define SNAND_MEMORG_8G_4K_256		SNAND_MEMORG(4096, 256, 64, 4096, 1, 1)
#define SNAND_MEMORG_2G_2K_64_2P	SNAND_MEMORG(2048, 64, 64, 2048, 2, 1)
#define SNAND_MEMORG_2G_2K_64_2D	SNAND_MEMORG(2048, 64, 64, 1024, 1, 2)
#define SNAND_MEMORG_4G_2K_128_2P_2D	SNAND_MEMORG(2048, 128, 64, 2048, 2, 2)
#define SNAND_MEMORG_8G_4K_256_2D	SNAND_MEMORG(4096, 256, 64, 2048, 1, 2)

static const SNAND_IO_CAP(snand_cap_read_from_cache_quad,
	SPI_IO_1_1_1 | SPI_IO_1_1_2 | SPI_IO_1_2_2 | SPI_IO_1_1_4 |
	SPI_IO_1_4_4,
	SNAND_OP(SNAND_IO_1_1_1, SNAND_CMD_READ_FROM_CACHE, 8),
	SNAND_OP(SNAND_IO_1_1_2, SNAND_CMD_READ_FROM_CACHE_X2, 8),
	SNAND_OP(SNAND_IO_1_2_2, SNAND_CMD_READ_FROM_CACHE_DUAL, 4),
	SNAND_OP(SNAND_IO_1_1_4, SNAND_CMD_READ_FROM_CACHE_X4, 8),
	SNAND_OP(SNAND_IO_1_4_4, SNAND_CMD_READ_FROM_CACHE_QUAD, 4));

static const SNAND_IO_CAP(snand_cap_read_from_cache_quad_q2d,
	SPI_IO_1_1_1 | SPI_IO_1_1_2 | SPI_IO_1_2_2 | SPI_IO_1_1_4 |
	SPI_IO_1_4_4,
	SNAND_OP(SNAND_IO_1_1_1, SNAND_CMD_READ_FROM_CACHE, 8),
	SNAND_OP(SNAND_IO_1_1_2, SNAND_CMD_READ_FROM_CACHE_X2, 8),
	SNAND_OP(SNAND_IO_1_2_2, SNAND_CMD_READ_FROM_CACHE_DUAL, 4),
	SNAND_OP(SNAND_IO_1_1_4, SNAND_CMD_READ_FROM_CACHE_X4, 8),
	SNAND_OP(SNAND_IO_1_4_4, SNAND_CMD_READ_FROM_CACHE_QUAD, 2));

static const SNAND_IO_CAP(snand_cap_read_from_cache_x4,
	SPI_IO_1_1_1 | SPI_IO_1_1_2 | SPI_IO_1_1_4,
	SNAND_OP(SNAND_IO_1_1_1, SNAND_CMD_READ_FROM_CACHE, 8),
	SNAND_OP(SNAND_IO_1_1_2, SNAND_CMD_READ_FROM_CACHE_X2, 8),
	SNAND_OP(SNAND_IO_1_1_4, SNAND_CMD_READ_FROM_CACHE_X4, 8));

static const SNAND_IO_CAP(snand_cap_program_load_x1,
	SPI_IO_1_1_1,
	SNAND_OP(SNAND_IO_1_1_1, SNAND_CMD_PROGRAM_LOAD, 0));

static const SNAND_IO_CAP(snand_cap_program_load_x4,
	SPI_IO_1_1_1 | SPI_IO_1_1_4,
	SNAND_OP(SNAND_IO_1_1_1, SNAND_CMD_PROGRAM_LOAD, 0),
	SNAND_OP(SNAND_IO_1_1_4, SNAND_CMD_PROGRAM_LOAD_X4, 0));

static const struct snand_flash_info snand_flash_ids[] = {
	SNAND_INFO("W25N01GV", SNAND_ID(SNAND_ID_DYMMY, 0xef, 0xaa, 0x21),
		   SNAND_MEMORG_1G_2K_64,
		   &snand_cap_read_from_cache_quad,
		   &snand_cap_program_load_x4),
	SNAND_INFO("W25M02GV", SNAND_ID(SNAND_ID_DYMMY, 0xef, 0xab, 0x21),
		   SNAND_MEMORG_2G_2K_64_2D,
		   &snand_cap_read_from_cache_quad,
		   &snand_cap_program_load_x4,
		   mtk_snand_winbond_select_die),

	SNAND_INFO("GD5F1GQ4UAWxx", SNAND_ID(SNAND_ID_ADDR, 0xc8, 0x10),
		   SNAND_MEMORG_1G_2K_64,
		   &snand_cap_read_from_cache_quad_q2d,
		   &snand_cap_program_load_x4),

	SNAND_INFO("MT29F2G01AAAED", SNAND_ID(SNAND_ID_DYMMY, 0x2c, 0x9f),
		   SNAND_MEMORG_2G_2K_64_2P,
		   &snand_cap_read_from_cache_x4,
		   &snand_cap_program_load_x1),
	SNAND_INFO("MT29F4G01ADAGD", SNAND_ID(SNAND_ID_DYMMY, 0x2c, 0x36),
		   SNAND_MEMORG_4G_2K_128_2P_2D,
		   &snand_cap_read_from_cache_quad,
		   &snand_cap_program_load_x4,
		   mtk_snand_micron_select_die),

	SNAND_INFO("F50L512M41A", SNAND_ID(SNAND_ID_DYMMY, 0xc8, 0x20),
		   SNAND_MEMORG_512M_2K_64,
		   &snand_cap_read_from_cache_x4,
		   &snand_cap_program_load_x4),
	SNAND_INFO("F50L1G41A", SNAND_ID(SNAND_ID_DYMMY, 0xc8, 0x21),
		   SNAND_MEMORG_1G_2K_64,
		   &snand_cap_read_from_cache_x4,
		   &snand_cap_program_load_x4),

	SNAND_INFO("EM73C044SNA", SNAND_ID(SNAND_ID_DYMMY, 0xd5, 0x19),
		   SNAND_MEMORG(2048, 64, 128, 512, 1, 1),
		   &snand_cap_read_from_cache_quad_q2d,
		   &snand_cap_program_load_x4),
	SNAND_INFO("EM73C044SND", SNAND_ID(SNAND_ID_DYMMY, 0xd5, 0x1d),
		   SNAND_MEMORG_1G_2K_64,
		   &snand_cap_read_from_cache_quad_q2d,
		   &snand_cap_program_load_x4),
	SNAND_INFO("EM73D044SND", SNAND_ID(SNAND_ID_DYMMY, 0xd5, 0x1e),
		   SNAND_MEMORG_2G_2K_64,
		   &snand_cap_read_from_cache_quad_q2d,
		   &snand_cap_program_load_x4),
	SNAND_INFO("EM73D044SND", SNAND_ID(SNAND_ID_DYMMY, 0xd5, 0x1d),
		   SNAND_MEMORG_2G_2K_64,
		   &snand_cap_read_from_cache_quad_q2d,
		   &snand_cap_program_load_x4),
	SNAND_INFO("EM73E044SNE", SNAND_ID(SNAND_ID_DYMMY, 0xd5, 0x0e),
		   SNAND_MEMORG_8G_4K_256,
		   &snand_cap_read_from_cache_quad_q2d,
		   &snand_cap_program_load_x4),

	SNAND_INFO("IS37SML01G1", SNAND_ID(SNAND_ID_DYMMY, 0xc8, 0x21),
		   SNAND_MEMORG_1G_2K_64,
		   &snand_cap_read_from_cache_x4,
		   &snand_cap_program_load_x4),
};

