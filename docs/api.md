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
| {py:mod}`spiflash.registers` | register bits: where the quad enable bit is, the quad enable requirement, the block-protection bits |
| {py:mod}`spiflash.derive` | what a record's stored fields imply, worked out at load |
| {py:mod}`spiflash.sfdp` | the SFDP ([JESD216](https://www.jedec.org/standards-documents/docs/jesd216b)) decoder |
| {py:mod}`spiflash.sfdp_tools` | SFDP tables from the database ({py:func}`~spiflash.sfdp_tools.encode`), to a database entry ({py:func}`~spiflash.sfdp_tools.to_entry`), and compared ({py:func}`~spiflash.sfdp_tools.diff`) |
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

- {py:attr}`~spiflash.model.Record.size` and
  {py:attr}`~spiflash.model.Record.page_size` are what the entry states
  ({py:attr}`~spiflash.model.Record.size_claim`,
  {py:attr}`~spiflash.model.Record.page_size_claim`), or where it states
  none, what its SFDP tables say ({py:meth}`Sfdp.facts
  <spiflash.sfdp.Sfdp.facts>`); {py:attr}`~spiflash.model.Record.erasers`
  is its {py:attr}`~spiflash.model.Record.eraser_claims` and its tables'
  erasers;
- {py:attr}`~spiflash.model.Record.opcodes` is the entry's
  {py:attr}`~spiflash.model.Record.opcode_claims` plus the id read its
  `id_method` gives (unless the entry names its own id command), the
  erase each of its stored erasers sends, and the operations its SFDP
  tables give, with their dummy clocks (SPI NOR only), each marked
  {py:attr}`~spiflash.opcodes.OpcodeUse.implied`;
- {py:attr}`~spiflash.model.Record.features` is its
  {py:attr}`~spiflash.model.Record.feature_claims` plus what its stated
  operations (not its driver's defaults), its SFDP tables' operations,
  its block erasers, its size, and what only SFDP says imply
  ({py:func}`spiflash.derive.features`), and its quad enable bit
  (`quad_read`) and block-protection bits (`lock`);
- {py:attr}`~spiflash.model.Record.quad_enable_requirement` is what the
  entry states, or failing that its SFDP tables' (BFPT DW15), and
  {py:attr}`~spiflash.model.Record.quad_enable` the bit the entry states, or
  failing that the one its requirement puts it at; the requirement gives
  the register operations writing the bit too;
- {py:attr}`~spiflash.model.Record.sector_size` is the block of its 0xd8
  eraser (failing that its 0xdc, then its 0x52 eraser; a SPI NAND part's
  block erase), and `None` for a part that needs no erase
  ({py:func}`spiflash.derive.sector_size`). It is only derived
  ({py:data}`~spiflash.model.DERIVED`), so
  {py:meth}`Record.given("sector_size") <spiflash.model.Record.given>` reads
  it where code compares what records give.

[](derived.md) lists the rules.

A field that is both stored and derived keeps the stored part in a
`*_claims` attribute (`*_claim` for a single value: {py:data}`~spiflash.model.CLAIMS`);
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

Changes in data format 6 (what a record's SFDP tables say is derived, and the
SFDP tools):

- `Record(...)` takes `size_claim=`, `page_size_claim=` and
  `eraser_claims=`, not `size=`, `page_size=` and `erasers=`, which are now
  the stated value or, where the entry states none, its SFDP tables' (read
  them as before; {py:meth}`Record.stored("size")
  <spiflash.model.Record.stored>` reads the stated one, and
  {py:meth}`~spiflash.model.Record.given` the whole value, so
  {py:meth}`Flash.values("size") <spiflash.model.Flash.values>` and
  {py:attr}`~spiflash.model.Flash.conflicts` are unchanged);
- the method `Record.sfdp_tables()` is the property
  {py:attr}`Record.parsed_sfdp <spiflash.model.Record.parsed_sfdp>`
  (no parentheses): {py:attr}`~spiflash.model.Record.sfdp_tables` is now
  the new field holding the tables a source copies (`{0xff00: bytes}`);
  {py:attr}`~spiflash.model.Record.sfdp_facts` and
  {py:meth}`~spiflash.model.Record.sfdp_disagreements` are new;
- {py:class}`~spiflash.model.SfdpDump`'s `tables` is `sfdp` (`tables` still
  reads it), and {py:attr}`Flash.sfdp_dumps
  <spiflash.model.Flash.sfdp_dumps>` lists copied tables after the whole
  dumps; such an {py:class}`~spiflash.sfdp.Sfdp` is
  {py:attr}`~spiflash.sfdp.Sfdp.partial`, with `major`, `minor` and
  `access_protocol` `None` (so {py:attr}`ParameterHeader.major
  <spiflash.sfdp.ParameterHeader.major>` and {py:attr}`Bfpt.major
  <spiflash.sfdp.Bfpt.major>` may be too);
- {py:meth}`Sfdp.features() <spiflash.sfdp.Sfdp.features>` is
  {py:func}`spiflash.derive.features`'s rules applied to the tables alone, so
  a 4-byte operation in the 4BAIT implies `4byte_opcodes` and its fast read
  (0x0c) `fast_read`, as they do from any source; {py:meth}`Sfdp.operations()
  <spiflash.sfdp.Sfdp.operations>` names the 3-byte 4-4-4 read `READ_4_4_4`
  (a new operation), yields the 4 KiB erase of BFPT DW1 where no erase type
  has it, and reads BFPT DW16's 4-byte modes only for a part that has one
  ({py:attr}`~spiflash.sfdp.Sfdp.four_byte_mode`);
- {py:meth}`Sfdp.to_json() <spiflash.sfdp.Sfdp.to_json>` (in
  {py:meth}`Flash.to_json() <spiflash.model.Flash.to_json>`'s `"sfdp"` and
  `spiflash sfdp --json`) gains `"partial"`; for a partial one, `"revision"`
  and `"access_protocol"` are `null`, `"revision_name"` is `"unknown
  revision"`, and each table's `"revision"` and `"pointer"` are `null`
  (its header was made up):

  ```text
  {"partial": true, "revision": null, "revision_name": "unknown revision",
   "access_protocol": null,
   "tables": [{"id": 65280, "name": "BFPT", "revision": null, "length": 16,
               "pointer": null, "dwords": [...]}], ...}
  ```

- {py:class}`~spiflash.opcodes.OpcodeUse` has a fifth field,
  `dummy_clocks`;
- the records gain `"sfdp_tables"` (`{}` without), and the {sfsrc}`qemu` and
  {sfsrc}`zephyr` records lose the operations, capabilities, sizes, page
  sizes, erasers and notes their tables give; their decoded `"sfdp-ff05"`
  and `"sfdp-ff84"` flags are tables now. A use in `"opcodes"` may give
  `"dummy_clocks"`;
- the command gains `spiflash sfdp --entry`, `spiflash sfdp-encode` and
  `spiflash sfdp-diff` ([](usage.md)), and the site an SFDP kind of data
  issue.

Changes in data format 7 (register bits):

- the records gain `"quad_enable"` (a register bit,
  `{"register": "sr2", "bit": 1}`, or `"none"`),
  `"quad_enable_requirement"` (`"S2B1v4"`, ...) and `"protection"` (register
  bits by role, `{"bp0": {...}, "tb": {...}}`), each `null` where the entry
  says nothing ({py:mod}`spiflash.registers`); a `"via"` key may name a
  role (`"protection.tb"`);
- {py:class}`~spiflash.model.Record` has
  {py:attr}`~spiflash.model.Record.quad_enable_claim`,
  {py:attr}`~spiflash.model.Record.quad_enable_requirement_claim` and
  {py:attr}`~spiflash.model.Record.protection` (stored), and
  {py:attr}`~spiflash.model.Record.quad_enable` and
  {py:attr}`~spiflash.model.Record.quad_enable_requirement` (the stated
  value or the one derived); {py:meth}`~spiflash.model.Record.given` reads a
  role as `"protection.tb"`;
- a `lock` or `quad_read` claim a record's register bits imply is no
  longer stored in its `"features"`; it is in
  {py:attr}`Record.features <spiflash.model.Record.features>` as before,
  now with that reason. A quad enable bit gives `quad_read` to parts no
  source gave it before (quad parts whose Dediprog entry lists no quad
  read);
- {py:class}`~spiflash.model.Flash` has
  {py:attr}`~spiflash.model.Flash.quad_enable`,
  {py:attr}`~spiflash.model.Flash.quad_enable_requirement`,
  {py:attr}`~spiflash.model.Flash.protection`,
  {py:meth}`~spiflash.model.Flash.shared_bits` and
  {py:meth}`~spiflash.model.Flash.value`, and
  {py:attr}`~spiflash.model.Flash.conflicts` and
  {py:meth}`~spiflash.model.Flash.by_ext_id` cover every value of
  {py:data}`~spiflash.model.COMPARED_VALUES` (`"protection.tb"`, ...);
  {py:meth}`Flash.to_json() <spiflash.model.Flash.to_json>` gains the three,
  and its `"conflicts"` give a register bit as its JSON;
- {py:class}`~spiflash.sfdp.SfdpFacts` has `quad_enable_requirement` (and
  `quad_enable`), and {py:func}`~spiflash.sfdp_tools.to_entry` stores the
  requirement; {py:func}`~spiflash.sfdp_tools.encode` writes a known one to
  DW15;
- new operations: [RDSR2](opcodes/RDSR2.md), [RDSR3](opcodes/RDSR3.md),
  [WRSR_16](opcodes/WRSR_16.md), [WRSR_24](opcodes/WRSR_24.md),
  [RDSCUR](opcodes/RDSCUR.md), [WRSCUR](opcodes/WRSCUR.md),
  [CLPEF](opcodes/CLPEF.md) and [ULBPR](opcodes/ULBPR.md); the command's
  description gains the QE bit (`QE SR2[1]`), and with `-v` the
  requirement and the protection bits; the site a Registers section on
  the chip pages, and a kind of data issue for two roles on one bit.
