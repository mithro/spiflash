[QEMU](https://www.qemu.org/) is a machine emulator. Its SPI NOR flash
model, {upstream}`qemu:hw/block/m25p80.c`, emulates the flash chips of the
boards it models: its `known_devices[]` table lists each part it can stand
in for, and the model answers [read-id](../opcodes/RDID.md) with the
entry's id. It is SPI NOR only.

The table is a 2012 copy of {sfsrc}`linux`'s, kept for the parts its boards
emulate: `INFO(name, jedec_id, ext_id, sector_size, n_sectors, flags)`,
`INFO6` with a three-byte extended id, and `INFO_STACKED` with a die count,
under a comment naming the vendor. So its geometry rarely adds anything, and
as the model decodes every opcode for every part (its `FlashCMD` enum), the
table says little per part beyond its geometry and its `ER_4K`/`ER_32K`
flags. The read, fast read, page program and chip erases it decodes for every
part are its defaults, which imply no capability.

It is alone, though, in carrying complete SFDP
([JESD216](https://www.jedec.org/standards-documents/docs/jesd216b)) dumps:
thirteen entries point at the tables in
{upstream}`qemu:hw/block/m25p80_sfdp.c`. Those records keep the dump whole
(their `sfdp`): byte for byte what those parts answer to the SFDP command,
which is data rather than code. From them come the fast reads with the
part's own dummy clocks, the erase types, the 4-byte-address opcodes and the
quad enable method, and the page size where the table has it, and the
capabilities they imply; the read, fast read and page program JESD216 takes
for granted are those parts' own, not the model's defaults. The chip pages
show each dump decoded.
