# spiflash

**A database of SPI flash chips**, merged from the flash tables of Linux,
U-Boot, flashrom, flashprog, OpenOCD and openFPGALoader: JEDEC ids, part
names, sizes, page and sector sizes, erase layouts, supply voltages,
capabilities and opcodes, every value traced back to the upstream line it
came from.

::::{grid} 2 3 3 3
:gutter: 3

:::{grid-item-card}
:class-card: sf-stat
:link: chips/index
:link-type: doc

{{chips}}

chip ids
:::

:::{grid-item-card}
:class-card: sf-stat
:link: vendors/index
:link-type: doc

{{vendors}}

vendors
:::

:::{grid-item-card}
:class-card: sf-stat

{{records}}

upstream entries
:::

:::{grid-item-card}
:class-card: sf-stat

{{nor}}

SPI NOR ids
:::

:::{grid-item-card}
:class-card: sf-stat

{{nand}}

SPI NAND ids
:::

:::{grid-item-card}
:class-card: sf-stat

{{multi}}

ids described by several sources
:::
::::

## Find a chip

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} {octicon}`organization` Browse by vendor
:link: vendors/index
:link-type: doc

A page per manufacturer, with a sortable, filterable table of every part.
:::

:::{grid-item-card} {octicon}`list-unordered` All chips
:link: chips/index
:link-type: doc

Every chip id in one table: filter by id, part number, vendor or size.
:::

:::{grid-item-card} {octicon}`command-palette` Opcodes
:link: opcodes
:link-type: doc

The SPI operations, their opcodes, and how many parts list each.
:::

:::{grid-item-card} {octicon}`search` Search
:link: search
:link-type: ref

Every part name and id is in the site's search: try `W25Q128JV` or `ef 40 18`.
:::
::::

## Or ask from the command line

```console
$ pip install spiflash
$ spiflash id ef4018
ef4018  Winbond  W25Q128, W25Q128.V, W25Q128FV, W25Q128JV  (nor)
    size 16 MiB, page 256 B, sector 64 KiB, 2.7-3.6 V
    features: dual_read erase_32k erase_4k erase_64k fast_read lock otp quad_read sfdp
    from: flashrom, flashprog, linux, u-boot, openocd, openfpgaloader
```

See [Using spiflash](usage.md) for the command and the Python library, and
[Install](usage.md#install) for the Debian packages.

This site describes spiflash {{version}}; its data comes from these upstream
commits:

```{include} _generated/sources-table.md
```

```{toctree}
:hidden:
:caption: Database

vendors/index
chips/index
opcodes
```

```{toctree}
:hidden:
:caption: Using it

usage
api
```

```{toctree}
:hidden:
:caption: About

SOURCES
DEVELOPING
GitHub <https://github.com/mithro/spiflash>
PyPI <https://pypi.org/project/spiflash/>
```
