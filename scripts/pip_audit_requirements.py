#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Emit the installed PyPI closure as pinned pip-audit requirements.

``pip-audit`` cannot query PyPI advisories for RVND's first-party VCS installs.
Exclude a distribution only when its installed ``direct_url.json`` proves it
came from VCS or a local path.  Names are deliberately irrelevant: a package
called ``loomground-*`` from PyPI must still be audited.
"""
from __future__ import annotations

import json
import sys
from importlib import metadata
from typing import Iterable
from urllib.parse import urlsplit


def canonical_name(value: str) -> str:
    return "-".join(filter(None, value.lower().replace("_", "-").split("-")))


def direct_origin(dist: metadata.Distribution) -> str | None:
    """Return ``vcs``/``local`` for proven non-PyPI installs.

    An unrecognised direct URL fails closed: treating an arbitrary archive as
    the same artifact published under that name/version on PyPI would produce
    misleading vulnerability evidence.
    """
    raw = dist.read_text("direct_url.json")
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid direct_url.json") from exc
    if not isinstance(record, dict) or not isinstance(record.get("url"), str):
        raise ValueError("invalid direct_url.json shape")
    if isinstance(record.get("vcs_info"), dict):
        return "vcs"
    if isinstance(record.get("dir_info"), dict):
        return "local"
    if urlsplit(record["url"]).scheme == "file":
        return "local"
    raise ValueError("unsupported direct URL origin")


def audit_requirements(
    distributions: Iterable[metadata.Distribution],
) -> tuple[list[str], list[tuple[str, str]]]:
    installed: dict[str, set[str]] = {}
    direct: dict[str, set[str]] = {}
    for dist in distributions:
        name = dist.metadata.get("Name")
        version = dist.version
        if not name or not version:
            raise ValueError("installed distribution lacks Name or Version metadata")
        normalized = canonical_name(name)
        try:
            origin = direct_origin(dist)
        except ValueError as exc:
            raise ValueError(f"{normalized}: {exc}") from exc
        installed.setdefault(normalized, set()).add(version)
        if origin:
            direct.setdefault(normalized, set()).add(origin)

    conflicting = {name: versions for name, versions in installed.items()
                   if name not in direct and len(versions) != 1}
    if conflicting:
        details = ", ".join(
            f"{name}={','.join(sorted(versions))}"
            for name, versions in sorted(conflicting.items())
        )
        raise ValueError(f"conflicting installed versions: {details}")

    excluded = [
        (name, "+".join(sorted(origins)))
        for name, origins in sorted(direct.items())
    ]
    requirements = [
        f"{name}=={next(iter(versions))}"
        for name, versions in sorted(installed.items())
        if name not in direct
    ]
    return requirements, excluded


def main() -> int:
    try:
        requirements, excluded = audit_requirements(metadata.distributions())
    except ValueError as exc:
        print(f"pip-audit-input: FAIL — {exc}", file=sys.stderr)
        return 1
    for name, origin in excluded:
        print(f"pip-audit-input: exclude {name} ({origin} direct install)",
              file=sys.stderr)
    if not requirements:
        print("pip-audit-input: FAIL — no PyPI distributions found", file=sys.stderr)
        return 1
    print("\n".join(requirements))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
