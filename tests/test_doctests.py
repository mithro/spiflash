"""The examples in the package's docstrings give what they show."""

from __future__ import annotations

import doctest
import importlib
import pkgutil

import pytest

import spiflash

MODULES = sorted(
    {"spiflash"} | {f"spiflash.{m.name}" for m in pkgutil.iter_modules(spiflash.__path__)}
)


@pytest.mark.parametrize("name", MODULES)
def test_docstring_examples(name: str) -> None:
    result = doctest.testmod(importlib.import_module(name), optionflags=doctest.ELLIPSIS)
    assert result.failed == 0, name


def test_the_package_has_examples() -> None:
    assert doctest.testmod(spiflash).attempted >= 3
