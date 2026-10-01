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
block-protection layout: the `flags` of its records (`bp_offset`,
`tb_register`, `quad_mask`, ...), which no other source gives. `bp_len` and
`quad_register`, which give a record its `lock` and `quad_read`, are those
capabilities' `via`. The
opcodes are what its {upstream}`openfpgaloader:src/spiFlash.cpp` sends:
read, page program, and the erases the table allows. The read and page
program, sent to every part, are its driver's defaults: they imply no
capability.
