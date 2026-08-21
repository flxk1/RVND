#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Build fail-closed dependency release artifacts from a pip install report.

The report must come from resolving every RVND extra on the target platform.
Every third-party artifact needs a version, SHA-256 digest, and recognized
licence. The command writes a hash lock, CycloneDX 1.6 SBOM, and notices file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

VCS_FIRST_PARTY = {
    "loomground-solver",
    "loomground-versum",
    "loomground-governance",
    "loomground-deontic",
    "loomground-ingest",
    "loomground-legal",
    "loomground-norm",
    "loomground-factual",
    "loomground-epistemic",
    "loomground-workspace",
    # Evidence layer (P0a): git-pinned like the planes, so it carries an exact
    # commit rather than a PyPI sha256.
    "enforcement-posture",
    # Portable oversight certificate (Art. 14) — same git-pin discipline.
    "oversight-certificate",
    # Complete-mediation evidence (P0b) — reconciles decisions vs effects; same git-pin discipline.
    "effect-reconciliation",
    # The bounded supervisor brief, extracted from the solver kernel in 0.5.0.
    "loomground-brief",
    "loomground-proxy",
    # The vertical-registration surface (subject vocabulary, jurisdiction packs,
    # requirements house) — same git-pin discipline as the planes.
    "loomground-vertical",
}
COPYLEFT = re.compile(r"\b(?:AGPL|GPL|LGPL|SSPL|EUPL|OSL|CECILL)\b", re.I)
KNOWN_LICENSES = (
    "apache",
    "mit",
    "bsd",
    "isc",
    "python software foundation",
    "psf",
    "mozilla public license",
    "mpl-2.0",
    "unlicense",
    "public domain",
    "ofl-1.1",
)
LICENSE_EXCEPTIONS = {
    "pyinstaller": "build-only GPL tool with the PyInstaller bundling exception",
    "pyinstaller-hooks-contrib": "build-only GPL tool with the PyInstaller bundling exception",
}


class ArtifactError(ValueError):
    """A dependency report is incomplete or unsuitable for release."""


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def licence_text(metadata: dict[str, Any]) -> str:
    value = metadata.get("license_expression") or metadata.get("license")
    if isinstance(value, dict):
        value = value.get("text") or value.get("file")
    classifiers = metadata.get("classifier") or metadata.get("classifiers") or []
    if isinstance(classifiers, str):
        classifiers = [classifiers]
    parts = [str(value or "")]
    parts.extend(str(item) for item in classifiers if "License" in str(item))
    return " | ".join(part.strip() for part in parts if part and part.strip())


def subject_component_version() -> str:
    """rvnd's own version — the subject the SBOM describes (metadata.component).

    rvnd is the install root, skipped from the dependency component list, but it
    is what the document is ABOUT; a CycloneDX SBOM without metadata.component
    leaves a consumer unable to tell what it describes. Read from the single
    version source (server/src/rvnd/_version.py) so it matches the release
    tag and is immune to how a given pip report represents the install root.
    """
    version_file = (
        Path(__file__).resolve().parent.parent
        / "server" / "src" / "rvnd" / "_version.py"
    )
    try:
        text = version_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactError(f"cannot read rvnd version source: {exc}") from exc
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        raise ArtifactError(f"no __version__ in {version_file}")
    return match.group(1)


def sha256_for(item: dict[str, Any]) -> str:
    archive = item.get("download_info", {}).get("archive_info", {})
    hashes = archive.get("hashes") or {}
    digest = hashes.get("sha256")
    if not digest:
        legacy = archive.get("hash", "")
        if legacy.startswith("sha256="):
            digest = legacy.partition("=")[2]
    if not digest or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise ArtifactError("missing immutable sha256")
    return digest.lower()


