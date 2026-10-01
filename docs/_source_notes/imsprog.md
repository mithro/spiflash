[IMSProg](https://github.com/bigbigmdm/IMSProg) is a programmer for I2C,
MicroWire and SPI EEPROM and flash chips, driven through cheap CH341A, CH347
and FT232H adapters. Its **Detect** button sends a JEDEC
[read-id](../opcodes/RDID.md) and looks the three bytes that come back up in
its chip database, {upstream}`imsprog:IMSProg_programmer/database/IMSProg.Dat`,
to know the part's size, page and block size and how to address it.

The database is a binary file, not source code: a run of 68-byte entries,
laid out as the "Chip database format" section of IMSProg's README says (and
as its {upstream}`imsprog:IMSProg_programmer/mainwindow.cpp` reads them). It
has no lines, so a record's `line` is the entry's number in the file, from 1,
and its link goes to the file itself.

- Only the SPI NOR and SPI NAND entries are taken. The rest, about a quarter
  of the file, are I2C (24xx), MicroWire (93xx) and SPI (25xx, 95xx)
  EEPROMs, among them the FRAMs, and AT45 DataFlash, which IMSProg files
  as an EEPROM.
- An entry gives the part, the manufacturer's name, the three id bytes, the
  size, the page size and the block size. Every SPI NOR entry has a 256 B
  page and a 64 KiB block, and IMSProg's GUI offers nothing else for a SPI
  NOR part: it programs every part in 256-byte pages and erases every part
  with 0xd8 at every 64 KiB, whatever its real erase blocks (256 KiB on the
  M25P128 and S25FL128S, 32 KiB on the M25P10, boot sectors on the EN25B
  and A25L...P parts). They are IMSProg's defaults, not facts about the part,
  so a SPI NOR record has them only as flags (`pageSize`, `blockSize`), with
  no page size, sector size, erase layout or 64 KiB erase capability; the
  read, page program and 0xd8 erase it sends are its driver's defaults,
  which imply no capability. The SPI NAND entries' page and block sizes
  vary by part, and are taken (the block as its block erase's layout).
- A SPI NAND id is read after a dummy byte. A part with a two-byte id sends
  its manufacturer byte again as the third, which IMSProg keeps. For the
  makers whose SPI NAND ids are two bytes (XTX, Micron and ESMT, Zetta,
  Macronix, GigaDevice, Dosilicon) it is dropped here, so the id is the one
  the other sources give; from any other maker, the extractor stops rather
  than guess. GigaDevice's GD5F1GQ4xF parts answer with no dummy byte ("9FH
  MID DID DID" in their datasheet, and in {sfsrc}`linux`), and their records
  say so. Ids longer than three bytes are cut short (the ESMT F50L1G41LB's
  {sfid}`c8 01 7f`).
- What a record has no field for is kept in its `flags`, under the names
  IMSProg's code gives them: `chipVCC`, the nominal supply (3.3, 1.8, 2.5
  or 5.0 V; not a range, so not the record's supply voltage), `addr4bit`,
  how it enters 4-byte addressing (`0x01` with EN4B, `0x11` the Winbond way,
  `0x21` Spansion's bank register; SPI NAND entries set it too, to values
  the README does not explain), `algorithmCode`, which of its routines
  reads the security registers (and, for SPI NAND, the status registers),
  and `delay`, a factor on the bus speed, in thousandths. A SPI NAND
  entry's `ECCsize`, its spare bytes per page in 64-byte units, is the
  record's `oob_size`: it agrees with the other sources on 63 of the 79
  chips they give one for; most of the rest are 192 against 256 bytes on a
  4 KiB-page part, or 64 against 128 on a 2 KiB one (each a
  [data issue](../issues/value.md)).
- The opcodes are what {upstream}`imsprog:IMSProg_programmer/spi_nor_flash.c`
  sends to a SPI NOR part: read, page program, the 0xd8 block erase (its
  erase is a loop of those; its chip-erase routine is never called), and,
  where the entry's `addr4bit` asks for it, the commands entering 4-byte
  addressing.

Some entries are wrong, and are left out rather than add a chip that does
not exist, or a size only IMSProg gives; {repo}`tools/update_db.py` counts
them, and {py:data}`spiflash_extract.imsprog.WRONG` lists them:

- **Size contradicts the part number and the id's capacity byte:** the
  Excel Semiconductor ES25P10, P20, P40, P80, P16 and P32 and ES25M40A,
  M80A and M16A are each listed at twice their size (the ES25P80 is 8 Mbit,
  its id's last byte 0x14 says 1 MiB, and {sfsrc}`flashrom` agrees); the
  ESMT F25L008A at 2 MiB (8 Mbit, 0x14; {sfsrc}`flashrom` and
  {sfsrc}`dediprog` say 1 MiB); the Eon EN25E40A at 256 KiB (4 Mbit, 0x13;
  {sfsrc}`dediprog` says 512 KiB).
- **Wrong id, per the datasheet:** the AMIC A25L40PT is listed under the
  A25L20PT's {sfid}`37 20 22` (its own is {sfid}`7f 37 20 13`); the Puya
  P25Q06H under {sfid}`85 00 10`, which no part answers (its datasheet, and
  {sfsrc}`flashrom` and {sfsrc}`dediprog`, give {sfid}`85 40 10`); the
  Micron MT29F4G01ABAFD under {sfid}`2c 36`, the MT29F4G01ADAGD's (its own is
  {sfid}`2c 34`); the PCT25VF010A under {sfid}`bf 49 00`, the SST25VF010A's
  REMS answer padded out, where the part has no JEDEC read-id.
- **Wrong name:** the "DS35Q4GM(1.8V)" at {sfid}`e5 a4` is the DS35M4GM:
  Dosilicon's Q parts are 3.3 V and its M parts 1.8 V, as the entry's own
  VCC byte says. Kept, it would have a search for the DS35Q4GM find it as
  well as the real one, at {sfid}`e5 f4`.

Each is named by its part, its id and, for a wrong size, that size, so an
entry corrected upstream is taken again, and the extraction stops on a
known error that is no longer there, for it to be removed.

IMSProg's README says the format was based on the databases of the EZP2019,
EZP2020 and EZP2023, Minipro and XP866+ programmers, whose software is
closed, so some of its values may have come from there and cannot be traced
further. That, and its known errors, is why it ranks below
the curated tables, above only {sfsrc}`qemu` and {sfsrc}`zephyr`. It is
still the only source for many parts, mostly from Chinese makers
(Zbit, Dosilicon, Boya, UCUNDATA, Zetta, XMC, Yuchuang, Fidelix, ...).
