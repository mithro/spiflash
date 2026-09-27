"""URLs on the site are links: the page generator's escaping, and the checker
that docs.yml runs over the built HTML."""

from __future__ import annotations

import pytest

from check_links import bare_urls
from spiflash_pages import esc, trim_url


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
