#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Dependency licence gate — RVND is AGPL-3.0-only, and downstream users rely
on being able to use, modify and redistribute it (including commercially)
without copyleft obligations, so no third-party dependency in the installed
environment may be copyleft. Scans every installed distribution's licence
metadata (License field, License-Expression, classifiers) for copyleft
markers and fails on any hit outside the allowlist. Missing or unrecognized
licence metadata also fails: absence is not release evidence. Run in CI after
``pip install -e ".[test]"``; run locally the same way.

  python3 scripts/dep_license_gate.py
"""
from __future__ import annotations

from importlib import metadata

COPYLEFT_MARKERS = ("GPL", "SSPL", "EUPL", "OSL", "CECILL", "MPL-2.0-no-copyleft-exception")
KNOWN_LICENSE_MARKERS = (
    "apache", "mit", "bsd", "isc", "python software foundation", "psf",
    "mozilla public license", "mpl-2.0", "unlicense", "public domain", "ofl-1.1",
)

ALLOW = {
    # First-party packages are governed by Rvnd's terms rather than treated as
    # third-party dependencies (all install names the project has carried).
    "workspaces": "the Rvnd server itself (first-party package)",
    "rvnd": "the Rvnd server itself (first-party package)",
    "cubes": "the Rvnd server under its pre-rename install name (ours)",
    # build-only tool, never distributed with the product; GPL-2.0 carries the
    # PyInstaller bundling exception
    "pyinstaller": "build tool only; GPL with bundling exception",
    "pyinstaller-hooks-contrib": "build tool only; GPL with bundling exception",
}


def licence_text(dist: metadata.Distribution) -> str:
    # License-Expression and classifiers are identifiers and safe to scan.
    # The free-text License field is only trusted when it is identifier-sized:
    # some packages paste their whole licence file there, and full BSD/MIT
    # prose can mention "GPL" without the package being copyleft.
    parts = [dist.metadata.get("License-Expression", "") or ""]
    free = (dist.metadata.get("License", "") or "").strip()
    if len(free) <= 100:
        parts.append(free)
    parts += [c for c in dist.metadata.get_all("Classifier", []) if c.startswith("License")]
    return " | ".join(p for p in parts if p)


import re

_REQ = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(?:\[([^\]]*)\])?")
_EXTRA_MARKER = re.compile(r"""extra\s*==\s*['"]([^'"]+)['"]""")


def _parse_req(req: str) -> tuple[str, frozenset[str], str | None]:
    """(name, extras requested OF the dep, extra of the PARENT gating this
    requirement — None when unconditional). Non-extra markers (python_version,
    platform) are ignored, which errs toward scanning more, never less."""
    m = _REQ.match(req)
    name = (m.group(1) if m else req).lower().replace("_", "-")
    extras = frozenset(e.strip().lower() for e in (m.group(2) or "").split(",") if e.strip())
    marker = req.split(";", 1)[1] if ";" in req else ""
    gate = _EXTRA_MARKER.search(marker)
    return name, extras, (gate.group(1).lower() if gate else None)


def closure(roots: list[tuple[str, frozenset[str]]]) -> dict[str, metadata.Distribution]:
    """The installed dependency closure, marker-aware: a requirement gated on
    ``extra == "x"`` is followed only when the parent was requested with that
    extra. A dep that is not installed is simply absent."""
    seen: dict[str, metadata.Distribution] = {}
    done: dict[str, set[str]] = {}
    frontier = list(roots)
    while frontier:
        name, extras = frontier.pop()
        if name in done and extras <= done[name]:
            continue
        done.setdefault(name, set()).update(extras)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen[name] = dist
        for req in dist.requires or []:
            child, child_extras, gate = _parse_req(req)
            if gate is None or gate in done[name]:
                frontier.append((child, child_extras))
    return seen


def main() -> int:
    # every install name the project has carried, with every extra it offers —
    # the gate covers everything the project can ask a user to install
    roots = []
    for own in ("rvnd", "workspaces", "cubes"):
        try:
            offered = metadata.distribution(own).metadata.get_all("Provides-Extra", [])
        except metadata.PackageNotFoundError:
            continue
        roots.append((own, frozenset(e.lower() for e in offered)))
    hits: list[tuple[str, str]] = []
    for name, dist in closure(roots).items():
        if name in ALLOW:
            continue
        text = licence_text(dist)
        if not text:
            hits.append((name, "missing licence metadata"))
        elif any(marker.lower() in text.lower() for marker in COPYLEFT_MARKERS):
            hits.append((name, text[:160]))
        elif not any(marker in text.lower() for marker in KNOWN_LICENSE_MARKERS):
            hits.append((name, f"unknown licence metadata: {text[:130]}"))
    if hits:
        for name, text in sorted(hits):
            print(f"unapproved dependency licence: {name} — {text}")
        print("dep-license-gate: FAIL — every dependency needs recognized,"
              " compatible licence metadata; correct upstream"
              " metadata, replace it, or allowlist it here with its reason")
        return 1
    print("dep-license-gate: clean (all installed dependency licences are"
          " recognized and approved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
