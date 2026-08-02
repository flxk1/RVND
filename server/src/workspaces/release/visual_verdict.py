#!/usr/bin/env python3
"""Packaged RVND-owned release verdict for Loomground Builder visual artifacts.

This policy is deliberately narrow.  It does not pretend that a machine is a
person.  It verifies immutable render provenance and objective release
properties, then verifies an auditable RVND policy verdict bound to the
candidate commit and canonical render-set digest.

Internal by design: the supported public surface is the
``rvnd-visual-verdict`` console entry point.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

POLICY = "loomground-builder.visual-release/v1"
REQUIRED_VIEWS = {
    ("rack", "2d"), ("synth", "2d"), ("daw", "2d"),
    ("spatial", "3d"), ("spatial", "2d"), ("spatial", "text"),
}
MIN_WIDTH = 1024
MIN_HEIGHT = 700
MIN_BYTES = 4_096


def _canonical_digest(commit: str, renders: list[dict]) -> str:
    bound = {
        "commit": commit,
        "policy": POLICY,
        "renders": sorted(
            [
                {
                    key: render.get(key)
                    for key in ("genre", "mode", "path", "sha256",
                                "viewport", "command")
                }
                for render in renders
            ],
            key=lambda item: (str(item["genre"]), str(item["mode"]),
                              str(item["path"])),
        ),
    }
    payload = json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _inside(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("render path must be a non-empty string")
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"render path escapes builder root: {value!r}") from exc
    return path


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("render is not a PNG")
    if data[12:16] != b"IHDR":
        raise ValueError("PNG has no leading IHDR")
    return struct.unpack(">II", data[16:24])


def _git_commit(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def verify(evidence_path: Path, builder_root: Path) -> tuple[list[str], str]:
    errors: list[str] = []
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read evidence: {exc}"], ""

    commit = evidence.get("commit")
    renders = evidence.get("renders")
    authority = evidence.get("authority")
    if not isinstance(commit, str) or not commit:
        errors.append("evidence requires commit")
    elif commit != _git_commit(builder_root):
        errors.append("evidence commit does not match builder HEAD")
    if evidence.get("disposition") != "pass":
        errors.append("evidence disposition is not pass")
    if not isinstance(evidence.get("date"), str) or not evidence.get("date"):
        errors.append("evidence requires date")
    if not isinstance(renders, list):
        errors.append("evidence renders must be a list")
        renders = []
    if not isinstance(authority, dict):
        errors.append("evidence requires authority object")
        authority = {}
    if authority.get("owner") != "RVND":
        errors.append("authority owner is not RVND")
    if authority.get("policy") != POLICY:
        errors.append("authority policy is unknown")
    if authority.get("verdict") != "GO":
        errors.append("authority verdict is not GO")

    seen: set[tuple[object, object]] = set()
    for render in renders:
        if not isinstance(render, dict):
            errors.append("each render must be an object")
            continue
        view = (render.get("genre"), render.get("mode"))
        if view in seen:
            errors.append(f"duplicate render view: {view}")
        seen.add(view)
        if not render.get("viewport") or not render.get("command"):
            errors.append(f"{view}: viewport and command are required")
        try:
            path = _inside(builder_root, render.get("path"))
            data = path.read_bytes()
            actual = hashlib.sha256(data).hexdigest()
            if actual != render.get("sha256"):
                errors.append(f"{view}: sha256 mismatch")
            width, height = _png_dimensions(data)
            if width < MIN_WIDTH or height < MIN_HEIGHT:
                errors.append(
                    f"{view}: render {width}x{height} is below "
                    f"{MIN_WIDTH}x{MIN_HEIGHT}")
            if len(data) < MIN_BYTES:
                errors.append(f"{view}: PNG is implausibly small ({len(data)} bytes)")
        except (OSError, ValueError) as exc:
            errors.append(f"{view}: {exc}")
    missing = REQUIRED_VIEWS - seen
    unexpected = seen - REQUIRED_VIEWS
    if missing:
        errors.append(f"render matrix missing: {sorted(missing)}")
    if unexpected:
        errors.append(f"render matrix has unexpected views: {sorted(unexpected)}")

    digest = _canonical_digest(commit or "", renders)
    if authority.get("input_digest") != digest:
        errors.append("authority input_digest does not bind canonical render set")
    triple = authority.get("audit_triple")
    if not isinstance(triple, dict):
        errors.append("authority audit_triple is required")
    else:
        if (triple.get("subject"), triple.get("predicate"), triple.get("object")) != (
                "RVND", "GO", POLICY):
            errors.append("authority audit_triple is not the RVND GO policy triple")
        if triple.get("input_digest") != digest:
            errors.append("audit triple is not bound to input_digest")

    findings = evidence.get("findings")
    if not isinstance(findings, list):
        errors.append("evidence findings must be a list")
        findings = []
    blocking = [
        finding for finding in findings
        if isinstance(finding, dict)
        and finding.get("severity") == "blocking"
        and finding.get("status") != "resolved"
    ]
    if blocking:
        errors.append(f"{len(blocking)} unresolved blocking finding(s)")
    return errors, digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", type=Path, required=True)
    parser.add_argument("--builder-root", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.builder_root.resolve()
    errors, digest = verify(args.verify.resolve(), root)
    if errors:
        print("RVND VISUAL VERDICT: NO-GO")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"RVND VISUAL VERDICT: GO policy={POLICY} input_digest={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
