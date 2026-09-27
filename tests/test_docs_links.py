"""URLs on the site are links: the page generator's escaping, and the checker
that docs.yml runs over the built HTML."""

from __future__ import annotations

import pytest

import spiflash
from check_links import bare_urls
from spiflash_pages import esc, repo_url, trim_url, upstream_url


@pytest.mark.parametrize(
    ("url", "trimmed"),
    [
        ("https://x.org/a.pdf", "https://x.org/a.pdf"),
        ("https://x.org/a.pdf.", "https://x.org/a.pdf"),
        ("https://x.org/a.pdf),", "https://x.org/a.pdf"),
        (
            "https://www.issi.com/WW/pdf/IS25LP(WP)256D.pdf",
            "https://www.issi.com/WW/pdf/IS25LP(WP)256D.pdf",
        ),
        ("https://x.org/a_(b)).", "https://x.org/a_(b)"),
    ],
)
def test_trim_url(url: str, trimmed: str) -> None:
    assert trim_url(url) == trimmed


def test_esc_links_urls_and_escapes_the_rest() -> None:
    text = "see https://x.org/IS25LP(WP)256D_x.pdf (and *this*)."
    assert esc(text) == r"see <https://x.org/IS25LP(WP)256D_x.pdf> (and \*this\*)."
    assert esc("no url_here") == r"no url\_here"


def test_bare_urls() -> None:
    html = (
        '<p>A <a href="https://a.org">https://a.org</a> link, '
        "a bare https://b.org/x. URL, <code>curl https://c.org</code>,<br>"
        "<pre>https://d.org</pre> and https://e.org/(f).</p>"
    )
    assert bare_urls(html) == ["https://b.org/x", "https://e.org/(f)"]


def test_file_names_paths_and_repos_must_be_links() -> None:
    html = (
        "<p>See <code>tools/sources.toml</code>, <code>pyproject.toml</code>, "
        "<code>src/spiflash/data/</code>, <code>mithro/apt-repo-action</code>, "
        '<a href="x"><code>docs/api.md</code></a>, and drivers/mtd/spi-nor/core.c.</p>'
    )
    assert bare_urls(html) == [
        "tools/sources.toml",
        "pyproject.toml",
        "src/spiflash/data/",
        "mithro/apt-repo-action",
        "drivers/mtd/spi-nor/core.c",
    ]


@pytest.mark.parametrize(
    "literal",
    ["w25q128fv/jv", "spiflash.lookup", "SNOR_ID(...)", "0x20/0x21", "READ_1_1_4", "a/b"],
)
def test_names_that_are_not_paths(literal: str) -> None:
    assert bare_urls(f"<p><code>{literal}</code></p>") == []


def test_code_blocks_are_exempt() -> None:
    assert bare_urls("<pre>uv run tools/update_db.py https://x.org</pre>") == []


def test_link_roles() -> None:
    assert repo_url("tools/sources.toml") == (
        "https://github.com/mithro/spiflash/blob/main/tools/sources.toml"
    )
    assert (
        repo_url("src/spiflash/data/")
        == "https://github.com/mithro/spiflash/tree/main/src/spiflash/data"
    )
    commit = spiflash.sources()["linux"].commit
    assert upstream_url("linux", "drivers/mtd/spi-nor/*.c") == (
        f"https://github.com/torvalds/linux/tree/{commit}/drivers/mtd/spi-nor"
    )
    assert upstream_url("flashprog", "flashchips.c").startswith(
        "https://github.com/SourceArcade/flashprog/blob/"
    )
