[flashprog](https://flashprog.org/) is a fork of {sfsrc}`flashrom`: the same
kind of utility, for detecting, reading, writing and erasing flash chips
through many programmers. Its repository is not on GitHub, so the links
here go to its official GitHub mirror. Like flashrom's, its chip table covers parallel, LPC, FWH and SPI
flash, and only the SPI chips are taken, as parallel, LPC and FWH parts are
not SPI flash. Every one of them is SPI NOR.

It keeps the single table flashrom had before splitting it per vendor,
{upstream}`flashprog:flashchips.c`, with the ids `#define`d in
{upstream}`flashprog:include/flashchips.h`. The entries look like
flashrom's, with the id under `.id` (`.id.manufacture`, `.id.model`, and
`.id.type = ID_SPI_RDID` for the probe).

It identifies a chip by probing it: most by JEDEC [read-id](../opcodes/RDID.md),
and chips that predate read-id by a legacy id ([REMS](../opcodes/REMS.md),
[RES](../opcodes/RES.md), [AT25F](../opcodes/RDID_ATMEL.md)).

With {sfsrc}`flashrom` it has the most detail per part: every erase opcode
with its block layout, the supply voltage, the test status, the feature bits
(`FEATURE_*`), and the legacy ids. Its names, like flashrom's, use `.` as a
wildcard (`W25Q128.V`).

Its "supports SFDP" comments are read as {sfsrc}`flashrom`'s are: only an
unqualified one claims SFDP.
