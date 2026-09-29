/*
 * ST M25P80 emulator. Emulate all SPI flash devices based on the m25p80 command
 * set. Known devices table current as of Jun/2012 and taken from linux.
 * See drivers/mtd/devices/m25p80.c.
 *
 * Copyright (C) 2011 Edgar E. Iglesias <edgar.iglesias@gmail.com>
 * Copyright (C) 2012 Peter A. G. Crosthwaite <peter.crosthwaite@petalogix.com>
 * Copyright (C) 2012 PetaLogix
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License as
 * published by the Free Software Foundation; either version 2 or
 * (at your option) a later version of the License.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, see <http://www.gnu.org/licenses/>.
 */

#include "qemu/osdep.h"
/* 16 MiB max in 3 byte address mode */
#define MAX_3BYTES_SIZE 0x1000000
#define SPI_NOR_MAX_ID_LEN 6

/* Fields for FlashPartInfo->flags */
enum spi_flash_option_flags {
    ER_4K                  = BIT(0),
    ER_32K                 = BIT(1),
    EEPROM                 = BIT(2),
    HAS_SR_TB              = BIT(3),
    HAS_SR_BP3_BIT6        = BIT(4),
};

typedef struct FlashPartInfo {
    const char *part_name;
    /*
     * This array stores the ID bytes.
     * The first three bytes are the JEDIC ID.
     * JEDEC ID zero means "no ID" (mostly older chips).
     */
    uint8_t id[SPI_NOR_MAX_ID_LEN];
    uint8_t id_len;
    /*
     * there is confusion between manufacturers as to what a sector is. In this
     * device model, a "sector" is the size that is erased by the ERASE_SECTOR
     * command (opcode 0xd8).
     */
    uint32_t sector_size;
    uint32_t n_sectors;
    uint32_t page_size;
    uint16_t flags;
    /*
     * Big sized spi nor are often stacked devices, thus sometime
     * replace chip erase with die erase.
     * This field inform how many die is in the chip.
     */
    uint8_t die_cnt;
    uint8_t (*sfdp_read)(uint32_t sfdp_addr);
} FlashPartInfo;

/* adapted from linux */
/* Used when the "_ext_id" is two bytes at most */
#define INFO(_part_name, _jedec_id, _ext_id, _sector_size, _n_sectors, _flags)\
    .part_name = _part_name,\
    .id = {\
        ((_jedec_id) >> 16) & 0xff,\
        ((_jedec_id) >> 8) & 0xff,\
        (_jedec_id) & 0xff,\
        ((_ext_id) >> 8) & 0xff,\
        (_ext_id) & 0xff,\
          },\
    .id_len = (!(_jedec_id) ? 0 : (3 + ((_ext_id) ? 2 : 0))),\
    .sector_size = (_sector_size),\
    .n_sectors = (_n_sectors),\
    .page_size = 256,\
    .flags = (_flags),\
    .die_cnt = 0

#define INFO6(_part_name, _jedec_id, _ext_id, _sector_size, _n_sectors, _flags)\
    .part_name = _part_name,\
    .id = {\
        ((_jedec_id) >> 16) & 0xff,\
        ((_jedec_id) >> 8) & 0xff,\
        (_jedec_id) & 0xff,\
        ((_ext_id) >> 16) & 0xff,\
        ((_ext_id) >> 8) & 0xff,\
        (_ext_id) & 0xff,\
          },\
    .id_len = 6,\
    .sector_size = (_sector_size),\
    .n_sectors = (_n_sectors),\
    .page_size = 256,\
    .flags = (_flags),\
    .die_cnt = 0

#define INFO_STACKED(_part_name, _jedec_id, _ext_id, _sector_size, _n_sectors,\
                    _flags, _die_cnt)\
    .part_name = _part_name,\
    .id = {\
        ((_jedec_id) >> 16) & 0xff,\
        ((_jedec_id) >> 8) & 0xff,\
        (_jedec_id) & 0xff,\
        ((_ext_id) >> 8) & 0xff,\
        (_ext_id) & 0xff,\
          },\
    .id_len = (!(_jedec_id) ? 0 : (3 + ((_ext_id) ? 2 : 0))),\
    .sector_size = (_sector_size),\
    .n_sectors = (_n_sectors),\
    .page_size = 256,\
    .flags = (_flags),\
    .die_cnt = _die_cnt

