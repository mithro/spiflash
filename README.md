# spiflash

A database of SPI flash chips — JEDEC ids, part names, sizes, page and sector
sizes, erase opcodes and block layouts, supply voltages and capabilities —
merged from the flash tables of every project that keeps one:

| source | what is read | records |
|---|---|---|
| [flashrom](https://github.com/flashrom/flashrom) | [`flashchips/*.c`](https://github.com/flashrom/flashrom/tree/main/flashchips) (SPI chips only) | 512 |
| [flashprog](https://review.sourcearcade.org/flashprog.git) | [`flashchips.c`](https://github.com/SourceArcade/flashprog/blob/main/flashchips.c) (SPI chips only) | 480 |
| [Linux](https://github.com/torvalds/linux) | [`drivers/mtd/spi-nor/*.c`](https://github.com/torvalds/linux/tree/master/drivers/mtd/spi-nor), [`drivers/mtd/nand/spi/*.c`](https://github.com/torvalds/linux/tree/master/drivers/mtd/nand/spi) | 377 (127 SPI NAND) |
| [U-Boot](https://github.com/u-boot/u-boot) | [`drivers/mtd/spi/spi-nor-ids.c`](https://github.com/u-boot/u-boot/blob/master/drivers/mtd/spi/spi-nor-ids.c) | 334 |
| [Dediprog](https://github.com/DediProgSW/SF100Linux) | [`ChipInfoDb.dedicfg`](https://github.com/DediProgSW/SF100Linux/blob/master/ChipInfoDb.dedicfg), the chip database of its SF100/SF600 programmers | 1873 (210 SPI NAND) |
| [Rockchip](https://github.com/rockchip-linux/u-boot) | [`drivers/rkflash/sfc_nor.c`](https://github.com/rockchip-linux/u-boot/blob/next-dev/drivers/rkflash/sfc_nor.c), [`drivers/rkflash/sfc_nand.c`](https://github.com/rockchip-linux/u-boot/blob/next-dev/drivers/rkflash/sfc_nand.c): the rkflash driver of its U-Boot | 226 (138 SPI NAND) |
| [OpenOCD](https://github.com/openocd-org/openocd) | [`src/flash/nor/spi.c`](https://github.com/openocd-org/openocd/blob/master/src/flash/nor/spi.c), and [`src/helper/jep106.inc`](https://github.com/openocd-org/openocd/blob/master/src/helper/jep106.inc) for manufacturer names | 190 |
| [openFPGALoader](https://github.com/trabucayre/openFPGALoader) | [`src/spiFlashdb.hpp`](https://github.com/trabucayre/openFPGALoader/blob/master/src/spiFlashdb.hpp) | 53 |
| [QEMU](https://gitlab.com/qemu-project/qemu) | [`hw/block/m25p80.c`](https://github.com/qemu/qemu/blob/master/hw/block/m25p80.c), and the SFDP dumps in [`hw/block/m25p80_sfdp.c`](https://github.com/qemu/qemu/blob/master/hw/block/m25p80_sfdp.c) | 137 |
| [Zephyr](https://github.com/zephyrproject-rtos/zephyr) | the devicetree of its [boards](https://github.com/zephyrproject-rtos/zephyr/tree/main/boards) and [SoCs](https://github.com/zephyrproject-rtos/zephyr/tree/main/dts): each flash node with a `jedec-id` | 99 |

Together that is 1233 distinct chip ids (969 SPI NOR, 264 SPI NAND) from 61
manufacturers, 703 of them described by more than one source, plus the full
JEP106 manufacturer list. Every entry keeps the upstream file and line it came
from, and where the sources disagree (89 ids do) both answers are kept.
Eleven of those ids (thirteen QEMU entries) also carry their complete SFDP
(JESD216) tables, decoded.

`spiflash sources` (or `spiflash.sources()`) names the exact upstream commits
the shipped data was extracted from.

Browse it at **<https://spiflash.readthedocs.io/>**:

- [a page per vendor](https://spiflash.readthedocs.io/en/latest/vendors/), with a table of all
  its parts;
- [a page per chip](https://spiflash.readthedocs.io/en/latest/chips/), with links to its
  datasheets;
- [a page per SPI operation](https://spiflash.readthedocs.io/en/latest/opcodes.html): what it
  does, a [WaveDrom](https://wavedrom.com/) timing diagram, and every part
  that supports it;
- the [data issues](https://spiflash.readthedocs.io/en/latest/issues/): every conflict or error
  found in the source data, by kind and by source.

<!-- usage-start: docs/usage.md includes from here -->

## Install

```sh
pip install spiflash          # or: uv tool install spiflash
```

It has no dependencies. Or, as a Debian package, from the signed apt
repository at <https://mith.ro/spiflash/>. There is one per suite (bookworm,
trixie, forky and sid), and the package is `Architecture: all`, so it installs
on any architecture. Put your suite's name in place of `trixie` below
(Raspberry Pi OS uses Debian's codenames):

```sh
sudo install -d -m0755 /etc/apt/keyrings
curl -fsSL https://mith.ro/spiflash/spiflash.gpg | sudo tee /etc/apt/keyrings/spiflash.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/spiflash.gpg] https://mith.ro/spiflash/trixie/ ./" \
  | sudo tee /etc/apt/sources.list.d/spiflash.list
sudo apt update
sudo apt install python3-spiflash      # provides the spiflash command
```

The repository's signing key is
`B528 6C61 A99A 9E6A 5B8A  8E5B 6B2D 7683 DE01 081D`
(`gpg --show-keys /etc/apt/keyrings/spiflash.gpg` shows it).

## The command

What answered `9f` with `ef 40 18`?

```console
$ spiflash id ef4018
ef4018  Winbond  W25Q128, W25Q128JV, W25Q128.V, W25Q128FV, W25Q128BV  (nor)
    size 16 MiB, page 256 B, sector 64 KiB, 2.7-3.6 V
    features: dual_read erase_32k erase_4k erase_64k fast_read lock otp quad_pp quad_read sfdp
    from: flashrom, flashprog, linux, u-boot, openocd, openfpgaloader, zephyr
    datasheet: https://www.winbond.com/resource-files/W25Q128JV%20RevH%2003102021%20Plus.pdf
```

`-v` lists every upstream entry with its file and line, `--json` prints it all
as JSON. Bytes after the three-byte id narrow the answer: a Spansion
S25FL128S answers `01 20 18 4d 01 80`, and `spiflash id '01 20 18 4d 01 80'`
drops the variants with other extended ids. A leading `7f` continuation code is
optional (`1c7018` and `7f1c7018` are both an Eon EN25QH128).

What does a part answer?

```console
$ spiflash find GD25Q64
c84017  GigaDevice  GD25Q64, GD25Q64C  (nor)
    size 8 MiB, page 256 B, sector 64 KiB, 2.7-3.6 V
    ...
```

A family (`W25Q128`), a part (`W25Q128JV`) or a full order code
(`S25FL128SAGMFI001`) all work; [flashrom](https://www.flashrom.org/)'s `.`
wildcards (`W25Q128.V`) are understood.

Which opcodes does a part support?

```console
$ spiflash opcodes W25Q128JV           # or by id: spiflash opcodes ef4018
ef4018  Winbond  W25Q128, W25Q128JV, W25Q128.V, W25Q128FV, W25Q128BV  (nor)
    0x9f  RDID             Read JEDEC id  [flashrom, flashprog, linux, u-boot, openocd, openfpgaloader, zephyr]
    0x5a  RDSFDP           Read SFDP (JESD216) parameters  [flashrom, flashprog]
    0x03  READ_1_1_1       Read data (low frequency)  [flashrom, flashprog, linux, u-boot, openocd, openfpgaloader]
    0x0b  READ_1_1_1_FAST  Fast read  [flashprog, linux, u-boot]
    0x3b  READ_1_1_2       Dual output fast read  [flashprog, linux, u-boot]
    0xbb  READ_1_2_2       Dual I/O fast read  [flashprog]
    0x6b  READ_1_1_4       Quad output fast read  [flashprog, linux, u-boot]
    0xeb  READ_1_4_4       Quad I/O fast read  [flashprog, openocd]
    0x02  PP_1_1_1         Page program  [flashrom, flashprog, linux, u-boot, openocd, openfpgaloader]
    0x32  PP_1_1_4         Quad input page program  [u-boot, zephyr]
    0x20  BE_4K            Erase a 4 KiB sector  [flashrom, flashprog, linux, u-boot, openfpgaloader]
    ...
```

`-v` says why each source lists each one (`SPI_NOR_QUAD_READ`, `FEATURE_FAST_READ_QIO`,
OpenOCD's `qread_cmd`, ...); `spiflash id --opcodes` adds the table to the usual
description. See [Opcodes](#opcodes) for what the list does and does not promise.

```sh
spiflash list --manufacturer winbond     # every Winbond id
spiflash list --type nand
spiflash id --method res1 10             # a legacy RES signature byte (M25P10)
spiflash sfdp W25Q512JV                  # its SFDP tables, decoded (see below)
spiflash jep106 7f1c                     # "Eon Silicon Devices"
spiflash sources                         # the upstream commits
```

## The library

```python
import spiflash

(chip,) = spiflash.lookup("ef4018")      # or b"\xef\x40\x18", 0xef4018, [0xef, 0x40, 0x18]
chip.manufacturer                        # 'Winbond'
chip.names                               # ('W25Q128', 'W25Q128JV', 'W25Q128.V', 'W25Q128FV', 'W25Q128BV')
chip.size, chip.page_size, chip.sector_size   # (16777216, 256, 65536)
chip.voltage                             # (2700, 3600), in mV
"quad_read" in chip.features             # True
chip.feature_sources("quad_read")        # ('flashprog', 'linux', 'u-boot', 'openocd')
chip.conflicts                           # {} -- or {"page_size": {256: (...), 512: (...)}}

for r in chip.records:                   # every upstream entry, as extracted
    print(r.source, r.name, r.url, r.erasers, r.opcodes, r.flags)

spiflash.find("MX25L12835F")             # by part name, best match first
spiflash.jep106(0xC2)                    # 'Macronix'
```

A `Flash` is one chip id, and several parts can share one (a W25Q128BV, FV and
JV all answer `ef4018`), so it lists every name the sources give. Its single
values (`size`, `page_size`, `sector_size`, `voltage`, `manufacturer`) are what
most sources agree on, ties going to flashrom, then flashprog, Linux, U-Boot, Dediprog,
Rockchip, OpenOCD, openFPGALoader, QEMU and Zephyr; `values("size")` shows who says what. `features`
is everything any source claims, from this list:

| feature | meaning |
|---|---|
| `erase_4k`, `erase_32k`, `erase_64k` | that erase size is supported (0x20, 0x52, 0xd8) |
| `sfdp` | answers SFDP (JESD216) queries |
| `fast_read`, `dual_read`, `quad_read`, `octal_read`, `octal_dtr_read` | read modes |
| `quad_pp`, `octal_dtr_pp` | page program modes |
| `qpi` | QPI (4-4-4) mode |
| `4byte_addr`, `4byte_opcodes` | 4-byte addressing; dedicated 4-byte opcodes |
| `otp`, `lock`, `rww`, `no_erase` | OTP area; status-register block protection; read-while-write; FRAM/MRAM |

The upstream's own flags (`SPI_NOR_HAS_TB`, `FEATURE_WRSR2`, ...) are kept on
each record's `flags` for anything this list does not capture.

## Searching part names

Besides `find`, part names can be searched by glob, by regular expression, and
for the nearest name to one that is not in the database:

```console
$ spiflash find 'MX25?12835F'            # a glob: * any run, ? any one, [...] one of a set
$ spiflash find --regex '^W25Q(64|128)J[VW]$'
$ spiflash find --nearest W25Q128JVSIQ   # -n 5 for five, --json for JSON
  3  W25Q128JV  ef4018     Winbond        the query adds SIQ
  3  W25Q128JV  ef7018     Winbond        the query adds SIQ
  7  W25Q128JW  ef6018     Winbond        differs after W25Q128J
  ...
```

```python
spiflash.find_glob("S25FL*S")            # [Flash, ...], in database order
spiflash.find_regex("^W25Q(64|128)J")    # searched for anywhere; anchor it with ^ and $
for m in spiflash.find_nearest("W25Q128JVSIQ", 5):
    print(m.score, m.name, m.flash.jedec_id, m.reason)
```

A glob covers the whole name and a regular expression is searched for in it;
both ignore case (unless a compiled pattern is given) and see the names as the
sources write them, flashrom's `W25Q128.V` included. A name with `*`, `?` or
`[` in it is a glob without `--glob`, and a `find` that matches nothing names
the three nearest parts.

The nearest names are for a marking read off a chip, a full order code, or a
typo. The score is an edit distance on the names' letters and digits (case,
`-`, `_`, `/` and spaces ignored; a `.` in a flashrom name matches any
character) where an edit (a character changed, added, dropped, or two swapped)
costs 4, and a character that one name has past the end of the other costs 1.
Part numbers go from the general to the specific: vendor, family, density,
variant, then package, temperature and ordering suffixes. So an order code is
close to its part (`W25Q128JVSIQ` is 3 from `W25Q128JV`), while a different
variant or density costs a full edit (`W25Q128FV`: 7). Equal scores go to the
name sharing more leading characters. Each chip is listed once, by its closest
name.

On the site, the [All chips](https://spiflash.readthedocs.io/en/latest/chips/)
page has a box that lists the nearest names as you type, scored the same way.
The filter box above every table of parts takes the same patterns: words are
matched anywhere in the row, a word with `*`, `?` or `[...]` is a glob for a
part name (`W25Q128*`), and `/.../` is a regular expression on the part names
(`/^MX25[LU]128/`).

## Opcodes

```python
chip.supports("READ_1_1_4")              # True
op = chip.opcodes["READ_1_4_4"]
op.opcode, op.operation.description      # (235, 'Quad I/O fast read'): 0xeb
op.because                               # (('flashprog', 'FEATURE_FAST_READ_QIO'), ('openocd', 'qread_cmd'))
```

Operations are named as [LiteSPI](https://github.com/litex-hub/litespi)'s
`SpiNorFlashOpCodes` names them, so a list can be used there directly:
`READ_1_1_4` sends the command on 1 line, the address on 1 and the data on 4,
and `_4B` marks the 4-byte-address form. `spiflash.opcodes.OPERATIONS` has them
all, each with:

- its `kind`: id, read, program, erase, register or mode;
- its `description`;
- its transaction shape: `protocol` (`"1-4-4"`), `address_bytes`,
  `dummy_clocks` and the `data` phase's direction.

Each source's list is what that source says; the
[opcodes page](https://spiflash.readthedocs.io/en/latest/opcodes.html#where-each-source-s-opcodes-come-from)
says where in each upstream it comes from. The opcode values are read from
each upstream's own headers, or from the SFDP tables, and checked against
`OPERATIONS` when the data is built.

So a listed operation is one some source says the part has. An operation that
is not listed may still be supported: no source here describes every opcode of
every part, and parts that Linux reads from SFDP get their read, program and
erase opcodes from the chip at run time, so Linux lists only its defaults for
them. Parts sharing an id can differ too; `because` says who vouches for what.
SPI NAND parts have no opcodes listed.

## SFDP

A chip that answers SFDP
([JESD216](https://www.jedec.org/standards-documents/docs/jesd216b), opcode
`0x5a`) describes itself: its
density, erase types and their opcodes, each fast-read mode with the dummy
clocks it needs, its page size, how to enter 4-byte addressing and set quad
mode. That is exactly what the tables above can only approximate, so where a
source carries a part's SFDP dump (QEMU's flash model does, for thirteen
parts) the record keeps it whole and the chip gets it decoded:

```python
chip = spiflash.find("W25Q512JV")[0]
t = chip.sfdp                            # None unless a source has a dump; chip.sfdp_source says which
t.revision_name, t.size, t.page_size     # ('JESD216B', 67108864, 256)
[(e.size, e.opcode, e.opcode_4b) for e in t.erase_types]   # [(4096, 0x20, 0x21), ...]
t.reads["1-4-4"].dummy_clocks            # 6: the part's own number, not a default
t.bfpt.quad_enable_description           # 'SR2 bit 1, written with a 2-byte WRSR ...'
t.features(), list(t.operations())       # as spiflash names them
```

Parts sharing an id can answer different tables (QEMU has one for the
MX25L25635E and another for the MX25L25635F, both `c22019`): `chip.sfdp` is
the best source's first, and `chip.sfdp_dumps` lists every distinct dump with
the parts it belongs to.

The same decoder reads a dump from a real chip, such as the one Linux
exposes at `/sys/bus/spi/devices/*/spi-nor/sfdp`:

```sh
spiflash sfdp /sys/bus/spi/devices/spi0.0/spi-nor/sfdp   # a file, or - for stdin
spiflash sfdp '53 46 44 50 00 01 01 ff ...'               # or the bytes in hex
spiflash sfdp W25Q512JV                                   # or a chip the database has a dump for
```

SFDP does not replace the tables. It says nothing about the vendor, the part
name, the supply voltage, block protection or OTP, nothing at all for parts
older than 2011, and what it does say is what the part's designers wrote:
Linux keeps per-part fixups for tables with a wrong density, a wrong page size
or a missing 4-byte method. `spiflash sfdp` reports what is written, and
`Sfdp.warnings` what did not decode cleanly.

## Datasheets

```python
d = chip.datasheets[0]                   # the best first
d.url, d.revision, d.date                # ('https://www.winbond.com/resource-files/W25Q128JV...pdf', 'Revision H', ...)
d.official                               # True: the manufacturer's own site
chip.key in d.confirmed                  # True: the document itself gives this id's bytes
```

Most chip ids have at least one datasheet link: the manufacturer's own where it
could be found, otherwise a copy elsewhere (`official` is false). A datasheet
that gives the id's bytes comes first, then the manufacturer's own, then the
newest. `spiflash id` prints the best one, and `-v` prints them all. Only the
links are shipped, not the documents.

<!-- usage-end -->

## Updating the data

The data is generated, never edited:
[`tools/sources.toml`](https://github.com/mithro/spiflash/blob/main/tools/sources.toml) pins each upstream to a commit,
and

```sh
uv run tools/update_db.py            # rebuild src/spiflash/data/ from the pins
uv run tools/update_db.py --latest   # move the pins to each upstream's HEAD first
```

fetches only the files it reads (a sparse, blobless, depth-1 fetch: about
15 MB for Linux) and parses the tables as text. A
[weekly workflow](https://github.com/mithro/spiflash/blob/main/.github/workflows/upstream.yml) reports when an upstream
table has changed. See [Developing spiflash](https://spiflash.readthedocs.io/en/latest/DEVELOPING.html) for how the
extraction works and [Where the data comes from](https://spiflash.readthedocs.io/en/latest/SOURCES.html) for what is
taken from where, and under what terms.

## Credits

The extraction follows the approach of LiteSPI's
[`spi_nor_config_generator`](https://github.com/litex-hub/litespi/tree/feature/module-generator-overrides/tools/spi_nor_config_generator)
([Antmicro](https://antmicro.com/), 2020): the same upstreams, a JSON record per upstream entry, and the
handling of OpenOCD's `w25q128fv/jv`-style names. The parsers are new, as Linux
and flashrom have both since changed their table formats. The data itself is
the work of the people who maintain those tables.

## License

Apache-2.0; see [LICENSE](https://github.com/mithro/spiflash/blob/main/LICENSE) and, for the data,
[Where the data comes from](https://spiflash.readthedocs.io/en/latest/SOURCES.html).
