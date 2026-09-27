"""The spiflash command."""

from __future__ import annotations

import json

import pytest

from spiflash import cli


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = cli.main(list(args))
    return code, capsys.readouterr().out


def test_id(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "id", "ef4018")
    assert code == 0
    assert out.startswith("ef4018  Winbond  ")
    assert "size 16 MiB, page 256 B, sector 64 KiB, 2.7-3.6 V" in out
    assert "from: flashrom" in out


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
    code, out = run(capsys, "jep106", "7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f01")
    assert code == 1
    assert "no JEP106 manufacturer 0x01 in bank 20" in out


def test_sources(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "sources")
    assert code == 0
    assert "https://github.com/torvalds/linux (GPL-2.0-only)" in out


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
    assert cli.human_size(n) == text


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
    assert "          openocd         erase_cmd" in out


def test_opcodes_json(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "opcodes", "--json", "ef4018")
    (doc,) = json.loads(out)
    assert any(o["op"] == "READ_1_1_4" and o["opcode"] == 0x6B for o in doc["opcodes"])


def test_id_with_opcodes_flag(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "id", "--opcodes", "ef4018")
    assert "    opcodes:" in out
    assert "0x9f  RDID" in out


def test_opcodes_none_known(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "opcodes", "--", "c220")  # a SPI NAND: no NOR opcodes
    assert "no opcodes known" in out
