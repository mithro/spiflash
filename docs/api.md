# API reference

Everything in the package, generated from its docstrings. The library's front
door is the {py:mod}`spiflash` module itself:

```python
import spiflash

(chip,) = spiflash.lookup("ef4018")      # by JEDEC id: b"\xef\x40\x18", 0xef4018, ... too
spiflash.find("W25Q128JV")               # by part name
spiflash.find_glob("W25Q128*"), spiflash.find_regex("^MX25[LU]128")
spiflash.find_nearest("W25Q128JVSIQ")    # the closest names, scored
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
| {py:mod}`spiflash.derive` | what a record's stored fields imply, worked out at load |
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

## What a record stores, and what it derives

A {py:class}`~spiflash.model.Record` holds what its upstream entry states,
and each fact once. What follows from those fields is worked out when the
record is made ({py:mod}`spiflash.derive`), never stored:

- {py:attr}`~spiflash.model.Record.opcodes` is the entry's
  {py:attr}`~spiflash.model.Record.opcode_claims` plus the id read its
  `id_method` gives (unless the entry names its own id command) and the
  erase each of its erasers sends (SPI NOR only),
  each marked {py:attr}`~spiflash.opcodes.OpcodeUse.implied`;
- {py:attr}`~spiflash.model.Record.features` is its
  {py:attr}`~spiflash.model.Record.feature_claims` (later releases add what
  its operations and erasers imply).

A field that is both stored and derived keeps the stored part in a
`*_claims` attribute ({py:data}`~spiflash.model.CLAIMS`);
{py:meth}`Record.stored("features") <spiflash.model.Record.stored>` reads it
by the field's name, and {py:meth}`~spiflash.model.Record.to_json` writes
exactly the stored fields, as the data holds them.
{py:attr}`~spiflash.model.Record.via` says which upstream token gave a stored
value nothing else accounts for (`{"feature:qpi": "QPIEnable"}`), and
{py:attr}`~spiflash.model.Record.flags` keeps only the tokens nothing holds.

On a chip, {py:attr}`Flash.opcodes <spiflash.model.Flash.opcodes>` lists the
implied operations too: a {py:class}`~spiflash.model.Claim` is `implied` where
its source only implies the operation, and a source that states it is not
also listed as implying it.

Changes in data format 4 (spiflash is a rolling v0.0 release, so these break
callers without a deprecation period):

- `Record(...)` takes `feature_claims=` and `opcode_claims=`, not `features=`
  and `opcodes=`, which are now derived (read them as before);
- {py:class}`~spiflash.opcodes.OpcodeUse` lives in {py:mod}`spiflash.opcodes`
  (`spiflash.model.OpcodeUse` still imports), takes no opcode, and gives
  {py:attr}`~spiflash.opcodes.OpcodeUse.opcode` from the table;
- {py:class}`~spiflash.model.Claim` has a third field, `implied`, so code
  unpacking it as `(source, via)` must take three;
- the records' `opcodes` lose their `"opcode"` key, gain no duplicate of an
  operation the record derives, and the records gain `"via"`.
