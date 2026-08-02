#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Fail closed if RVND's vendored Patchbay presentation contract drifts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "release" / "patchbay-consumption.json"


def main() -> int:
    contract = json.loads(MANIFEST.read_text(encoding="utf-8"))
    commit = contract.get("commit", "")
    files = contract.get("files", [])
    expected = contract.get("aggregate_sha256", "")
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
        raise SystemExit("patchbay-consumption: invalid immutable upstream commit")
    if not files or files != sorted(set(files)):
        raise SystemExit("patchbay-consumption: files must be non-empty, sorted and unique")

    aggregate = hashlib.sha256()
    for relative in files:
        path = ROOT / relative
        if not path.is_file():
            raise SystemExit(f"patchbay-consumption: missing vendored file: {relative}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        aggregate.update(relative.removeprefix("app/").encode())
        aggregate.update(b"\0")
        aggregate.update(digest.encode())
        aggregate.update(b"\n")

    actual = aggregate.hexdigest()
    if actual != expected:
        raise SystemExit(
            "patchbay-consumption: vendored Patchbay contract drifted "
            f"(expected {expected}, got {actual})"
        )
    print(
        "patchbay-consumption: clean "
        f"({len(files)} files from {contract['upstream']}@{commit})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
