<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Release dependency resolution

As of 2026-08-02, RVND installs the five Loomground plane packages from exact
Git commits. RVND also consumes a pinned, hash-verified subset of Patchbay's
presentation contract. Each commit is the target of the release tag named in
`pyproject.toml`; using the commit prevents a moved tag from changing an RVND
build.

The release package order remains authoritative. Replace a VCS requirement
with a normal package-index requirement only after that package and version
have been published and an isolated installation from the index succeeds.
Until then, the immutable VCS requirement is the release path. A tag, branch,
local sibling checkout, or unpublished version is not an acceptable
substitute.

Current tag-to-commit mapping:

| Package | Release tag | Pinned commit |
|---|---|---|
| `loomground-solver` | `solver-v0.2.1` | `f8ac006de541215dc82a4cbd5bbd1497a4e658d1` |
| `loomground-versum` | `loomground-versum-v0.13.0` | `1147d7fecd7b991ed87809fae263839ed92372ee` |
| `loomground-governance` | `loomground-governance-v0.8.2` | `b69e0e17b8ab313f9ec0303523deeffdfe7ff115` |
| `loomground-deontic` | `main` | `e346601a5d09d53cee410e22973ff1ec52246338` |
| `loomground-ingest` | `ingest-v0.2.0` | `dd277ef5c967b86f05ee0fa45c29634836affad0` |
| `loomground-legal` | `legal-v0.2.1` | `3638910292886b7812cac0c3a6b5d1e954522fc3` |
| `loomground-norm` | `norm-v0.1.0` | `72f3962e0495027b083c66962b4de78198bea7a4` |
| `loomground-factual` | `factual-v0.1.0` | `db60a0592eb7741732944f05279b27def0c9685b` |
| `loomground-epistemic` | `epistemic-v0.1.0` | `2c1dc8ea8278fe3d1aeffa470319573c53dee932` |
| `loomground-patchbay` | `v0.1.0` | `36e70ada8d51583b7071a51edf12e6d65b1a0cc5` |

The Release-tag column is of two kinds. A **version tag** (`legal-v0.2.1`,
`norm-v0.1.0`, `loomground-governance-v0.8.2`, …) names an immutable release
tag that must resolve to the pinned commit. A **branch** (`feat/…`, `main`) is
a branch-pin: the plane has no release tag at the pinned commit yet, so the
immutable commit itself is the whole contract and the branch names only where
that work lives. deontic is pinned to `main` at a commit that is ahead of the last
`loomground-deontic-v0.1.3` tag (an unreleased feature sits on top), so it is a
branch-pin until release-please cuts the next version. `scripts/verify_pin_tags.py`
(a step in the `resolve-pins` job) enforces the distinction: for every row whose
Release-tag column names a version tag, it asserts `git ls-remote` resolves that
tag, on that plane's repo, to exactly the pinned commit — so a mislabelled or
moved tag fails ci instead of silently misleading. Branch-pins are exempt by
construction: a branch is not a release claim.

Patchbay is consumed by vendoring the shared presentation contract rather than
through a runtime import. `release/patchbay-consumption.json` binds the exact
upstream root and aggregate content hash; `make gates` refuses any unreviewed
drift. RVND-specific units and backend operations remain downstream code.

## Platform dependency evidence

Every release candidate resolves the base dependencies and all optional extras
(`test`, `llm`, `onnx`, `build`, and `extractors`) independently on Linux
x86-64, macOS arm64, and Windows x86-64 with Python 3.12. Each matrix leg
produces three artifacts from pip's selected distributions:

- a complete platform lock containing exact versions and SHA-256 artifact
  hashes, plus exact 40-character commits for Loomground VCS packages;
- a CycloneDX 1.6 SBOM containing the same hashes;
- third-party notices containing each selected package's licence metadata.

`scripts/release_dependency_artifacts.py` denies the build when a third-party
package lacks a version, recognized licence, or immutable identity. Package
index artifacts require SHA-256; Loomground VCS packages require an exact
40-character requested commit that matches the resolved commit. Unknown and
copyleft licences are denied unless the script contains a narrow documented
exception. The PyInstaller build tools are the only exception; their GPL terms
carry the upstream bundling exception and they are not runtime dependencies.

The `release-dependencies` workflow runs this matrix on pull requests and main.
For a `v*` tag, its attachment job creates the GitHub release when necessary
and attaches all nine platform artifacts. A tag is not dependency-release
complete until that job succeeds.

That matrix is thorough but slow (three platforms, py312 only, a 45-minute
budget), so it is not where a broken pin should first show. The `resolve-pins`
job in `ci` is the fast preflight: it runs pip's real resolver over the base
and `test` closures with `--dry-run` on the declared 3.10 floor and fails in
seconds when the plane pins do not resolve. It exists because an editable or
`--no-deps` install hides an unsatisfiable graph — the failure mode that once
left RVND un-installable-from-pins (a phantom `loomground-solver` floor plus an
unpinned `loomground-norm`) behind a green `ci`. The floor interpreter is
deliberate: `release-dependencies` only exercises py312, so a plane that
quietly requires `>=3.11` is caught only here. Native/wheel-only extras
(`llm`, `onnx`, `build`, `extractors`) stay in `release-dependencies`, where
py312 wheels exist and a missing upstream wheel is not mistaken for a pin
conflict.

Local execution can reproduce one resolved platform:

```sh
python -m pip install --dry-run --ignore-installed --report pip-report.json \
  ".[test,llm,onnx,build,extractors]"
make dependency-artifacts PIP_REPORT=pip-report.json PLATFORM=local-py312
```
