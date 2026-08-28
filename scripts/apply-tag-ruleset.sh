#!/usr/bin/env bash
# Create or update a repository ruleset that protects every `v*` tag from a
# force-move or a delete, via `gh api repos/{owner}/{repo}/rulesets`.
#
# ============================================================================
# DO NOT RUN THIS UNATTENDED.
#
#   * It needs repo-admin on the target repository — creating or editing a
#     ruleset is itself an admin-scoped write.
#   * REVIEW THE JSON THIS SCRIPT BUILDS (run with --dry-run first, read the
#     output) before it is ever POSTed or PATCHed to the API.
#   * CONFIRM THE BYPASS ACTOR before applying. The script defaults to
#     bypassing the repository admin role so the owner is never locked out
#     of cutting a release or, per docs/releases/RUNBOOK.md, correcting a
#     bad tag. That default is a guess at the correct `actor_id` for the
#     "admin" repository role and MUST be confirmed against this specific
#     repository before the ruleset goes live — see the CONFIRM THE BYPASS
#     ACTOR section below for how.
#   * A release-blocking ruleset with no working bypass is worse than no
#     ruleset: it can lock every release out, including the correction path
#     in the runbook. Treat the bypass check as the load-bearing step, not
#     a formality.
# ============================================================================
#
# What this creates: a ruleset targeting `refs/tags/v*` with:
#   - non_fast_forward   — blocks force-moving a protected tag's ref
#   - deletion           — blocks deleting a protected tag's ref
#   - bypass_actors      — the repository admin role, so releases and
#                           runbook-driven corrections still work
#
# What this does NOT create: a required-status-check tying the ruleset to
# release-tag-integrity.yml passing. As of this writing, GitHub rulesets
# only support `required_status_checks` / `required_workflows` rules on
# target=branch rulesets, not target=tag — there is no merge to gate on a
# tag push the way there is on a PR into a branch. release-tag-integrity.yml
# enforces its check the only way available to a tag-triggered workflow:
# refusing to attach a GitHub Release when the check fails, and failing the
# run loudly (see .github/workflows/release-tag-integrity.yml). If GitHub
# adds status-check support to tag rulesets, add that rule type here instead
# of leaving this note stale.
#
# Idempotent: re-running this script updates the existing ruleset (matched
# by name) rather than creating a duplicate.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/apply-tag-ruleset.sh --owner OWNER --repo REPO [--dry-run] [--apply]

  --owner OWNER   repository owner (e.g. flxk1)
  --repo REPO     repository name (e.g. RVND)
  --dry-run       build and print the ruleset JSON; make no API call (default)
  --apply         actually create or update the ruleset via `gh api`
                   (requires repo-admin; see the warning block at the top
                   of this file)

Requires: `gh` authenticated with repo-admin on OWNER/REPO.
EOF
}

RULESET_NAME="release tags: protect v*"
MODE="dry-run"
OWNER=""
REPO=""

while [ $# -gt 0 ]; do
  case "$1" in
    --owner) OWNER="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --dry-run) MODE="dry-run"; shift ;;
    --apply) MODE="apply"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unrecognized argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [ -z "$OWNER" ] || [ -z "$REPO" ]; then
  echo "error: --owner and --repo are required" >&2
  usage >&2
  exit 1
fi

# --- CONFIRM THE BYPASS ACTOR -----------------------------------------------
# The repository-role bypass actor_id for "admin" is a per-installation
# value, not a documented constant — confirm it for THIS repository before
# --apply, by either:
#   1. Creating any ruleset once by hand in the GitHub UI (Settings > Rules >
#      Rulesets), adding an admin bypass there, then reading it back with:
#        gh api repos/OWNER/REPO/rulesets --jq '.[] | .bypass_actors'
#      and copying the actor_id this script should use, or
#   2. Listing the org/repo's custom roles and their IDs:
#        gh api repos/OWNER/REPO/rulesets/rule-suites  # (context, not IDs)
#        gh api orgs/OWNER/rulesets                     # if OWNER is an org
# The value below (5) is GitHub's conventional ID for the built-in "admin"
# repository role at the time of writing. TREAT IT AS UNVERIFIED for this
# repository until confirmed by one of the steps above.
BYPASS_ACTOR_ID="${BYPASS_ACTOR_ID:-5}"
BYPASS_ACTOR_TYPE="${BYPASS_ACTOR_TYPE:-RepositoryRole}"

PAYLOAD="$(cat <<JSON
{
  "name": "${RULESET_NAME}",
  "target": "tag",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["refs/tags/v*"],
      "exclude": []
    }
  },
  "bypass_actors": [
    {
      "actor_id": ${BYPASS_ACTOR_ID},
      "actor_type": "${BYPASS_ACTOR_TYPE}",
      "bypass_mode": "always"
    }
  ],
  "rules": [
    { "type": "non_fast_forward" },
    { "type": "deletion" }
  ]
}
JSON
)"

echo "$PAYLOAD"

if [ "$MODE" = "dry-run" ]; then
  echo "--- dry-run: no API call made. Re-run with --apply once the JSON above and the bypass actor are confirmed. ---" >&2
  exit 0
fi

echo "--- applying: confirm this is intended before proceeding ---" >&2
read -r -p "Type the repository (OWNER/REPO) to confirm: " CONFIRM
if [ "$CONFIRM" != "${OWNER}/${REPO}" ]; then
  echo "confirmation did not match ${OWNER}/${REPO}; aborting" >&2
  exit 1
fi

EXISTING_ID="$(gh api "repos/${OWNER}/${REPO}/rulesets" --jq ".[] | select(.name == \"${RULESET_NAME}\") | .id" | head -n1)"

if [ -n "$EXISTING_ID" ]; then
  echo "updating existing ruleset id=${EXISTING_ID}" >&2
  echo "$PAYLOAD" | gh api "repos/${OWNER}/${REPO}/rulesets/${EXISTING_ID}" \
    --method PUT \
    --input -
else
  echo "creating new ruleset" >&2
  echo "$PAYLOAD" | gh api "repos/${OWNER}/${REPO}/rulesets" \
    --method POST \
    --input -
fi
