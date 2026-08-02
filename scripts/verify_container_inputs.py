#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Deny mutable container images and unsafe Docker build contexts."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def main() -> int:
    failures: list[str] = []
    dockerfile = (ROOT / "deploy/Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "deploy/compose.yml").read_text(encoding="utf-8")

    from_lines = [
        line.split(maxsplit=1)[1].strip()
        for line in dockerfile.splitlines()
        if line.startswith("FROM ")
    ]
    images = [
        line.split(":", 1)[1].strip()
        for line in compose.splitlines()
        if line.lstrip().startswith("image:")
    ]
    for image in from_lines + images:
        if not DIGEST.search(image):
            failures.append(f"mutable container image: {image}")

    ignore = ROOT / ".dockerignore"
    if not ignore.is_file():
        failures.append(".dockerignore is missing")
    else:
        ignored = {
            line.strip() for line in ignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        for required in (".git", ".env", ".venv", "**/node_modules"):
            if required not in ignored:
                failures.append(f".dockerignore does not exclude {required}")

    if failures:
        for failure in failures:
            print(f"container-inputs: FAIL — {failure}")
        return 1
    print(
        "container-inputs: clean "
        f"({len(from_lines)} base and {len(images)} service images digest-pinned)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
