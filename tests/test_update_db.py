"""tools/update_db.py and tools/spiflash_extract/fetch.py, without the network."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

import update_db
from spiflash import db
from spiflash.opcodes import OpcodeUse
from spiflash_extract import fetch, record

if TYPE_CHECKING:
    from pathlib import Path


def test_json_lines() -> None:
    text = update_db.json_lines({"format": 1}, "records", [{"b": 1, "a": 2}, {"c": "é"}])
    assert text == '{"format": 1, "records": [\n{"a": 2, "b": 1},\n{"c": "é"}\n]}\n'
    assert json.loads(text)["records"][1] == {"c": "é"}
    assert json.loads(update_db.json_lines({}, "x", [1]))["x"] == [1]


def test_every_source_has_an_extractor() -> None:
    ups = fetch.load(update_db.SOURCES)
    assert [u.name for u in ups] == list(update_db.EXTRACTORS)
    for u in ups:
        assert len(u.commit) == 40
        assert u.paths
        assert u.license


def test_set_commit(tmp_path: Path) -> None:
    p = tmp_path / "sources.toml"
    p.write_text('# keep me\n[a]\ncommit = "1"\n\n[b]\n# and me\ncommit = "2"\n')
    fetch.set_commit(p, "b", "3")
    assert p.read_text() == '# keep me\n[a]\ncommit = "1"\n\n[b]\n# and me\ncommit = "3"\n'
    with pytest.raises(KeyError, match=r"\[c\]"):
        fetch.set_commit(p, "c", "4")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> fetch.Upstream:
    """A local git repository standing in for an upstream."""
    repo = tmp_path / "remote"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "uploadpack.allowFilter", "true")
    _git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    (repo / "want").mkdir()
    (repo / "want" / "a.c").write_text("wanted\n")
    (repo / "other.c").write_text("not wanted\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "one")
    commit = _git(repo, "rev-parse", "HEAD")
    return fetch.Upstream("up", f"file://{repo}", "main", commit, ["want/"], "MIT")


def test_fetch_is_sparse_and_idempotent(tmp_path: Path, upstream: fetch.Upstream) -> None:
    tree = fetch.fetch(upstream, tmp_path / "work")
    assert (tree / "want" / "a.c").read_text() == "wanted\n"
    assert not (tree / "other.c").exists()
    assert fetch.fetch(upstream, tmp_path / "work") == tree  # already there
    assert fetch.commit_date(tree)
    assert fetch.remote_head(upstream) == upstream.commit


def test_remote_head_missing_branch(upstream: fetch.Upstream) -> None:
    upstream.branch = "nope"
    with pytest.raises(RuntimeError, match="no branch nope"):
        fetch.remote_head(upstream)


def test_data_files_share_the_format() -> None:
    assert update_db.FORMAT == db.FORMAT == 11
    for name in ("records.json", "manufacturers.json", "sources.json", "datasheets.json"):
        assert json.loads((update_db.DATA / name).read_text())["format"] == db.FORMAT, name


def test_make_refuses_to_lose_an_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    # A rule that gives SE while make() drops the implied claims and the
    # stored operation, and not after:
    # the record would lose SE, which make() refuses.
    se = (OpcodeUse("SE", "eraser", implied=True),)
    calls = iter([se, se, se])
    monkeypatch.setattr(record.derive, "opcodes", lambda _: next(calls, ()))
    with pytest.raises(AssertionError, match=r"lost \['SE'\]"):
        record.make("linux", "f", 1, "n", opcodes=[{"op": "SE", "via": "x"}])


def test_db_snapshot_refuses_a_src_without_the_package(tmp_path: Path) -> None:
    # Else it would quietly snapshot the installed package instead.
    import db_snapshot  # noqa: PLC0415 - a tool, imported where it is tested

    with pytest.raises(SystemExit):
        db_snapshot.main(["--src", str(tmp_path / "nope"), str(tmp_path / "out.json")])
    assert not (tmp_path / "out.json").exists()
