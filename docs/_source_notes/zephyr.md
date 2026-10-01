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
  (`enter-4byte-addr`, `has-lock`, ...), kept in the record's `flags`.
- The part's times are the record's `timings` ([](../derived.md#times)),
  each bounded as its binding's words say
  ({upstream}`jedec,spi-nor-common.yaml <zephyr:dts/bindings/mtd/jedec,spi-nor-common.yaml>`,
  {upstream}`jedec,nor-mspi.yaml <zephyr:dts/bindings/mtd/jedec,nor-mspi.yaml>`,
  {upstream}`atmel,at45.yaml <zephyr:dts/bindings/mtd/atmel,at45.yaml>`,
  {upstream}`jedec,spi-nand.yaml <zephyr:dts/bindings/mtd/jedec,spi-nand.yaml>`;
  the extractor checks each property it reads is declared there): `t-enter-dpd`
  ("Duration required to complete the DPD command": tDP, a maximum),
  `t-exit-dpd` (tRES1, a maximum), `t-reset-recovery` ("Minimum time ... the
  chip needs to recover after reset": the host's least wait, so the part's
  maximum), `t-reset-pulse` (a minimum), the AT45's `enter-dpd-delay` and
  `exit-dpd-delay` (maxima; not with `use-udpd`, when they are ultra-deep
  power-down's), and `dpd-wakeup-sequence`'s tDPDD, tCDRP and tRDP (minimum,
  minimum, maximum, as the MX25R datasheets give them). 0 is not given. SPI
  NAND's `*-duration-max` (in µs) would be maxima, but no node naming its
  part gives them (the two that do name it only in a comment). The
  st_b_m2mem_pack1 shield's 5 ms reset pulse and 10 ms recovery, on every
  module it carries, are not taken: its reset line drives the module's
  supply (its overlay: "The reset line drives the module LDO enable, so a
  reset is a power cycle"), so they are the rail's, not the part's RESET#
  times, and a note says so
  ({py:data}`~spiflash_extract.zephyr.BOARD_MARGINS`). Some `t-exit-dpd`s
  are not the part's either, and are not taken, with a note
  ({py:data}`~spiflash_extract.zephyr.NOT_THE_PARTS`): the GD25Q16C boards'
  100 µs, a margin over its datasheet's tRES1 of 20 µs; nrf7002dk's 5 µs,
  shorter than the MX25R6435F's tRDP; rm1xx_dvk's 20 ns for its AT25DF041B,
  a unit slip. (The MX25L3233F's 100 µs is its datasheet's tRES1.) A
  `t-exit-dpd` is often more precise than the BFPT's DW14, which rounds it
  up (the MX25R6435F's 35 µs is 40 µs there): it is kept, and is no
  disagreement.
  `spi-max-frequency` and the other clock and controller properties are the
  board's settings, not the part's, and are not taken.
- `has-dpd` gives [DP](../opcodes/DP.md) and [RDPD](../opcodes/RDPD.md)
  (the binding: "implies that the RDPD (0xAB) Release from Deep Power Down
  command is also supported"), but RDPD not where the node gives a
  `dpd-wakeup-sequence`, whose part wakes by a pulse of chip select. Where
  the node's BFPT has DW14, both are its tables'.
  `quad-enable-requirements` is JESD216's quad enable requirement, the
  record's `quad_enable_requirement` (which gives its quad enable bit), but
  on a `jedec,spi-nor` node, whose driver takes the requirement from the
  BFPT and ignores the property: there it stays a flag. `requires-ulbpr`
  gives [ULBPR](../opcodes/ULBPR.md).
- `enter-4byte-addr` is BFPT DW16[31:24] as a byte
  ({upstream}`jedec,jesd216.yaml <zephyr:dts/bindings/mtd/jedec,jesd216.yaml>`;
  {upstream}`spi_nor.c <zephyr:drivers/flash/spi_nor.c>`'s
  `spi_nor_set_address_mode` and
  {upstream}`nrf_qspi_nor.c <zephyr:drivers/flash/nrf_qspi_nor.c>` read it so):
  the record's ways into 4-byte mode ([](../derived.md#4-byte-addressing)),
  bit 5 (dedicated 4-byte opcodes) a `4byte_opcodes` claim, 0 and 0xff
  nothing. The only one in the tree, p2d's GD25LE255E `<0xb7>`, is EN4B's
  opcode, with the reserved bit 7 set: it is not read, and a note says so
  (its BFPT's DW16 gives `en4b` anyway). The Renesas OSPI binding's
  `enter-4byte-command`, the opcode its driver sends with no write enable
  ({upstream}`flash_renesas_ra_ospi_b.c <zephyr:drivers/flash/flash_renesas_ra_ospi_b.c>`),
  is `en4b` for `<0xb7>`.
- About a fifth of the nodes carry a copy of the chip's own SFDP Basic
  Flash Parameter table (`sfdp-bfp`), and a few its 4-byte instruction and
  xSPI profile tables (`sfdp-ff84`, `sfdp-ff05`). The record stores them (its
  `sfdp_tables`) and works out from them its density, fast reads with their
  dummy clocks, erase types and page size as the chip itself reports them
  ([](../derived.md#sfdp-tables)). A size or page size the node gives too is
  stored only where it differs from the table's, and is then a
  [data issue](../issues/sfdp.md): the `spi_nor` driver refuses a size its
  table contradicts. The tables are what the board's porter copied, not
  always the part's: frdm_mcxe247's W25Q64 carries the MX25R6435F's BFPT
  byte for byte (its node says the quad enable is S2B1v1, the table SR1 bit
  6), and wio_tracker_l1's P25Q16H, 2 MiB, carries another part's, a 16 MiB
  one with DTR. Tables whose density is not the node's size, or whose quad
  enable requirement is not the node's, are another part's: they are not
  taken, and a note says so.
  The W25Q64 nodes' S2B1v1 is wrong too: the W25Q64JV's 1-byte WRSR leaves
  its Status Register-2 alone, which is S2B1v4, and the record has that,
  with a note (`QER_WRONG`).
- `page-size` is the part's page for `jedec,spi-nor`, but two drivers take
  it as their own setting, though every binding inherits
  {upstream}`jedec,jesd216.yaml <zephyr:dts/bindings/mtd/jedec,jesd216.yaml>`'s description, "Number of bytes in a page from
  JESD216 BFP DW11": `adi,max32-spixf-nor`'s driver
  ({upstream}`flash_max32_spixf_nor.c <zephyr:drivers/flash/flash_max32_spixf_nor.c>`) uses it only as its flash layout page
  (`.layout.pages_size = DT_INST_PROP(0, page_size)`), and `jedec,nor`'s
  ({upstream}`flash_mspi_nor.c <zephyr:drivers/flash/flash_mspi_nor.c>`) as its program chunk, which must fit the
  controller (`FLASH_PAGE_SIZE_INST(inst) <= PACKET_DATA_LIMIT(inst)`;
  frdm_mcxe247: "Single QSPI IP write must fit the 128-byte Tx FIFO."). For
  those it is kept as a flag (`page-size=128`), not as the part's page.
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
- Two nodes carrying a table make no record, and so their tables are not
  kept: qemu_cortex_r5's `flash@0` and `flash@1` (id `20 bb 20`, a 512 Mbit
  Micron part, with a BFPT) have no name that looks like a part number;
  and stm32l4r9i_disco's MX25LM51245 node has no `jedec-id`, its
  `sfdp-bfp` being a whole SFDP area (it starts `53 46 44 50`, "SFDP")
  rather than the BFPT the property is for.
