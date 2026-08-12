#!/bin/sh
# Register lint — mechanical checks for the project's writing register.
# Fails closed on the unambiguous patterns; judgment rules (moral language, header
# length) stay with review.
# Session-schema documents are excluded because they intentionally use protocol
# phase identifiers that would otherwise resemble internal planning labels.
set -u
fail=0
hit() { echo ""; echo "register-lint FAIL: $1"; fail=1; }

G="grep -rnE --exclude-dir=node_modules --exclude-dir=__pycache__ --exclude-dir=_loomground_data --exclude-dir=.git --exclude=register_lint.sh"
PATHS="server/src server/tests app docs scripts README.md CHANGELOG.md"

$G "panel 2026-06-04|Kleppmann|Dwork|Helland" $PATHS && hit "citation of an AI-panel/researcher as design authority"
$G "this chat|sibling session|the RVND session" --exclude="session-io*" $PATHS && hit "session-relative reference in a durable artifact"
$G "PLAN P[0-9]|Slice[- ][0-9]+ —|sweep-[0-9]|review [AB][0-9]" --exclude=reasoning_phases.py $PATHS && hit "plan/slice/sweep/review tag (state the rule, not the meeting)"
$G "no longer dead code|NOT YET (WIRED|MOUNTED)" $PATHS && hit "build-status language in a durable artifact"

# dangling-reference check: every cited _docs/ path must exist in the repo
refs=$($G -o "_docs/[A-Za-z0-9._/-]+" $PATHS | cut -d: -f3- | sort -u)
for r in $refs; do
  [ -e "$r" ] || { echo "$r"; hit "reference to a file that does not exist: $r"; }
done

# No build junk in the tree: node_modules must never be tracked (a symlink named
# node_modules slips past .gitignore's directory-only pattern), and the repo
# carries no tracked symlinks.
git ls-files | grep -E '(^|/)node_modules(/|$)' && hit "node_modules is tracked (build junk; a symlink evades the dir-only .gitignore)"
symlinks=$(git ls-files -s | awk '$1=="120000"{print $4}')
[ -n "$symlinks" ] && { echo "$symlinks"; hit "tracked symlink(s) in the repo (link junk)"; }

[ "$fail" -eq 0 ] && echo "register-lint: clean"
exit $fail
