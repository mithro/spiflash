# Opcodes

Every chip page lists the SPI operations the sources say that part has. This
page lists the operations spiflash knows, and how many chip ids list each.

Operations are named as LiteSPI's `SpiNorFlashOpCodes` names them, so a part's
list can be used there directly. In `READ_1_4_4` the numbers are the data
lines used for the command, the address and the data (here: command on one
line, address and data on four); `_4B` is the form taking a 4-byte address,
and `8D` marks double transfer rate.

## Where each source's opcodes come from

| Source | Where the opcodes come from |
|---|---|
| flashrom, flashprog | the probe, the `.read`/`.write` functions, each block eraser (`spi_block_erase_20` sends 0x20), and the feature bits (`FEATURE_FAST_READ_QIO`, `FEATURE_4BA_ENTER`, `FEATURE_QPI_38_FF`, ...) |
| Linux | what {upstream}`linux:drivers/mtd/spi-nor/core.c` sets up for the entry: read, fast read and page program by default; the `no_sfdp_flags` (dual, quad and octal read, 4 KiB erase); sector and chip erase; and the 4-byte forms for `SPI_NOR_4B_OPCODES` |
| U-Boot | the same from its {upstream}`u-boot:drivers/mtd/spi/spi-nor-core.c` (`SPI_NOR_NO_FR`, `SST_WRITE`, `USE_FSR`, `NO_CHIP_ERASE`, ...) |
| OpenOCD | the columns of its table: read, fastest read, page program, sector erase and chip erase |
| openFPGALoader | what its {upstream}`openfpgaloader:src/spiFlash.cpp` sends: read, page program, and the erases its table allows |
| QEMU | what its model ({upstream}`qemu:hw/block/m25p80.c`) decodes for every part (read, fast read, page program, sector erase, and chip erase as 0xc7 and 0x60), the erases its `ER_4K`/`ER_32K` flags allow, die erase for stacked parts, and, for the parts it has SFDP tables for ({upstream}`qemu:hw/block/m25p80_sfdp.c`), everything those tables list, with the part's own dummy clocks |
| Zephyr | the board's devicetree: the reads and erase types in the chip's own SFDP table where the board copies it (`sfdp-bfp`, JESD216's Basic Flash Parameter table), and the read and program modes the board uses (`readoc`, `writeoc`, `use-fast-read`, `enter-4byte-addr`, ...) |

The opcode values themselves are read from each upstream's own headers
(`SPINOR_OP_*`, `JEDEC_*`, `SPIFLASH_READ_ID`, `FLASH_*`, QEMU's `FlashCMD`
enum), or from the SFDP tables (QEMU, Zephyr), and checked against the table
below when the data is built: a disagreement fails the build.

:::{important}
A listed operation is one some source says the part has. **An operation that
is not listed may still be supported**: no source here describes every opcode
of every part, and for parts it reads from SFDP, Linux gets the read, program
and erase opcodes from the chip at run time, so it lists only its defaults.
Parts that share an id can differ as well. Each chip page says which sources
list which opcode, and why.
:::

## The operations

Each has a page: what it does, its timing diagram, and the parts that support
it.

```{include} _generated/opcodes-table.md
```

```{toctree}
:hidden:
:glob:

opcodes/*
```
