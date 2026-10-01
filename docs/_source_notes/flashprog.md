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

Its `.reg_bits` are read as {sfsrc}`flashrom`'s are, and have one more role,
`.qe`: the quad enable bit, the record's `quad_enable`, which no flashrom
entry gives. Seven are `{STATUS2, 1, RO}` with the comment "Fixed QE=1": a
bit that is always set, so read only. Eight more (the GD25LF, GD25LB512MF
and GD55LB parts) have the same comment on `{STATUS2, 1, RW}`: the comment
is right (their datasheets: "QE = 1 permanently"), and their records have
the bit read only, with a note. Its {upstream}`flashprog:spi25_statusreg.c` reads a
`CONFIG` bit with RDCR (0x15) and a `SECURITY` bit with RDSCUR whatever the
feature bits, so an entry naming them has [RDSR3](../opcodes/RDSR3.md) and
[RDSCUR](../opcodes/RDSCUR.md). Its `.dc` (the bits setting the dummy
clocks) has no field yet. Its `.dummy_cycles` give the QPI quad I/O read's
([READ_4_4_4](../opcodes/READ_4_4_4.md), 0xeb in QPI mode) dummy clocks:
`.qpi_fast_read_qio`'s, or `.qpi_read_params`'s setting 00, the one after
reset (the other settings stay a flag, `qpi_read_params.01-11=4,6,8`);
`.qpi_fast_read` (0x0b in QPI mode) has no operation here, and stays a flag.

Its "supports SFDP" comments are read as {sfsrc}`flashrom`'s are: only an
unqualified one claims SFDP.

Its `FEATURE_4BA_*` ways into 4-byte mode and its `OTP:` comments are read
as {sfsrc}`flashrom`'s are ([](../derived.md#4-byte-addressing),
[](../derived.md#otp)). Like flashrom, it gives no time per SPI part: its
`.probe_timing` is for parallel chips, and its waits are its driver's.
