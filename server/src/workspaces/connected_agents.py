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
import secrets
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
                        root: Optional[str] = None) -> str:
    """Record a connected agent (post-handshake) and return its connection id.
    Best-effort: a registry hiccup must never break serving, so this never
    raises."""
    connid = secrets.token_hex(8)
    rec = {
        "connid": connid,
        "agent": (agent or "").strip() or "unnamed-agent",
        "transport": transport,
        "pid": int(pid if pid is not None else os.getpid()),
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
    dropped. Read-only projection; never raises."""
    now = float(now if now is not None else time.time())
    out: list[dict] = []
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
