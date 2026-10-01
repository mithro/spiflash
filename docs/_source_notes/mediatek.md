MediaTek keeps its own U-Boot for the routers and boards built on its SoCs
(MT7622, MT7981, MT7986, MT7988 and others), {github}`mtk-openwrt/u-boot`,
which OpenWrt builds. Its default branch, `mtksoc`, is the maintained one
(the `mtksoc-YYYYMMDD` branches are snapshots of it). Its SPI NAND driver,
mtk-snand, identifies a part by the table in
{upstream}`mediatek:drivers/mtd/mtk-snand/mtk-snand-ids.c`: SPI NAND only,
some ninety entries, with no vendor names. The files are GPL-2.0 or
BSD-3-Clause.

Each entry is a `SNAND_INFO`: the part name, its id, its memory organisation,
the read-from-cache and program-load I/O modes it allows, and, for a part
of two dies, the function that selects a die:

```c
SNAND_INFO("W25M02GV", SNAND_ID(SNAND_ID_DYMMY, 0xef, 0xab, 0x21),
           SNAND_MEMORG_2G_2K_64_2D,
           &snand_cap_read_from_cache_quad,
           &snand_cap_program_load_x4,
           mtk_snand_winbond_select_die),
```

`SNAND_MEMORG` ({upstream}`mediatek:drivers/mtd/mtk-snand/mtk-snand-def.h`)
is the page size, the spare (OOB) size, pages per block, blocks per die,
planes per die and dies. What each value means is what the driver does with
it (`mtk_snand_setup()` in
{upstream}`mediatek:drivers/mtd/mtk-snand/mtk-snand.c`):

- the size is page x pages per block x blocks per die x dies. The spare
  area is not in it, and nor are the planes, which the driver never reads:
  a two-plane part's blocks per die are all its blocks (the
  MT29F2G01AAAED's 2048), and the plane is one bit of the page address,
  set from the page number (`mtk_snand_get_plane_address()`). So a wrong
  `planes_per_die` does not change the size: the MT29F1G01AAADD's is 1,
  where its datasheet has 2 planes of 512 blocks;
- the sector size is the erase block, page x pages per block, as
  {sfsrc}`linux` gives it;
- the id is every byte the entry lists, the manufacturer's first.
  `mtk_snand_id_probe()` sends 0x9f and a zero byte, then 0x9f alone, and
  compares each answer with the entries in turn, taking the first whose
  bytes it starts with.

**The driver tries every entry both ways**, with the dummy byte and without.
Each entry names a way, `SNAND_ID_DYMMY` (a dummy byte) or `SNAND_ID_ADDR`
(an address byte), and the lookup compares it, but both probes pass
`SNAND_ID_DYMMY` and every entry's type is that same value (`SNAND_ID_ADDR`
is defined as it), so the label decides nothing. The records keep it, as
`rdid_opcode_dummy` or `rdid_opcode_addr`, so the "Ids read with" counts on
this page are the table's labels, not how the driver reads. One is wrong:
the GD5F4GQ4UCxIG ({sfid}`c8 b4`) is labelled with a dummy byte, but its
datasheet and {sfsrc}`linux` read it with none; the driver's second read
finds it.

Each entry's I/O modes are one of a few `SNAND_IO_CAP` tables: read from
cache on one, two or four lines (x1, x2, x4), with the address on two or
four lines too (dual and quad I/O, with 4, 2 or 8 dummy clocks), and program
load on one line or four. The driver takes the widest the controller has.
They give the record's `dual_read`, `quad_read` and `quad_pp`. The opcodes
are SPI NAND's own (read from cache, program load), and have no place among
the [SPI NOR operations](../opcodes.md), so a record lists none.

What a record has no field for is kept in its `flags`, under the driver's
names: `sparesize` (bytes per page), `planes_per_die`, `ndies`, `cap_rd` and
`cap_pl` (the I/O tables) with the modes each allows (`read_from_cache`,
`program_load`), and `select_die`, which of the two ways a two-die part is
switched (Winbond's 0xc2 command, or Micron's die-select feature bit).
A note repeats the spare area, planes and dies in words.

The table repeats two ids. The IS37SML01G1 is listed after the ESMT
F50L1G41A under the same {sfid}`c8 21` (ISSI's part answers ESMT's id), so
the driver never reaches it; its record says so. And two entries are wrong,
and are left out; {repo}`tools/update_db.py` counts them, and
{py:data}`spiflash_extract.mediatek.WRONG` lists them:

- **Wrong id:** the EM73D044SND, a 2 Gbit part, is listed twice: under
  {sfid}`d5 1e`, and again, after it, under {sfid}`d5 1d`, the 1 Gbit
  EM73C044SND's id, which an earlier entry gives and the driver matches
  first.
- **Size contradicts the part number:** the EM73E044SNE, {sfid}`d5 0e`, is
  listed at 8 Gbit. The letter after "EM73" is Etron's density: C 1 Gbit,
  D 2 Gbit, E 4 Gbit, F 8 Gbit, as in every other entry here, in
  {sfsrc}`dediprog`, and on Etron's own product list.

Each is named by its part, its id and, for a wrong size, that size, so an
entry corrected upstream is taken again, and the extraction stops on a
known error that is no longer there, for it to be removed.

The table names no manufacturer. As for {sfsrc}`rockchip`, a chip that only
MediaTek lists gets the manufacturer inferred from the other sources' parts
that answer its first id byte and whose names start like its own (Etron for
the EM73 parts, ESMT for the F50L2G41LB). A part MediaTek lists under another
maker's id does not count for that maker: the F50L1G41A, on GigaDevice's
{sfid}`c8 21`, does not make the F50 parts GigaDevice's. Its two-byte ids
that start a longer id another source gives for the same part fold into it,
as {sfsrc}`rockchip`'s do: eleven of them (the MX35LF2G24AD's {sfid}`c2 24`
into {sfid}`c2 24 03`, the GD5F4GQ4UCxIG's {sfid}`c8 b4` into
{sfid}`c8 b4 68`, ...).

The table is a chip vendor's production data, the geometry its boards boot
with, like {sfsrc}`rockchip`'s, but SPI NAND only, which puts it just after
Rockchip. It is not reviewed in the open, though, and has the errors above,
so it ranks below the curated tables. It is the only source here for many
Etron parts, the older Micron MT29F1G01AAADD and MT29F4G01AAADD, and
HeYangTek's HYF1GQ4U.
[Its data issues page](../issues/source-mediatek.md) lists where it
disagrees with the others.
