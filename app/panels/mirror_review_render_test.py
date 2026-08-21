#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render gate for GUI-5 — the mirror review lifecycle in the Data drawer.

Boots serve.py, seeds a lock mirror with one span and opens a draft + one edit
revision (mirror_editor), then drives mirror_review_render.mjs which opens the
Data drawer, clicks "Show revision history" on that mirror, and asserts the real
edit revision renders. Closes the half-built B9 mirror-edit lifecycle in the UI.

  python3 app/mirror_review_render_test.py
"""
from __future__ import annotations
import os, sys, time, json, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="mirrev_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve  # noqa: E402
from rvnd import mirror_editor  # noqa: E402

F = os.path.join(tmp, "org")
LR = os.environ["WORKSPACE_L0_LOG_ROOT"]
LOCK = os.path.join(F, "mirrors", "lock")
os.makedirs(LOCK, exist_ok=True)
MIR = os.path.join(LOCK, "doc.cleaned.md")
Path(MIR).write_text("Hello [REDACTED:email] please confirm.\n", encoding="utf-8")
Path(os.path.join(LOCK, "doc.spans.json")).write_text(json.dumps({
    "schema": "workspace.mirror.spans/v1", "source_path": os.path.join(F, "doc.md"),
    "source_hash": "sha256:abc", "mirror_kind": "lock", "created_at": 0,
    "spans": [{"start": 6, "end": 17, "kind": "tier_b.pii_in_argument",
               "original_hash": "sha256:zzz", "replacement": "[REDACTED:email]",
               "span_id": "span:e1"}]}), encoding="utf-8")


def setup():
    import rvnd.mcp_server as S
    S.workspace_workspace("add", {"folder_context": F})
    # open a draft + apply one edit so the draft has a real revision history
    mirror_editor.open_revision(F, MIR, actor="alice", log_root=LR)
    mirror_editor.edit_span(F, MIR, "span:e1", "change_replacement",
                            actor="alice", reason="prefer typed marker",
                            new_replacement="[anonymized contact]", log_root=LR)


def main() -> int:
    setup()
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "mirror_review_render.mjs"), str(PORT), F, MIR],
                           capture_output=True, text=True, timeout=40)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