static const FlashPartInfo known_devices[] = {
    /* Atmel -- some are (confusingly) marketed as "DataFlash" */
    { INFO("at25fs010",   0x1f6601,      0,  32 << 10,   4, ER_4K) },

    /*
     * Atmel EEPROMS - it is assumed, that don't care bit in command
     * is set to 0. Block protection is not supported.
     */
    { INFO("at25128a-nonjedec", 0x0,     0,         1, 131072, EEPROM) },
    { INFO("at25256a-nonjedec", 0x0,     0,         1, 262144, EEPROM) },

    /* Macronix */
    { INFO6("mx25l25635e", 0xc22019,     0xc22019,  64 << 10, 512,
            ER_4K | ER_32K), .sfdp_read = m25p80_sfdp_mx25l25635e },

    /* Micron */
    { INFO("n25q256a",    0x20ba19,      0,  64 << 10, 512,
           ER_4K | HAS_SR_BP3_BIT6 | HAS_SR_TB),
      .sfdp_read = m25p80_sfdp_n25q256a },
    { INFO_STACKED("mt35xu01g", 0x2c5b1b, 0x104100, 128 << 10, 1024,
                   ER_4K | ER_32K, 2),
                   .sfdp_read = m25p80_sfdp_mt35xu01g },
    { INFO_STACKED("n25q00",    0x20ba21, 0x1000, 64 << 10, 2048, ER_4K, 4) },

    /*
     * Spansion -- single (large) sector size only, at least
     * for the chips listed here (without boot sectors).
     */
    { INFO("s25sl032p",   0x010215, 0x4d00,  64 << 10,  64, ER_4K) },
    { INFO("s25fl016k",   0xef4015,      0,  64 << 10,  32, ER_4K | ER_32K) },

    /* Winbond -- w25x "blocks" are 64k, "sectors" are 4KiB */
    { INFO("w25q512jv",   0xef4020,      0,  64 << 10, 1024, ER_4K),
      .sfdp_read = m25p80_sfdp_w25q512jv },

    /* Microchip */
    { INFO("25csm04",      0x29cc00,      0x100,  64 << 10,  8, 0) },
};

typedef enum {
    NOP = 0,
    WRSR = 0x1,
    WRDI = 0x4,
    RDSR = 0x5,
    WREN = 0x6,
    BRRD = 0x16,
    BRWR = 0x17,
    JEDEC_READ = 0x9f,
    BULK_ERASE_60 = 0x60,
    BULK_ERASE = 0xc7,
    READ_FSR = 0x70,
    RDCR = 0x15,
    RDSFDP = 0x5a,

    READ = 0x03,
    READ4 = 0x13,
    FAST_READ = 0x0b,
    FAST_READ4 = 0x0c,
    DOR = 0x3b,
    DOR4 = 0x3c,
    QOR = 0x6b,
    QOR4 = 0x6c,
    DIOR = 0xbb,
    DIOR4 = 0xbc,
    QIOR = 0xeb,
    QIOR4 = 0xec,

    PP = 0x02,
    PP4 = 0x12,
    PP4_4 = 0x3e,
    DPP = 0xa2,
    QPP = 0x32,
    QPP_4 = 0x34,
    RDID_90 = 0x90,
    RDID_AB = 0xab,
    AAI_WP = 0xad,

    ERASE_4K = 0x20,
    ERASE4_4K = 0x21,
    ERASE_32K = 0x52,
    ERASE4_32K = 0x5c,
    ERASE_SECTOR = 0xd8,
    ERASE4_SECTOR = 0xdc,

    EN_4BYTE_ADDR = 0xB7,
    EX_4BYTE_ADDR = 0xE9,

    EXTEND_ADDR_READ = 0xC8,
    EXTEND_ADDR_WRITE = 0xC5,

    RESET_ENABLE = 0x66,
    RESET_MEMORY = 0x99,

    /*
     * Micron: 0x35 - enable QPI
     * Spansion: 0x35 - read control register
     * Winbond: 0x35 - quad enable
     */
    RDCR_EQIO = 0x35,
    RSTQIO = 0xf5,

    /*
     * Winbond: 0x31 - write status register 2
     */
    WRSR2 = 0x31,

    RNVCR = 0xB5,
    WNVCR = 0xB1,

    RVCR = 0x85,
    WVCR = 0x81,

    REVCR = 0x65,
    WEVCR = 0x61,

    DIE_ERASE = 0xC4,
} FlashCMD;
