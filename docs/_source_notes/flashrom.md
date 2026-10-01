[flashrom](https://www.flashrom.org/) is a utility for detecting, reading,
writing, verifying and erasing flash chips, through a wide range of
programmers: USB adapters, mainboard chipsets, a Raspberry Pi's SPI pins and
more. Its chip table covers parallel, LPC, FWH and SPI flash; only the SPI
chips are taken, as parallel, LPC and FWH parts are not SPI flash. Every one
of them is SPI NOR.

The table is kept one file per vendor under {upstream}`flashrom:flashchips/`,
as `struct flashchip` initialisers, with the ids `#define`d in
{upstream}`flashrom:include/flashchips.h`: a manufacturer in a later JEP106
bank carries its 0x7f continuation codes (`EON_ID 0x7F1C`) and has a
`_NOPREFIX` twin for chips that leave them out.

It identifies a chip by probing it: most by JEDEC [read-id](../opcodes/RDID.md),
and chips that predate read-id by a legacy id ([REMS](../opcodes/REMS.md),
[RES](../opcodes/RES.md), [AT25F](../opcodes/RDID_ATMEL.md)).

With {sfsrc}`flashprog`, its fork, it has the most detail per part: every
erase opcode with its block layout, the supply voltage, the test status
(`TEST_OK_PREW`: probe, read, erase and write tested), the feature bits
(`FEATURE_*`), and the legacy ids. Its names use `.` as a wildcard
(`W25Q128.V` is the BV, FV and JV), so such a name is a family rather than a
part. A die erase eraser (`spi_block_erase_c4` over `{64 MiB, 2}` on the
MT25QL01G) gives the record's `dies` and its [DIE_ERASE](../opcodes/DIE_ERASE.md);
its layout is the dies', so is not stored ([](../derived.md#dies)).

Its `.reg_bits` say where each status register bit with a role is, by what
the bit does (`.tb` is a bit that works as TB, whatever the datasheet calls
it): the record's `protection`, `.bp` as `bp0`, `bp1`, ... in order, each
register named by the command reading it (`STATUS2` is SR2, read with 0x35;
`CONFIG`, Macronix's configuration register read with 0x15, is SR3), and
so its `lock` ([](../derived.md#registers)). The `FEATURE_WRSR*`, `CFGR` and
`SCUR` bits say how it reads and writes the second and third registers
([RDSR2](../opcodes/RDSR2.md), [WRSR_16](../opcodes/WRSR_16.md), ...).
`FEATURE_WRSR_EXT3` is the `FEATURE_WRSR_EXT2` bit and one of its own,
with no name, so a record gives it by its own name.

A comment on an entry saying it "supports SFDP" gives it the `sfdp`
capability and the [RDSFDP](../opcodes/RDSFDP.md) operation, whose `via`
then holds the comment. A comment qualified to one model of a multi-part
entry ("the latter supports SFDP", "F model supports SFDP", "MX25L1006E
supports SFDP") is kept as a note and claims nothing: the database has no
per-model claim within an entry yet, so that model's SFDP is a known loss
(six entries, in {sfsrc}`flashprog` too).
