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
  {py:attr}`~spiflash.model.Record.feature_claims` plus what its stored
  operations (not its driver's defaults), block erasers, size and SFDP
  tables imply ({py:func}`spiflash.derive.features`);
- {py:attr}`~spiflash.model.Record.sector_size` is the block of its 0xd8
  eraser (failing that its 0xdc, then its 0x52 eraser; a SPI NAND part's
  block erase), and `None` for a part that needs no erase
  ({py:func}`spiflash.derive.sector_size`). It is only derived
  ({py:data}`~spiflash.model.DERIVED`), so
  {py:meth}`Record.given("sector_size") <spiflash.model.Record.given>` reads
  it where code compares what records give.

[](derived.md) lists the rules.

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
its source only implies the operation, and `assumed` where it is only the
source's driver default ({py:attr}`~spiflash.opcodes.OpcodeUse.assumed`: an
operation the driver sends to every part, whatever the entry says); a source
that states it is not also listed as implying it, nor one that gives it for the
part as assuming it.
{py:meth}`Flash.feature_sources() <spiflash.model.Flash.feature_sources>`
says, for each source giving a capability, whether it claims it or only
implies it, and why ({py:class}`~spiflash.model.FeatureSource`).

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

Changes in data format 5:

- {py:attr}`Flash.features <spiflash.model.Flash.features>` (and
  {py:attr}`Record.features <spiflash.model.Record.features>`) include the
  capabilities a source's entry implies as well as those it claims; a
  driver default implies none. So a chip can gain a capability no source
  claimed (`fast_read` where flashrom lists 0x0b) and lose one only a
  default gave (`fast_read` on the AT45DB parts, which only U-Boot's and
  QEMU's every-part fast read gave), and `erase_64k` no longer comes from a
  chip erase that covers 64 KiB;
- {py:meth}`Flash.feature_sources() <spiflash.model.Flash.feature_sources>`
  returns {py:class}`~spiflash.model.FeatureSource`s, `(source, implied,
  because)`, not sources: `"linux" in f.feature_sources("quad_read")` is now
  always false; write `"linux" in [s.source for s in f.feature_sources(...)]`;
- `Record(...)` takes no `sector_size=`: give the erasers;
  {py:attr}`Record.sector_size <spiflash.model.Record.sector_size>` is derived
  from them, {py:meth}`Record.stored("sector_size") <spiflash.model.Record.stored>`
  raises `KeyError`, and {py:meth}`Flash.values("sector_size")
  <spiflash.model.Flash.values>` gives each record's derived value;
- {py:class}`~spiflash.opcodes.OpcodeUse` and {py:class}`~spiflash.model.Claim`
  have a fourth field, `assumed`; {py:class}`~spiflash.model.SupportedOperation`
  has {py:attr}`~spiflash.model.SupportedOperation.assumed_by`;
- the records lose `"sector_size"`, an implied capability is not stored in
  their `"features"`, Linux's, U-Boot's, openFPGALoader's and the SPI NAND
  records gain erasers, and an operation or eraser that is a driver default
  has `"assumed": true` ({py:attr}`Eraser.assumed
  <spiflash.model.Eraser.assumed>`: Linux's 64 KiB sector for an entry that
  gives none, which gives no sector size); a Linux entry without
  `.page_size` has no page size (the driver's 256-byte default is not the
  part's);
- {py:meth}`Flash.to_json() <spiflash.model.Flash.to_json>` (the command's
  `--json`) gains `"feature_sources"` and each operation's `"assumed_by"`.
