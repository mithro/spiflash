[OpenOCD](https://openocd.org/), the Open On-Chip Debugger, programs
microcontrollers and SoCs through JTAG and SWD, including the SPI flash
beside them: several of its flash drivers (for the STM32 QSPI and OctoSPI
controllers, the SiFive SPI controller, the Atheros ath79's and others)
share the one table in {upstream}`openocd:src/flash/nor/spi.c`. It is SPI
NOR only, FRAM included.

An entry is `FLASH_ID(name, read_cmd, qread_cmd, pprog_cmd, erase_cmd,
chip_erase_cmd, device_id, pagesize, sectorsize, size)`, or `FRAM_ID(...)`
without the erase commands and sizes. `device_id` holds the JEDEC
[read-id](../opcodes/RDID.md) bytes little-endian, with the number of 0x7f
continuation codes in its top byte: the driver matches the chip's read-id
answer against it. Names are "vendor abbreviation, part"
(`"win w25q128fv/jv"`), and some leave off the family prefix
(`"mac 25l12845"` is the MX25L12845); the parser puts it back.

It gives the read, quad read, page program, sector erase and chip erase
opcodes per part. Its chip erase, 0xc7, is wrong for Micron's stacked
MT25QL01G, MT25QU01G, MT25QL02G and MT25QU02G, which have only a die erase
(0xc4): it is left out, with a note. Its AT25F512, AT25F1024, AT25F2048 and
AT25F4096 `device_id`s (`0x0065001f`, ...) are no answer to 0x9f, which
those parts do not have: their own id read, 0x15, answers 1f 65, 1f 60,
1f 63 and 1f 64, and the records have those ids, read so, with a note. It gives no time: its drivers' timeouts are their own
([](../derived.md#times)). Its {upstream}`openocd:src/helper/jep106.inc`, a copy of
[JEDEC's list](https://www.jedec.org/standards-documents/docs/jep-106ab), is
where the [JEP106 manufacturer names](../jep106/index.md) come from.
