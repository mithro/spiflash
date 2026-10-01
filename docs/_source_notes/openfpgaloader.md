[openFPGALoader](https://github.com/trabucayre/openFPGALoader) is a
universal utility for programming FPGAs, and the SPI flash that configures
them at power-up. It reads a flash's JEDEC [read-id](../opcodes/RDID.md)
answer and looks it up in {upstream}`openfpgaloader:src/spiFlashdb.hpp`, a
C++ `std::map` keyed by the three id bytes, to know how to lift and set the
chip's write protection. It is SPI NOR only, and its table is the smallest
here.

An entry gives the manufacturer and model, the number of 64 KiB sectors,
which erases the part has (`sector_erase`, 0xd8 over the 64 KiB sectors, and
`subsector_erase`, 0x20 over 4 KiB ones: the records' erase layouts), and its
status register bits:

- `bp_offset`'s masks are the BP bits in SR1, in order (`bp_len` counts them,
  wrongly for the MX25L parts, 5 for 4 masks; 0 is no block protection);
- `tb_register` and `tb_offset` are the TB bit: `STATR` is SR1, `FUNCR`
  ISSI's function register (read with 0x48), `CONFR` SR2 (read with 0x35),
  or on a Macronix part its configuration register, read with 0x15: SR3.
  Its `get_tb()` means to read that one with 0x15, but tests a four-byte id
  against two bytes, so never does. A TB of `(1 << 14)` (the GD25Q16C and
  GD25Q32C, whose comment says it is CMP) is past the one byte `get_tb()`
  reads, and the AT25DF321A's is one of its BP bits: neither is taken, and
  a note says why. `tb_otp` makes it one-time programmable;
- `quad_register` and `quad_mask` are the quad enable bit its
  `set_quad_bit()` sets; `NONER` or a mask of 0 is "not filled in" (its
  error says "or spiFlashdb must be updated"), so no bit. The GD25Q32C's
  `STATR (1 << 6)` is wrong: its datasheet has QE at S9 (SR2 bit 1), and
  bit 6 is BP4;
- `global_lock` parts (the SST26VF032B and 064B) power up locked, and it
  unlocks them with [ULBPR](../opcodes/ULBPR.md).

The rest stay its records' `flags`. The
opcodes are what its {upstream}`openfpgaloader:src/spiFlash.cpp` sends:
read, page program, and the erases the table allows. The read and page
program, sent to every part, and the 4-byte forms of all of them, which it
sends for any address above 16 MiB whatever the part, are its driver's
defaults: they imply no capability.

Its XT25F32B (`0x0b4016`) has `nr_sector = 1024`, which makes the 4 MiB part
64 MiB: a size the other sources contradict, and the only source of the
record's `4byte_addr`.
