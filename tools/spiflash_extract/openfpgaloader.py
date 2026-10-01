"""openFPGALoader: :upstream:`openfpgaloader:src/spiFlashdb.hpp`.

A C++ ``std::map<uint32_t, flash_t>`` keyed by the three RDID bytes::

    {0xef4018, {
        .manufacturer = "Winbond",
        .model = "W25Q128",
        .nr_sector = 256,        // 64 KiB sectors
        .sector_erase = true,    // 64 KiB erase
        .subsector_erase = true, // 4 KiB erase
        ...
        .quad_register = STATR,
        .quad_mask = (1 << 9),
    }},
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from spiflash import derive

from . import cparse
from .ops import Opcodes
from .record import Record, make

if TYPE_CHECKING:
    from pathlib import Path

DB = "src/spiFlashdb.hpp"
FLASH_CPP = "src/spiFlash.cpp"  # the FLASH_* opcodes it sends


def extract(root: Path) -> list[Record]:
    raw = (root / DB).read_text()
    flash_defs: dict[str, str | int] = dict(
        cparse.defines(cparse.strip_comments((root / FLASH_CPP).read_text()))
    )
    text = cparse.drop_preprocessor(cparse.strip_comments(raw))
    table = cparse.array_body(text, r"std::map\s*<\s*uint32_t\s*,\s*flash_t\s*>\s*flash_list")
    if table is None:
        msg = f"{DB}: no flash_list map"
        raise ValueError(msg)
    symbols: dict[str, str | int] = {
        "STATR": 0,
        "FUNCR": 1,
        "CONFR": 2,
        "NVCONFR": 3,
        "NONER": 99,
        "true": 1,
        "false": 0,
    }
    records = []
    for entry in cparse.braced_items(table.body, table.offset):
        key, value = cparse.split_top(entry.body)
        fields = cparse.designated(value.strip()[1:-1])
        nr_sector = cparse.evaluate(fields["nr_sector"], symbols)
        size = nr_sector * 64 * 1024
        # The table's sectors are 64 KiB, its subsectors 4 KiB: the blocks
        # block64_erase() (0xd8) and sector_erase() (0x20) erase.
        erasers, via = [], {}
        for field, opcode, block in (
            ("subsector_erase", 0x20, 4096),
            ("sector_erase", 0xD8, 64 << 10),
        ):
            if cparse.evaluate(fields.get(field, "false"), symbols):
                erasers.append(derive.block_eraser(opcode, block, size).to_json())
                via[f"erasers:0x{opcode:02x}"] = f"{field}={fields[field].strip()}"
        chip_id = cparse.evaluate(key, symbols)
        notes = cparse.comments(raw[entry.offset : entry.offset + len(entry.body)])
        layout, layout_via = _protection(fields, symbols, chip_id, notes)
        quad_enable, quad_via = _quad_enable(fields, symbols)
        flags = [
            f"{k}={v.strip()}"
            for k, v in fields.items()
            if k not in ("manufacturer", "model", "nr_sector")
        ]
        records.append(
            make(
                "openfpgaloader",
                DB,
                cparse.line_of(raw, entry.offset),
                cparse.c_string(fields["model"]),
                vendor=cparse.c_string(fields["manufacturer"]),
                id=f"{chip_id:06x}",
                size=size,
                erasers=erasers or None,
                flags=flags,
                via={**via, **layout_via, **quad_via},
                quad_enable=quad_enable,
                protection=layout,
                opcodes=_opcodes(
                    fields, {e["opcode"] for e in erasers}, size, flash_defs | symbols
                ),
                notes=notes,
            )
        )
    return records


def _mask_bit(mask: int) -> int | None:
    """The bit a one-bit mask (``(1 << 5)``) sets; ``None`` for 0."""
    if not mask:
        return None
    if mask & (mask - 1):
        msg = f"a mask of more than one bit: 0x{mask:x}"
        raise ValueError(msg)
    return mask.bit_length() - 1


def _quad_enable(
    fields: dict[str, str], symbols: dict[str, str | int]
) -> tuple[dict[str, object] | None, dict[str, str]]:
    """The quad enable bit set_quad_bit() (spiFlash.cpp) sets: ``quad_mask``
    of the status register (``STATR``, read with 0x05) or of ``CONFR``, read
    with 0x35. ``NONER`` or a mask of 0 is "not filled in" (its error says
    "has no Quad bit (or spiFlashdb must be updated)"), so no bit; and
    nothing uses ``NVCONFR``."""
    register = fields.get("quad_register", "NONER").strip()
    mask = cparse.evaluate(fields.get("quad_mask", "0"), symbols)
    if register == "NONER" or not mask:
        return None, {}
    if register not in _QUAD_REGISTERS:
        msg = f"quad_register {register}: no register for it"
        raise ValueError(msg)
    bit = _mask_bit(mask)
    via = {"quad_enable": f"quad_register={register}; quad_mask={fields['quad_mask'].strip()}"}
    return {"register": _QUAD_REGISTERS[register], "bit": bit}, via


_QUAD_REGISTERS = {"STATR": "sr1", "CONFR": "sr2"}

#: The register get_tb() reads TB from, by ``tb_register``: STATR with
#: 0x05, FUNCR with 0x48 (ISSI's function register), CONFR with 0x35;
#: on a Macronix part, CONFR is its configuration register, read with
#: 0x15 (TB is its bit 3). get_tb() means to read that with 0x15 too, but
#: its test (``(_jedec_id >> 8) == 0xC220``, of a 4-byte id) never holds,
#: so it reads 0x35 on every part.
_TB_REGISTERS = {"STATR": "sr1", "FUNCR": "function", "CONFR": "sr2"}
MACRONIX = 0xC2


def _protection(
    fields: dict[str, str], symbols: dict[str, str | int], chip_id: int, notes: list[str]
) -> tuple[dict[str, object] | None, dict[str, str]]:
    """The block-protection bits: the BP bits at ``bp_offset``'s non-zero
    masks, in order, in the status register (``bp_len`` counts them, though
    wrongly for the MX25L parts, 5 for 4 offsets; 0 is "no block
    protection"); TB at ``tb_offset`` in ``tb_register``, OTP where
    ``tb_otp``. ``NONER``, an offset of 0 ("unused") or one past the first
    byte (``(1 << 14)``: get_tb() reads one byte) is no TB, and a note says
    why for the last. A TB on a BP bit is left out, with a note."""
    bp_len = fields.get("bp_len", "0").strip()
    if not cparse.evaluate(bp_len, symbols):
        return None, {}
    # An offset left out is 0, as C initialises it.
    given = fields.get("bp_offset", "{}").strip()
    offsets = cparse.split_top(given[1:-1])
    bits = [b for o in offsets if (b := _mask_bit(cparse.evaluate(o, symbols))) is not None]
    if not bits:
        return None, {}
    out: dict[str, object] = {f"bp{i}": {"register": "sr1", "bit": b} for i, b in enumerate(bits)}
    via = {"protection": f"bp_len={bp_len}; bp_offset={given}"}
    register = fields.get("tb_register", "NONER").strip()
    mask = cparse.evaluate(fields.get("tb_offset", "0"), symbols)
    if register == "NONER" or not mask:
        return out, via
    tb = f"tb_offset={fields['tb_offset'].strip()}"
    if mask > 0xFF:
        notes.append(f"{tb} left out: get_tb() reads one byte of the status register")
        return out, via
    reg = _TB_REGISTERS[register]
    if register == "CONFR" and chip_id >> 16 == MACRONIX:
        reg = "sr3"
    bit = _mask_bit(mask)
    if reg == "sr1" and bit in bits:
        notes.append(f"{tb} left out: it is a BP bit, bp_offset's")
        return out, via
    otp = fields.get("tb_otp", "false").strip()
    writability = {"writability": "otp"} if cparse.evaluate(otp, symbols) else {}
    out["tb"] = {"register": reg, "bit": bit, **writability}
    via["protection.tb"] = f"tb_register={register}; {tb}; tb_otp={otp}"
    return out, via


def _opcodes(
    fields: dict[str, str], erasers: set[int], size: int, symbols: dict[str, str | int]
) -> list[dict[str, object]]:
    """What openFPGALoader's src/spiFlash.cpp sends to a part: read and page
    program for every part (driver defaults: assumed); its sector_erase()
    (0x20) for a table subsector_erase and block64_erase() (0xd8) for
    sector_erase (the erasers give those, but the FLASH_* defines check
    them); and above 16 MiB the 4-byte form of each, which its erase
    functions switch to for any address above 0xffffff, whatever the part:
    a driver default too. The values are spiFlash.cpp's FLASH_* defines."""
    big = size > 16 * 1024 * 1024
    ops = Opcodes(symbols)
    ops.add("RDID", "JEDEC id match")
    ops.add("READ_1_1_1", "every read", "FLASH_READ", assumed=True)
    ops.add("PP_1_1_1", "every write", "FLASH_PP", assumed=True)
    if big:
        ops.add("READ_1_1_1_4B", "every read above 16 MiB", "FLASH_4READ", assumed=True)
        ops.add("PP_1_1_1_4B", "every write above 16 MiB", "FLASH_4PP", assumed=True)
    if 0x20 in erasers:
        ops.add("BE_4K", "subsector_erase = true", "FLASH_SE")
        if big:
            ops.add("BE_4K_4B", "subsector_erase = true, above 16 MiB", "FLASH_4SE", assumed=True)
    if 0xD8 in erasers:
        ops.add("SE", "sector_erase = true", "FLASH_BE64")
        if big:
            ops.add("SE_4B", "sector_erase = true, above 16 MiB", "FLASH_4BE64", assumed=True)
    # set_quad_bit() reads CONFR with 0x35 and writes it back with a 2-byte
    # WRSR (SR1, CONFR).
    quad, _ = _quad_enable(fields, symbols)
    if quad is not None and quad["register"] == "sr2":
        ops.add("RDSR2", "set_quad_bit: CONFR", "FLASH_RDCR")
        ops.add("WRSR_16", "set_quad_bit: CONFR", "FLASH_WRSR")
    # SST26VF032B/064B lock at power-up: the driver unlocks them first.
    if cparse.evaluate(fields.get("global_lock", "false"), symbols):
        ops.add("ULBPR", f"global_lock={fields['global_lock'].strip()}", "FLASH_ULBPR")
    return ops.to_json()
