# Releasing

This project is a **rolling release**. There are no manual version bumps: the
version is derived from `git describe` by
[`hatch-vcs`](https://github.com/ofek/hatch-vcs) — `X.Y` at a `vX.Y`
tag, `X.Y.postN` N commits after it — and **every green push to `main`
publishes a new package** automatically:

- [`.github/workflows/deb.yml`](https://github.com/mithro/spiflash/blob/main/.github/workflows/deb.yml)
  ("Debian packages"), on every push and pull request:
  - its `test` job runs the gates (ruff, mypy --strict, pytest with coverage);
    a green run is what "mergeable" means;
  - it builds `python3-spiflash` for Debian bookworm, trixie, forky and sid
    with [`mithro/apt-repo-action`](https://github.com/mithro/apt-repo-action)'s
    shared `build-deb`, and install-tests each;
  - on `main` only, it republishes the signed apt repository on GitHub Pages
    (<https://mith.ro/spiflash/>).
- [`.github/workflows/publish-pypi.yml`](https://github.com/mithro/spiflash/blob/main/.github/workflows/publish-pypi.yml)
  builds and uploads the wheel + sdist to PyPI when "Debian packages"
  **succeeds** on `main` (`workflow_run`; a failed or cancelled run publishes
  nothing, and the checkout is pinned to the SHA it validated).

The .deb's version is the wheel's plus the suite:

| Suite | Version |
|---|---|
| bookworm | `X.Y.postN~deb12` |
| trixie | `X.Y.postN~deb13` |
| forky | `X.Y.postN~deb14` |
| sid | `X.Y.postN` |

A pull request's preview build adds `~pr<P>`.

Merges to `main` use merge commits (the only merge the repository allows), so
a pull request's preview version always sorts below the build of its merge.

A data update is a commit like any other: `uv run tools/update_db.py --latest`,
review, commit, merge. The next green run publishes it.

## Tags

Tags are the only human input to the version. `v0.0` sits on the root commit
(so `git describe` works from the start of history). To cut a new series, push
an annotated `vX.Y` tag on `main` (a GitHub tag ruleset only admits
`vXX.ZZZ`-shaped tags) — the next green run publishes `X.Y`, and every commit
after it `X.Y.postN`. Never move or delete a tag that has been published from.

## One-time setup

**No secret or key is ever committed to the repo.**

### 1. PyPI trusted publishing (OIDC)

1. On <https://pypi.org/manage/account/publishing/>, add a **pending
   publisher** (the first upload creates the project):
   - PyPI project name: `spiflash`
   - Owner: `mithro`
   - Repository name: `spiflash`
   - Workflow name: [`publish-pypi.yml`](https://github.com/mithro/spiflash/blob/main/.github/workflows/publish-pypi.yml)
   - Environment name: `pypi`
2. In the GitHub repo, the **Environment** named `pypi` exists (created at
   repo setup). No secrets needed — OIDC handles auth.

Until this is done, the publish workflow runs but its upload step fails
safely. After it, re-run the latest "Publish to PyPI" run (or push); `skip-
existing: true` makes re-runs idempotent.

### 2. apt repo signing key

The private half of the repository's own key is the `APT_GPG_PRIVATE_KEY`
repository secret; the publish workflow exports the public half as
`spiflash.gpg` (binary) and `spiflash.asc` (armoured) at the site root. The
key was made as
[apt-repo-action's conventions](https://github.com/mithro/apt-repo-action/blob/main/docs/conventions.md#one-signing-setup)
say, and kept only in the maintainer's keyring:

```sh
gpg --batch --passphrase '' --quick-gen-key \
  'spiflash apt repository <me@mith.ro>' rsa4096 sign never
gpg --armor --export-secret-keys 'spiflash apt repository' \
  | gh secret set APT_GPG_PRIVATE_KEY --repo mithro/spiflash
```

Fingerprint: `B528 6C61 A99A 9E6A 5B8A  8E5B 6B2D 7683 DE01 081D`.

### 3. GitHub Pages

Settings → Pages → Source: **GitHub Actions** (`gh api repos/mithro/spiflash/pages
-X POST -f build_type=workflow`), with HTTPS enforced, and the `github-pages`
environment limited to deployments from `main`.

## Verifying a release

- PyPI: <https://pypi.org/project/spiflash/> shows the new `X.Y.postN`.
- apt: `sudo apt update && apt-cache policy python3-spiflash` on a machine set
  up per <https://mith.ro/spiflash/> shows the same version.
