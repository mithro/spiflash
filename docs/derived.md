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
(0xc4, 0x61), whose single block is no block erase however small the chip. A
SPI NAND part's block erase implies none of them, as its operations are
not the SPI NOR ones. Its own read from cache and program load operations
imply its `fast_read`, `dual_read`, `quad_read`, `octal_read` and `quad_pp`,
as the SPI NOR ones do.

## SFDP tables

Where a source carries a part's SFDP
([JESD216](https://www.jedec.org/standards-documents/docs/jesd216b)) tables,
its record stores them as they are: {sfsrc}`qemu`'s whole dumps (a record's
`sfdp`), and the Basic Flash Parameter, 4-byte instruction and xSPI tables
{sfsrc}`zephyr`'s boards copy (`sfdp-bfp`, `sfdp-ff84`, `sfdp-ff05`: its
`sfdp_tables`). What they say is worked out from them when the data is
loaded ({py:meth}`Sfdp.facts <spiflash.sfdp.Sfdp.facts>`), and not stored
again:

- the size (the density) and the page size, where the entry gives none, and
  the dies its SCCR multi-chip table describes;
- an eraser for each erase type over the whole part, one for each 4-byte
  erase opcode of the 4-byte instruction table, and the 4 KiB erase of
  BFPT DWORD 1 where no erase type has it;
- each operation the tables name, the part's own: its fast reads, each with
  the dummy clocks the tables give it, the erases, the 4-byte forms the
  instruction table lists (a dual, quad or octal read only where the BFPT
  lists its 3-byte form, as Linux takes them; its 1-1-1 read 0x13 and fast
  read 0x0c, which the BFPT has no bits for, always), the ways into and out
  of 4-byte mode (DWORD 16), the
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
- for SPI NAND, {sfsrc}`linux`, {sfsrc}`mediatek` and {sfsrc}`rockchip`: the
  page read, program execute and feature commands their drivers send every
  part, and Rockchip's 0x03 read from cache and 0x02 program load;

and the 4-byte-address form of each. So an AT45DB DataFlash part, which
has a fast read, does not show one: the only sources listing it for those
parts are U-Boot's and QEMU's defaults, and none says so for the part. An
operation JESD216 guarantees on a part whose SFDP tables a source carries
is that part's own, not a default.

## Registers

Some sources say where a part's status register bits are: its quad enable
(QE) bit, and its block-protection bits ({py:mod}`spiflash.registers`).

A register is named by the command that reads it
({py:class}`~spiflash.registers.Register`), not by what a datasheet calls
it, so one name is one register however the makers name it:

| Register | Read with | Is |
|---|---|---|
| SR1 | RDSR, 0x05 (every part has it) | status register 1 |
| SR2 | [RDSR2](opcodes/RDSR2.md), 0x35 | Winbond's and GigaDevice's status register 2, Spansion's CR1 |
| SR3 | [RDSR3](opcodes/RDSR3.md), 0x15 | Winbond's status register 3, Macronix's configuration register |
| function register | 0x48 | ISSI's function register |
| security register | [RDSCUR](opcodes/RDSCUR.md), 0x2b | Macronix's security register |
| configuration feature | GET FEATURE (0x0f) at 0xb0 | a SPI NAND part's configuration register |
| die select feature | GET FEATURE (0x0f) at 0xd0 | Micron's SPI NAND die select register |

A bit is read and write, or volatile (lost at power off), one-time
programmable, or read only ({py:class}`~spiflash.registers.Writability`),
where its source says: {sfsrc}`flashrom` and {sfsrc}`flashprog` always do,
{sfsrc}`linux` where it marks a part's protection volatile
(`SPI_NOR_SWP_IS_VOLATILE`), openFPGALoader where its TB is one-time
programmable, and the rest not. A source that does not say how a bit is
written does not disagree with one that does: the sources are compared on
where a bit is, and a chip's bit is written as most of the sources saying
so say.

### The quad enable bit

