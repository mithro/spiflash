# spiflash

**A database of SPI flash chips**, merged from the flash tables of {sfsrc}`flashrom`,
{sfsrc}`flashprog`, {sfsrc}`linux`, {sfsrc}`u-boot`, {sfsrc}`dediprog`, {sfsrc}`rockchip`, {sfsrc}`mediatek`, {sfsrc}`openocd`, {sfsrc}`openfpgaloader`,
{sfsrc}`imsprog` and {sfsrc}`qemu`, and from the flash chips {sfsrc}`zephyr`'s boards describe. For each chip id:

- its part names, size, page and sector sizes, erase layouts, supply voltage,
  capabilities and [opcodes](opcodes.md);
- every value traced back to the upstream line it came from, and every
  [disagreement between the sources](issues/index.md) kept;
- the [SFDP](https://www.jedec.org/standards-documents/docs/jesd216b) (JESD216)
  tables, decoded, of the parts {sfsrc}`qemu` has them for;
- links to its datasheets.

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

:::{grid-item-card} {octicon}`repo` Sources
:link: sources/index
:link-type: doc

The twelve upstream projects the data is merged from: what each gives, and every entry taken.
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
ef4018  Winbond  W25Q128, W25Q128JV, W25Q128FV, W25Q128BV, W25R128FV, W25R128JV, S25FL128K, W25Q128.V  (nor)
    size 16 MiB, page 256 B, sector 64 KiB, 2.7-3.6 V, QE SR2[1]
    features: dual_read erase_32k erase_4k erase_64k fast_read lock otp qpi quad_pp quad_read sfdp
    from: flashrom, flashprog, linux, u-boot, dediprog, rockchip, openocd, openfpgaloader, imsprog, zephyr
    datasheet: https://www.winbond.com/resource-files/W25Q128JV%20RevH%2003102021%20Plus.pdf
```

See [Using spiflash](usage.md) for the command and the Python library, and
[Install](usage.md#install) for the Debian packages.

This site describes spiflash {{version}}; its data comes from these upstream
commits ([more on each source](sources/index.md)):

```{include} _generated/sources-table.md
```

```{toctree}
:hidden:
:caption: Database

vendors/index
chips/index
opcodes
derived
issues/index
jep106/index
sources/index
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
