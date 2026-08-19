# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""broker_probe — ask the running egress proxy whether it holds a folder's plug.

The egress board must not say ``enforced`` on trust: a track's LLM egress is
enforced only while a broker-bound proxy is actually in the call path, for that
track's own folder. This probe is the attestation — it reads the proxy's local
health endpoint and compares the bound folder, fail-closed: an unreachable
proxy, a proxy without a bound folder, or a proxy bound to a *different*
folder all mean "not enforced here" (the board then says ``attested``).

Read-only; carries no request body and no credentials.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

_HEALTH_PATH = "/__lock_health__"
_DEFAULT_PORT = 8443


def _same_folder(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    try:
        return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
    except OSError:
        return False


def probe_broker(folder_context: str, *, proxy_url: Optional[str] = None,
                 timeout: float = 1.5) -> dict[str, Any]:
    """Whether a reachable egress proxy is broker-bound to ``folder_context``.

    ``proxy_url`` defaults to the local proxy on ``AGENT_TOOL_LOCK_PROXY_PORT``.
    Returns ``{"reachable", "bound_here"}``; every failure mode resolves to
    ``bound_here=False`` — the caller may upgrade a track to ``enforced`` only
    on an explicit True.
    """
    if proxy_url is None:
        port = os.environ.get("AGENT_TOOL_LOCK_PROXY_PORT", "") or _DEFAULT_PORT
        proxy_url = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(proxy_url.rstrip("/") + _HEALTH_PATH,
                                    timeout=timeout) as resp:
            health = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return {"reachable": False, "bound_here": False}
    bound_here = bool(health.get("broker_bound")) and _same_folder(
        health.get("broker_folder"), folder_context)
    return {"reachable": True, "bound_here": bound_here}