A part's quad enable bit (a record's `quad_enable`) is where it is, or
*none* where the source says the part needs nothing set for quad I/O
({py:data}`~spiflash.registers.QE_NONE`); a source that says nothing gives
nothing. A source's driver default is not a part's: Linux sets SR2 bit 1
for every part, and Macronix's and ISSI's SR1 bit 6 and Micron's none for
every part of theirs, all overridden by a part's own SFDP tables; Dediprog's
template gives SR2 bit 1 to parts whose bit is elsewhere; openFPGALoader's
`NONER` is "not filled in". None of those gives a bit, nor does a
Dediprog `QEbitAddr` in SR1 other than bit 6 (bit 7 is SRWD, bits 2 to 5
block-protect bits: the EN25QH256's 0x20 is its BP3). A quad enable bit
implies `quad_read`: a part has one only to enable quad I/O.

*None* is what a source's driver does: it reads with four lines setting no
bit. That the driver sets nothing is not always that the part has no bit:
{sfsrc}`rockchip` sets none on Toshiba's second-generation parts (`98 e2 40`,
`98 eb 40`, `98 ed 51`: the TC58CVG0S3HRAIJ, ...), where
{sfsrc}`linux` sets bit 0 of their configuration register, a HOLD_D bit its
{upstream}`toshiba.c <linux:drivers/mtd/nand/spi/toshiba.c>` calls the
equivalent of the QE bit; the two are a [data issue](issues/value.md).
{sfsrc}`linux`'s SPI NAND core is the same: for an entry without
`SPINAND_HAS_QE_BIT`, `spinand_init_quad_enable()` clears the bit, and the
driver still reads on four lines where the entry has a quad op variant, so
that entry's bit is *none*. XTX's XT26G0xD and XT26Q0xD are such entries,
though their datasheet's feature 0xb0 bit 0 is a QE bit quad reads need
({sfsrc}`rockchip` sets it): Linux's entries are wrong, and a
[data issue](issues/value.md).

JESD216's quad enable requirement (QER, BFPT DWORD 15: a record's
`quad_enable_requirement`) says where the bit is and how it is written.
{sfsrc}`zephyr`'s boards give it (`quad-enable-requirements`), and a
record's SFDP tables do. A record stores a bit or a requirement, never both,
and the bit is the requirement's
({py:attr}`QuadEnableRequirement.bit <spiflash.registers.QuadEnableRequirement.bit>`),
as are the register operations writing it
({py:data}`~spiflash.derive.REQUIREMENT_OPERATIONS`):

| QER | The QE bit | Its operations |
|---|---|---|
| NONE | none | |
| S2B1v1 | SR2 bit 1 | [WRSR_16](opcodes/WRSR_16.md) (a 1-byte WRSR clears SR2) |
| S1B6 | SR1 bit 6 | (a 1-byte WRSR) |
| S2B7 | SR2 bit 7, read with 0x3f | (0x3e and 0x3f, which have no operation here) |
| S2B1v4 | SR2 bit 1 | [WRSR_16](opcodes/WRSR_16.md) |
| S2B1v5 | SR2 bit 1 | [WRSR_16](opcodes/WRSR_16.md), [RDSR2](opcodes/RDSR2.md) |
| S2B1v6 | SR2 bit 1 | [WRSR2](opcodes/WRSR2.md), [RDSR2](opcodes/RDSR2.md) |

No requirement is worked out from a bit and the operations a source gives:
the W25Q512JV's flashprog entry would make it S2B1v6 or v5, its SFDP says
v4. A chip's requirement is the one most sources give of those putting the
bit where the chip's bit is; where none does, it has none, and the sources
disagree on the bit (a [data issue](issues/value.md)). Writing a chip's SFDP
tables ({py:func}`~spiflash.sfdp_tools.encode`), its requirement goes in
DWORD 15; failing one, a QE bit at SR1 bit 6 is S1B6 (its only code) and no
QE bit NONE, but SR2 bit 1, which four codes write differently, is written
as the reserved code 7 and listed missing, never as 0, "no QE bit".

### Protection bits

A part's block-protection bits (a record's `protection`) are given by role,
as {sfsrc}`flashrom` names them, by what the bit does rather than what the
datasheet calls it: the block-protect bits BP0 to BP4 (by position: a source
may give BP3 alone), top/bottom (TB), sector/block (SEC), complement (CMP),
the status register protect and lock bits (SRP, SRL) and write-protect
selection (WPS) ({py:class}`~spiflash.registers.Protection`). No two roles
are on one bit, nor is the quad enable bit one of them. A block-protection
bit (a BP bit, or TB, SEC or CMP, which only change what the BP bits
protect) implies `lock`. {sfsrc}`linux`'s `SPI_NOR_SWP_IS_VOLATILE` makes
a part's BP bits volatile.

