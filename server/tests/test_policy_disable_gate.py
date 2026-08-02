# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""A protection may not be disabled over MCP without attribution + a reason.

The Policy drawer requires accepted_by + reason client-side, but governance must
hold server-side too: the MCP `disable` path must REFUSE an empty accepted_by or
reason rather than silently default to a system actor. Closes the Policy-drawer
build-loop blocker (mcp_impl defaulted accepted_by → silent disable)."""
from __future__ import annotations
import os, tempfile

import workspaces.mcp_server as M


def _ws():
    os.environ.setdefault("WORKSPACE_KEY_DIR", tempfile.mkdtemp())
    os.environ.setdefault("WORKSPACE_L0_LOG_ROOT", tempfile.mkdtemp())
    return tempfile.mkdtemp(prefix="disgate_")


def test_disable_refused_without_accepted_by_or_reason():
    ws = _ws()
    assert M.workspace_policy("snapshot", {"folder_context": ws})["lock_is_active"] is True

    # both missing → refused up front (declared required params)
    r = M.workspace_policy("disable", {"folder_context": ws, "dial": "lock"})
    assert "error" in r and not r.get("ok", False)
    assert M.workspace_policy("snapshot", {"folder_context": ws})["lock_is_active"] is True

    # accepted_by but blank reason → refused at the handler (no silent disable)
    r = M.workspace_policy("disable", {"folder_context": ws, "dial": "lock",
                                  "accepted_by": "alex", "reason": "   "})
    assert r.get("ok") is False and "reason" in r["error"]
    assert M.workspace_policy("snapshot", {"folder_context": ws})["lock_is_active"] is True

    # blank accepted_by → refused (never defaulted to a system actor)
    r = M.workspace_policy("disable", {"folder_context": ws, "dial": "lock",
                                  "accepted_by": "  ", "reason": "scratch data"})
    assert r.get("ok") is False
    assert M.workspace_policy("snapshot", {"folder_context": ws})["lock_is_active"] is True


def test_disable_succeeds_with_both_and_is_attributed():
    ws = _ws()
    r = M.workspace_policy("disable", {"folder_context": ws, "dial": "oversight",
                                  "accepted_by": "alex", "reason": "public-data scratch"})
    assert r.get("ok") is True and r["oversight_is_active"] is False
    assert r["accepted_by"] == "alex"          # attributed to the real acceptor, not a default
    assert M.workspace_policy("snapshot", {"folder_context": ws})["oversight_is_active"] is False
