# What is derived

An upstream entry states some facts, and others follow from them. spiflash
stores what the entry states, each fact once, and works out the rest when
the data is loaded ({py:mod}`spiflash.derive`), so the two can never
disagree. This page lists the rules.

## Capabilities

A chip's capabilities are what its sources' entries claim, and what the rest
of each entry implies: the operations it states, its erase layouts, its size
and its SFDP tables. A chip page marks each source's capability {sfyes}`✓`
where the entry claims it and {sfhollow}`○` where it only implies it, with
the reason.

```{include} _generated/derived-table.md
```

An erase opcode alone implies no block size: 0x20, 0x52 and 0xd7 are not
the same size on every part (flashrom's MX25L1605 erases 64 KiB with 0x20,
its AT25F2048 64 KiB with 0x52, its LE25FW106 2 KiB with 0xd7). So only an
erase layout gives `erase_4k`, `erase_32k` or `erase_64k`: one opcode over
blocks of one size, and not a chip erase (0xc7, 0x60, 0x62) or die erase
(0xc4), whose single block is no block erase however small the chip. A
SPI NAND part's block erase implies none of them, as its operations are
not the SPI NOR ones.

## SFDP tables

Where a source carries a part's SFDP
([JESD216](https://www.jedec.org/standards-documents/docs/jesd216b)) tables,
its record stores them as they are: {sfsrc}`qemu`'s whole dumps (a record's
`sfdp`), and the Basic Flash Parameter, 4-byte instruction and xSPI tables
{sfsrc}`zephyr`'s boards copy (`sfdp-bfp`, `sfdp-ff84`, `sfdp-ff05`: its
`sfdp_tables`). What they say is worked out from them when the data is
loaded ({py:meth}`Sfdp.facts <spiflash.sfdp.Sfdp.facts>`), and not stored
again:

- the size (the density) and the page size, where the entry gives none;
- an eraser for each erase type over the whole part, one for each 4-byte
  erase opcode of the 4-byte instruction table, and the 4 KiB erase of
  BFPT DWORD 1 where no erase type has it;
- each operation the tables name, the part's own: its fast reads, each with
  the dummy clocks the tables give it, the erases, the 4-byte forms the
  instruction table lists (a read only where the BFPT lists its 3-byte form,
  as Linux takes them), the ways into and out of 4-byte mode (DWORD 16), the
  read 0x03 JESD216 guarantees, and RDSFDP itself.

Those operations and erasers imply capabilities by the rules above, the same
as a source's. Three things only SFDP says imply them too: the BFPT's address
bytes (3 or 4, or 4) and its ways into 4-byte mode imply `4byte_addr`;
DWORD 16 bit 29, dedicated 4-byte opcodes, implies `4byte_opcodes`; and an
xSPI profile 1.0 table implies `octal_dtr_read` and `octal_dtr_pp`, whose
opcodes have no name here.

