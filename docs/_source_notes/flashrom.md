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
([](../derived.md#otp)). ISSI's information row is read, programmed and
erased with [IRRD](../opcodes/IRRD.md) (0x68), [IRP](../opcodes/IRP.md)
(0x62) and [IRER](../opcodes/IRER.md) (0x64); Atmel's security register
(0x77, 0x9b, 0x9a) and PMC's 0xb1 program have no operation here, and stay
in the comment. A comment qualified to one model
or revision of the entry ("(B version only)", "later 3x 512B", "06E 64B
total") stays a note; a command qualified so ("(A version only:) read ID
0x4B") is left out, and the rest taken.

Some comments are wrong, and the record says so in a note:
- ISSI's IS25LP and IS25WP entries give "read 0x48; write 0x42", but on
  ISSI's parts those read and write the function register (as
  {sfsrc}`openfpgaloader` reads its TB bit); their OTP area is the
  information row, read, programmed and erased with 0x68, 0x62 and 0x64
  (the IS25LP256D datasheet, 8.38 to 8.41, and flashrom's own IS25LP256
  comment). The area's size is taken, and IRRD, IRP and IRER;
  [RSECR](../opcodes/RSECR.md) and [PSECR](../opcodes/PSECR.md) are not.
- The S25FL132K's "768B total, 256B reserved" would leave 512 bytes, but
  its datasheet (S25FL1-K, 8.3) gives four 256-byte registers, register 0
  holding the SFDP tables: 768 are the user's.
- The W25Q40.V's "756B total" is 768 (four 256-byte registers, register 0
  reserved).

(`OTP_COMMANDS_WRONG` and `OTP_SIZE_WRONG` in
{repo}`tools/spiflash_extract/flashrom.py`.)

Some entries' values are wrong for their part, and the record stores the
datasheet's, with a note (`ENTRY_WRONG`, both sources):
- "S25FL128S_UL", "S25FL128S_US" and "S25FL256S Large" and "Small Sectors"
  give the S25FS-S's 1.7 V to 2.0 V: the S25FL128S and S25FL256S are 2.7 V
  to 3.6 V parts;
- the "S25FL128S_UL" (extended id 4d 00 80) has uniform 256 KB sectors and
  a 512-byte page, not 128 KB and 256 bytes, as the "S25FL128S......1"
  entry has it, and the "S25FL256S Large Sectors" a 512-byte page too;
- the "S25FL256S" entries give half the part ("This is just half the
  size"): it is 32 MiB;
- the S25FL512S's page is 512 bytes, not 256;
- GigaDevice's 1.8 V GD25LQ, GD25LB, GD25LR and GD25LF entries give 1.695 V
  ({sfsrc}`flashrom`) or 1.65 V ({sfsrc}`flashprog`) to 1.95 V: their
  datasheets give 1.65 V to 2.0 V (2.1 V for the GD25LQ16C and E);
- the XM25QH64C is a 2.3 V to 3.6 V part, not 2.7 V;
- the W25Q128JW is a 1.7 V to 1.95 V part ("W25Q128.JW.DTR"), and
  "W25Q128.W" covers it and the 1.65 V W25Q128FW: the range they share.

The IS25WP256's `FEATURE_4BA_EAR_C5C8` (`wrear`) is the part's, though its
SFDP tables ({sfsrc}`qemu`'s dump) give only `en4b` and `brwr`: ISSI's bank
address register is written with 17h or C5h and read with 16h or C8h (the
IS25LP256D datasheet, 8.47 and 8.48), and DW16 lists one way of each.

An SST entry written a byte or a word (AAI) at a time (`spi_chip_write1`,
`spi_aai_write`) has no page size: the part has no page program, and its
`.page_size` is no page (the SST25VF010A has Byte-Program and AAI only).

And some OTP comments' sizes (`OTP_SIZE_WRONG`): the GD25LQ128D and E, the
GD25Q127C and the GD25Q128E have three 1024-byte security registers, not
"1024B total, 256B reserved" or "1536B total", and the P25Q32SH three
1024-byte ones, not "3 x 512 bytes".

The erase routines that send one opcode are that erase: `s25fl_block_erase`
is 0xdc, `s25fs_block_erase_d8` 0xd8 (`FUNCTION_OPCODES`).

Some other comments say what the whole entry has, and are read so (the
operation's or the field's `via` holds the comment): "Fast read (0x0B)
supported" and its kin give [READ_1_1_1_FAST](../opcodes/READ_1_1_1_FAST.md)
(not "... supported by SST25VF512A only"), "QPI enable 0x38, disable 0xFF"
and "QPI enable 0x35, disable 0xF5" the ways into and out of QPI mode, and
"bit6 is quad enable", on the status register line, the quad enable bit,
SR1 bit 6, where `.reg_bits` puts nothing there, on Macronix's, ISSI's and
PMC's parts; not on Eon's, whose EN25QH parts have no QE bit (SR6 is EBL,
or WHDIS in OTP mode: the EN25QH128A datasheet, Table 7), nor XMC's, which
is not checked. `.die_size` (in KiB, as
`.total_size`) gives the dies, and `.die_select = SPI_DIESELECT_C2`
[DIE_SELECT](../opcodes/DIE_SELECT.md) (Winbond's W77Q12NW and W77T12NW).
`FEATURE_ADDR_2BYTE` is a `2byte_addr` claim (the M95320 EEPROM's 2-byte
addresses). A `.decode_range` other than the usual `decode_range_spi25`
(64 KiB blocks, CMP, ...) says how the protection bits map to a range,
which no field holds: it stays a flag (`decode_range=...`), as do
`FEATURE_ERASED_ZERO` and `FEATURE_STATUS_PER_DIE`. `.printlock`,
`.unlock`, `.spi_cmd_set` and the `.wp_*` functions are its driver's
routines, not the part's facts, and are not kept; nor is `.gran`, the
write granularity flashrom programs in, which the page and the program
operations give.

A comment on an entry saying it "supports SFDP" gives it the `sfdp`
capability and the [RDSFDP](../opcodes/RDSFDP.md) operation, whose `via`
then holds the comment. A comment qualified to one model of a multi-part
entry ("the latter supports SFDP", "F model supports SFDP", "MX25L1006E
supports SFDP") is kept as a note and claims nothing: the database has no
per-model claim within an entry yet, so that model's SFDP is a known loss
(six entries, in {sfsrc}`flashprog` too).

It gives no time per SPI part ([](../derived.md#times)): `.probe_timing` is
for parallel, LPC and FWH chips ("SPI devices will always have zero delay
and ignore this field", {upstream}`flashrom:include/flash.h`), and the poll
intervals of its SPI erase and program routines and its status register
write wait are its driver's, for every part.
