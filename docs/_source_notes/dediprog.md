[Dediprog](https://www.dediprog.com/) makes SPI flash programmers: the SF100,
SF600 and SF700. The Linux software for them, {github}`DediProgSW/SF100Linux`,
ships the chip database of Dediprog's own programming software,
{upstream}`dediprog:ChipInfoDb.dedicfg`. It is a UTF-16 XML file with one
`<Chip .../>` per line and some seventy attributes on each. Its values are
Dediprog's own, not copied from another project here. It is the largest
table here, with SPI NOR and SPI NAND. It also lists microcontrollers and
FPGA configuration memory that the programmers write; those have no id and
are left out.

GitHub does not display a file this large, so a link to one of its lines
opens the file's page without jumping to the line. The line number is in
each link's text (`ChipInfoDb.dedicfg:947` on the chip pages), and the
file's raw copy has the same lines.

The Linux software reads few of the attributes
({upstream}`dediprog:parse.c`). To identify a chip, it sends a fixed
sequence of commands (read-id, 0x9f, reading 4, 3 and 2 bytes, then 0x15,
0xab and 0x90) and compares each answer, as a number, with every entry's
`JedecDeviceID` ({upstream}`dediprog:dpcmd.c`). Each entry also names a
command (`RDIDCommand`) and a byte count (`IDNumber`), which Dediprog's
parser ignores; spiflash's reads the id with them:

- JEDEC [read-id](../opcodes/RDID.md) (0x9f) for most parts, and Micron's
  multiple I/O read-id (0xaf), which answers the same bytes;
- [REMS](../opcodes/REMS.md) (0x90), [RES](../opcodes/RES.md) (0xab) or
  [AT25F](../opcodes/RDID_ATMEL.md) (0x15) for older parts.

`RDIDCommand` is where a record's id method comes from. Elsewhere the id
method gives the id operation, but a record whose command is not 0x9f names
the command it sends: the command is the provenance of its id method (`via`,
under `id_method`), not a flag, and the record states that command's
operation rather than deriving one. A 0x9f entry whose answer is a RES id
(Sanyo's) states [`RDID`](../opcodes/RDID.md), and derives no RES. 0xaf (14
MT25Q entries) is no operation here: Micron's parts answer it in their dual
and quad I/O protocols with the bytes 0x9f answers in single-line SPI, and a
spiflash operation has one shape on the bus, so those records give no id
operation at all.

The number holds the bytes as they come. An SPI NAND id read with a dummy
byte first starts with that byte, 00 (`0x00EFAA21`), which is not part of
the id. RES, read without its three dummy address bytes, starts with three
0xff. The entries' SPI NAND ids give two or three bytes after that dummy
byte, and spiflash keeps them as given, so a NAND id can be a byte longer or
shorter than the one {sfsrc}`linux` matches for the same part. The shorter
id is folded into the longer one where that is unambiguous (the chip page
lists it as matched on its first bytes; see {sfsrc}`rockchip`'s page).

Some ids are under the wrong command, and spiflash's parser reads each for
what it is:

- a three-byte REMS id is the part's JEDEC id;
- Sanyo's parts answer 0x9f with their two id bytes repeated, which the
  parser takes as the RES id {sfsrc}`flashrom` gives them;
- a two-byte 0x9f id whose `UniqueID` is those two bytes with a third in
  front takes the three.

Where `JedecDeviceID` is missing, `UniqueID`, which otherwise repeats it,
gives the id. Left out, and counted when the data is rebuilt, are:

- entries with no id at all (the microcontrollers, the iCE65 and two flash
  parts);
- an id with an odd number of hex digits;
- the REMS or RES ids of parts that do not answer 0x9f, listed under 0x9f.

An entry gives the size, the page size and the opcodes, one per byte of
`ReadCmd`, `ProgramCmd` and `EraseCmd`: the single, dual and quad read or
program, and the chip, block and die erase. The fourth byte is reserved in
the software's `struct ReadCommand`, though octal parts have an octal opcode
there. Only the single-line read and program are taken, because the wider
ones are often a template's defaults (the single-I/O SST25LF040A lists quad
read and quad page program). For the same reason the 0xd8 and 0xdc erases
have no layout. `BlockSizeInByte` is 64 KiB in nearly every entry, where the
other sources give those erases 32 KiB (M25P05, EN25F10, SST25VF010A),
128 KiB (MT35XU01G), 256 KiB (M25P128, S25FL512S) or boot blocks (AMIC's
A25L..P). An SPI NOR record has an erase layout only for 0x20
(`SectorSizeInByte`) and 0x52: 32 KiB on the SST parts, and
`SectorSizeInByte` on the AT25F parts where it is not the template's 4 KiB
(0x52 erases 64 KiB on the AT25F2048); so a sector size only from its 0x52
blocks. An SPI NAND record's block erase is over its erase block,
`BlockSizeInByte`, as {sfsrc}`linux` gives it.

Some classes of part are not what their attributes say:

- DataFlash (`AT45DB...`) entries carry a SPI NOR template, while
  Dediprog's software reads the part's page size from the chip, so only
  their id and size are taken.
- The SST parts Dediprog writes a byte or a word at a time have 0x02 as
  [byte program](../opcodes/BP.md).
- SPI NAND sizes that count the spare area are scaled back to the data.

The raw command words, the nominal `Voltage`, the `ProgramIOMethod` and the
`Class` are kept in the record's `flags`, unless an operation or a value
already names one as where it came from (`via`). `QEbitAddr` is the quad
enable bit, a mask over the status registers (SR1 its low byte, then SR2):
`0x40` is SR1 bit 6. Its template's `0x200` (SR2 bit 1, on 255 of 356
Macronix parts and 89 of 90 Micron ones, whose bit is elsewhere or none) and
`0` say nothing of the part, and are not taken; nor is an SR1 bit other
than 6, which is a status, block-protect or SRWD bit (the MX25U51271G's
`0x80` is SRWD, the EN25QH256's `0x20` its BP3, a part whose SFDP says it
has no QE bit), and a note says so. `ProtectBlockMask` is
the bits its software clears to unprotect the part, not where each role is
(0x9C on the W25Q128FV, 0xFC on the W25Q128JV, whose bits are the same), so
it gives only the `lock` claim. `DieSizeInKByte` gives the record's `dies`
where the die is smaller than the chip (six entries, the MT25Q and N25Q00
parts): 252 of the 260 entries giving one give the chip's size, a
template's value, and two (the S79FL01GS and S79FS01GS "one die" entries) a
die larger than the chip. Its die erase layout is the dies', never stored.
A SPI NAND entry's `SpareSizeInByte` holds two spare sizes, one in each
half: the high half, the whole spare area of a page, is the record's
`oob_size`; the low half (the spare left free beside the part's own ECC) is
not taken (the XT26Q01D's high half, 64 bytes, is wrong: its datasheet
gives 128). Its `ReadCmd` and `ProgramCmd` words (`0x006B000B`,
`0x00320002` on nearly every SPI NAND entry) are a template, and are not
taken. Its `SupportLUT` is the bad block lookup table's swap and read
([NAND_BBM_SWAP](../opcodes/NAND_BBM_SWAP.md),
[NAND_READ_BBM_LUT](../opcodes/NAND_READ_BBM_LUT.md)), and its read-id is
SPI NAND's, from the id method. The entry has much that the database has no
field for yet: the maximum clock, the status and configuration register
commands, erase and program timeouts, the 4 KiB sector size where no opcode
goes with it, the classes' die counts (`N25Qxxx_Large_2Die`, which are its
programming algorithms'), and for SPI NAND the ECC layout
(`DefaultErrorBits`, `DefaultDataUnitSize`), the read dummy length and the
bad block marker.

Its entries are not reviewed in the open, and some are wrong: an entry's
read and program words swapped, a block size larger than the chip, several
entries for one id with different sizes. A source listing a part several
times still has one vote when the sources disagree.
[Its data issues page](../issues/source-dediprog.md) lists where it
disagrees with the others.
