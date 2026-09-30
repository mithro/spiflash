[Rockchip](https://www.rock-chips.com/) makes the SoCs of many single-board
computers, tablets and set-top boxes. Its own fork of U-Boot,
{github}`rockchip-linux/u-boot` (the `next-dev` branch, which is the one
Rockchip maintains it on), has a driver of its own for the flash its boards
boot from, rkflash. The driver keeps two tables: SPI NOR in
{upstream}`rockchip:drivers/rkflash/sfc_nor.c` and SPI NAND in
{upstream}`rockchip:drivers/rkflash/sfc_nand.c`. It is a chip vendor's
production code, and its SPI NAND table has many parts from Chinese makers
that no other source here lists.

Each entry is a positional `struct` initialiser, with the part name in a
comment above it. A comment can name several parts
(`MT29F2G01ABA, XT26G02E, F50L2G41XA`) or go on in prose (`1*4096`, kept in
the record's notes). The one written as a pattern, `XM25QH(QU)256B`, is read
as the XM25QH256B and the XM25QU256B. The tables name no manufacturer, so a
chip that only Rockchip lists has none.

The fields are read as the driver uses them. For an SPI NOR entry,
`snor_parse_flash_table()` in sfc_nor.c:

- the id is the three bytes of [read-id](../opcodes/RDID.md);
- the size is `1 << density` sectors of 512 bytes, and `block_size` is in the
  same sectors;
- the page is the driver's 256 bytes (`NOR_PAGE_SIZE`) for every part;
- the driver erases with `sector_erase_cmd` 4 KiB at a time in the first and
  last 256 KiB of the chip, and with `block_erase_cmd` a block at a time in
  between (`snor_write()`);
- `read_cmd` and `prog_cmd` are the single-line read and page program; the
  quad forms, `read_cmd_4` and `prog_cmd_4`, are only used where the
  feature bits (`FEA_4BIT_READ`, `FEA_4BIT_PROG`) say so, and are listed only
  then;
- `FEA_4BYTE_ADDR` sends 4-byte addresses, and `FEA_4BYTE_ADDR_MODE` enters
  4-byte mode first ([EN4B](../opcodes/EN4B.md)).

A quad program of 0x38 or 0x3e has its address on four lines too
([PP_1_4_4](../opcodes/PP_1_4_4.md), [PP_1_4_4_4B](../opcodes/PP_1_4_4_4B.md))
only on Macronix parts. The driver sends it with the address on one line on
the other parts that list it, GigaDevice's GD25Q256 and GD25Q512MC and XMC's
XM25QH256B, so it is left out of their records, with a note. The driver also
changes one entry at run time: a GD25Q256 (c84019) whose parameter page says
version 6 gets quad enable bit 9 and quad program 0x34 instead.

For an SPI NAND entry, `sfc_nand_init()` in sfc_nand.c:

- the id is read with 0x9f and an address byte, like {sfsrc}`linux`'s
  `SPINAND_READID_METHOD_OPCODE_ADDR`; the first two bytes must match, and
  the third only where the entry's is not 0, so an id of 0 in the third
  byte is not part of it;
- the page is `sec_per_page` sectors of 512 bytes, the erase block
  `page_per_blk` pages, and the size `plane_per_die` times `blk_per_plane`
  blocks, which `density` agrees with in every entry;
- `FEA_4BIT_READ` and `FEA_4BIT_PROG` are quad read (0x6b) and quad program
  load (0x32). SPI NAND records have no opcodes, as {sfsrc}`linux`'s have
  none.

An SPI NAND id is two bytes where the third is 0. It merges with
{sfsrc}`linux`'s id for the same part wherever Linux matches the same bytes,
whether Linux reads the id after a dummy byte or an address byte. Linux
matches a third byte for some parts that Rockchip does not, such as Macronix's
MX35LF2GE4AD (`c2 26 03`) and Foresee's F35SQA001G (`cd 71 71`). Rockchip
matches `c2 26` and `cd 71`, so these are separate chip ids here.

The driver takes the first entry an id matches. A later entry for the same
id is never used, and its record says which line wins
(`XT26Q04DWSIGT-B`, after `XT26Q04DWSIGA`).

The raw fields are kept in the record's `flags`: the feature bits
(`FEA_4BIT_READ`, `FEA_SOFT_QOP_BIT`, ...), and the following fields, which
the database has no field for yet:

- the SPI NOR quad enable bit (`QE_bits=9`: status register 2, bit 1) and
  the function that writes the status registers
  (`write_status=snor_write_status1`: both registers in one write);
- whether an SPI NAND part has a quad enable bit (`has_qe_bits=1`: bit 0 of
  feature register 0xb0);
- the ECC strength its FTL expects (`max_ecc_bits=8`);
- where the FTL keeps its metadata in the spare area
  (`meta={ 0x04, 0x08, 0xFF, 0xFF }`);
- the function that decodes its ECC status
  (`ecc_status=sfc_nand_get_ecc_status0`), since the parts report ECC
  results in different bits of feature registers 0xc0 and 0xf0.

The number of planes is in the notes (`2 plane(s) of 1024 blocks`).

Its entries are not reviewed in the open, and a few disagree with the other
sources: it gives the F50L1G41LC 2 Gbit, where {sfsrc}`linux` and
{sfsrc}`dediprog` give 1 Gbit, and the FM25S02BI3 1 Gbit, where
{sfsrc}`dediprog` gives 2 Gbit.
[Its data issues page](../issues/source-rockchip.md) lists where it
disagrees with the others.
