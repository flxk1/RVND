# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Proof: a USER CAN DROP A DOCUMENT INTO THE WORKSPACE AND GET A TWIN — via MCP.

The MCP `policy_ingest` op now accepts a `path` to a file in the workspace folder (txt / pdf /
docx, read with format_extractors), sandboxed so it cannot read arbitrary paths off the box.
This drives it end-to-end through the MCP tool for a .txt and a generated .pdf, applies the
result to the chain, and confirms the sandbox refuses an outside path.
"""
from __future__ import annotations

from pathlib import Path

from rvnd import mcp_server as M

POLICY = "Generated content must be approved by a moderator."


def _make_pdf(path: Path, text: str) -> None:
    """A minimal one-page PDF carrying `text` (pypdf-readable). Self-contained — no deps."""
    t = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\nBT /F1 12 Tf 72 720 Td (%b) Tj ET\nendstream" % (len(t) + 26, t),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offs = []
    for i, body in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n%b\nendobj\n" % (i, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    path.write_bytes(out)


def _ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "org"; f.mkdir()
    M.workspace_workspace("add", {"folder_context": str(f)})
    return f


def _ingest_path(folder, path):
    return M.workspace_workflow("policy_ingest",
                                {"folder_context": str(folder), "path": str(path)})


def test_txt_file_ingest_via_mcp(tmp_path, monkeypatch):
    f = _ws(tmp_path, monkeypatch)
    (f / "policy.txt").write_text(POLICY, encoding="utf-8")
    twin = _ingest_path(f, "policy.txt")              # relative path inside the workspace
    assert twin.get("ok"), twin
    assert any(r["kind"] == "generated_content" and r["by"] == "moderator"
               for r in twin["patch"].get("reservations", []))
    # and it applies to the chain via MCP
    res = M.workspace_workflow("patch_apply",
                               {"folder_context": str(f), "actor": "alex", "netlist": twin["netlist"]})
    assert res.get("ok")


def test_pdf_file_ingest_via_mcp(tmp_path, monkeypatch):
    f = _ws(tmp_path, monkeypatch)
    _make_pdf(f / "policy.pdf", POLICY)
    twin = _ingest_path(f, "policy.pdf")
    assert twin.get("ok"), twin
    assert any(r["kind"] == "generated_content" for r in twin["patch"].get("reservations", [])), twin


def test_sandbox_refuses_path_outside_workspace(tmp_path, monkeypatch):
    f = _ws(tmp_path, monkeypatch)
    outside = tmp_path / "secret.txt"               # sibling of the workspace, not inside it
    outside.write_text("Secrets must be approved by nobody.", encoding="utf-8")
    twin = _ingest_path(f, str(outside))
    assert twin.get("ok") is False
    assert any("outside the workspace" in e for e in twin.get("errors", []))


def test_missing_file_is_a_clean_error(tmp_path, monkeypatch):
    f = _ws(tmp_path, monkeypatch)
    twin = _ingest_path(f, "does_not_exist.txt")
    assert twin.get("ok") is False and any("not a file" in e for e in twin.get("errors", []))
