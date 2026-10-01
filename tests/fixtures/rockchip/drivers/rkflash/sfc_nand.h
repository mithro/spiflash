/*
 * Copyright (c) 2018 Fuzhou Rockchip Electronics Co., Ltd
 *
 * SPDX-License-Identifier:	GPL-2.0
 */

#ifndef __SFC_NAND_H
#define __SFC_NAND_H


#define FEA_READ_STATUE_MASK    (0x3 << 0)
#define FEA_STATUE_MODE1        0
#define FEA_STATUE_MODE2        1
#define FEA_4BIT_READ           BIT(2)
#define FEA_4BIT_PROG           BIT(3)
#define FEA_4BYTE_ADDR          BIT(4)
#define FEA_4BYTE_ADDR_MODE	BIT(5)
#define FEA_SOFT_QOP_BIT	BIT(6)

struct nand_mega_area {
	u8 off0;
	u8 off1;
	u8 off2;
	u8 off3;
};

struct nand_info {
	u8 id0;
	u8 id1;
	u8 id2;

	u16 sec_per_page;
	u16 page_per_blk;
	u16 plane_per_die;
	u16 blk_per_plane;

	u8 feature;

	u8 density;  /* (1 << density) sectors*/
	u8 max_ecc_bits;
	u8 has_qe_bits;

	struct nand_mega_area meta;
	u32 (*ecc_status)(void);
};


#endif
