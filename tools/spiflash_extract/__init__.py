"""Extract SPI flash tables from upstream source trees into spiflash records.

Each extractor here (one module per upstream format) reads one upstream
project's flash table straight from its C (or C++) source, or for Zephyr the
flash nodes of its boards' devicetree (:mod:`spiflash_extract.dts`), and
returns a list of records in the common schema that
:repo:`src/spiflash/data/records.json` holds (see :mod:`spiflash_extract.record`).
Nothing is compiled: the tables are parsed as text, with just enough of a C
expression evaluator to turn ``SZ_16M`` or ``64 * 1024`` into numbers.

This is a rewrite of the approach of LiteSPI's `spi_nor_config_generator
<https://github.com/litex-hub/litespi/tree/feature/module-generator-overrides/tools/spi_nor_config_generator>`_
(Copyright (c) 2020 Antmicro, BSD-2-Clause), which built JSON from the same
upstreams by compiling their tables. The upstream formats have since changed
(Linux 6.x replaced ``INFO()`` with ``SNOR_ID()`` and designated initialisers;
flashrom split :upstream:`flashrom:flashchips.c` per vendor), so the parsing is
new, but the source list, the intermediate JSON-per-source step and the
handling of "w25q128fv/jv"-style combined names follow that tool.
"""