A position a source's driver uses for every part is not the part's:
{sfsrc}`u-boot` reads TB at SR1 bit 5 for every `SPI_NOR_HAS_TB` part (it
has no flag for bit 6, where the W25Q256 and W25Q512 families have it), and
{sfsrc}`qemu`'s model puts every `HAS_SR_TB` part's TB at bit 5 and every
part's BP0 to BP2 at bits 2 to 4; so neither gives a TB, and their
`HAS_SR_TB`, `SPI_NOR_HAS_TB` stay a `lock` claim or a flag. Linux and
U-Boot also lock with BP0 to BP2 at SR1 bits 2 to 4 for every
`SPI_NOR_HAS_LOCK` part; those are kept, as no source puts any of them
elsewhere on any part they list. A source that unlocks a part some other way
({sfsrc}`linux`'s Atmel global protection and SST26 block protection
register, {sfsrc}`u-boot`'s SST26 parts) or only says which bits to clear
({sfsrc}`dediprog`'s `ProtectBlockMask`, {sfsrc}`zephyr`'s `has-lock`) gives
no layout, and its `lock` is a claim.

The sources are compared on each role on its own, and one not giving a role
does not vote on it, so a source giving only TB agrees with a fuller layout
with the same TB. Where the most-given bit of each role puts two roles on
one bit (flashrom's TB is the bit openFPGALoader calls BP3, on the
GD25Q32, XT25F32B and P25Q32H), the chip is given the best source's own
layout, from the most specific records giving one, and it is a
[data issue](issues/shared-bit.md).

## SPI NAND geometry

A SPI NAND part's sources state its page, its erase block (its block
erase's layout), its spare (out-of-band) area per page (`oob_size`), its
planes and most bad blocks per die (`planes`, `max_bad_blocks`), and the
error correction it needs (`ecc`: bits corrected per step of so many bytes,
or bits alone where {sfsrc}`rockchip` gives no step). What follows from
them is worked out, not stored: the blocks (the size over the block), the
pages per block, the blocks per die, a die's size, and the spare area in all.
A chip page lists them as worked out. The sources are compared on the ECC
requirement's strength and step each on its own, and a source giving no step
does not vote on it, so Rockchip's 8 bits agrees with Linux's 8 bits per
512 bytes.

## Dies

A part's `dies` are how many it has in its package, SPI NOR or SPI NAND, as
a source states them or its SFDP tables' SCCR multi-chip table describes.
A die erase is a whole-die erase, so its layout is the dies': the stated die
erase ([DIE_ERASE](opcodes/DIE_ERASE.md), Micron's 0xc4, or
[DIE_ERASE_61](opcodes/DIE_ERASE_61.md), Infineon's 0x61) over the part's
size in that many dies ({py:func}`~spiflash.derive.die_erasers`). It is never
stored: {sfsrc}`flashrom`'s `spi_block_erase_c4` layout, {sfsrc}`dediprog`'s
`DieSizeInKByte` (where it is smaller than the chip) and {sfsrc}`qemu`'s
`die_cnt` are each stored as the dies, and the operation, where the source
has it. Some parts have dies and no die erase (Winbond's W25Q01JV and
W25Q02JV).

A part of several dies may select one, by a command or a register bit, never
both: Winbond's [DIE_SELECT](opcodes/DIE_SELECT.md) and
[NAND_DIE_SELECT](opcodes/NAND_DIE_SELECT.md) (0xc2 and the die), or
Micron's SPI NAND die select bit (feature 0xd0 bit 6, a record's
`die_select_bit`). A SPI NAND part of several LUNs and one target has dies
but no select: they are row address bits.

## Dummy clocks

An operation's page gives its usual dummy clocks
({py:attr}`Operation.dummy_clocks <spiflash.opcodes.Operation.dummy_clocks>`);
a part's own are its sources', stored with the operation wherever an entry
states them, even where they are the usual number
({py:attr}`OpcodeUse.dummy_clocks <spiflash.opcodes.OpcodeUse.dummy_clocks>`):
{sfsrc}`linux`'s SPI NAND op variants (dummy bytes, times 8, over the dummy
phase's lines) and {sfsrc}`mediatek`'s `SNAND_OP`s. Those a part's SFDP
tables give are derived from them, and a stored use giving the same as its
tables is not stored. {sfsrc}`rockchip`'s 8 for every SPI NAND read is its
driver's, no part's. {sfsrc}`flashprog`'s `.dc` bits are not dummy clocks:
they are where the register bits that set them are, which has no field yet.

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
