"""Fetch just the needed files of one upstream commit.

A blobless, depth-1, sparse fetch of a single commit: for Linux that is a few
hundred kilobytes instead of a multi-gigabyte clone.
"""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class Upstream:
    name: str
    url: str
    branch: str
    commit: str
    paths: list[str]
    license: str
    browse: str | None = None  # where to link to its files, if not `url`


def load(path: Path) -> list[Upstream]:
    data = tomllib.loads(path.read_text())
    return [Upstream(name=k, **v) for k, v in data.items()]


def set_commit(path: Path, name: str, commit: str) -> None:
    """Rewrite one ``commit = "..."`` line in sources.toml, keeping the
    comments and layout of the rest of the file."""
    lines = path.read_text().splitlines(keepends=True)
    section = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
        elif section == name and stripped.startswith("commit"):
            lines[i] = f'commit = "{commit}"\n'
            path.write_text("".join(lines))
            return
    raise KeyError(f"no commit line for [{name}] in {path}")


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def remote_head(up: Upstream) -> str:
    """The commit at the head of the upstream's branch, right now."""
    out = _git("ls-remote", up.url, f"refs/heads/{up.branch}")
    if not out:
        raise RuntimeError(f"{up.url} has no branch {up.branch}")
    return out.split()[0]


def fetch(up: Upstream, dest: Path) -> Path:
    """Check out ``up.paths`` of ``up.commit`` into ``dest / up.name``."""
    tree = dest / up.name
    sparse = ["sparse-checkout", "set", "--no-cone", *[f"/{p}" for p in up.paths]]
    if (tree / ".git").exists():
        if _git("rev-parse", "HEAD", cwd=tree) == up.commit:
            # Same commit, but `paths` may have grown since it was fetched.
            _git(*sparse, cwd=tree)
            return tree
    else:
        tree.mkdir(parents=True, exist_ok=True)
        _git("init", "-q", cwd=tree)
        _git("remote", "add", "origin", up.url, cwd=tree)
    _git(*sparse, cwd=tree)
    _git("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", up.commit, cwd=tree)
    _git("checkout", "-q", "--force", up.commit, cwd=tree)
    return tree


def commit_date(tree: Path) -> str:
    return _git("log", "-1", "--format=%cI", cwd=tree)
