/*
 * Copyright (c) 2018 Fuzhou Rockchip Electronics Co., Ltd
 *
 * SPDX-License-Identifier:	GPL-2.0
 */

#ifndef _SFC_NOR_H
#define _SFC_NOR_H

#include "sfc.h"

#define NOR_PAGE_SIZE		256

#define FEA_READ_STATUE_MASK	(0x3 << 0)
#define FEA_STATUE_MODE1	0
#define FEA_STATUE_MODE2	1
#define FEA_4BIT_READ		BIT(2)
#define FEA_4BIT_PROG		BIT(3)
#define FEA_4BYTE_ADDR		BIT(4)
#define FEA_4BYTE_ADDR_MODE	BIT(5)
/*Command Set*/
#define CMD_READ_JEDECID        (0x9F)
#define CMD_ENTER_4BYTE_MODE    (0xB7)

struct flash_info {
	u32 id;

	u8 block_size;
	u8 sector_size;
	u8 read_cmd;
	u8 prog_cmd;

	u8 read_cmd_4;
	u8 prog_cmd_4;
	u8 sector_erase_cmd;
	u8 block_erase_cmd;

	u8 feature;
	u8 density;  /* (1 << density) sectors*/
	u8 QE_bits;
	u8 reserved2;
};
#endif
