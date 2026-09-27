"""Sphinx configuration for https://spiflash.readthedocs.io/.

The vendor and chip pages are generated from the installed spiflash package's
data by _ext/spiflash_pages.py, so the site always matches the release it is
built from.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "_ext"))

from spiflash_pages import stats  # noqa: E402

import spiflash  # noqa: E402

project = "spiflash"
author = "Tim Ansell"
copyright = "2026, Tim Ansell"
release = spiflash.__version__
version = release

extensions = [
    "myst_parser",
    "sphinx_design",
    "sphinx_copybutton",
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "spiflash_pages",
]

source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
exclude_patterns = ["_build", "_generated", "superpowers", "Thumbs.db", ".DS_Store"]

myst_enable_extensions = ["colon_fence", "deflist", "substitution", "attrs_inline"]
myst_heading_anchors = 3
_s = stats(spiflash.database())
myst_substitutions = {
    **{k: f"{v:,}" for k, v in _s.items()},
    "version": release,
}

autodoc_member_order = "bysource"
autodoc_typehints = "description"
intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

html_theme = "furo"
html_title = "spiflash"
html_static_path = ["_static"]
html_css_files = ["spiflash.css"]
html_js_files = ["tables.js"]
html_logo = "_static/logo.svg"
html_favicon = "_static/logo.svg"
html_theme_options = {
    "source_repository": "https://github.com/mithro/spiflash/",
    "source_branch": "main",
    "source_directory": "docs/",
    "light_css_variables": {
        "color-brand-primary": "#0f766e",
        "color-brand-content": "#0f766e",
        "font-stack--monospace": "'JetBrains Mono', 'SFMono-Regular', Menlo, Consolas, monospace",
    },
    "dark_css_variables": {
        "color-brand-primary": "#2dd4bf",
        "color-brand-content": "#5eead4",
    },
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/mithro/spiflash",
            "html": "GitHub",
            "class": "",
        },
    ],
}
# Generated pages carry no "edit this page" source to edit.
html_show_sourcelink = False
