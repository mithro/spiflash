"""The spiflash command."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

import spiflash
from spiflash import cli, units
from spiflash.enums import FlashType
from spiflash.model import Flash
from spiflash.sfdp import parse
from test_sfdp import MX25L25635E, W25Q512JV

if TYPE_CHECKING:
    from pathlib import Path


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = cli.main(list(args))
    return code, capsys.readouterr().out


def test_id(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "id", "ef4018")
    assert code == 0
    assert out.startswith("ef4018  Winbond  ")
    assert "size 16 MiB, page 256 B, sector 64 KiB, 2.7\N{EN DASH}3.6 V" in out
    assert "from: flashrom" in out


def test_id_timing(capsys: pytest.CaptureFixture[str]) -> None:
    # QEMU's IS25WP256 dump: typical and maximum (DW10's multiplier, 8);
    # Dediprog's chip erase time, its bound not given.
    _, out = run(capsys, "id", "9d7019")
    assert (
        "    timing: chip erase 60 s typ, ≤ 480 s, ~180 s (bound not given; also 70 s, 90 s);"
        in out
    )
    assert "page program 200 µs typ, ≤ 1.2 ms; DPD exit ≤ 15 µs" in out
    # -v: every time, each value with who gives it; and the clock.
    _, out = run(capsys, "id", "c22817", "-v")
    assert "    time: DPD exit, maximum: 5 µs (zephyr); 35 µs (zephyr)" in out
    assert "    time: block erase 0x20, maximum: 384 ms (zephyr (SFDP))" in out
    assert "    clock: Dediprog lists 70 MHz" in out
    # Compared at SFDP resolution, shown as given.
    assert "sources disagree on timings.dpd_exit.maximum: 5 µs (zephyr); 35 µs (zephyr)" in out
    _, js = run(capsys, "id", "c22817", "--json")
    (chip,) = json.loads(js)
    assert chip["timings"]["dpd_exit"]["maximum"]["value"] == 35_000
    assert chip["listed_clock_hz"] == 70_000_000


def test_id_registers(capsys: pytest.CaptureFixture[str]) -> None:
    # The QE bit on the detail line; the layout and requirement with -v.
    code, out = run(capsys, "id", "c84016", "-v")
    assert code == 0
    assert "2.7\N{EN DASH}3.6 V, QE SR2[1]" in out
    # openFPGALoader's GD25Q32C entry has the QE bit wrong (S9 is QE).
    assert "sources disagree on quad_enable: SR2 bit 1 (flashprog, rockchip); SR1 bit 6" in out
    # Its bp3 is the bit flashrom's tb is.
    assert "sources put two roles on SR1 bit 5: bp3, tb" in out
    assert "    protection: bp0 SR1 bit 2, bp1 SR1 bit 3, bp2 SR1 bit 4, tb SR1 bit 5" in out
    _, out = run(capsys, "id", "ef4020", "-v")
    assert "quad enable requirement: S2B1v4 (SR2 bit 1, written with a 2-byte WRSR" in out
    _, js = run(capsys, "id", "ef4020", "--json")
    (doc,) = json.loads(js)
    assert doc["quad_enable"] == {"register": "sr2", "bit": 1, "writability": "rw"}
    assert doc["quad_enable_requirement"] == "S2B1v4"
    assert doc["protection"]["bp0"] == {"register": "sr1", "bit": 2}


def test_id_finds_a_folded_nand_id(capsys: pytest.CaptureFixture[str]) -> None:
    # Linux matches the TC58CVG0S3HRAIJ's first two bytes, Dediprog three.
    for query in ("98e2", "98e240"):
        code, out = run(capsys, "id", "--type", "nand", query)
        assert code == 0
        assert out.startswith("98e240  Toshiba  TC58CVG0S3HRAIJ"), query


def test_id_marks_an_inferred_manufacturer(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "id", "--type", "nand", "c952")
    assert out.startswith("c952  HeYangTek (inferred)  HYF2GQ4UAACAE")
    _, out = run(capsys, "id", "ef4018")
    assert "(inferred)" not in out


def test_id_json_lists_every_id(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "id", "--json", "--type", "nand", "c226")
    (doc,) = json.loads(out)
    assert (doc["id"], doc["ids"]) == ("c22603", ["c22603", "c226"])
    assert doc["manufacturer_inferred"] is False


def test_id_verbose_lists_records(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "id", "-v", "01 20 18 4d 01 80")
    assert "ext 4d0180" in out
    assert "drivers/mtd/spi-nor/spansion.c:" in out
    assert "sources disagree on page_size" in out


def test_id_json(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "id", "--json", "ef4018")
    (doc,) = json.loads(out)
    assert doc["manufacturer"] == "Winbond"
    assert doc["size"] == 16 << 20
    # Who gives each capability: claimed, or implied and why.
    qpi = doc["feature_sources"]["qpi"]
    assert qpi == [{"source": "dediprog", "implied": False, "because": "claimed: QPIEnable"}]
    linux = next(s for s in doc["feature_sources"]["quad_read"] if s["source"] == "linux")
    assert linux == {
        "source": "linux",
        "implied": True,
        "because": "implied by READ_1_1_4 (SPI_NOR_QUAD_READ)",
    }


def test_id_legacy_and_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "id", "--method", "res1", "05")
    assert code == 0
    assert out.startswith("res1:05")
    assert run(capsys, "id", "123456")[0] == 1


def test_bad_id(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["id", "xyz"]) == 2
    assert "not a hex id" in capsys.readouterr().err


def test_find(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "find", "w25q128jv")
    assert code == 0
    assert "ef4018" in out


def test_find_unknown_suggests_the_closest(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["find", "W25Q182JV"]) == 1
    err = capsys.readouterr().err
    assert "no part W25Q182JV; the closest: W25Q128JV (ef4018), W25Q128JV (ef7018)" in err


def test_find_glob(capsys: pytest.CaptureFixture[str]) -> None:
    # A name with * ? or [ in it is a glob; --glob says so outright.
    code, out = run(capsys, "find", "MX25?12835F")
    assert code == 0
    assert out.startswith("c22018  Macronix")
    assert run(capsys, "find", "--glob", "MX25?12835F") == (0, out)
    assert run(capsys, "find", "--glob", "W25Q128")[1] != run(capsys, "find", "W25Q128*")[1]
    assert run(capsys, "find", "NOPE*")[0] == 1


def test_find_regex(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "find", "--regex", "--json", "^W25Q(64|128)J[VW]$")
    assert code == 0
    assert {"ef4017", "ef4018", "ef6018", "ef8018"} <= {d["jedec_id"] for d in json.loads(out)}
    assert cli.main(["find", "--regex", "("]) == 2
    assert "spiflash: not a regular expression: '('" in capsys.readouterr().err


def test_find_nearest(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "find", "--nearest", "-n", "3", "W25Q128JVSIQ")
    assert code == 0
    lines = out.splitlines()
    assert len(lines) == 3
    assert lines[0] == "  3  W25Q128JV  ef4018     Winbond        the query adds SIQ"
    _, js = run(capsys, "find", "--nearest", "--json", "-n", "2", "w25q128jvsiq")
    docs = json.loads(js)
    assert [(d["name"], d["score"], d["chip"]["jedec_id"]) for d in docs] == [
        ("W25Q128JV", 3, "ef4018"),
        ("W25Q128JV", 3, "ef7018"),
    ]
    _, verbose = run(capsys, "find", "--nearest", "-n", "1", "--opcodes", "W25Q128JVSIQ")
    assert "\nef4018  Winbond  " in verbose
    assert "0x9f  RDID" in verbose
    assert run(capsys, "find", "--nearest", "--", "--")[0] == 1
    assert run(capsys, "find", "--nearest", "--json", "--", "--") == (1, "[]\n")
    assert cli.main(["find", "--nearest", "-n", "0", "W25Q"]) == 2
    assert "count must be at least 1" in capsys.readouterr().err


def test_find_modes_are_exclusive(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["find", "--regex", "--nearest", "W25Q"])
    assert "not allowed with argument" in capsys.readouterr().err


def test_list(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "list", "--manufacturer", "win", "--type", "nor")
    assert code == 0
    lines = out.splitlines()
    assert len(lines) > 20
    assert all(" nor  Winbond " in line for line in lines)
    _, out = run(capsys, "list", "--type", "nand", "--json")
    assert all(d["type"] == "nand" for d in json.loads(out))


def test_jep106(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "jep106", "c2") == (0, "Macronix\n")
    assert run(capsys, "jep106", "7f1c") == (0, "Eon Silicon Devices\n")
    assert cli.main(["jep106", "7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f01"]) == 1
    printed = capsys.readouterr()
    assert printed.out == ""
    assert "spiflash: no JEP106 manufacturer 0x01 in bank 20" in printed.err
    code, out = run(capsys, "jep106", "--json", "c2")
    assert (code, json.loads(out)) == (0, {"code": "c2", "bank": 1, "manufacturer": "Macronix"})


def test_sources(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "sources")
    assert code == 0
    assert "https://github.com/torvalds/linux (GPL-2.0-only)" in out
    code, out = run(capsys, "sources", "--json")
    doc = json.loads(out)
    assert (code, doc["linux"]["license"]) == (0, "GPL-2.0-only")
    assert spiflash.database().sources["linux"].to_json() == doc["linux"]


def test_legacy_keys_are_taken_where_they_are_printed(capsys: pytest.CaptureFixture[str]) -> None:
    # id, find and the site write res1:05; every command takes it back.
    code, out = run(capsys, "id", "res1:05")
    assert (code, out.splitlines()[0]) == (0, "res1:05  Micron  M25P05  (nor)")
    code, out = run(capsys, "opcodes", "res1:05")
    assert code == 0
    assert "RES" in out
    assert cli.main(["sfdp-encode", "res1:14"]) == 0  # the EPCS16S, not the JEDEC parts
    assert capsys.readouterr().err.startswith("res1:14 EPCS16S:")
    assert cli.main(["id", "res1:05", "--method", "rems"]) == 2
    assert "res1:05 is a res1 id, not a rems one" in capsys.readouterr().err
    # list shows a legacy chip's key, and --json gives it, and no JEDEC id.
    code, out = run(capsys, "list", "--manufacturer", "micron")
    assert any(line.startswith("res1:05    nor  Micron") for line in out.splitlines())
    code, out = run(capsys, "list", "--json", "--manufacturer", "micron")
    m25p05 = next(d for d in json.loads(out) if d["key"] == "res1:05")
    assert (m25p05["jedec_id"], m25p05["id"], m25p05["id_family"]) == (None, "05", "res1")


def test_every_failure_says_why(capsys: pytest.CaptureFixture[str]) -> None:
    for args, said in (
        (["opcodes", "nosuch"], "spiflash: no chip nosuch"),
        (["find", ""], "spiflash: no part name matches ''"),
        (["find", "--nearest", ""], "spiflash: no part name is near ''"),
        (["find", "--regex", "^NOSUCH$"], "spiflash: no part name matches '^NOSUCH$'"),
        (["id", "bf", "--method", "rems"], "spiflash: no chip answers bf to REMS"),
        (["id", "14", "--method", "res2"], "spiflash: no chip answers 14 to RES2"),
        (["id", "res2:14"], "spiflash: no chip answers res2:14"),
        (["list", "--manufacturer", "nosuch"], "spiflash: no manufacturer 'nosuch'"),
        (["list", "--manufacturer", "eon", "--type", "nand"], "spiflash: no NAND chip of eon"),
    ):
        assert cli.main(args) == 1, args
        assert said in capsys.readouterr().err, args
    # A SPI NAND id read with its leading dummy byte: a hint.
    assert cli.main(["id", "00efaa21"]) == 1
    assert "a SPI NAND part sends a dummy byte before its id: try efaa21" in (
        capsys.readouterr().err
    )
    # A method that is none is argparse's to refuse, naming them all.
    with pytest.raises(SystemExit):
        cli.main(["id", "14", "--method", "bogus"])
    assert "jedec, rems, res1, res2, at25f, st95" in capsys.readouterr().err


def test_a_dump_is_chosen_by_number(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["sfdp-diff", "ef4020#x", "ef4020"]) == 2
    assert "ef4020#x: a dump is chosen by its number (#2), not #x" in capsys.readouterr().err
    assert cli.main(["sfdp-diff", "ef4020#3", "ef4020"]) == 2
    assert "ef4020 has 1 SFDP dump, not a dump #3" in capsys.readouterr().err


def test_a_closed_pipe_ends_it_quietly() -> None:
    # spiflash list | head: no BrokenPipeError traceback.
    with subprocess.Popen(
        [sys.executable, "-m", "spiflash.cli", "list"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as p:
        assert p.stdout is not None
        assert p.stderr is not None
        p.stdout.readline()
        p.stdout.close()
        err = p.stderr.read()
    assert p.returncode == 1
    assert b"Traceback" not in err, err


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.startswith("spiflash ")


@pytest.mark.parametrize(
    ("n", "text"),
    [
        (None, "?"),
        (512, "512 B"),
        (4096, "4 KiB"),
        (16 << 20, "16 MiB"),
        (1 << 30, "1 GiB"),
        (1536, "1536 B"),
    ],
)
def test_human_size(n: int | None, text: str) -> None:
    assert units.human_size(n) == text


@pytest.mark.parametrize(
    ("ns", "text"),
    [
        (20, "20 ns"),
        (704_000, "704 µs"),
        (64_000_000, "64 ms"),
        (192_000_000_000, "192 s"),
        (1_500_000, "1.5 ms"),
        (35_000, "35 µs"),
    ],
)
def test_human_duration(ns: int, text: str) -> None:
    assert units.human_duration(ns) == text


@pytest.mark.parametrize(
    ("hz", "text"), [(104_000_000, "104 MHz"), (500_000, "500 kHz"), (33_300_000, "33.3 MHz")]
)
def test_human_frequency(hz: int, text: str) -> None:
    assert units.human_frequency(hz) == text


def test_opcodes_by_id_and_by_name(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "opcodes", "ef4018")
    assert code == 0
    assert out.startswith("ef4018  Winbond  ")
    assert "    0x6b  READ_1_1_4" in out
    assert "Quad output fast read" in out
    code, by_name = run(capsys, "opcodes", "W25Q128JV")
    assert code == 0
    assert by_name.startswith(out.splitlines()[0])
    assert run(capsys, "opcodes", "NOT-A-PART-XYZ")[0] == 1


def test_opcodes_verbose_says_why(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "opcodes", "-v", "ef4018")
    assert "          linux           SPI_NOR_QUAD_READ" in out
    assert "          openocd         eraser: 256 x 65536 (implied)" in out  # from its eraser
    # Linux's fast read is a devicetree choice, for every part.
    assert "(spi_nor_init_default_params), m25p,fast-read (driver default)" in out


def test_opcodes_json(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "opcodes", "--json", "ef4018")
    (doc,) = json.loads(out)
    assert any(o["op"] == "READ_1_1_4" and o["opcode"] == 0x6B for o in doc["opcodes"])


def test_id_with_opcodes_flag(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "id", "--opcodes", "ef4018")
    assert "    opcodes:" in out
    assert "0x9f  RDID" in out


def test_opcodes_none_known() -> None:
    # Every chip the data holds has some, if only its derived read-id.
    lonely = Flash(b"\xc2\x20", FlashType.NAND, ())
    assert cli.opcode_table(lonely) == [
        "    no opcodes known (the sources describe none for this part)"
    ]


def test_spi_nand_opcodes(capsys: pytest.CaptureFixture[str]) -> None:
    # SPI NAND's own command set, with the part's dummy clocks where a
    # source gives them.
    _, out = run(capsys, "opcodes", "-v", "--", "efab21")
    assert "0x9f  NAND_RDID_DUMMY" in out
    assert "0xeb  NAND_READ_CACHE_1_4_4" in out
    assert "0xc2  NAND_DIE_SELECT" in out
    assert "READ_1_1_4 " not in out  # no SPI NOR operation
    assert "linux           read_cache_variants, 4 dummy clocks" in out


def test_sfdp_from_hex_and_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, out = run(capsys, "sfdp", MX25L25635E.hex())
    assert code == 0
    assert out.startswith("SFDP 1.0 (JESD216), 2 parameter headers")
    assert "0xeb  READ_1_4_4" in out
    dump = tmp_path / "sfdp"
    dump.write_bytes(MX25L25635E)
    assert run(capsys, "sfdp", str(dump)) == (0, out)
    _, verbose = run(capsys, "sfdp", "-v", str(dump))
    assert "DW1: 0xfff320e5" in verbose
    _, js = run(capsys, "sfdp", "--json", str(dump))
    (doc,) = json.loads(js)
    assert doc["size"] == 32 << 20
    assert doc["features"] == sorted(doc["features"])


def test_sfdp_from_stdin(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(W25Q512JV)))
    code, out = run(capsys, "sfdp", "-")
    assert code == 0
    assert "SFDP 1.6 (JESD216B)" in out


def test_sfdp_of_a_chip(capsys: pytest.CaptureFixture[str]) -> None:
    # QEMU has different dumps for two parts answering c22019: a part name
    # picks its own, the id shows both.
    code, out = run(capsys, "sfdp", "MX25L25635F")
    assert code == 0
    assert out.startswith("c22019  Macronix")
    assert "    from qemu: MX25L25635F\nSFDP 1.0 (JESD216)" in out
    assert "MX25L25635E\n" not in out
    _, both = run(capsys, "sfdp", "c22019")
    assert both.count("SFDP 1.0 (JESD216)") == 2
    assert "from qemu: MX25L25635E\n" in both
    _, js = run(capsys, "sfdp", "--json", "MX25L25635F")
    (doc,) = json.loads(js)
    assert (doc["chip"], doc["source"], doc["parts"]) == ("c22019", "qemu", ["MX25L25635F"])
    _, summary = run(capsys, "id", "c22019")
    assert "    sfdp: JESD216 (BFPT 1.0, vendor table (0xc2) 1.0)  [qemu: MX25L25635E]" in summary
    assert "[qemu: MX25L25635F]" in summary


def test_sfdp_of_a_chip_without_a_dump(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["sfdp", "w25q128jv"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "no SFDP dump for w25q128jv" in err


def test_sfdp_garbage() -> None:
    assert cli.main(["sfdp", "deadbeef"]) == 1  # a hex string that is not a dump, nor an id
    assert cli.main(["sfdp", "no such part"]) == 1


def test_sfdp_summary() -> None:
    assert cli.sfdp_summary(parse(W25Q512JV)) == "JESD216B (BFPT 1.6, 4BAIT 1.0)"


def test_sfdp_of_copied_tables(capsys: pytest.CaptureFixture[str]) -> None:
    # Zephyr's boards copy the MX25R6435F's BFPT alone, in two versions.
    code, out = run(capsys, "sfdp", "c22817")
    assert code == 0
    assert out.count("SFDP parameter table without the SFDP header: BFPT") == 2
    assert "(not read: the part has no 4-byte mode)" in out
    assert "    from zephyr: MX25R6435F at boards/ezurio/bl5340_dvk/" in out
    _, summary = run(capsys, "id", "c22817")
    # The two sets are told apart by the boards copying them.
    lines = [line for line in summary.splitlines() if line.startswith("    sfdp:")]
    assert len(lines) == 2
    assert lines[0].startswith(
        "    sfdp: BFPT of 16 dwords, without the SFDP header  [zephyr: MX25R6435F at boards/"
    )
    assert lines[0] != lines[1]


def test_sfdp_entry(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "sfdp", "--entry", W25Q512JV.hex())
    assert code == 0
    (entry,) = json.loads(out)
    assert (entry["source"], entry["size"], entry["page_size"]) == (None, 64 << 20, 256)
    ops = {o["op"]: o.get("dummy_clocks") for o in entry["opcodes"]}
    assert ops["READ_1_4_4"] == 6
    assert "BE_4K" not in ops  # its eraser gives it
    assert entry["features"] == []  # its 4-byte operations imply 4byte_opcodes


def test_sfdp_encode(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, out = run(capsys, "sfdp-encode", "ef4020")
    assert code == 0
    data = bytes.fromhex(out.strip())
    assert parse(data).size == 64 << 20
    # What it assumed and left out goes to stderr (run() keeps stdout only).
    assert cli.main(["sfdp-encode", "ef4020"]) == 0
    out, err = capsys.readouterr()
    assert "ef4020 W25Q512JV: SFDP 1.0, 68 bytes" in err
    assert "missing: DW11: the page size, 256 bytes, known but left out" in err
    assert "assumed: DW1 bits 3-4" in err
    dump = tmp_path / "sfdp.bin"
    assert cli.main(["sfdp-encode", "ef4020", "--assume", "-o", str(dump)]) == 0
    assert parse(dump.read_bytes()).revision_name == "JESD216B"
    code, js = run(capsys, "sfdp-encode", "--json", "--revision", "1.5", "--assume", "ef4020")
    doc = json.loads(js)
    assert (doc["chip"], doc["revision"], doc["missing"]) == ("ef4020", "1.5", [])
    # Its quad enable requirement is known (QEMU's dump: S2B1v4): written,
    # not assumed.
    assert not any(a.startswith("DW15: the quad enable requirement") for a in doc["assumed"])
    bfpt = parse(bytes.fromhex(doc["data"])).bfpt
    assert bfpt is not None
    assert bfpt.quad_enable == 4
    # A query naming several chips, or none, or a revision it cannot write.
    assert cli.main(["sfdp-encode", "W25Q512J"]) == 2
    assert "names 2 SPI NOR chips, not one: ef4020 (W25Q512JV), ef7020" in (capsys.readouterr().err)
    # A name two chips have, but one's own part: that one (the documented
    # example), as an exact name beats the prefix matches.
    assert cli.main(["sfdp-encode", "W25Q512JV"]) == 0
    assert capsys.readouterr().err.startswith("ef4020 W25Q512JV: SFDP 1.0")
    assert cli.main(["sfdp-encode", "S25FL512S"]) == 2  # refused: QPI, no 4-4-4 read
    assert "it has qpi, but no READ_4_4_4" in capsys.readouterr().err
    assert cli.main(["sfdp-encode", "nothing-like-it"]) == 2
    assert "no chip nothing-like-it: give a JEDEC id or a part name" in capsys.readouterr().err
    assert cli.main(["sfdp-encode", "efaa21"]) == 2  # the W25N01GV
    assert "efaa21 is SPI NAND (efaa21 (W25N01GV" in capsys.readouterr().err
    assert cli.main(["sfdp-encode", "W25Q512*"]) == 2
    assert "W25Q512* is a glob, which names no one chip here" in capsys.readouterr().err
    assert cli.main(["sfdp-encode", "--revision", "1.8", "ef4020"]) == 2
    assert "not 1.8" in capsys.readouterr().err
    assert cli.main(["sfdp-encode", "--revision", "two", "ef4020"]) == 2


def test_sfdp_diff(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    # Two dumps of one id: exit 1, as diff does when they differ.
    code, out = run(capsys, "sfdp-diff", "c22019", "c22019#2")
    assert code == 1
    assert out.startswith("A: c22019#1 (qemu: MX25L25635E)\nB: c22019#2 (qemu: MX25L25635F)\n")
    assert "reads.4-4-4: none in A, 0xeb, 2 mode + 4 wait clocks in B" in out
    # The same dump, as a chip and as a file: exit 0.
    dump = tmp_path / "sfdp"
    dump.write_bytes(MX25L25635E)
    code, out = run(capsys, "sfdp-diff", "MX25L25635E", str(dump))
    assert (code, out.splitlines()[-1]) == (0, "no differences")
    # A dump against what the database says of its chip.
    code, out = run(capsys, "sfdp-diff", "--json", "ef4020", "encoded:ef4020")
    assert code == 1
    doc = json.loads(out)
    assert doc["b"] == "encoded ef4020"
    split = [f for f in doc["fields"] if f["path"] == "reads.1-4-4"]
    assert split[0]["expected"] == "the same 6 dummy clocks, split differently"
    assert cli.main(["sfdp-diff", "c22019#3", "c22019"]) == 2
    assert "c22019 has 2 SFDP dumps, not a dump #3" in capsys.readouterr().err


def test_id_says_where_parts_differ_by_ext_id(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "id", "--type", "nand", "c841")
    assert "    parts differ on size by ext id: 256 MiB (7f), 128 MiB (c8)\n" in out
    _, out = run(capsys, "id", "--type", "nand", "c8417f")
    assert "parts differ" not in out
    assert "datasheet:" not in out  # GigaDevice's is not the F50L2G41KA's


def test_id_says_where_parts_differ_on_supply(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "id", "010220")
    assert "parts differ on voltage by ext id: " in out
    assert "1.7\N{EN DASH}2 V (4d0081)" in out


def test_id_shows_phase_6_values(capsys: pytest.CaptureFixture[str]) -> None:
    # A programmer's supply where no source gives a range; the OTP area; the
    # ways into 4-byte mode.
    _, out = run(capsys, "id", "ba4013")
    assert ", supply 3.3 V" in out
    _, out = run(capsys, "id", "ef4019")
    assert "OTP 768 B" in out
    assert "    4-byte: en4b, wrear, wren_en4b (3 or 4 address bytes)\n" in out
    # -v: the legacy ids, and a supply outside the part's range.
    _, out = run(capsys, "id", "-v", "ef4013")
    assert "    legacy id: rems:ef12 (dediprog)\n" in out
    _, out = run(capsys, "id", "-v", "1c3813")
    assert "    supply 3.3 V (dediprog) is outside the part's range\n" in out


def test_id_legacy_lists_the_jedec_chips_answering_it(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "id", "--method", "rems", "ef12")
    assert out.startswith("ef4013  Winbond")  # no rems:ef12 chip of its own
    assert "    also answers rems:ef12 (dediprog)\n" in out
    # Dediprog's ZD25Q40 0xef12 is Winbond's id, left out.
    assert "ba4013" not in out
    _, out = run(capsys, "id", "--json", "--method", "rems", "ef12")
    assert '"answers_legacy": "rems:ef12"' in out


def test_id_without_a_method_hints_at_a_legacy_one(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["id", "bf48"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "try --method rems" in captured.err
    assert cli.main(["id", "123456"]) == 1
    assert "try" not in capsys.readouterr().err
