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
part.
