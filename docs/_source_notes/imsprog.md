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
  page and a 64 KiB block: that is the 0xd8 block erase IMSProg sends, so
  it is the record's sector size, and parts with other erase blocks
  disagree with the other sources about it.
- A SPI NAND id is read after a dummy byte. A part with a two-byte id sends
  its manufacturer byte again as the third, which IMSProg keeps and which
  is dropped here, so the id is the one the other sources give. Ids longer
  than three bytes are cut short (the ESMT F50L1G41LB's {sfid}`c8 01 7f`).
- What a record has no field for is kept in its `flags`, under the names
  IMSProg's code gives them: `chipVCC`, the nominal supply (3.3, 1.8, 2.5
  or 5.0 V; not a range, so not the record's supply voltage), `addr4bit`,
  how it enters 4-byte addressing (`0x01` with EN4B, `0x11` the Winbond way,
  `0x21` Spansion's bank register; SPI NAND entries set it too, to values
  the README does not explain), `algorithmCode`, which of its routines
  reads the security registers (and, for SPI NAND, the status registers),
  `delay`, a factor on the bus speed, in thousandths, and for SPI NAND
  `ECCsize`, the spare bytes per page.
- The opcodes are what {upstream}`imsprog:IMSProg_programmer/spi_nor_flash.c`
  sends to a SPI NOR part: read, page program, block and chip erase, and,
  where the entry's `addr4bit` asks for it, the commands entering 4-byte
  addressing.

IMSProg's README says the format was based on the databases of the EZP2019,
EZP2020 and EZP2023, Minipro and XP866+ programmers, whose software is
closed, so some of its values may have come from there and cannot be traced
further. That, and the uniform page and block sizes, is why it ranks below
the curated tables, above only {sfsrc}`qemu` and {sfsrc}`zephyr`. It is
still the only source for many parts, mostly from Chinese makers
(Dosilicon, Zbit, Boya, Fidelix, Giantec, Zetta, XTX, ...), and for many
newer GigaDevice and Eon ones.
