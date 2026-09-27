# spiflash

A database of SPI flash chips — JEDEC ids, part names, sizes, page and sector
sizes, erase opcodes and block layouts, supply voltages and capabilities —
merged from the flash tables of every project that keeps one:

| source | what is read | records |
|---|---|---|
| [Linux](https://github.com/torvalds/linux) | `drivers/mtd/spi-nor/*.c`, `drivers/mtd/nand/spi/*.c` | 377 (127 SPI NAND) |
| [U-Boot](https://github.com/u-boot/u-boot) | `drivers/mtd/spi/spi-nor-ids.c` | 334 |
| [flashrom](https://github.com/flashrom/flashrom) | `flashchips/*.c` (SPI chips only) | 512 |
| [flashprog](https://review.sourcearcade.org/flashprog.git) | `flashchips.c` (SPI chips only) | 480 |
| [OpenOCD](https://github.com/openocd-org/openocd) | `src/flash/nor/spi.c`, and `src/helper/jep106.inc` for manufacturer names | 190 |
| [openFPGALoader](https://github.com/trabucayre/openFPGALoader) | `src/spiFlashdb.hpp` | 53 |

Together that is 778 distinct chip ids (651 SPI NOR, 127 SPI NAND) from 37
manufacturers, 459 of them described by more than one source, plus the full
JEP106 manufacturer list. Every entry keeps the upstream file and line it came
from, and where the sources disagree (60 ids do) both answers are kept.

`spiflash sources` (or `spiflash.sources()`) names the exact upstream commits
the shipped data was extracted from.

## Install

```sh
pip install spiflash          # or: uv tool install spiflash
```

It has no dependencies. Or, as a Debian package, from the signed apt
repository at https://mith.ro/spiflash/. There is one per suite (bookworm,
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
ef4018  Winbond  W25Q128, W25Q128.V, W25Q128FV, W25Q128JV  (nor)
    size 16 MiB, page 256 B, sector 64 KiB, 2.7-3.6 V
    features: dual_read erase_32k erase_4k erase_64k fast_read lock otp quad_read sfdp
    from: flashrom, flashprog, linux, u-boot, openocd, openfpgaloader
```

`-v` lists every upstream entry with its file and line, `--json` prints it all
as JSON. Bytes after the three-byte id narrow the answer: a Spansion
S25FL128S answers `01 20 18 4d 01 80`, and `spiflash id '01 20 18 4d 01 80'`
drops the variants with other extended ids. A leading `7f` continuation code is
optional (`1c7018` and `7f1c7018` are both an Eon EN25QH128).

What does a part answer?

```console
$ spiflash find gd25q64
c84017  GigaDevice  GD25Q64, GD25Q64C  (nor)
    size 8 MiB, page 256 B, sector 64 KiB, 2.7-3.6 V
    ...
```

A family (`w25q128`), a part (`W25Q128JV`) or a full order code
(`S25FL128SAGMFI001`) all work; flashrom's `.` wildcards (`W25Q128.V`) are
understood.

```sh
spiflash list --manufacturer winbond     # every Winbond id
spiflash list --type nand
spiflash id --method res1 10             # a legacy RES signature byte (M25P10)
spiflash jep106 7f1c                     # "Eon Silicon Devices"
spiflash sources                         # the upstream commits
```

## The library

```python
import spiflash

(chip,) = spiflash.lookup("ef4018")      # or b"\xef\x40\x18", 0xef4018, [0xef, 0x40, 0x18]
chip.manufacturer                        # 'Winbond'
chip.names                               # ('W25Q128', 'W25Q128.V', 'W25Q128FV', 'W25Q128JV')
chip.size, chip.page_size, chip.sector_size   # (16777216, 256, 65536)
chip.voltage                             # (2700, 3600), in mV
"quad_read" in chip.features             # True
chip.feature_sources("quad_read")        # ('flashprog', 'linux', 'openocd', 'u-boot')
chip.conflicts                           # {} -- or {"page_size": {256: (...), 512: (...)}}

for r in chip.records:                   # every upstream entry, as extracted
    print(r.source, r.name, r.url, r.erasers, r.opcodes, r.flags)

spiflash.find("MX25L12835F")             # by part name, best match first
spiflash.jep106(0xC2)                    # 'Macronix'
```

A `Flash` is one chip id, and several parts can share one (a W25Q128BV, FV and
JV all answer `ef4018`), so it lists every name the sources give. Its single
values (`size`, `page_size`, `sector_size`, `voltage`, `manufacturer`) are what
most sources agree on, ties going to flashrom, then flashprog, Linux, U-Boot,
OpenOCD and openFPGALoader; `values("size")` shows who says what. `features`
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

## Updating the data

The data is generated, never edited: `tools/sources.toml` pins each upstream to
a commit, and

```sh
uv run tools/update_db.py            # rebuild src/spiflash/data/ from the pins
uv run tools/update_db.py --latest   # move the pins to each upstream's HEAD first
```

fetches only the files it reads (a sparse, blobless, depth-1 fetch: about
15 MB for Linux) and parses the tables as text. A weekly workflow reports when an
upstream table has changed. See [docs/DEVELOPING.md](docs/DEVELOPING.md) for
how the extraction works and [docs/SOURCES.md](docs/SOURCES.md) for what is
taken from where, and under what terms.

## Credits

The extraction follows the approach of LiteSPI's
[`spi_nor_config_generator`](https://github.com/litex-hub/litespi/tree/feature/module-generator-overrides/tools/spi_nor_config_generator)
(Antmicro, 2020): the same upstreams, a JSON record per upstream entry, and the
handling of OpenOCD's `w25q128fv/jv`-style names. The parsers are new, as Linux
and flashrom have both since changed their table formats. The data itself is
the work of the people who maintain those tables.

## License

Apache-2.0; see [LICENSE](LICENSE) and, for the data,
[docs/SOURCES.md](docs/SOURCES.md).