A 3-byte part's DWORD 16 often has every way into and out of 4-byte mode set
(Macronix's MX25R6435F: all ones), which says nothing. Its 4-byte fields are
read only for a part with a 4-byte mode: one whose BFPT allows 4-byte
addresses, as Zephyr's `spi_nor_process_bfp()` reads them, or one over
16 MiB.

A value the source also gives outside its tables is stored only where it
differs from theirs, and is then the record's value, as it is the one the
source's own code uses: {sfsrc}`qemu`'s model takes the geometry of its
`INFO()` line, and {sfsrc}`zephyr`'s `spi_nor` driver refuses a size its BFPT
contradicts, while its other drivers use `page-size` as the controller's
write chunk. Each such value is a [data issue](issues/sfdp.md)
({py:meth}`Record.sfdp_disagreements
<spiflash.model.Record.sfdp_disagreements>`). A chip page marks a value a
record has from its own tables "(SFDP)".

### What SFDP says of fast read

SFDP gives no sign that a part has the 1-1-1 fast read (0x0b), nor the 1-1-1
page program (0x02), so neither a carried table nor a source's `sfdp` claim
implies `fast_read`. The JESD216 text itself could not be consulted (JEDEC
requires a login); the rule rests on two independent implementations of it,
and will be revisited if someone supplies the text:

- {sfsrc}`zephyr`, `jesd216_bfp_read_support()` in
  [`drivers/flash/jesd216.h`](https://github.com/zephyrproject-rtos/zephyr/blob/9f0253dcc66ccecc92a699dd81cb1034c3f6875b/drivers/flash/jesd216.h#L331-L359):
  "For @p mode JESD216_MODE_111 this function will return zero to indicate
  that standard read (instruction 03h) is supported, but without providing
  information on how. SFDP does not provide an indication of support for
  1-1-1 Fast Read (0Bh).";
- {sfsrc}`linux`, `spi_nor_parse_bfpt()` in
  {upstream}`linux:drivers/mtd/spi-nor/sfdp.c`, which turns on only the reads
  the Basic Flash Parameter Table has bits for (1-1-2, 1-2-2, 2-2-2, 1-1-4,
  1-4-4, 4-4-4; fast read 0x0b only when the board's devicetree asks,
  `m25p,fast-read`), and `spi_nor_parse_4bait()`: "4BAIT is the only SFDP
  table that indicates page program support".

So on a part with SFDP tables only the read 0x03 is taken as the part's own
(with RDSFDP, 0x5a, the table itself); {sfsrc}`qemu`'s fast read and page
program, which its model decodes for every part, stay driver defaults. The
4-byte instruction table is another matter: its fast read (0x0c) and page
program (0x12) bits are the part's own 4-byte forms, so they imply
`fast_read` as any source's `READ_1_1_1_FAST_4B` does.

## Driver defaults imply nothing

Some operations a source lists because its driver sends them to every part
(or every part of a kind), whatever the entry says. Those are *driver
defaults* ({py:attr}`OpcodeUse.assumed <spiflash.opcodes.OpcodeUse.assumed>`),
shown on the chip pages as a grey {sfgrey}`◌` (where a
source states the operation for the part, {sfyes}`✓`), and they imply no
capability:

- {sfsrc}`linux`: read, fast read (a board's devicetree choice,
  `m25p,fast-read`), page program and chip erase, and the 64 KiB 0xd8 sector
  (and 256-byte page) it takes for an entry that gives none: that erase
  layout is marked a default too ({py:attr}`Eraser.assumed
  <spiflash.model.Eraser.assumed>`), and the page size is left out;
- {sfsrc}`u-boot`: read, fast read (unless `SPI_NOR_NO_FR`), page program
  and chip erase (unless `NO_CHIP_ERASE`), and the quad page program
  (0x32) it adds for every `SPI_NOR_QUAD_READ` part;
- {sfsrc}`qemu`: the read, fast read, page program and chip erases its
  model decodes for every part;
- {sfsrc}`openfpgaloader`: every read and page program, and the 4-byte form
  of every erase (for any address above 16 MiB);
- {sfsrc}`imsprog`: every read, page program and 0xd8 erase;

and the 4-byte-address form of each. So an AT45DB DataFlash part, which
has a fast read, does not show one: the only sources listing it for those
parts are U-Boot's and QEMU's defaults, and none says so for the part. An
operation JESD216 guarantees on a part whose SFDP tables a source carries
is that part's own, not a default.

## Sector size

A part's sector size is the block of its 0xd8 erase layout, or failing
that of its 0xdc (0xd8's 4-byte-address form), or of its 0x52 (the AT25F
and SST25LF parts, which have no 0xd8, erase 32 KiB blocks with it, or 64 KiB
on the AT25F2048 and AT25F4096); a SPI NAND part's is its block erase's
block. A part that needs no erase (FRAM, MRAM: `no_erase`) has none, and
none is made up for it.

Sources that give a sector size and no layout give the layout instead:
{sfsrc}`linux` (0xd8 over its `.sector_size`, and 0x20 over 4 KiB for
`SECT_4K`; where the entry gives no `.sector_size`, the driver's 64 KiB
default is a layout marked a default, which gives no sector size), {sfsrc}`u-boot`
(the same from its `INFO()` sectors), {sfsrc}`openfpgaloader`
(`sector_erase` and `subsector_erase`), and every SPI NAND source (the
erase block). A Linux part read from SFDP has no layout, as the kernel
takes it from the part's tables at run time.

## Operations

The id read of the way an entry reads the id, and the erase each of its
erase layouts sends, are derived too: see
[](opcodes.md), which says where each source's opcodes come from.
