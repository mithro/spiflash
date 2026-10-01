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

The capabilities a part's SFDP tables support are implied too, where a
source carries the tables (the {sfsrc}`qemu` dumps).

## Driver defaults imply nothing

Some operations a source lists because its driver sends them to every part
(or every part of a kind), whatever the entry says. Those are *driver
defaults* ({py:attr}`OpcodeUse.assumed <spiflash.opcodes.OpcodeUse.assumed>`),
shown on the chip pages as a grey {sfgrey}`◌` (where a
source states the operation for the part, {sfyes}`✓`), and they imply no
capability:

- {sfsrc}`linux`: read, fast read (a board's devicetree choice,
  `m25p,fast-read`), page program and chip erase;
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
{sfsrc}`linux` (0xd8 over its `.sector_size`, 64 KiB where the entry gives
none, which is taken as the entry's own: an entry for a part with other
blocks gives its own; and 0x20 over 4 KiB for `SECT_4K`), {sfsrc}`u-boot`
(the same from its `INFO()` sectors), {sfsrc}`openfpgaloader`
(`sector_erase` and `subsector_erase`), and every SPI NAND source (the
erase block). A Linux part read from SFDP has no layout, as the kernel
takes it from the part's tables at run time.

## Operations

The id read of the way an entry reads the id, and the erase each of its
erase layouts sends, are derived too: see
[](opcodes.md), which says where each source's opcodes come from.
