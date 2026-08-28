# Runbook: correcting a published release tag

Moving or deleting a `v*` tag that already carries a GitHub Release is a
reserved act. It is never done silently, and it is never done by a
scheduled job — `tag-audit.yml` reports drift; it does not fix it. This
runbook is for the human sequence that follows a `tag-audit.yml` finding, a
`release-tag-integrity.yml` failure, or any other discovery that a
published tag names the wrong commit or the wrong version.

## Why this is reserved, not automated

A pushed tag is a promise the rest of the world can build on. Some
downstream consumers pin RVND with a `git+https` URL against a specific tag
(`pip install "rvnd @ git+https://github.com/flxk1/RVND@v0.6.9.10"` and
equivalents in a lockfile). Moving that tag's ref out from under them
silently changes what their next clean install resolves to, with no error
and no signal on their side. Force-moving or deleting a tag is also blocked
at the repository level by a `pre-receive hook declined` on any ref that
carries a release — the platform itself treats this as a reserved act, not
just a convention here.

## Sequence

1. **Notify downstream `git+https` consumers first.** Before anything else
   changes. Identify who pins RVND by tag (lockfiles, `pyproject.toml`
   dependency specs, deployment manifests reported to the team) and tell
   them what is wrong with the tag, what the corrected commit will be, and
   when. Silence here is the failure mode this step exists to prevent.

2. **Get repo-admin approval.** The person requesting the fix and the
   person approving it are not the same person. State the finding (cite the
   `tag-audit.yml` issue or the `release-tag-integrity.yml` run), the
   corrected commit, and the planned action before proceeding.

3. **Prefer delete-and-recreate the GitHub Release over force-moving the
   tag ref.** A tag that already carries a Release is protected — a
   force-push that tries to move it hits `pre-receive hook declined`. The
   available, non-destructive path is:
   - Cut a new, correctly-versioned tag on the corrected commit (for
     `v0.6.9.10`, that is `v0.6.9.11` — see `RELEASE-LEDGER.md`).
   - Delete the old Release object (not the tag) if its content actively
     misleads readers, or edit it to point readers at the corrected tag.
   - Leave the old tag ref in place, named for what it is, in the ledger.
   Only force-move or delete the tag ref itself if repo-admin approval
   explicitly covers that stronger action — most corrections do not need
   it.

4. **Record the decision.** Add a new row to `RELEASE-LEDGER.md` for the
   corrected tag (the ledger is append-only — the old row stays, marked
   with its finding; a new row documents the fix). Write an incident note
   covering: what was wrong, who approved the fix, what changed, and who
   was notified and when. Keep the incident note wherever this repository's
   other release records live.

## What a scheduled job is and is not allowed to do

`tag-audit.yml` reads tags and reconciles a single pinned issue. It does
not, and must not be changed to, force-push, delete, or recreate a tag or
Release. `release-tag-integrity.yml` only ever creates or edits a Release
for the tag that just triggered it — it never touches an existing tag ref.
If either workflow's authority needs to grow, that is a change to the
workflow file, reviewed like any other code change, not an unattended
capability upgrade.
