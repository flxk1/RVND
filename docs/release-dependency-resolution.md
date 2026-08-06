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
| `loomground-solver` | `feat/scale-reasoning` | `2129b64c03bf91fd86792333cae1164626cae62a` |
| `loomground-versum` | `loomground-versum-v0.9.0` | `035d8dca1f043e72d4e3b730b1d29a498c7e1262` |
| `loomground-governance` | `v0.8.2` | `b69e0e17b8ab313f9ec0303523deeffdfe7ff115` |
| `loomground-deontic` | `v0.1.3` | `e346601a5d09d53cee410e22973ff1ec52246338` |
| `loomground-ingest` | `feat/governance-compiler` | `1b276389c22835563c0f1dec573ac627694cc6e9` |
| `loomground-legal` | `feat/legal-crossref-summary-defs-referral` | `1991a63e72b029c575e06d474e9654d941857141` |
| `loomground-patchbay` | `v0.1.0` | `36e70ada8d51583b7071a51edf12e6d65b1a0cc5` |

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
complete until that job succeeds. Local execution can reproduce one resolved
platform:

```sh
python -m pip install --dry-run --ignore-installed --report pip-report.json \
  ".[test,llm,onnx,build,extractors]"
make dependency-artifacts PIP_REPORT=pip-report.json PLATFORM=local-py312
```
