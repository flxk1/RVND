# Release ledger

The durable record of what every `v*` tag actually resolves to. GitHub
Actions log retention expires; this file does not. Where a workflow run's
findings and this ledger disagree, treat the disagreement itself as a
finding — the tag is the truth and both records exist to describe it.

This file is append-only: existing rows are never edited or deleted, only
superseded by a new row for the corrected tag. A new release adds its row
here as part of the release-prep pull request, before the tag is pushed —
`release-tag-integrity.yml` then verifies the pushed tag against
`server/src/rvnd/_version.py` and authorship, and its own output (the
GitHub Release evidence block) is the independent, automated cross-check
against what this table claims by hand.

`tag-audit.yml` runs the same reachability and authorship checks on a
schedule and reports drift via a single pinned issue; it does not write to
this file. Reconciling a reported drift into a new ledger row is a human
step.

## Table

| tag | resolved `_version.py` | commit | reachable from main | authorship-clean | date | notes |
|---|---|---|---|---|---|---|
| v0.6.9.0 | 0.6.9.0 | `b0e75cbbd5e68a1bee8ad3bdf5e6a8bc889d753d` | no | not evaluated | 2026-08-11 | **ORPHANED (pre-rewrite history)** — not an ancestor of current `main`; the tag name is retained by GitHub but its history predates a rewrite. |
| v0.6.9.1 | 0.6.9.1 | `ba9f9284fbe4f6a0d776fb83ee043727748ecf0f` | no | not evaluated | 2026-08-11 | **ORPHANED (pre-rewrite history)** |
| v0.6.9.2 | 0.6.9.2 | `48168d8a90e213b7ec475b087a094c51d07dae14` | no | not evaluated | 2026-08-11 | **ORPHANED (pre-rewrite history)** |
| v0.6.9.3 | 0.6.9.3 | `e3e32e15c4d7cf4574e35f30f933d2f7d7c805bd` | no | not evaluated | 2026-08-11 | **ORPHANED (pre-rewrite history)** |
| v0.6.9.4 | 0.6.9.4 | `76cf7b301e87cf9c6c1372f63aa1fbbcf178678c` | no | not evaluated | 2026-08-12 | **ORPHANED (pre-rewrite history)** |
| v0.6.9.5 | 0.6.9.5 | `aa99d572e35f9bdb82d6b4e25e3c74383f2a3f7f` | no | not evaluated | 2026-08-12 | **ORPHANED (pre-rewrite history)** |
| v0.6.9.6 | 0.6.9.6 | `31494915e6f8cc8b12bee85b46e58ebce77f0b3b` | no | not evaluated | 2026-08-12 | **ORPHANED (pre-rewrite history)** |
| v0.6.9.7 | — | — | — | — | — | no tag by this name exists on the remote; the sequence skips from v0.6.9.6 to v0.6.9.9 |
| v0.6.9.8 | — | — | — | — | — | no tag by this name exists on the remote; see v0.6.9.7 |
| v0.6.9.9 | 0.6.9.9 | `bc83e0dd55058a1513ab6834cc16a90ddd4a8614` | no | not evaluated | 2026-08-14 | **ORPHANED (pre-rewrite history)** |
| v0.6.9.10 | 0.6.9.9 | `18209acc53a490b6d7cac681f82edb0531c53134` | yes | not evaluated | 2026-08-28 | **VERSION MISMATCH** — tag `v0.6.9.10` self-reports `0.6.9.9` in `server/src/rvnd/_version.py`; superseded by v0.6.9.11. |

"not evaluated" in the authorship-clean column means this ledger was seeded
by reading tag/commit/version data directly, not by running the authorship
rule against each historical commit; `tag-audit.yml` will backfill a real
verdict for every row above the next time it runs and any operator should
treat that run's issue (if any) as the authoritative authorship finding for
these rows, not this table.

## Reading the two failure modes above

- **ORPHANED**: the tag exists on GitHub but its commit is not an ancestor
  of the current `main` branch. This is what a history rewrite (force-push
  to `main`, or a repository-level history edit) produces: the tag ref
  still resolves, but `git merge-base --is-ancestor <tag> main` fails.
  `release-tag-integrity.yml` cannot catch this after the fact for tags
  pushed before it existed — it only gates tags pushed from here forward.
  `tag-audit.yml` is what surfaces it for older tags, on a schedule.
- **VERSION MISMATCH**: the tag name and the `__version__` string baked
  into the tagged commit's `server/src/rvnd/_version.py` disagree. v0.6.9.10
  is the case in point: the tag says `.10`, the file says `.9`. The
  corrected release is v0.6.9.11 — cut against a commit that carries the
  matching `_version.py` and, from that point on, verified automatically
  before a Release is attached.
