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
| {py:mod}`spiflash.timings` | a part's durations ({py:class}`~spiflash.timings.Timings`): erases, programs, suspend, power-down and reset, each with its bound |
| {py:mod}`spiflash.derive` | what a record's stored fields imply, worked out at load |
| {py:mod}`spiflash.sfdp` | the SFDP ([JESD216](https://www.jedec.org/standards-documents/docs/jesd216b)) decoder |
| {py:mod}`spiflash.sfdp_tools` | SFDP tables from the database ({py:func}`~spiflash.sfdp_tools.encode`), to a database entry ({py:func}`~spiflash.sfdp_tools.to_entry`), and compared ({py:func}`~spiflash.sfdp_tools.diff`) |
| {py:mod}`spiflash.vendors` | the vendor spellings |
| {py:mod}`spiflash.units` | sizes, times, frequencies and supplies as people read them |
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
- {py:attr}`~spiflash.model.Record.four_byte_modes` is the ways into 4-byte
  mode the entry states ({py:attr}`~spiflash.model.Record.four_byte_mode_claims`)
  and those its SFDP tables give (BFPT DW16); they give their operations
  (EN4B, WREAR and RDEAR, BRWR and BRRD) and `4byte_addr`, and
  {py:attr}`~spiflash.model.Record.address_bytes` follows; an OTP area
  ({py:attr}`~spiflash.model.Record.otp`) gives `otp`;
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
  says nothing ({py:mod}`spiflash.registers`); a register bit has
  `"writability"` only where its source says how it is written; a `"via"`
  key may name a role (`"protection.tb"`);
- sources are compared on where a register bit is, not on how it is
  written ({py:meth}`Record.compared <spiflash.model.Record.compared>`,
  {py:func}`~spiflash.model.compared_value`), so
  {py:meth}`Flash.values <spiflash.model.Flash.values>` gives a bit without
  its writability, and a chip's bit is written as most sources saying so say;
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

Changes in data format 8 (SPI NAND geometry, dies, SPI NAND operations):

- the records gain `"oob_size"` (spare bytes per page), `"planes"` (per
  die), `"max_bad_blocks"` (per die) and `"ecc"`
  (`{"strength_bits": 8, "step_bytes": 512}`, without `"step_bytes"` where
  the source gives none), SPI NAND only; `"dies"`, SPI NOR and SPI NAND; and
  `"die_select_bit"` (a register bit, `{"register": "nand-d0", "bit": 6}`),
  each `null` where the entry says nothing. A `"via"` key may name `dies`
  or `die_select_bit`;
- a die erase layout (0xc4, 0x61) is no longer stored: it is derived from
  `"dies"` and the stated die erase ({py:func}`~spiflash.derive.die_erasers`),
  so {sfsrc}`flashrom`'s, {sfsrc}`flashprog`'s and {sfsrc}`dediprog`'s 0xc4
  erasers are `"dies"` now, and {sfsrc}`qemu`'s `die_cnt` flag too;
- the SPI NAND records gain SPI NAND's own operations ({ref}`opcodes-nand`),
  each read with `"dummy_clocks"` where the entry states them
  ({sfsrc}`linux`'s op variants, {sfsrc}`mediatek`'s I/O modes), and lose
  {sfsrc}`dediprog`'s SPI NOR `RDID` (a SPI NAND part's read-id is derived
  from its id method); {sfsrc}`mediatek`'s and {sfsrc}`rockchip`'s
  `dual_read`, `quad_read` and `quad_pp` claims go, as their operations imply
  them, and so do the flags and notes the new fields hold (MediaTek's
  `sparesize`, `planes_per_die`, `ndies`, `select_die`, `read_from_cache` and
  `program_load`; Rockchip's `max_ecc_bits` and planes note; Linux's "OOB
  per page" note);
- {py:class}`~spiflash.opcodes.Operation` has `flash_type`; new operations:
  the `NAND_*` ones, [DIE_SELECT](opcodes/DIE_SELECT.md) and
  [DIE_ERASE_61](opcodes/DIE_ERASE_61.md);
  {py:class}`~spiflash.registers.Register` has `NAND_DIE`, and
  {py:class}`~spiflash.enums.ShapeSource` (then `TimingSource`) `LINUX_SPINAND`;
- {py:class}`~spiflash.model.Record` has
  {py:attr}`~spiflash.model.Record.oob_size`,
  {py:attr}`~spiflash.model.Record.planes`,
  {py:attr}`~spiflash.model.Record.dies_claim` (stored, JSON `"dies"`),
  {py:attr}`~spiflash.model.Record.dies` (it, or its SFDP tables'),
  {py:attr}`~spiflash.model.Record.die_select_bit`,
  {py:attr}`~spiflash.model.Record.max_bad_blocks` and
  {py:attr}`~spiflash.model.Record.ecc` (a new
  {py:class}`~spiflash.model.EccRequirement`); a SPI NAND record's
  {py:attr}`~spiflash.model.Record.opcodes` now has its derived read-id and
  block erase, and its {py:attr}`~spiflash.model.Record.features` what its
  operations imply;
- {py:class}`~spiflash.model.Flash` has
  {py:attr}`~spiflash.model.Flash.oob_size`,
  {py:attr}`~spiflash.model.Flash.planes`,
  {py:attr}`~spiflash.model.Flash.dies`,
  {py:attr}`~spiflash.model.Flash.die_select_bit`,
  {py:attr}`~spiflash.model.Flash.max_bad_blocks` and
  {py:attr}`~spiflash.model.Flash.ecc`, and
  {py:meth}`Flash.to_json() <spiflash.model.Flash.to_json>` gains them and
  each operation's `"dummy_clocks"`;
- {py:data}`~spiflash.model.COMPARED` gains them, the ECC requirement
  compared by its strength and its step each on its own
  (`Compared.PER_ROLE` is {py:attr}`Compared.PER_COMPONENT
  <spiflash.model.Compared.PER_COMPONENT>`, its components in
  {py:data}`~spiflash.model.COMPONENTS`), so
  {py:data}`~spiflash.model.COMPARED_VALUES` has `"ecc.strength_bits"` and
  `"ecc.step_bytes"`, which {py:meth}`Record.given
  <spiflash.model.Record.given>` and {py:meth}`Flash.value
  <spiflash.model.Flash.value>` read;
- {py:class}`~spiflash.model.Claim` has a fifth field, `dummy_clocks`, and
  {py:class}`~spiflash.model.SupportedOperation` has
  {py:attr}`~spiflash.model.SupportedOperation.dummy_clocks` and
  {py:meth}`~spiflash.model.SupportedOperation.dummy_clocks_given`;
- {py:class}`~spiflash.sfdp.SfdpFacts` has `dies`, and
  {py:func}`~spiflash.sfdp_tools.to_entry` stores them;
- the command's description gains a SPI NAND part's spare area, block,
  planes, dies and ECC requirement, and a SPI NOR part's dies, and its
  opcode table a part's own dummy clocks; the site a NAND geometry (or
  Dies) section and a Dummy column on the chip pages, a SPI NAND table on
  [](opcodes.md), and data issues for the new values.

Changes in data format 9 (4-byte addressing, supply, OTP, legacy ids):

- {py:class}`~spiflash.enums.FourByteMethod` and
  {py:class}`~spiflash.enums.AddressBytes` live in {py:mod}`spiflash.enums`
  (`spiflash.sfdp` still imports them); a `FourByteMethod`'s value is a
  token (`"en4b"`, `"wren_en4b"`, `"wrear"`, `"ear_bit7"` (new: flashrom's
  bit 7 of the extended address register), `"brwr"`, `"nv_cr"`,
  `"opcodes_4b"`, `"always_4b"`, and the ways out `"hw_reset"`,
  `"sw_reset"`, `"power_cycle"`), and its old display string is
  {py:attr}`~spiflash.enums.FourByteMethod.label`. So
  {py:meth}`Sfdp.to_json() <spiflash.sfdp.Sfdp.to_json>`'s
  `"four_byte_enter"` and `"four_byte_exit"` (in `spiflash sfdp --json` and
  {py:meth}`Flash.to_json() <spiflash.model.Flash.to_json>`'s `"sfdp"`) are
  lists of tokens, `["en4b", "wrear"]`, not of display strings;
- the records gain `"four_byte_modes"` (the ways into 4-byte address mode
  the entry states, `[]` without; never `"opcodes_4b"` nor a way out),
  `"supply_mv"` (the voltage, in millivolts, a programmer's table says to
  power the part at; never with `"voltage"`), `"otp"` (`{"size": 768,
  "regions": 3}`: the OTP bytes the user can program, `"regions"` only where
  given) and `"legacy_ids"` (`[["res1", "15"], ["rems", "ef12"]]`, `[]`
  without). A `"via"` key may name a way in (`"four_byte_modes:en4b"`, or
  `"four_byte_modes"` for one token giving several), `supply_mv` and `otp`;
- the operations a way in gives (EN4B; WREAR and RDEAR; BRWR and BRRD:
  {py:data}`~spiflash.derive.FOUR_BYTE_MODE_OPERATIONS`) are no longer
  stored, but derived, and {py:class}`~spiflash.sfdp.SfdpFacts` gives
  `four_byte_modes` and `opcodes_4b` instead of `four_byte_enter`, and no
  operation a way in gives; {sfsrc}`flashrom`'s and {sfsrc}`flashprog`'s
  `FEATURE_4BA_ENTER_EAR7` no longer gives 0xc5/0xc8 (its Spansion parts
  write bit 7 with 0x17); the `otp` claims an area or an OTP operation
  implies, and the flags and notes the new fields hold, go (Dediprog's
  `Voltage` and `AlternativeID`, IMSProg's `chipVCC` and its page and
  block template, flashrom's OTP comments, the `FEATURE_4BA_` ways in,
  Rockchip's `FEA_4BYTE_ADDR_MODE`);
- {py:class}`~spiflash.model.Record` has
  {py:attr}`~spiflash.model.Record.four_byte_mode_claims` (stored, JSON
  `"four_byte_modes"`), {py:attr}`~spiflash.model.Record.four_byte_modes`
  (it and its SFDP tables'), {py:attr}`~spiflash.model.Record.supply_mv`,
  {py:attr}`~spiflash.model.Record.otp` (a new
  {py:class}`~spiflash.model.Otp`),
  {py:attr}`~spiflash.model.Record.legacy_ids` (new
  {py:class}`~spiflash.model.LegacyId`s), and derived
  {py:attr}`~spiflash.model.Record.address_bytes` and
  {py:attr}`~spiflash.model.Record.test_status`
  ({py:func}`~spiflash.model.parse_tested`, a
  {py:class}`~spiflash.model.TestStatus` of
  {py:class}`~spiflash.enums.TestResult`s); a way in implies `4byte_addr`,
  an OTP area or OTP operation `otp`;
- {py:class}`~spiflash.model.Flash` has
  {py:attr}`~spiflash.model.Flash.four_byte_modes`,
  {py:meth}`~spiflash.model.Flash.four_byte_mode_sources`,
  {py:attr}`~spiflash.model.Flash.address_bytes` (`4byte_addr` is in
  {py:attr}`~spiflash.model.Flash.features` exactly when it is neither
  `"3"` nor `None`), {py:attr}`~spiflash.model.Flash.supply_mv`,
  {py:meth}`~spiflash.model.Flash.supply_outside`,
  {py:attr}`~spiflash.model.Flash.otp`,
  {py:attr}`~spiflash.model.Flash.legacy_ids` and
  {py:attr}`~spiflash.model.Flash.answers_legacy`; {py:meth}`Flash.to_json()
  <spiflash.model.Flash.to_json>` gains them;
- {py:data}`~spiflash.model.COMPARED` gains `otp`, compared per component
  (`"otp.size"`, `"otp.regions"`); `supply_mv` is not compared for
  equality, but against the ranges (a new SUPPLY kind of data issue);
- {py:meth}`Database.lookup(id, method="res1") <spiflash.db.Database.lookup>`
  also gives the JEDEC chips whose records list the id, after the legacy
  chips, each with `answers_legacy` set;
- new operations: [RSECR](opcodes/RSECR.md), [PSECR](opcodes/PSECR.md),
  [ESECR](opcodes/ESECR.md), [READ_OTP](opcodes/READ_OTP.md),
  [ENSO](opcodes/ENSO.md), [EXSO](opcodes/EXSO.md),
  [ENTER_OTP_3A](opcodes/ENTER_OTP_3A.md) and [RUID](opcodes/RUID.md) (an id
  read, so listed before the reads in `spiflash opcodes`);
- the command's description gains `supply 3.3 V` (where no source gives a
  range), `OTP 768 B (3 x 256 B)`, `4-byte: ...` and `also answers
  res1:15`, and with `-v` the legacy ids and the supplies outside a range;
  the site a 4-byte addressing section, legacy ids and the OTP area on the
  chip pages, and the SUPPLY data issues.

Changes in data format 10 (the timing model, and the listed clock):

- **`Operation.timing` is {py:attr}`Operation.shape_source
  <spiflash.opcodes.Operation.shape_source>`**, and its enum
  `spiflash.enums.TimingSource` is {py:class}`~spiflash.enums.ShapeSource`
  (the same members): where an operation's address bytes and dummy clocks
  come from, not a duration, which the old name suggested beside the new
  {py:attr}`Record.timings <spiflash.model.Record.timings>`;
- one unit of time, the nanosecond: the SFDP decoder's
  `EraseType.typical_us`, `Bfpt.page_program_us` and `Bfpt.chip_erase_us`
  are {py:attr}`~spiflash.sfdp.EraseType.typical_ns`,
  {py:attr}`~spiflash.sfdp.Bfpt.page_program_ns` and
  {py:attr}`~spiflash.sfdp.Bfpt.chip_erase_ns`, and so are their keys in
  {py:meth}`Sfdp.to_json() <spiflash.sfdp.Sfdp.to_json>` (`spiflash sfdp
  --json`, and {py:meth}`Flash.to_json() <spiflash.model.Flash.to_json>`'s
  `"sfdp"`), which gains the multipliers, the byte program, suspend and
  deep power-down exit times, and `"timings"`;
  `spiflash.units.human_time(us)` is
  {py:func}`~spiflash.units.human_duration` (in ns; `µs`, not `us`), and
  {py:func}`~spiflash.units.human_frequency` is new;
- {py:class}`~spiflash.sfdp.Bfpt` gains `erase_max_multiplier` (DW10[3:0])
  and `program_max_multiplier` (DW11[3:0]), the byte program times, DW12's
  suspend latencies and resume-to-suspend intervals, and DW14's
  `exit_deep_power_down_delay_ns`; {py:class}`~spiflash.sfdp.SfdpFacts`
  gains `timings`; {py:meth}`Sfdp.operations()
  <spiflash.sfdp.Sfdp.operations>` gives DW14's deep power-down opcodes;
- new: {py:mod}`spiflash.timings` ({py:class}`~spiflash.timings.Timings`,
  {py:class}`~spiflash.timings.TimingKey`, {py:data}`~spiflash.timings.BOUNDS`),
  {py:class}`~spiflash.enums.TimedEvent`, {py:class}`~spiflash.enums.Bound`,
  and {py:func}`spiflash.derive.sfdp_timings`,
  {py:data}`~spiflash.derive.CHIP_ERASE_MULTIPLIER` and
  {py:func}`~spiflash.derive.compared_time`;
- the records gain `"timings"` (`{"chip_erase": {"unspecified":
  200000000000}}`, `{}` without) and `"listed_clock_hz"` (`null` without); a
  `"via"` key may name a time (`"timings.dpd_exit"`, or `"timings"` for one
  token giving several) and `listed_clock_hz`. {sfsrc}`zephyr`'s `has-dpd` and
  `dpd-wakeup-sequence` flags go: the first is the new
  [DP](opcodes/DP.md) and [RDPD](opcodes/RDPD.md) operations (a release by
  0xab alone, not [RES](opcodes/RES.md)'s signature read), the second three
  times;
- {py:class}`~spiflash.model.Record` has
  {py:attr}`~spiflash.model.Record.timing_claims` (stored, JSON
  `"timings"`), {py:attr}`~spiflash.model.Record.timings` (them, over what
  its SFDP tables give) and {py:attr}`~spiflash.model.Record.listed_clock_hz`;
  {py:meth}`~spiflash.model.Record.given` reads a time as
  `"timings.chip_erase.maximum"`, and
  {py:meth}`~spiflash.model.Record.sfdp_disagreements` gives a stated time
  its tables give otherwise;
- {py:class}`~spiflash.model.Flash` has
  {py:meth}`~spiflash.model.Flash.timing`,
  {py:attr}`~spiflash.model.Flash.timings`,
  {py:meth}`~spiflash.model.Flash.timing_order` and
  {py:attr}`~spiflash.model.Flash.listed_clock_hz`, and
  {py:meth}`Flash.to_json() <spiflash.model.Flash.to_json>` gains
  `"timings"` and `"listed_clock_hz"`;
- {py:data}`~spiflash.model.COMPARED` gains `timings`, compared per
  component, each (event, bound) its own
  ({py:data}`~spiflash.model.TIMING_COMPONENTS`: `"timings.chip_erase.unspecified"`,
  ...), at SFDP resolution where a BFPT writes the time directly; not
  `listed_clock_hz`, the clock Dediprog lists, a catalogue figure of
  unspecified meaning and no safe maximum;
  {py:func}`~spiflash.model.compared_value` takes the value's name;
- {py:func}`~spiflash.sfdp_tools.encode` writes DW10, DW11, DW12 and DW14
  where the database holds exactly what they say (DW13's suspend opcodes
  assumed), and {py:func}`~spiflash.sfdp_tools.to_entry` stores the tables'
  times;
- {py:attr}`Operation.description <spiflash.opcodes.Operation.description>`
  of [RES](opcodes/RES.md) is "Read electronic signature";
- the data issues compare one source's entries with each other only within
  one part (source, extended id and part number: the EN25Q32 and EN25Q32C
  are two parts, W25Q512JV and W25Q512JV-IQ one), so SAME_SOURCE goes from
  73 issues to 4 before the new fields; {py:attr}`Flash.conflicts
  <spiflash.model.Flash.conflicts>` likewise, so it loses five conflicts of
  one source's different parts (sizes on d5b2, 966018 and 9d4010,
  flashprog's `protection.wps` on 0b4018 and `protection.cmp` on a14014);
  {py:func}`~spiflash.model.part_key` and
  {py:func}`~spiflash.model.record_part` are new;
- the command's description gains a `timing:` line, and with `-v` every
  time and the clock Dediprog lists; the site a Timing section on the chip
  pages, and a TIMING kind of data issue.

Changes since data format 10 (the format is unchanged):

- {py:func}`~spiflash.sfdp_tools.encode` never writes the opposite of what
  the database holds. A maximum the BFPT writes directly (DW14's deep
  power-down exit delay, DW12's suspend latencies) is rounded up to the next
  one it can write; a dword whose value is known but not writable (a chip
  erase time of unspecified bound, an erase time off DW10's grid, a page
  size that is not a power of two, deep power-down with no release or exit
  delay known) lowers the revision even with `assume`; the QER is the
  reserved 7 where nothing is known of the QE bit (never 0, "no QE bit"),
  and where the QE bit is SR2 bit 1, the code its status-register
  operations give; no way out of 4-byte mode is written that the part's own
  tables leave out; and a part whose first nine dwords would deny what the
  database says (QPI with no 4-4-4 read known, a quad read with no quad
  read, a 4 KiB erase with no uniform 3-byte eraser of it, a read with no
  dummy clocks known) is refused with `ValueError`;
- {py:func}`~spiflash.sfdp_tools.diff` compares erase types by opcode
  (`erase_types.0x20`, not by index), and takes `encoded=True` to mark
  every loss {py:data}`~spiflash.sfdp_tools.ENCODE_LOSSES` documents as
  expected;
- {py:meth}`Sfdp.operations() <spiflash.sfdp.Sfdp.operations>` gives DW15's
  QPI enable and disable commands ([EQPI_38](opcodes/EQPI_38.md),
  [EQPI_35](opcodes/EQPI_35.md), [RSTQIO_FF](opcodes/RSTQIO_FF.md),
  [RSTQIO_F5](opcodes/RSTQIO_F5.md));
- {py:meth}`Database.lookup <spiflash.db.Database.lookup>` reads a legacy
  chip's key (`"res1:15"`), puts the longest matching id first, and gives a
  one-byte id (Linux's `c2`) only where no longer one fits;
  {py:meth}`Database.find <spiflash.db.Database.find>` takes `exact=True`;
- {py:meth}`Flash.to_json() <spiflash.model.Flash.to_json>` gains `"key"`,
  and its `"jedec_id"` is `null` for a legacy chip;
  {py:meth}`SourceInfo.to_json() <spiflash.db.SourceInfo.to_json>` is new;
- `FastRead.address_dtr` is gone; {py:func}`spiflash.units.human_supply` and
  {py:data}`spiflash.opcodes.DIE_SELECT_OPERATIONS` are new;
- the command: `sources` and `jep106` take `--json`, `id --method` lists its
  choices, every failure says why on stderr, `list` prints each chip's key
  and exits 1 for a maker with no chips, and a supply range is written with
  an en dash.
