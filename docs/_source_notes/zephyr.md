[Zephyr](https://www.zephyrproject.org/) is a real-time operating system
for microcontrollers. It has no table of parts: its boards describe the flash
chip each carries, in devicetree, for the driver to check at start-up (it
compares the chip's [read-id](../opcodes/RDID.md) answer with the node's
`jedec-id`). Every node with a `jedec-id` in {upstream}`zephyr:boards/` and
{upstream}`zephyr:dts/` is read (`.dts`, `.dtsi` and `.overlay` files); the
overlays in `samples/` and `tests/` are not, as they configure test set-ups,
some with placeholder ids.

- A node gives the id, the size (in bits in the JESD216-based bindings), and,
  where the board sets them, the page size, the read and program modes it
  uses (`readoc`, `writeoc`, the MSPI I/O mode) and what the chip needs
  (`has-dpd`, `quad-enable-requirements`, `enter-4byte-addr`, ...), kept in
  the record's `flags`.
- About a fifth of the nodes carry a copy of the chip's own SFDP Basic
  Flash Parameter table (`sfdp-bfp`), and a few its 4-byte instruction and
  xSPI profile tables (`sfdp-ff84`, `sfdp-ff05`). The record stores them (its
  `sfdp_tables`) and works out from them its density, fast reads with their
  dummy clocks, erase types and page size as the chip itself reports them
  ([](../derived.md#sfdp-tables)). A size or page size the node gives too is
  stored only where it differs from the table's, and is then a
  [data issue](../issues/sfdp.md): the `spi_nor` driver refuses a size its
  table contradicts, and other drivers use `page-size` as their controller's
  write chunk rather than the part's page.
- Devicetree has no field for the part name: it is the node's name, a label,
  a comment on the `jedec-id` line or a descriptive `compatible`, whichever
  first looks like a part number, and a node none of them names is left out.
- The maker is named only where a `compatible` does (`"issi,is25lp128"`,
  `"mxicy,mx25u"`), so a chip only {sfsrc}`zephyr` has may have no
  manufacturer.
- Boards often share a chip: nodes that give the same values are one record,
  at the first file, with a note listing the others.
- The values are written, and copied between boards, by each board's porter,
  and some are wrong (a size given in bytes where bits are meant, an id
  copied from another board). That is why {sfsrc}`zephyr` comes last when
  sources are tied, and why its disagreements are worth reading on
  [its data issues page](../issues/source-zephyr.md).
