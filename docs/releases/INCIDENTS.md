# Release-integrity incidents

A durable, dated record of release-identity incidents and the decisions taken —
so the rationale outlives Actions-log retention and chat history. Paired with
[`RELEASE-LEDGER.md`](../../RELEASE-LEDGER.md) (per-tag state) and
[`RUNBOOK.md`](RUNBOOK.md) (the reserved procedure for correcting a published tag).

---

## 2026-08-28 — orphaned release tags + version/tag mismatch

### What happened
1. **Identity history rewrite.** `main`'s history was rewritten to canonical `flxk1`
   authorship (Claude removed as author/co-author across the repo), per the
   non-negotiable AI-attribution policy. `main` is clean post-rewrite.
2. **Orphaned release tags.** 8 published release tags — `v0.6.9.0`–`v0.6.9.6` and
   `v0.6.9.9` — still point at **pre-rewrite commits that are NOT reachable from the
   rewritten `main`**, so old-identity (Claude-authored) commits stayed publicly
   fetchable through those tags. (`v0.6.9.7`/`v0.6.9.8` never existed as tags.)
3. **Version/tag mismatch.** `v0.6.9.10` was tagged on `main` *before* `_version.py`
   was bumped, so the tag's committed `_version.py` self-reports `0.6.9.9`.

### Why the tags were not corrected in place
Force-pushing the corrected (clean) tag refs was **declined server-side**:
`! [remote rejected] v0.6.9.0 -> v0.6.9.0 (pre-receive hook declined)` — no App
name surfaced via the API. Diagnostic evidence:
- The only ruleset (`Loomground Rules`, id 20237690) targets **branches**, not tags.
- Classic tag protection absent (`repos/flxk1/RVND/tags/protection` → 404).
- `v0.6.8.5`–`v0.6.8.9` pushed fine while `v0.6.9.0`–`v0.6.9.9` were declined — the
  common factor is that the declined tags each carry a **published GitHub Release**.
  Most likely a release-association pre-receive guard (a GitHub App the repo owner
  can see under Settings → Integrations that the session could not enumerate).

### Decision — ACCEPT + DOCUMENT (governance panel, domain: governance)
- **Published tags are treated as IMMUTABLE.** Do not re-point or force-move them —
  a reserved act, and one that is blocked server-side anyway.
- **Forward-fix:** cut `v0.6.9.11` with a corrected `_version.py`; it supersedes the
  mismatched `v0.6.9.10`.
- **Document:** advisory annotations were added to the affected Releases
  (`v0.6.9.0`–`v0.6.9.6`, `v0.6.9.9`, `v0.6.9.10`) on 2026-08-28; this record; the ledger.
- **Re-pointing the orphaned tags remains an OPEN reserved option**, requiring
  repo-admin resolution of the pre-receive guard **plus** a downstream-consumer
  notice (`git+https` pins) — see `RUNBOOK.md`. Not performed.

### Prevention (shipped in this change)
- `release-tag-integrity.yml` — on tag push, refuses to attach a Release unless
  `_version.py == tag` **and** the tagged commit is authorship-clean; fails loudly.
- `pr-authorship-check.yml` — catches Claude authorship **pre-merge** (a cheap amend,
  not a future history rewrite — the very event that caused this).
- `tag-audit.yml` — weekly **read-only** reachability + authorship audit → one pinned
  issue on state transition. Never mutates tags/releases.
- `scripts/apply-tag-ruleset.sh` — (admin) a `v*` tag ruleset blocking force-move/delete.

### Evidence — clean-rewritten `tag → commit SHA` (held locally by the rewrite; NOT pushed)
```
v0.6.9.0 -> 415dc2f0668cd03fe506a32e1e38f960bf9da4c0
v0.6.9.1 -> f264be5e99c2eaa09599d7ba069ad996a913180d
v0.6.9.2 -> a3cc3826ead2b19bbdb2a32f3a3b1c1e8282353f
v0.6.9.3 -> b74da6aa1e3da26a995959ee0f20b03b12b9376b
v0.6.9.4 -> 2729d17357944e822a32d98035166881f1ed1812
v0.6.9.5 -> 6b303168dd32595a9881096c887a1981249bdbbe
v0.6.9.6 -> ce151fffacd09d72f957ee44e0e194344f304b0a
v0.6.9.9 -> f3e7c92d7afbb450c7d7224bb3c0b4d09e32d095
```

### Residual / follow-ups
- Making `pr-authorship-check` a **required** check and **applying the tag ruleset**
  need repo-admin action.
- Reconciling `tag-audit.yml` findings into the ledger is a manual (runbook) step.
- The orphaned-tag re-point stays open pending the pre-receive-guard resolution above.
