"""A fixture that builds a small Sphinx site, for the docs extensions' tests."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from sphinx.application import Sphinx
from sphinx.util.docutils import docutils_namespace, patch_docutils

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture
def build_site(tmp_path: Path) -> Callable[..., tuple[Path, Path, str]]:
    """Builds a site from ``{path: text}`` (``conf.py`` among them) with a
    builder (``html`` by default); gives its output directory, its doctree
    directory and the warnings."""

    def build(files: dict[str, str], builder: str = "html") -> tuple[Path, Path, str]:
        src = tmp_path / "src"
        for name, text in files.items():
            (src / name).parent.mkdir(parents=True, exist_ok=True)
            (src / name).write_text(text, encoding="utf-8")
        out, doctrees = tmp_path / builder, tmp_path / f"doctrees-{builder}"
        warnings = io.StringIO()
        with patch_docutils(src), docutils_namespace():
            app = Sphinx(src, src, out, doctrees, builder, status=None, warning=warnings)
            app.build()
        return out, doctrees, warnings.getvalue()

    return build
