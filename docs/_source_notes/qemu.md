[QEMU](https://www.qemu.org/) is a machine emulator. Its SPI NOR flash
model, {upstream}`qemu:hw/block/m25p80.c`, emulates the flash chips of the
boards it models: its `known_devices[]` table lists each part it can stand
in for, and the model answers [read-id](../opcodes/RDID.md) with the
entry's id. It is SPI NOR only.

The table is a 2012 copy of {sfsrc}`linux`'s, kept for the parts its boards
emulate: `INFO(name, jedec_id, ext_id, sector_size, n_sectors, flags)`,
`INFO6` with a three-byte extended id, and `INFO_STACKED` with a die count
(the record's `dies`; the model erases a die, its size over the count, for
[DIE_ERASE](../opcodes/DIE_ERASE.md)), under a comment naming the vendor.
Its `.page_size = 256` is every entry's (`INFO` writes it), the model's
default, so a record has none but what its SFDP dump gives. So its geometry rarely adds anything, and
as the model decodes every opcode for every part (its `FlashCMD` enum), the
table says little per part beyond its geometry and its `ER_4K`/`ER_32K`
flags. Its model keeps BP0 to BP2 at SR1 bits 2 to 4 for every part, and
TB at bit 5 for every `HAS_SR_TB` part, so those are no part's own:
`HAS_SR_TB` is a `lock` claim, and `HAS_SR_BP3_BIT6` (BP3 at bit 6) all of
a record's `protection`. The read, fast read, page program and chip erases it decodes for every
part are its defaults, which imply no capability.

It is alone, though, in carrying complete SFDP
([JESD216](https://www.jedec.org/standards-documents/docs/jesd216b)) dumps:
thirteen entries point at the tables in
{upstream}`qemu:hw/block/m25p80_sfdp.c`. Those records keep the dump whole
(their `sfdp`, with the `.sfdp_read` function serving it as its `via`):
byte for byte what those parts answer to the SFDP command, which is data
rather than code. Everything the dump says is worked out from it when the
data is loaded, not stored again: the fast reads with the part's own dummy
clocks, the erase types, the 4-byte-address opcodes, the page size and the
quad enable requirement where the table has them, and the capabilities they
imply; the read 0x03 a part with a
BFPT has is those parts' own, but SFDP gives no sign of fast read 0x0b or
page program 0x02, so those stay the model's defaults
([](../derived.md#sfdp-tables)). The entry's own geometry, which the model
uses, is stored only where it differs from the dump's: on every one of the
thirteen it agrees, so their size and erase layouts come from the dumps; the
`ER_4K` and `ER_32K` flags stating an erase layout a dump gives stay its
provenance (the record's `via`, shown in a chip page's erase layouts). The
chip pages show each dump decoded. Its model keeps no time (it never sets
the busy bit), but the eight records whose dumps have DWORDs 10 to 14 have
the times those give, derived ([](../derived.md#times)), and deep
power-down (DP and RDPD); the BFPT 1.0 dumps give none.
