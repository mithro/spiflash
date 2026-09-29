# API reference

Everything in the package, generated from its docstrings. The library's front
door is the `spiflash` module itself:

```python
import spiflash

(chip,) = spiflash.lookup("ef4018")      # by JEDEC id: b"\xef\x40\x18", 0xef4018, ... too
spiflash.find("W25Q128JV")               # by part name
chip.manufacturer, chip.names, chip.size, chip.features
chip.supports("READ_1_1_4"), chip.opcodes["SE"].opcode
spiflash.database().link(chip.records[0])   # the upstream line it came from
```

`spiflash.db` loads the data and answers queries, `spiflash.model` holds the
types (`Record` is one upstream entry, `Flash` everything known about one
chip id), `spiflash.opcodes` the named SPI operations, `spiflash.vendors` the
vendor spellings, and `spiflash.cli` the `spiflash` command.

The extraction tools, `spiflash_extract` (in {repo}`tools/`, shipped in the sdist,
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
