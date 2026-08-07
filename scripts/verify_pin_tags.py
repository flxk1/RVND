#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tag-to-commit traceability gate for the Loomground plane pins.

`docs/release-dependency-resolution.md` maps each plane to a Release-tag label
and a pinned commit. The label is one of two kinds:

  * a version tag (``legal-v0.2.1``, ``loomground-governance-v0.8.2``, …) — an
    immutable release tag that MUST resolve, on that plane's repo, to exactly
    the pinned commit; or
  * a branch (``feat/…``, ``main``) — a branch-pin, where the immutable commit
    is the whole contract and no release tag names it yet.

The SHA pins install fine either way (that is what `resolve-pins` proves), but a
version-tag label that does not actually resolve to its pin is a silent lie in
the release record: it invites a reader to `git checkout` a tag that points
somewhere else, or at nothing. This gate closes that hole. For every version-tag
row it runs `git ls-remote` against the plane's own GitHub repo and asserts the
tag dereferences to the pinned commit. Branch rows are exempt by construction —
a branch is not a release claim, so there is nothing to verify against.

Cross-checked inputs (both read as files, no venv):
  * pyproject.toml  — the authoritative {package: 40-hex commit} git pins.
  * the doc table   — {package: (release-tag label, documented commit)}.
The doc commit must equal the pyproject commit (a cheap re-assertion of the
existing `test_upstream_consumption` contract); the tag is then verified against
that commit. Runs offline-parse first, network last, so a malformed table fails
fast without touching the network.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
DOC = ROOT / "docs" / "release-dependency-resolution.md"

# git+https://github.com/flxk1/<name>@<40-hex>
_PIN_RE = re.compile(
    r"git\+https://github\.com/(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)@(?P<sha>[0-9a-fA-F]{40})"
)
# A Release-tag label counts as a version tag when it ends in vMAJOR.MINOR.PATCH
# (optionally prefixed, e.g. `legal-v0.2.1` or `loomground-governance-v0.8.2`)
# and names no branch path. Everything else (feat/…, main, master) is a branch.
_VERSION_TAG_RE = re.compile(r"(?:^|-)v\d+\.\d+\.\d+$")


def parse_pins(text: str) -> dict[str, tuple[str, str]]:
    """{package: (owner, sha)} for every first-party git pin in pyproject."""
    pins: dict[str, tuple[str, str]] = {}
    for m in _PIN_RE.finditer(text):
        pins[m.group("name")] = (m.group("owner"), m.group("sha").lower())
    return pins


def parse_doc_table(text: str) -> dict[str, tuple[str, str]]:
    """{package: (release_tag_label, documented_commit)} from the mapping table.

    Only rows whose commit cell is a 40-hex SHA are release rows; the header and
    separator rows are skipped by that same test.
    """
    rows: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip().strip("`").strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        pkg, label, commit = cells
        if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
            continue
        rows[pkg] = (label, commit.lower())
    return rows


def ls_remote_tag(owner: str, name: str, tag: str) -> str | None:
    """Resolve a tag to a commit on GitHub. Dereferences annotated tags.

    Returns the lowercase 40-hex commit the tag points to, or None if the tag
    does not exist on the repo.
    """
    url = f"https://github.com/{owner}/{name}"
    out = subprocess.run(
        ["git", "ls-remote", "--tags", url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    peeled: str | None = None
    direct: str | None = None
    for row in out.splitlines():
        sha, _, ref = row.partition("\t")
        if ref == f"refs/tags/{tag}^{{}}":
            peeled = sha.strip().lower()
        elif ref == f"refs/tags/{tag}":
            direct = sha.strip().lower()
    # Annotated tags expose the commit via the peeled ref; lightweight tags via
    # the direct ref. The peeled commit is authoritative when present.
    return peeled or direct


def main() -> int:
    pins = parse_pins(PYPROJECT.read_text(encoding="utf-8"))
    doc = parse_doc_table(DOC.read_text(encoding="utf-8"))

    errors: list[str] = []
    # Only planes RVND actually pins in pyproject are in scope; the doc's
    # vendored patchbay row has no git pin and is verified by its own hash gate.
    planes = sorted(pins)

    verified: list[str] = []
    exempt: list[str] = []
    for name in planes:
        owner, sha = pins[name]
        if name not in doc:
            errors.append(f"{name}: pinned in pyproject but absent from the doc table")
            continue
        label, doc_sha = doc[name]
        if doc_sha != sha:
            errors.append(
                f"{name}: doc commit {doc_sha[:7]} != pyproject pin {sha[:7]} "
                f"(fix the doc table row or the pin)"
            )
            continue
        if not _VERSION_TAG_RE.search(label):
            exempt.append(f"{name} (branch-pin: {label})")
            continue
        try:
            resolved = ls_remote_tag(owner, name, label)
        except subprocess.CalledProcessError as exc:
            errors.append(f"{name}: git ls-remote failed for tag {label}: {exc.stderr.strip()}")
            continue
        except subprocess.TimeoutExpired:
            errors.append(f"{name}: git ls-remote timed out resolving tag {label}")
            continue
        if resolved is None:
            errors.append(
                f"{name}: doc names version tag {label!r}, but no such tag exists on "
                f"github.com/{owner}/{name} (create the tag at {sha[:7]}, or relabel the "
                f"row as a branch-pin)"
            )
        elif resolved != sha:
            errors.append(
                f"{name}: tag {label} resolves to {resolved[:7]} but the pin is {sha[:7]} "
                f"(tag was moved or points at the wrong commit)"
            )
        else:
            verified.append(f"{name} {label} -> {sha[:7]}")

    for line in verified:
        print(f"  ok   {line}")
    for line in exempt:
        print(f"  skip {line}")

    if errors:
        print("\nFAIL — tag/pin traceability:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"\nverify_pin_tags: {len(verified)} tag(s) verified, {len(exempt)} branch-pin(s) exempt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
