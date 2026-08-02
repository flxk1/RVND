# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
import json

import pytest

from scripts.pip_audit_requirements import audit_requirements, direct_origin


class FakeDist:
    def __init__(self, name="Example_Pkg", version="1.2.3", direct=None):
        self.metadata = {"Name": name}
        self.version = version
        self._direct = direct

    def read_text(self, filename):
        assert filename == "direct_url.json"
        if self._direct is None:
            return None
        return self._direct if isinstance(self._direct, str) else json.dumps(self._direct)


def test_pypi_distribution_is_pinned_for_audit():
    requirements, excluded = audit_requirements([FakeDist()])
    assert requirements == ["example-pkg==1.2.3"]
    assert excluded == []


@pytest.mark.parametrize("record, expected", [
    ({"url": "https://example.invalid/repo.git", "vcs_info": {"vcs": "git"}}, "vcs"),
    ({"url": "file:///workspace/rvnd", "dir_info": {"editable": True}}, "local"),
    ({"url": "file:///tmp/package.whl", "archive_info": {}}, "local"),
])
def test_proven_vcs_and_local_distributions_are_excluded(record, expected):
    dist = FakeDist(name="Not_Necessarily_Loomground", direct=record)
    requirements, excluded = audit_requirements([dist])
    assert requirements == []
    assert excluded == [("not-necessarily-loomground", expected)]


def test_name_prefix_does_not_exempt_a_pypi_distribution():
    requirements, excluded = audit_requirements([FakeDist(name="loomground-impostor")])
    assert requirements == ["loomground-impostor==1.2.3"]
    assert excluded == []


@pytest.mark.parametrize("direct", [
    "not-json",
    {"vcs_info": {"vcs": "git"}},
    {"url": "https://downloads.example.invalid/package.whl", "archive_info": {}},
])
def test_unknown_or_malformed_direct_origin_fails_closed(direct):
    with pytest.raises(ValueError):
        direct_origin(FakeDist(direct=direct))


def test_duplicate_installed_metadata_has_one_deterministic_requirement():
    requirements, _ = audit_requirements([
        FakeDist(name="Example-Pkg", version="1.2.3"),
        FakeDist(name="example_pkg", version="1.2.3"),
    ])
    assert requirements == ["example-pkg==1.2.3"]


def test_direct_origin_wins_over_duplicate_plain_metadata_in_any_order():
    direct = FakeDist(name="rvnd", direct={
        "url": "file:///workspace/rvnd", "dir_info": {"editable": True},
    })
    plain = FakeDist(name="RVND")
    for records in ([plain, direct], [direct, plain]):
        requirements, excluded = audit_requirements(records)
        assert requirements == []
        assert excluded == [("rvnd", "local")]


def test_conflicting_installed_versions_fail_closed():
    with pytest.raises(ValueError, match="conflicting installed versions"):
        audit_requirements([
            FakeDist(name="example", version="1.0"),
            FakeDist(name="example", version="2.0"),
        ])
