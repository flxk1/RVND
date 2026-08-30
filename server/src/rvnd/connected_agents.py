# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Server-level registry of CONNECTED agents — those that completed the MCP
handshake with this RVND server, independent of any workspace.

Two different questions, kept apart:

  * the per-workspace govlive board answers "who is ADMITTED to act HERE" —
    a folder-scoped session on that workspace's signed chain;
  * this registry answers the prior question "who is CONNECTED to the server at
    all" — an agent is present the moment it handshakes, and a workspace (with
    its Privacy Lock) only enters when the agent tries to act on a folder.

Local-first + multi-process: each stdio MCP connection is its OWN process, so the
registry is a small directory of per-connection files under
``~/.workspace/agents/connected/``. Liveness is the connecting process itself — a
record whose PID is no longer alive is stale and self-heals out of the list; a
clean exit deregisters explicitly. Nothing here is folder-scoped and nothing is
signed: it is presence, not authority (authority stays the per-folder chain).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Optional


def _agents_dir(root: Optional[str] = None) -> Path:
    """The server-level connected-agents directory. Override via ``root`` (tests)
    or the ``WORKSPACE_AGENTS_DIR`` env; defaults beside keys/ and log/ under
    ``~/.workspace``."""
    if root:
        base = Path(root)
    else:
        env = os.environ.get("WORKSPACE_AGENTS_DIR")
        base = Path(env) if env else Path.home() / ".workspace" / "agents"
    return base / "connected"


def register_connection(*, agent: str, transport: str = "stdio",
                        pid: Optional[int] = None, now: Optional[float] = None,
                        session_id: Optional[str] = None,
                        root: Optional[str] = None) -> str:
    """Record a connected agent (post-handshake) and return its connection id.

    Captures the host session id (``CLAUDE_CODE_SESSION_ID``) as ``session_id``
    when the connecting process has one in its environment. This is the join
    key to the signed chain: the actor the PreToolUse hook records IS that same
    session id, so a live connection whose ``session_id`` equals a chain actor
    is that acting session's real presence. Absent (empty) when the host sets no
    such id — never fabricated.

    Best-effort: a registry hiccup must never break serving, so this never
    raises."""
    connid = secrets.token_hex(8)
    sid = (session_id if session_id is not None
           else os.environ.get("CLAUDE_CODE_SESSION_ID") or "")
    rec = {
        "connid": connid,
        "agent": (agent or "").strip() or "unnamed-agent",
        "transport": transport,
        "pid": int(pid if pid is not None else os.getpid()),
        "session_id": (sid or "").strip(),
        "connected_at": float(now if now is not None else time.time()),
    }
    try:
        d = _agents_dir(root)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{connid}.json").write_text(json.dumps(rec), encoding="utf-8")
    except Exception:
        pass
    return connid


def deregister_connection(connid: str, *, root: Optional[str] = None) -> None:
    """Remove a connection record on clean disconnect. Never raises."""
    try:
        (_agents_dir(root) / f"{connid}.json").unlink(missing_ok=True)
    except Exception:
        pass


_SID_RE = re.compile(r"CLAUDE_CODE_SESSION_ID=([0-9A-Za-z._-]+)")


def _pid_session_id(pid: int) -> Optional[str]:
    """Read a live process's ``CLAUDE_CODE_SESSION_ID`` from its own environment
    (``ps eww`` lists a process's env after its command). This is a READ of the
    real running process — the same session id the process would have written on
    connect — not a guess. Returns None when the tool is unavailable, the pid is
    gone, or the process carries no such id. Never raises."""
    if not pid:
        return None
    try:
        p = subprocess.run(
            ["ps", "eww", "-o", "command=", str(int(pid))],
            capture_output=True, text=True, timeout=3.0, check=False)
    except Exception:
        return None
    m = _SID_RE.search(p.stdout or "")
    return m.group(1) if m else None


def backfill_session_ids(*, root: Optional[str] = None) -> int:
    """Fill in ``session_id`` for existing connection records that lack one, by
    reading each live pid's ``CLAUDE_CODE_SESSION_ID`` from its process
    environment and writing it back into the record. This reflects the real
    running process, not a fabrication. Dead pids are left untouched (the list
    projection self-heals them out). Returns how many records were updated;
    never raises."""
    updated = 0
    try:
        d = _agents_dir(root)
        if not d.exists():
            return 0
        for f in d.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if (rec.get("session_id") or "").strip():
                continue
            pid = int(rec.get("pid", 0) or 0)
            if not pid or not _pid_alive(pid):
                continue
            sid = _pid_session_id(pid)
            if not sid:
                continue
            rec["session_id"] = sid
            try:
                f.write_text(json.dumps(rec), encoding="utf-8")
                updated += 1
            except Exception:
                pass
    except Exception:
        return updated
    return updated


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, just not ours
    except Exception:
        return True          # unknown platform quirk — don't falsely drop a live agent


def list_connected(*, now: Optional[float] = None, root: Optional[str] = None,
                   ttl_seconds: float = 86400.0) -> list[dict]:
    """Live connected agents, newest first. Records whose process is gone (a crash
    left the file behind) self-heal out, and anything older than ``ttl_seconds`` is
    dropped. Records that predate session-id capture, or whose id was not yet
    resolvable, are backfilled from their live process's own environment before
    the scan — so a live connection carries its real ``session_id`` join key.
    Read-only projection; never raises."""
    now = float(now if now is not None else time.time())
    out: list[dict] = []
    try:
        backfill_session_ids(root=root)
    except Exception:
        pass
    try:
        d = _agents_dir(root)
        if not d.exists():
            return []
        for f in d.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            pid = int(rec.get("pid", 0) or 0)
            if pid and not _pid_alive(pid):
                try:
                    f.unlink(missing_ok=True)      # self-heal a stale record
                except Exception:
                    pass
                continue
            if now - float(rec.get("connected_at", 0) or 0) > ttl_seconds:
                continue
            out.append(rec)
    except Exception:
        return out
    out.sort(key=lambda r: r.get("connected_at", 0), reverse=True)
    return out
