# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Extension seams: access check + policy resolver. No-ops in core; an overlay
(tenant layer) overrides them. Core behaviour unchanged when not overridden."""
import os
from pathlib import Path

from rvnd import cli, workspace_hooks
from rvnd.workspace_orchestrate import ask_workspace
from rvnd.workspace_contract import describe_workspace

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


def _ingest(folder: Path, name: str, text: str, lr: Path) -> None:
    (folder / "Inbox").mkdir(parents=True, exist_ok=True)
    f = folder / "Inbox" / name
    f.write_text(text)
    cli.main(["--log-root", str(lr), "ingest", str(f), "--folder", str(folder)])


def test_access_check_seam_denies_then_resets(tmp_path):
    lr = tmp_path / "log"
    c = tmp_path / "c"
    _ingest(c, "a.txt", "x", lr)
    try:
        workspace_hooks.set_access_check(lambda actor, workspace: False)   # overlay: deny all
        r = ask_workspace("hi", c, log_root=lr)
        assert r["ok"] is False
        assert "access denied" in r["error"]
    finally:
        workspace_hooks.reset_hooks()
    # default restored: not access-denied (may still lack a model tier)
    r2 = ask_workspace("hi", c, log_root=lr)
    assert "access denied" not in (r2.get("error") or "")


def test_policy_resolver_seam_overrides_context(tmp_path):
    lr = tmp_path / "log"
    c = tmp_path / "c"
    _ingest(c, "a.txt", "x", lr)

    class _FakePolicy:
        oversight_default_level = "manual"
        privacy_lock_enabled = True
        lock_mode = "clean-room"

    try:
        workspace_hooks.set_policy_resolver(lambda folder: _FakePolicy())
        cc = describe_workspace(c, depth=0, log_root=lr)
        assert cc.context["oversight"] == "manual"
        assert cc.governance["oversight"] == "manual"
    finally:
        workspace_hooks.reset_hooks()

    # default restored: real policy again (default oversight is "approve")
    cc2 = describe_workspace(c, depth=0, log_root=lr)
    assert cc2.context["oversight"] != "manual" or cc2.context["oversight"] == "approve"
