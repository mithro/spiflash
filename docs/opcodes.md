# Opcodes

Every chip page lists the SPI operations the sources say that part has. This
page lists the operations spiflash knows, and how many chip ids list each.

Operations are named as {github}`LiteSPI <litex-hub/litespi>`'s
`SpiNorFlashOpCodes` names them, so a part's list can be used there directly:

- the numbers are the lines used for the command, the address and the data:
  [`READ_1_4_4`](opcodes/READ_1_4_4.md) sends the command on one line and the
  address and data on four;
- `8D` marks double transfer rate, on eight lines
  ([`READ_8D_8D_8D`](opcodes/READ_8D_8D_8D.md));
- `_4B` is the form taking a 4-byte address
  ([`READ_1_4_4_4B`](opcodes/READ_1_4_4_4B.md)).

## Where each source's opcodes come from

| Source | Where the opcodes come from |
|---|---|
| {sfsrc}`flashrom`, {sfsrc}`flashprog` | the probe, the `.read`/`.write` functions, each block eraser (`spi_block_erase_20` sends 0x20), and the feature bits (`FEATURE_FAST_READ_QIO`, `FEATURE_4BA_ENTER`, `FEATURE_QPI_38_FF`, ...) |
| {sfsrc}`linux` | what {upstream}`linux:drivers/mtd/spi-nor/core.c` sets up for the entry: read, fast read and page program by default; the `no_sfdp_flags` (dual, quad and octal read, 4 KiB erase); sector and chip erase; and the 4-byte forms for `SPI_NOR_4B_OPCODES` |
| {sfsrc}`u-boot` | the same from its {upstream}`u-boot:drivers/mtd/spi/spi-nor-core.c` (`SPI_NOR_NO_FR`, `SST_WRITE`, `USE_FSR`, `NO_CHIP_ERASE`, ...) |
| {sfsrc}`dediprog` | the id command, and the opcodes its entry packs into `ReadCmd`, `ProgramCmd` and `EraseCmd`: the single-line read and page program, and the chip, block and die erase (SPI NOR only) |
| {sfsrc}`rockchip` | what its rkflash driver sets up for an SPI NOR entry ({upstream}`rockchip:drivers/rkflash/sfc_nor.c`): the read, page program, 4 KiB and block erase opcodes the entry gives, its quad read and program where its feature bits turn them on, and entering 4-byte mode for `FEA_4BYTE_ADDR_MODE` |
| {sfsrc}`mediatek` | none: its table is SPI NAND only, and the read-from-cache and program-load modes each entry allows (x1, x2, x4, dual and quad I/O) are kept in the record's `flags` |
| {sfsrc}`openocd` | the columns of its table: read, fastest read, page program, sector erase and chip erase |
| {sfsrc}`openfpgaloader` | what its {upstream}`openfpgaloader:src/spiFlash.cpp` sends: read, page program, and the erases its table allows |
| {sfsrc}`imsprog` | what its {upstream}`imsprog:IMSProg_programmer/spi_nor_flash.c` sends to a SPI NOR part: read, page program (in 256-byte pages) and the 0xd8 block erase (at every 64 KiB, whatever the part's blocks; it never sends a chip erase), and above 16 MiB the way the entry says to enter 4-byte addressing (`EN4B`, Winbond's, or Spansion's bank register) |
| {sfsrc}`qemu` | what its model ({upstream}`qemu:hw/block/m25p80.c`) decodes for every part (read, fast read, page program, sector erase, and chip erase as 0xc7 and 0x60), the erases its `ER_4K`/`ER_32K` flags allow, die erase for stacked parts, and, for the parts it has SFDP tables for ({upstream}`qemu:hw/block/m25p80_sfdp.c`), everything those tables list, with the part's own dummy clocks |
| {sfsrc}`zephyr` | the board's devicetree: the reads and erase types in the chip's own SFDP table where the board copies it (`sfdp-bfp`, JESD216's Basic Flash Parameter table), and the read and program modes the board uses (`readoc`, `writeoc`, `use-fast-read`, `enter-4byte-addr`, ...) |

Two kinds follow from what any SPI NOR entry already says, so they are worked
out when the data is loaded rather than stored ({py:mod}`spiflash.derive`):
the id read of the way the entry reads its id ([`RDID`](opcodes/RDID.md),
[`REMS`](opcodes/REMS.md), [`RES`](opcodes/RES.md), ...), and the erase each of
its erase layouts sends. A chip page marks those *implied* in "Why each source
lists each opcode".

The opcode values themselves are read from each upstream's own headers
(`SPINOR_OP_*`, `JEDEC_*`, `SPIFLASH_READ_ID`, `FLASH_*`, {sfsrc}`qemu`'s `FlashCMD`
enum), from the SFDP tables ({sfsrc}`qemu`, {sfsrc}`zephyr`), or from {sfsrc}`dediprog`'s
command words and {sfsrc}`rockchip`'s tables, and checked against
[the table below](#the-operations) by {repo}`tools/spiflash_extract/ops.py` when the
data is built: a disagreement fails the build.

:::{important}
A listed operation is one some source says the part has. **An operation that
is not listed may still be supported**: no source here describes every opcode
of every part, and for parts it reads from SFDP, {sfsrc}`linux` gets the read, program
and erase opcodes from the chip at run time, so it lists only its defaults.
Parts that share an id can differ as well. Each chip page says which sources
list which opcode, and why.
:::

## The operations

Each has a page: what it does, its timing diagram, and the parts that support
it.

```{include} _generated/opcodes-table.md
```

```{toctree}
:hidden:
:glob:

opcodes/*
```
