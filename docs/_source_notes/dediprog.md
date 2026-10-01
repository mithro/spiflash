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
`BlockSizeInByte`, as {sfsrc}`linux` gives it. An SPI NOR entry's
`PageSizeInByte` is the template's 256 on all but seven entries, the SST
parts Dediprog writes a byte or a word at a time among them, so only the
other value, 512 (the S25FL512S, the S25HL and S28HS parts), is stored as a
page size.

Some classes of part are not what their attributes say:

- DataFlash (`AT45DB...`) entries carry a SPI NOR template, while
  Dediprog's software reads the part's page size from the chip, so only
  their id and size are taken.
- The SST parts Dediprog writes a byte or a word at a time have 0x02 as
  [byte program](../opcodes/BP.md).
- SPI NAND sizes that count the spare area are scaled back to the data.

`Voltage` is the supply dpcmd powers the part at once it has found it
(parse.c maps "3.3V", "2.5V" and "1.8V" to `VoltageInMv`; project.c,
`GetFirstDetectionMatch`, sets `g_Vcc` from it), so it is the record's
`supply_mv`: a setting of the programmer's, not a supply range, which is
checked against the ranges other sources give the part
([data issues](../issues/supply.md)). Nine parts are 1.2 V, which dpcmd
would power at 3.3 V; the table's value is kept.

`AlternativeID` is another id the part answers, compared with what dpcmd's
probe read as `UniqueID` is; Dediprog does not say which command reads it,
and its forms do: one byte is RES's (0xab) electronic signature (the
M25P16's 0x14, as {sfsrc}`flashrom`'s M25P05 to M25P40-OLD res1 entries
give), two bytes REMS's (0x90) maker and part (the W25Q40's 0xef12, the
EN25QH128's 0x1c17, as their datasheets give). Those are the record's
`legacy_ids` ([](../derived.md#legacy-ids)). Left out: a copy of the id (174
entries, and a legacy-id entry's own tail), an empty `0x`, two templates
noted on their records (Intel's S33 parts of 16, 32 and 64 Mbit all give
0x15; ESMT's F25L parts of every density their maker's byte, 0x8c), Sanyo's
one-byte values (its RES answers two bytes, as flashrom's res2 entries
read it), ZB25VQ80B's three-byte 0x8E6014, and the wrong ones
({py:data}`spiflash_extract.dediprog.ALTERNATIVE_WRONG`, each noted):
XM25QH128A's and XM25QU128C's REMS ids (their datasheets give 20 17, not
0x2016 and 0x2118), ZD25Q40's 0xef12 (Winbond's maker byte, not Zetta's),
and the M25PX parts' one-byte values (0xab only releases them from deep
power-down, with no signature: M25PX80 datasheet). The XM25QH128B's 0x2016,
the 64 Mbit parts' REMS id, looks copied too: not checked.

`UniqueID` is mostly the JEDEC id again (a copy, or with its 0x7f
continuation codes). Its two-byte forms are REMS answers, Eon's (EN25P20's
0x1c11, its datasheet's 90h answer; EN25T80, EN25B40, EN25S16), and are
legacy ids too; a three-byte one that is another JEDEC id stays a flag.

The raw command words, the `ProgramIOMethod` and the
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
gives 128). Its `ReadCmd`, `ProgramCmd` and `EraseCmd` words
(`0x006B000B`, `0x00320002` on nearly every SPI NAND entry) are a
template, and SPI NOR's opcodes besides: they are not taken as operations,
and stay flags. Its `SupportLUT` is the bad block lookup table's swap and read
([NAND_BBM_SWAP](../opcodes/NAND_BBM_SWAP.md),
[NAND_READ_BBM_LUT](../opcodes/NAND_READ_BBM_LUT.md)), and its read-id is
SPI NAND's, from the id method.

`ChipEraseTime` (seconds; set on 1,747 entries, 0 on 846) is a chip erase
time whose bound the table does not say: the record's
`chip_erase.unspecified` ([](../derived.md#times)), 0 being not given.
Against nine datasheets it is the maximum three times (W25Q16JV 25 s,
GD25Q32C 80 s, AT25SF128A 120 s), the typical twice (W25Q64JV 20 s, where
the W25Q64JV-DTR entry gives the maximum, 100 s; S25FL256S 66 s), and
neither four times (W25Q128JV 50 s against 40 s and 200 s; GD25Q64C 160 s
against 25 s and 60 s; MX25L12835F 72 s against 50 s and 80 s; N25Q128A13
240 s against 170 s and 250 s). Dpcmd never reads it (parse.c fills
neither it nor `RDSRCnt` and `WRSRCnt`). So it is compared only with other
unspecified times: Dediprog's own, at an id several entries share.
`Timeout` is a template's poll count (a step function of the size, one
value in 28 of 41 templates, and never filled by dpcmd), not a time of the
part, and is not taken.

`Clock` (`clock` on 40 entries, `CLOCK` on one) is the record's
`listed_clock_hz` where it is one clock in MHz: the clock Dediprog lists,
a catalogue figure whose meaning it does not give (not read by dpcmd), and
not a safe maximum, so it is shown as "Dediprog lists 104 MHz" and not
compared. It is a poor guide to the part's fastest clock: against twelve
datasheets it was that three times
(GD25WB256E 104 MHz, MX66L1G45G 166 MHz, W25Q512JV 133 MHz), a slower clock
seven times (AT25SF128A 104 against 133 MHz, GD25LB256E 133 against 166,
MX25L12833F 104 against 133, MX25R6435F 70 against 80, MX25R8035F 70
against 108, MX25U25645G 104 against 166, MX25U6432F 85 against 133), above
it once (W25Q80BL 75 MHz, against 50 MHz in its datasheet, Rev. G1), and
once no clock (the IS25WP256D's `166Mbit`). Its `Description`'s "With
NN MHz SPI Bus" is a template too, so is not taken either; where it names
other clocks than `Clock`, a note on the record says so. The `Description`
is a note only where it says more than the size, supply class and clock
("... with Boot and Parameter Sectors", "DataFlash"): "128 Mbit, Low
Voltage, Serial Flash Memory With 104MHz SPI Bus Interface", on 782
entries, says only what the fields hold. Left out, each with a note: two clocks, a read's and a
fast read's (`33/100MHz` and the like, 55 entries: not one fact), a value
in no unit or the wrong one (`166`, `166Mbit`, `A13112`), and `416MHz` (the
A25LQ64's and the W25Q64FW's 104 MHz quad read, as 416 Mbit/s).

The entry has much that the database has no
field for yet: the status and configuration register
commands, the 4 KiB sector size where no opcode
goes with it, the classes' die counts (`N25Qxxx_Large_2Die`, which are its
programming algorithms'), and for SPI NAND the read dummy length and the
bad block marker. Its SPI NAND `DefaultErrorBits` and `DefaultDataUnitSize`
would be the record's `ecc`, but they are a template's (8 bits per 528
bytes on 202 of the 210 entries, whatever the part needs; one gives 544
bits), so are not taken.

Its entries are not reviewed in the open, and some are wrong: an entry's
read and program words swapped, a block size larger than the chip, several
entries for one id with different sizes, a chip erase (0xc7) on the stacked
MT25QL01GBBB and MT25QU01GB, which erase a die at a time and have none (left
out, with a note). A source listing a part several
times still has one vote when the sources disagree.
[Its data issues page](../issues/source-dediprog.md) lists where it
disagrees with the others.
