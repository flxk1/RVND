# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
import json
from pathlib import Path

import pytest

from scripts.release_dependency_artifacts import ArtifactError, components, main
from scripts import dep_license_gate


def item(name="Example", version="1.2.3", licence="MIT", digest="a" * 64):
    return {
        "metadata": {"name": name, "version": version, "license": licence},
        "download_info": {
            "url": f"https://files.example/{name}.whl",
            "archive_info": {"hashes": {"sha256": digest}},
        },
    }


def test_report_generates_platform_lock_sbom_and_notices(tmp_path: Path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"install": [item()]}), encoding="utf-8")

    assert main([
        "--pip-report", str(report),
        "--platform", "linux-x86_64-py312",
        "--output-dir", str(tmp_path / "out"),
    ]) == 0

    lock = (tmp_path / "out/requirements-linux-x86_64-py312.lock").read_text()
    assert "example==1.2.3 --hash=sha256:" + "a" * 64 in lock
    sbom = json.loads((tmp_path / "out/sbom-linux-x86_64-py312.cdx.json").read_text())
    assert sbom["specVersion"] == "1.6"
    assert sbom["components"][0]["licenses"][0]["license"]["name"] == "MIT"
    assert (tmp_path / "out/THIRD_PARTY_NOTICES-linux-x86_64-py312.md").exists()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"licence": ""}, "missing licence"),
        ({"licence": "Custom Proprietary Terms"}, "unknown licence"),
        ({"licence": "GPL-3.0-only"}, "copyleft"),
        ({"digest": ""}, "sha256"),
    ],
)
def test_report_denies_incomplete_or_unapproved_metadata(change, message):
    dependency = item(
        licence=change.get("licence", "MIT"),
        digest=change.get("digest", "a" * 64),
    )
    with pytest.raises(ArtifactError, match=message):
        components({"install": [dependency]})


def test_first_party_local_artifacts_do_not_require_archive_hash():
    own = {"metadata": {"name": "rvnd", "version": "0.0.0"}}
    assert components({"install": [own, item()]})[0]["name"] == "example"


def test_loomground_dependency_requires_exact_requested_commit():
    dependency = item(name="loomground-versum", licence="Apache-2.0")
    dependency["download_info"] = {
        "url": "https://github.com/flxk1/loomground-versum",
        "vcs_info": {
            "vcs": "git",
            "commit_id": "7" * 40,
            "requested_revision": "main",
        },
    }
    with pytest.raises(ArtifactError, match="exact 40-character Git commit"):
        components({"install": [dependency]})


class FakeMetadata(dict):
    def get_all(self, key, default=None):
        return self.get(key, default or [])


class FakeDistribution:
    def __init__(self, licence):
        self.metadata = FakeMetadata({"License": licence} if licence is not None else {})


@pytest.mark.parametrize("licence", [None, "Private Custom Licence"])
def test_installed_gate_denies_missing_or_unknown_licence(monkeypatch, licence):
    monkeypatch.setattr(
        dep_license_gate,
        "closure",
        lambda roots: {"unreviewed-package": FakeDistribution(licence)},
    )
    assert dep_license_gate.main() == 1
