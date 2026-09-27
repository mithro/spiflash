"""Check that the site's external links lead somewhere.

    uv run docs/check_link_targets.py [docs/_build/html] [--sample N]

Collects every external ``href`` from the built pages (from the chip and
vendor pages, only the first ``N`` of each kind of host, since they hold a
link per upstream entry), asks each once for its status, and reports those
that fail. Unlike check_links.py this needs the network, so CI does not run
it on every build.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

HREF = re.compile(r'href="(https?://[^"#]+)[^"]*"')
USER_AGENT = "Mozilla/5.0 (spiflash docs link check)"


def status(url: str) -> int:
    """The HTTP status ``url`` answers with (0 if it could not be reached)."""
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return int(response.status)
        except urllib.error.HTTPError as e:
            if method == "GET" or e.code not in (403, 405):
                return e.code
        except (urllib.error.URLError, TimeoutError):
            return 0
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("root", nargs="?", default="docs/_build/html")
    ap.add_argument("--sample", type=int, default=5, help="links per host from generated pages")
    args = ap.parse_args()
    root = Path(args.root)
    urls: set[str] = set()
    per_host: dict[str, int] = defaultdict(int)
    for page in sorted(root.rglob("*.html")):
        generated = page.parent.name in ("chips", "vendors")
        for url in HREF.findall(page.read_text(encoding="utf-8")):
            host = url.split("/")[2]
            if generated and url not in urls:
                if per_host[host] >= args.sample:
                    continue
                per_host[host] += 1
            urls.add(url)
    bad = [(code, url) for url in sorted(urls) if not 200 <= (code := status(url)) < 400]
    for code, url in bad:
        print(f"{code or 'unreachable'}  {url}")
    print(f"{len(urls)} links checked, {len(bad)} failed", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
