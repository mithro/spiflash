# API reference

Everything in the package, generated from its docstrings. The library's front
door is the {py:mod}`spiflash` module itself:

```python
import spiflash

(chip,) = spiflash.lookup("ef4018")      # by JEDEC id: b"\xef\x40\x18", 0xef4018, ... too
spiflash.find("W25Q128JV")               # by part name
chip.manufacturer, chip.names, chip.size, chip.features
chip.supports("READ_1_1_4"), chip.opcodes["SE"].opcode
spiflash.database().link(chip.records[0])   # the upstream line it came from
```

The modules behind it:

| Module | What it holds |
|---|---|
| {py:mod}`spiflash.db` | loading the data, and answering queries |
| {py:mod}`spiflash.model` | the types: {py:class}`~spiflash.model.Record` is one upstream entry, {py:class}`~spiflash.model.Flash` everything known about one chip id |
| {py:mod}`spiflash.enums` | the fixed vocabularies, as enums: sources, flash types, id families, features, kinds of operation, ... |
| {py:mod}`spiflash.opcodes` | the named SPI operations |
| {py:mod}`spiflash.sfdp` | the SFDP ([JESD216](https://www.jedec.org/standards-documents/docs/jesd216b)) decoder |
| {py:mod}`spiflash.vendors` | the vendor spellings |
| {py:mod}`spiflash.units` | sizes and times as people read them |
| {py:mod}`spiflash.cli` | the `spiflash` command |

The extraction tools, {py:mod}`spiflash_extract` (in {repo}`tools/`, shipped in the sdist,
not the wheel), are documented too: they are how the data is built, and what
to read when an upstream changes its format.

```{eval-rst}
.. autosummary::
   :toctree: _autosummary
   :recursive:

   spiflash
   spiflash_extract
   update_db
   import_datasheets
```
