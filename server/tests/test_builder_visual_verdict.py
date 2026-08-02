from __future__ import annotations

import hashlib
import json
import struct

from workspaces.release import visual_verdict as POLICY



def test_canonical_digest_is_order_independent():
    render_a = {
        "genre": "rack", "mode": "2d", "path": "a.png", "sha256": "a" * 64,
        "viewport": "1440x1000", "command": "render rack",
    }
    render_b = {
        "genre": "synth", "mode": "2d", "path": "b.png", "sha256": "b" * 64,
        "viewport": "1440x1000", "command": "render synth",
    }
    assert POLICY._canonical_digest("abc", [render_a, render_b]) == (
        POLICY._canonical_digest("abc", [render_b, render_a]))


def test_png_dimensions_rejects_non_png():
    try:
        POLICY._png_dimensions(b"not a png")
    except ValueError as exc:
        assert "not a PNG" in str(exc)
    else:
        raise AssertionError("non-PNG accepted")


def test_inside_rejects_escape(tmp_path):
    try:
        POLICY._inside(tmp_path.resolve(), "../escape.png")
    except ValueError as exc:
        assert "escapes builder root" in str(exc)
    else:
        raise AssertionError("path escape accepted")


def test_verify_binds_rvnd_verdict_to_commit_and_render_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(POLICY, "_git_commit", lambda _root: "candidate")
    renders = []
    for index, (genre, mode) in enumerate(sorted(POLICY.REQUIRED_VIEWS)):
        relpath = f"render-{index}.png"
        data = (
            b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", 13)
            + b"IHDR"
            + struct.pack(">II", 1440, 1000)
            + b"\x08\x06\x00\x00\x00"
            + b"x" * 5000
        )
        (tmp_path / relpath).write_bytes(data)
        renders.append({
            "genre": genre, "mode": mode, "path": relpath,
            "sha256": hashlib.sha256(data).hexdigest(),
            "viewport": "1440x1000", "command": f"render {genre} {mode}",
        })
    digest = POLICY._canonical_digest("candidate", renders)
    evidence = {
        "commit": "candidate",
        "date": "2026-07-25",
        "disposition": "pass",
        "renders": renders,
        "findings": [],
        "authority": {
            "owner": "RVND",
            "policy": POLICY.POLICY,
            "verdict": "GO",
            "input_digest": digest,
            "audit_triple": {
                "subject": "RVND",
                "predicate": "GO",
                "object": POLICY.POLICY,
                "input_digest": digest,
            },
        },
    }
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    errors, actual_digest = POLICY.verify(evidence_path, tmp_path.resolve())
    assert errors == []
    assert actual_digest == digest

    evidence["renders"][0]["sha256"] = "0" * 64
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    errors, _ = POLICY.verify(evidence_path, tmp_path.resolve())
    assert any("sha256 mismatch" in error for error in errors)
    assert any("input_digest" in error for error in errors)