def components(report: dict[str, Any]) -> list[dict[str, str]]:
    install = report.get("install")
    if not isinstance(install, list) or not install:
        raise ArtifactError("pip report has no resolved install set")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in install:
        metadata = item.get("metadata") or {}
        name = canonical(str(metadata.get("name") or ""))
        version = str(metadata.get("version") or "").strip()
        if not name or not version:
            raise ArtifactError("dependency is missing name or version")
        if name in seen:
            raise ArtifactError(f"duplicate dependency: {name}")
        seen.add(name)
        if name == "rvnd":
            continue
        licence = licence_text(metadata)
        if not licence:
            raise ArtifactError(f"{name}: missing licence metadata")
        if COPYLEFT.search(licence) and name not in LICENSE_EXCEPTIONS:
            raise ArtifactError(f"{name}: disallowed copyleft licence: {licence}")
        if (
            not any(marker in licence.lower() for marker in KNOWN_LICENSES)
            and name not in LICENSE_EXCEPTIONS
        ):
            raise ArtifactError(f"{name}: unknown licence: {licence}")
        download = item.get("download_info", {})
        vcs = download.get("vcs_info") or {}
        if name in VCS_FIRST_PARTY:
            digest = str(vcs.get("commit_id") or "")
            requested = str(vcs.get("requested_revision") or "")
            if (
                vcs.get("vcs") != "git"
                or not re.fullmatch(r"[0-9a-f]{40}", digest)
                or requested != digest
            ):
                raise ArtifactError(f"{name}: missing exact 40-character Git commit")
            source = f"git+{download.get('url')}@{digest}"
            integrity = "git-commit"
        else:
            try:
                digest = sha256_for(item)
            except ArtifactError as exc:
                raise ArtifactError(f"{name}: {exc}") from exc
            source = str(download.get("url") or "")
            integrity = "sha256"
        result.append({
            "name": name,
            "version": version,
            "licence": licence,
            "digest": digest,
            "integrity": integrity,
            "url": source,
        })
    if not result:
        raise ArtifactError("pip report has no third-party dependencies")
    return sorted(result, key=lambda entry: entry["name"])


def write_artifacts(entries: list[dict[str, str]], platform: str, output: Path,
                    subject_version: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    lock_lines = [
        "# Generated complete resolution lock.",
        "# Index artifacts carry SHA-256; Loomground VCS packages carry exact Git commits.",
        f"# platform: {platform}",
    ]
    lock_lines.extend(
        (
            f"{entry['name']} @ {entry['url']}"
            if entry["integrity"] == "git-commit"
            else f"{entry['name']}=={entry['version']} --hash=sha256:{entry['digest']}"
        )
        for entry in entries
    )
    (output / f"requirements-{platform}.lock").write_text(
        "\n".join(lock_lines) + "\n", encoding="utf-8"
    )
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "rvnd",
                "version": subject_version,
                "purl": f"pkg:pypi/rvnd@{subject_version}",
            },
            "properties": [{"name": "rvnd:platform", "value": platform}],
        },
        "components": [
            {
                "type": "library",
                "name": entry["name"],
                "version": entry["version"],
                "purl": f"pkg:pypi/{entry['name']}@{entry['version']}",
                "hashes": (
                    [{"alg": "SHA-256", "content": entry["digest"]}]
                    if entry["integrity"] == "sha256" else []
                ),
                "properties": [
                    {"name": f"rvnd:{entry['integrity']}", "value": entry["digest"]}
                ],
                "licenses": [{"license": {"name": entry["licence"]}}],
                "externalReferences": (
                    [{"type": "distribution", "url": entry["url"]}]
                    if entry["url"] else []
                ),
            }
            for entry in entries
        ],
    }
    (output / f"sbom-{platform}.cdx.json").write_text(
        json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    notices = [
        "<!-- SPDX-License-" + "Identifier: AGPL-3.0-only -->",
        "# RVND third-party dependency notices",
        "",
        f"Resolved platform: `{platform}`.",
        "",
        "| Package | Version | Licence metadata | Integrity |",
        "|---|---:|---|---|",
    ]
    notices.extend(
        f"| {entry['name']} | {entry['version']} | "
        f"{entry['licence'].replace('|', '&#124;')} | "
        f"`{entry['integrity']}:{entry['digest']}` |"
        for entry in entries
    )
    (output / f"THIRD_PARTY_NOTICES-{platform}.md").write_text(
        "\n".join(notices) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pip-report", type=Path, required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.pip_report.read_text(encoding="utf-8"))
        entries = components(report)
        write_artifacts(entries, args.platform, args.output_dir,
                        subject_component_version())
    except (OSError, json.JSONDecodeError, ArtifactError) as exc:
        print(f"release-dependency-artifacts: FAIL — {exc}", file=sys.stderr)
        return 1
    print(f"release-dependency-artifacts: wrote {len(entries)} components for {args.platform}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
