"""The command-line examples in the README and on the site's front page
print what the command prints: they are generated data, and go stale when
it changes."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from spiflash import cli

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parent.parent
DOCS = ("README.md", "docs/index.md")
_BLOCK = re.compile(r"```console\n(.*?)```", re.DOTALL)


def examples(text: str) -> Iterator[tuple[list[str], list[str]]]:
    """Each ``$ spiflash ...`` line of the console blocks in ``text``, as
    its arguments, and the lines shown after it."""
    for block in _BLOCK.findall(text):
        command: list[str] | None = None
        shown: list[str] = []
        for line in block.splitlines():
            if line.startswith("$ "):
                if command:
                    yield command, shown
                command, shown = shlex.split(line[2:], comments=True), []
            else:
                shown.append(line)
        if command:
            yield command, shown


CASES = [
    pytest.param(command[1:], shown, id=f"{doc}: {' '.join(command)}")
    for doc in DOCS
    for command, shown in examples((ROOT / doc).read_text())
    if command[0] == "spiflash" and shown
]


def test_the_docs_have_examples() -> None:
    assert len(CASES) >= 4


@pytest.mark.parametrize(("args", "shown"), CASES)
def test_example_output(
    capsys: pytest.CaptureFixture[str], args: list[str], shown: list[str]
) -> None:
    assert cli.main(args) == 0
    printed = [line.rstrip() for line in capsys.readouterr().out.splitlines()]
    # A last line of "..." stands for the rest.
    if shown[-1].strip() == "...":
        shown = shown[:-1]
        printed = printed[: len(shown)]
    assert printed == shown


def test_examples_split_at_each_command() -> None:
    text = "```console\n$ spiflash a 'b c'  # note\nout\n$ spiflash d\n```\n"
    assert list(examples(text)) == [(["spiflash", "a", "b c"], ["out"]), (["spiflash", "d"], [])]
