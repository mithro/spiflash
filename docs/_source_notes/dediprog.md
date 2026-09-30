[Dediprog](https://www.dediprog.com/) makes SPI flash programmers, the SF100,
SF600 and SF700. The Linux software for them, {github}`DediProgSW/SF100Linux`,
ships the chip database of Dediprog's own programming software,
{upstream}`dediprog:ChipInfoDb.dedicfg`: a UTF-16 XML file with one
`<Chip .../>` per line and some seventy attributes on each. Its values are
Dediprog's own, not copied from another project here. It is the largest
table here, and has SPI NOR and SPI NAND; it also lists microcontrollers and
FPGA configuration memory the programmers write, which have no id and are
left out.

The programmer identifies a chip by sending the entry's `RDIDCommand` and
comparing the `IDNumber` bytes it reads back with `JedecDeviceID`. Most
entries use JEDEC [read-id](../opcodes/RDID.md) (0x9f); older parts
[REMS](../opcodes/REMS.md) (0x90), [RES](../opcodes/RES.md) (0xab) or
[AT25F](../opcodes/RDID_ATMEL.md) (0x15), and Micron's multiple I/O read-id
(0xaf), which answers the JEDEC id. The bytes it reads are taken as they come:
an SPI NAND read with a dummy byte before its id has that 00 byte first
(`0x00EFAA21`), which is not part of the id, and RES, read without its three
dummy address bytes, has three 0xff first. Up to three bytes are compared, so
a NAND id can be a byte longer or shorter than the one {sfsrc}`linux` gives
the same part.

An entry gives the size, the page size and the erase block
(`BlockSizeInByte`, the record's sector size), and the opcodes it uses, one
per byte of `ReadCmd`, `ProgramCmd` and `EraseCmd`: the single, dual, quad
and octal read or program, and the chip, block and die erase. Only the
single-line read and program are taken: the wider ones are often a
template's defaults (the single-I/O SST25LF040A lists quad read and quad
page program). The raw words, the `ProgramIOMethod` and the `Class` are kept
in the record's `flags`.

It gives much that the database has no field for yet: the maximum clock, the
supply voltage (only its nominal value, `3.3V`), the status and
configuration register commands and the quad enable bit, erase and program
timeouts, the 4 KiB sector size with no opcode, the die size, and for SPI
NAND the spare area, the ECC layout and the bad block marker.

Its entries are not reviewed in the open, and some are wrong: ids under the
wrong command, an entry's read and program words swapped, a block size
larger than the chip, SPI NAND sizes that count the spare area. The entries
{repo}`the parser <tools/spiflash_extract/dediprog.py>` cannot take (no id,
an id that does not fit its command) are left out and counted when the data
is rebuilt; [its data issues page](../issues/source-dediprog.md) lists where
it disagrees with the others.
