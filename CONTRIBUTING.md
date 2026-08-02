<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Contributing

## Licensing of contributions

Rvnd is offered under AGPL-3.0-only or separate commercial terms. Before a
contribution can be accepted, the contributor must agree to [CLA.md](CLA.md).
The grant lets the project maintain the dual-licensing model. Pull requests
must retain the template's CLA declaration, and cannot merge until a maintainer
records the `cla:accepted` label. DCO sign-off does not replace CLA assent.

Each commit must be signed off (`git commit -s`), certifying the
[Developer Certificate of Origin](https://developercertificate.org/) — that
you wrote the contribution or otherwise have the right to submit it under the
project licence.

- **Bug reports, findings and design discussion** are welcome as issues — no paperwork.

House rules for anything that lands in the tree:

- Follow the repository's concise writing register in code comments, docstrings,
  tests, documentation, and commit messages; `scripts/register_lint.sh`
  enforces the mechanical part.
- Commit subjects are imperative and at most 72 bytes. Commits never carry an AI
  co-author trailer; AI assistance is acknowledged in `NOTICE.md`, not as authorship.
- The tree stays green: `python3 scripts/verify_completeness.py` (UI render gates) and the server
  suite must pass, and `python3 scripts/verify_surface.py` must report no new gaps.
  `make gates` runs every fast gate; `make help` lists the rest.
