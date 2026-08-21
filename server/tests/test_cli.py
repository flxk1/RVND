# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the workspace-l0 CLI (A4)."""

from __future__ import annotations

import io
import json

import pytest

from rvnd import WorkspaceMemory
from rvnd.cli import main


@pytest.fixture
def log_root(tmp_path):
    return tmp_path / "logs"


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "vault"
    f.mkdir()
    return f


def _sample_pair(suffix: str, *, summary: str, source_document: str | None = None) -> dict:
    pid = f"sha256:problem-{suffix}"
    sid = f"sha256:solution-{suffix}"
    return {
        "id": sid,
        "problem": {"id": pid, "scope": "test", "type": "test",
                    "summary": summary, "facets": {},
                    "source_document": source_document},
        "solution": {"id": sid, "problem_id": pid, "body": "x",
                     "body_format": "prose", "authority_tier": 3,
                     "confidence": 0.9, "cited_sources": ["test://1"]},
    }


# ===========================================================================
# list + show + folders
# ===========================================================================


def test_list_empty_folder(folder, log_root, capsys):
    """`list` on an empty folder reports no pairs cleanly."""
    rc = main(["--log-root", str(log_root), "list", "--folder", str(folder)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no pairs in scope" in out


def test_list_after_remember(folder, log_root, capsys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    mem.remember(_sample_pair("p1", summary="findable in list"))

    rc = main(["--log-root", str(log_root), "list", "--folder", str(folder)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "findable in list" in out
    assert "sha256:solution-p1" in out


def test_list_respects_limit(folder, log_root, capsys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    for i in range(10):
        mem.remember(_sample_pair(f"p{i}", summary=f"pair {i}"))

    rc = main(["--log-root", str(log_root), "list", "--folder", str(folder), "--limit", "3"])
    assert rc == 0
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) == 3


def test_show_pair_as_json(folder, log_root, capsys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    pid = mem.remember(_sample_pair("p1", summary="show me"))

    rc = main(["--log-root", str(log_root), "show", "--folder", str(folder), pid])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["problem"]["summary"] == "show me"


def test_show_unknown_pair_returns_1(folder, log_root, capsys):
    rc = main(["--log-root", str(log_root), "show", "--folder", str(folder),
               "sha256:does-not-exist"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_folders_lists_known(folder, log_root, capsys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    mem.remember(_sample_pair("p1", summary="x"))

    rc = main(["--log-root", str(log_root), "folders"])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(folder.resolve()) in out


def test_folders_empty(tmp_path, capsys):
    rc = main(["--log-root", str(tmp_path / "empty"), "folders"])
    assert rc == 0


# ===========================================================================
# delete (logical) + confirmation
# ===========================================================================


def test_delete_with_yes_flag(folder, log_root, capsys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    pid = mem.remember(_sample_pair("doomed", summary="doomed pair"))

    rc = main(["--log-root", str(log_root), "delete", "--folder", str(folder),
               pid, "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deleted" in out
    # Pair is hidden from reads.
    assert WorkspaceMemory(folder, log_root=log_root).by_id(pid) is None


def test_delete_prompts_without_yes(folder, log_root, capsys, monkeypatch):
    mem = WorkspaceMemory(folder, log_root=log_root)
    pid = mem.remember(_sample_pair("doomed", summary="doomed pair"))
    # Type 'y' on the prompt.
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))

    rc = main(["--log-root", str(log_root), "delete", "--folder", str(folder), pid])
    assert rc == 0


def test_delete_user_aborts(folder, log_root, monkeypatch, capsys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    pid = mem.remember(_sample_pair("safe", summary="should survive"))
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))

    rc = main(["--log-root", str(log_root), "delete", "--folder", str(folder), pid])
    assert rc == 2
    out = capsys.readouterr().out
    assert "aborted" in out
    # Pair survives.
    assert WorkspaceMemory(folder, log_root=log_root).by_id(pid) is not None


def test_delete_unknown_pair(folder, log_root, capsys):
    rc = main(["--log-root", str(log_root), "delete", "--folder", str(folder),
               "sha256:nope", "--yes"])
    assert rc == 1


# ===========================================================================
# delete-document (logical cascade)
# ===========================================================================


def test_delete_document_cascades(folder, log_root, capsys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    mem.remember(_sample_pair("p1", summary="from contract",
                              source_document="/inbox/contract.pdf"))
    mem.remember(_sample_pair("p2", summary="from contract",
                              source_document="/inbox/contract.pdf"))
    mem.remember(_sample_pair("p3", summary="from elsewhere",
                              source_document="/inbox/other.pdf"))

    rc = main(["--log-root", str(log_root), "delete-document", "--folder", str(folder),
               "/inbox/contract.pdf", "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deleted 2 pair" in out
    # Only the unrelated one remains.
    summaries = {p["problem"]["summary"] for p in WorkspaceMemory(folder, log_root=log_root).all_pairs()}
    assert summaries == {"from elsewhere"}


def test_delete_document_with_no_matches(folder, log_root, capsys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    mem.remember(_sample_pair("p1", summary="x", source_document="/inbox/other.pdf"))

    rc = main(["--log-root", str(log_root), "delete-document", "--folder", str(folder),
               "/inbox/nothing.pdf", "--yes"])
    assert rc == 1


# ===========================================================================
# purge (physical, irreversible)
# ===========================================================================


_PURGE_FLAGS = [
    "--legal-basis", "art_17_1_a",
    "--requester-ref", "cli-test-001",
    "--reason", "cli unit test",
]


@pytest.fixture
def _cli_purge_keys(tmp_path, monkeypatch):
    """B1: purge requires a controller keypair. Bind WORKSPACE_KEY_DIR
    to a tmp dir + initialise both keypairs."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "cli-keys"))
    from rvnd import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()
    yield


def test_purge_requires_explicit_flag(folder, log_root, monkeypatch, capsys, _cli_purge_keys):
    """Without --yes-i-mean-it the command refuses even on 'yes' input."""
    mem = WorkspaceMemory(folder, log_root=log_root)
    pid = mem.remember(_sample_pair("doomed", summary="will be purged"))
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))

    rc = main(["--log-root", str(log_root), "purge", "--folder", str(folder), pid,
               *_PURGE_FLAGS])
    # Purge demands the flag specifically; 'y' is not enough.
    assert rc == 2
    err = capsys.readouterr().err
    assert "yes-i-mean-it" in err
    # Pair survives.
    assert WorkspaceMemory(folder, log_root=log_root).by_id(pid) is not None


def test_purge_with_explicit_flag(folder, log_root, capsys, _cli_purge_keys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    pid = mem.remember(_sample_pair("doomed", summary="purge me"))

    rc = main(["--log-root", str(log_root), "purge", "--folder", str(folder),
               pid, "--yes-i-mean-it", *_PURGE_FLAGS])
    assert rc == 0
    out = capsys.readouterr().out
    assert "purged" in out
    # Physically gone — even all_pairs() doesn't see it.
    assert all(p["id"] != pid for p in WorkspaceMemory(folder, log_root=log_root).all_pairs())


def test_purge_unknown(folder, log_root, capsys, _cli_purge_keys):
    rc = main(["--log-root", str(log_root), "purge", "--folder", str(folder),
               "sha256:nope", "--yes-i-mean-it", *_PURGE_FLAGS])
    assert rc == 1


def test_purge_refuses_without_legal_basis_flag(folder, log_root, capsys, _cli_purge_keys):
    """B1: --legal-basis is now mandatory. Without it, exit 3."""
    mem = WorkspaceMemory(folder, log_root=log_root)
    pid = mem.remember(_sample_pair("doomed", summary="x"))

    rc = main(["--log-root", str(log_root), "purge", "--folder", str(folder), pid,
               "--yes-i-mean-it"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "legal-basis" in err


# ===========================================================================
# purge-document
# ===========================================================================


def test_purge_document_with_flag(folder, log_root, capsys, _cli_purge_keys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    mem.remember(_sample_pair("p1", summary="from contract",
                              source_document="/inbox/contract.pdf"))
    mem.remember(_sample_pair("p2", summary="from contract",
                              source_document="/inbox/contract.pdf"))

    rc = main(["--log-root", str(log_root), "purge-document", "--folder", str(folder),
               "/inbox/contract.pdf", "--yes-i-mean-it", *_PURGE_FLAGS])
    assert rc == 0
    out = capsys.readouterr().out
    assert "purged" in out
    assert WorkspaceMemory(folder, log_root=log_root).all_pairs() == []


def test_purge_document_requires_flag(folder, log_root, capsys, _cli_purge_keys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    mem.remember(_sample_pair("p1", summary="x", source_document="/inbox/contract.pdf"))

    rc = main(["--log-root", str(log_root), "purge-document", "--folder", str(folder),
               "/inbox/contract.pdf", *_PURGE_FLAGS])
    assert rc == 2
    err = capsys.readouterr().err
    assert "yes-i-mean-it" in err


# ===========================================================================
# audit-tail
# ===========================================================================


def test_audit_tail_after_remember_and_delete(folder, log_root, capsys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    pid = mem.remember(_sample_pair("p1", summary="x"))
    mem.delete(pid)

    rc = main(["--log-root", str(log_root), "audit-tail", "--folder", str(folder)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ingest" in out
    assert "delete" in out
    assert pid in out


def test_audit_tail_respects_limit(folder, log_root, capsys):
    mem = WorkspaceMemory(folder, log_root=log_root)
    for i in range(10):
        mem.remember(_sample_pair(f"p{i}", summary=f"x{i}"))

    rc = main(["--log-root", str(log_root), "audit-tail", "--folder", str(folder),
               "--limit", "3"])
    assert rc == 0
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) == 3


# ===========================================================================
# Folder resolution
# ===========================================================================


def test_missing_folder_returns_3(log_root, capsys, monkeypatch):
    monkeypatch.delenv("WORKSPACE_FOLDER_CONTEXT", raising=False)
    rc = main(["--log-root", str(log_root), "list"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "folder context" in err.lower()


def test_env_var_provides_folder(folder, log_root, capsys, monkeypatch):
    """If --folder is omitted, the env var is the fallback."""
    monkeypatch.setenv("WORKSPACE_FOLDER_CONTEXT", str(folder))
    rc = main(["--log-root", str(log_root), "list"])
    assert rc == 0


# ===========================================================================
# Asymmetric rule still holds at the CLI layer
# ===========================================================================


def test_cli_respects_asymmetric_rule(tmp_path, log_root, capsys):
    """`list --folder /acme` sees pairs from /acme/HR/; `list --folder /acme/Engineering/`
    does NOT see those pairs."""
    acme = tmp_path / "acme"
    hr = tmp_path / "acme" / "HR"
    eng = tmp_path / "acme" / "Engineering"
    for p in (acme, hr, eng):
        p.mkdir(parents=True)

    WorkspaceMemory(hr, log_root=log_root).remember(_sample_pair("hr1", summary="hr pair"))

    # /acme/ sees the HR pair.
    rc = main(["--log-root", str(log_root), "list", "--folder", str(acme)])
    assert rc == 0
    assert "hr pair" in capsys.readouterr().out

    # /acme/Engineering/ does NOT see the HR pair.
    rc = main(["--log-root", str(log_root), "list", "--folder", str(eng)])
    assert rc == 0
    assert "hr pair" not in capsys.readouterr().out
