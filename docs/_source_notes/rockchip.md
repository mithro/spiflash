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
as the XM25QH256B alone, with a note: the XM25QU256B answers `20 70 19`
({sfsrc}`dediprog`), not the entry's `20 60 19`.

The tables name no manufacturer. For a chip that only Rockchip lists, the
chip page gives the manufacturer inferred from the other sources' parts: the
one they all name for parts that answer its first id byte and whose names
start like its own (HeYangTek for the HYF2GQ4UAACAE). The byte alone is not
enough, as clones answer another maker's (GSS's GSS01GSAK1 answers Alliance
Memory's 0x52). Where nothing confirms one, as for GSS's and Unim's parts,
the chip has none.

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
  4-byte mode first (`snor_enter_4byte_mode()` sends 0xb7, no write
  enable): the record's way in, `en4b`, which gives
  [EN4B](../opcodes/EN4B.md).

A quad program of 0x38 or 0x3e has its address on four lines too
([PP_1_4_4](../opcodes/PP_1_4_4.md), [PP_1_4_4_4B](../opcodes/PP_1_4_4_4B.md))
only on Macronix parts. The driver sends it with the address on one line on
the other parts that list it, GigaDevice's GD25Q256 and GD25Q512MC and XMC's
XM25QH256B, so it is left out of their records, with a note. The driver also
changes one entry at run time: a GD25Q256 (c84019) whose parameter page says
version 6 gets quad enable bit 9 and quad program 0x34 instead.

`QE_bits` is the quad enable bit (the record's `quad_enable`): register
`n >> 3` of those `snor_read_status()` reads (0x05, 0x35, 0x15), bit
`n & 7`, so `QE_bits=9` is SR2 bit 1. `QE_bits=0` on a part with quad reads
is no bit: the driver reads with four lines without setting one. The
GD25Q256's bit depends on its revision, as above (the C's SR1 bit 6, the D's
and E's SR2 bit 1, as {sfsrc}`linux` splits them too), so its record has
none. To set the bit the driver reads its register, then writes it with the
entry's `write_status` function (`feature & 3`): `snor_write_status` with
the register's own command ([WRSR2](../opcodes/WRSR2.md) for SR2),
`snor_write_status1` with a 2-byte 0x01 after reading SR2
([WRSR_16](../opcodes/WRSR_16.md), [RDSR2](../opcodes/RDSR2.md)), and
`snor_write_status2`, Macronix's, with a 2-byte 0x01 of SR1 and the
configuration register, read with 0x15 ([RDSR3](../opcodes/RDSR3.md)). The
records have those operations; a part whose bit is in SR1, written with a
plain 0x01, needs none more.

For an SPI NAND entry, `sfc_nand_init()` in sfc_nand.c:

- the id is read with 0x9f and an address byte, like {sfsrc}`linux`'s
  `SPINAND_READID_METHOD_OPCODE_ADDR`; the first two bytes must match, and
  the third only where the entry's is not 0, so an id of 0 in the third
  byte is not part of it. A third byte that repeats the manufacturer's, or
  is 0x7f, is what the part sends after its id, and is taken as its
  extended id: the GD5F1GQ5REYIG (`c8 41`, then `c8`) and the F50L2G41KA
  (`c8 41`, then `7f`);
- the page is `sec_per_page` sectors of 512 bytes, the erase block
  `page_per_blk` pages (its block erase's layout), and the size `plane_per_die` times `blk_per_plane`
  blocks, which `density` agrees with in every entry;
- the record's `planes` is `plane_per_die`, which also sets the plane bit
  of the column address; Rockchip states no dies (its FTL's `die_num` is 1
  for every part), so on a part of two LUNs (the GD5F4GQ6) its
  `plane_per_die` of 2 stands for the LUNs, where {sfsrc}`linux` gives one
  plane in each of two;
- its `ecc` is `max_ecc_bits`, which the driver passes to its FTL with no
  step: 8 bits, not 8 bits per 512 bytes;
- the driver reads from cache with 0x03 and loads with 0x02, then for
  `FEA_4BIT_READ` reads with the quad output 0x6b
  ([`NAND_READ_CACHE_1_1_4`](../opcodes/NAND_READ_CACHE_1_1_4.md)), and for
  `FEA_4BIT_PROG` (while it reads on four lines) loads with 0x32
  ([`NAND_PROGRAM_LOAD_1_1_4`](../opcodes/NAND_PROGRAM_LOAD_1_1_4.md)). The
  feature bits are those operations' `via`, which imply `quad_read` and
  `quad_pp`. Its 0x03 and 0x02, and the page read, program execute and
  feature commands it sends every part, are driver defaults, 8 dummy clocks
  for every read: no part's own;
- `has_qe_bits=1` is a quad enable bit, bit 0 of the configuration feature
  (0xb0), which `sfc_nand_enable_QE()` sets before quad reads; with
  `has_qe_bits=0` a part with quad reads is read with four lines setting
  nothing, as a SPI NOR entry's `QE_bits=0` is: no bit.

An SPI NAND id is two bytes where the third is 0. It is the same chip as
{sfsrc}`linux`'s id for the same part wherever Linux matches the same bytes,
whether Linux reads the id after a dummy byte or an address byte. Linux and
{sfsrc}`dediprog` match a third byte for some parts that Rockchip does not,
such as Macronix's MX35LF2GE4AD (`c2 26 03`, where Rockchip matches
`c2 26`) and Foresee's F35SQA001G (`cd 71 71`). Such a shorter id is folded
into the longer one when every record of it names the longer id's part, with
the same size, page and erase block, and no other longer id of that part
starts with it (unless the longer ids start one another: the F50L1G41LB's
`c8 01`, `c8 01 7f` and `c8 01 7f 7f 7f` are one chip). The chip page lists
it as matched on its first bytes and says which id each source gives, and a
lookup finds the chip by either id. Twenty of Rockchip's ids fold this way.

The GD5F1GQ5REYIG and the F50L2G41KA both answer `c8 41`, and Rockchip
alone tells them apart by the byte after it. A lookup of `c8 41 7f` gives
the F50L2G41KA alone, as ESMT's, and of `c8 41 c8` the GigaDevice part
with the other sources' GD5F1GQ5REXXG; neither is a disagreement between
the sources.

The driver takes the first entry an id matches. A later entry for the same
id is never used, and its record says which line wins
(`XT26Q04DWSIGT-B`, after `XT26Q04DWSIGA`).

The raw fields are kept in the record's `flags`: the feature bits that give
no capability (`FEA_SOFT_QOP_BIT`, ...; `FEA_4BIT_READ` and the others that
do are an operation's or a capability's `via`), the function writing the status registers
where it sends nothing the record has as an operation
(`write_status=snor_write_status`), `has_qe_bits=0` on a part without quad
reads, which says nothing, and the following fields, which the database has
no field for yet:

- where the FTL keeps its metadata in the spare area
  (`meta={ 0x04, 0x08, 0xFF, 0xFF }`);
- the function that decodes its ECC status
  (`ecc_status=sfc_nand_get_ecc_status0`), since the parts report ECC
  results in different bits of feature registers 0xc0 and 0xf0.

Its entries are not reviewed in the open, and a few disagree with the other
sources: it gives the F50L1G41LC 2 Gbit, where {sfsrc}`linux` and
{sfsrc}`dediprog` give 1 Gbit, and the FM25S02BI3 1 Gbit, where
{sfsrc}`dediprog` gives 2 Gbit.
[Its data issues page](../issues/source-rockchip.md) lists where it
disagrees with the others.
