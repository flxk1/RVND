# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Fail-closed structural checks for an independently public RVND snapshot."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "release" / "public-snapshot.json"


def _git(*args: str, repo: Path = REPO) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False)


def load_manifest(path: Path = MANIFEST) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "rvnd-public-snapshot-1":
        raise ValueError("unsupported public snapshot schema")
    for key in (
        "required_paths", "forbidden_path_prefixes",
        "forbidden_tracked_suffixes", "forbidden_content_markers",
    ):
        values = manifest.get(key)
        if not isinstance(values, list) or not values:
            raise ValueError(f"{key} must be a non-empty list")
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(f"{key} entries must be non-empty strings")
        if len(values) != len(set(values)):
            raise ValueError(f"{key} contains duplicate entries")
    return manifest


def tracked_entries(repo: Path = REPO) -> list[tuple[str, str]]:
    result = _git("ls-files", "--stage", "-z", repo=repo)
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {result.stderr.strip()}")
    entries: list[tuple[str, str]] = []
    for record in result.stdout.split("\0"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        entries.append((metadata.split()[0], path))
    return entries


def violations(
    manifest: dict, entries: list[tuple[str, str]], repo: Path = REPO,
) -> list[str]:
    paths = {path for _, path in entries}
    failures: list[str] = []
    for required in manifest["required_paths"]:
        if required not in paths:
            failures.append(f"required tracked path missing: {required}")
    prefixes = tuple(prefix.rstrip("/") for prefix in manifest["forbidden_path_prefixes"])
    suffixes = tuple(suffix.lower() for suffix in manifest["forbidden_tracked_suffixes"])
    for mode, path in entries:
        if mode == "160000":
            failures.append(f"git submodule is not self-contained: {path}")
        if mode == "120000":
            failures.append(f"symbolic link is not self-contained: {path}")
        if any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes):
            failures.append(f"private/workspace path is tracked: {path}")
        if path.lower().endswith(suffixes):
            failures.append(f"secret-bearing file type is tracked: {path}")
        candidate = repo / path
        # PEM may legitimately contain public verification keys. Inspect its
        # payload and refuse private-key blocks instead of banning the format.
        if mode == "100644" and path.lower().endswith(".pem") and candidate.is_file():
            try:
                content = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = ""
            if any(marker in content for marker in manifest["forbidden_content_markers"]):
                failures.append(f"private-key marker is tracked: {path}")
    return sorted(set(failures))


def main() -> int:
    try:
        failures = violations(load_manifest(), tracked_entries())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"public-snapshot: FAIL: {exc}")
        return 1
    if failures:
        print("public-snapshot: FAIL")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("public-snapshot: OK — tracked tree is self-contained and excludes private workspace material")
    return 0


if __name__ == "__main__":
    sys.exit(main())
