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
with no name, so a record gives it by its own name. `.decode_range` (how the
BP, TB, SEC and CMP bits map to a protected range: `DECODE_RANGE_SPI25` and
its `_64K_BLOCK`, `_BIT_CMP`, `_2X_BLOCK` and `_BP3_TO_1_16` variants) has no
field: it is what a layout's bits mean, not where they are, and waits for a
model of protected ranges. Nor do `FEATURE_ERASED_ZERO`,
`FEATURE_STATUS_PER_DIE` and `FEATURE_ADDR_2BYTE` (a 2-byte address, which
the address bytes do not cover).

The `FEATURE_4BA_ENTER`, `_ENTER_WREN`, `_ENTER_EAR7`, `_EAR_C5C8` and
`_EAR_1716` bits are the record's ways into 4-byte mode, `en4b`,
`wren_en4b`, `ear_bit7`, `wrear` and `brwr` ({upstream}`flashrom:include/flash.h`;
spi25.c's `spi_enter_exit_4ba` and `spi_write_extended_address_register`),
which give their operations ([](../derived.md#4-byte-addressing)); the way
out, [EX4B](../opcodes/EX4B.md), is stated beside the first two. `_ENTER_EAR7`
writes bit 7 of the extended address register with 0xc5 or, on the
Spansion S25FL256S and S25FL512S entries, which have no `_EAR_C5C8`, with
0x17: it gives no operation of its own. `FEATURE_4BA_READ`, `_FAST_READ`
and `_WRITE` are 4-byte operations.

An `OTP:` comment about the whole entry is its OTP area, the bytes the user
can program ("1024B total, 256B reserved" is 768: Winbond reserves security
register 0), in regions where it says ("3x 512B"), and the commands it
names are its operations ([RSECR](../opcodes/RSECR.md),
[PSECR](../opcodes/PSECR.md), [ESECR](../opcodes/ESECR.md),
[READ_OTP](../opcodes/READ_OTP.md) where 0x4b reads what 0x42 programs,
[ENSO](../opcodes/ENSO.md), [EXSO](../opcodes/EXSO.md),
[ENTER_OTP_3A](../opcodes/ENTER_OTP_3A.md); "read ID 0x4B" is
[RUID](../opcodes/RUID.md)); the comment leaves the notes for the area's
`via`, with `FEATURE_OTP`, which the area implies
([](../derived.md#otp)). ISSI's information row (0x68, 0x62, 0x64),
Atmel's security register (0x77, 0x9b, 0x9a) and PMC's 0xb1 program have no
operation here, and stay in the comment. A comment qualified to one model
or revision of the entry ("(B version only)", "later 3x 512B", "06E 64B
total") stays a note.

A comment on an entry saying it "supports SFDP" gives it the `sfdp`
capability and the [RDSFDP](../opcodes/RDSFDP.md) operation, whose `via`
then holds the comment. A comment qualified to one model of a multi-part
entry ("the latter supports SFDP", "F model supports SFDP", "MX25L1006E
supports SFDP") is kept as a note and claims nothing: the database has no
per-model claim within an entry yet, so that model's SFDP is a known loss
(six entries, in {sfsrc}`flashprog` too).
