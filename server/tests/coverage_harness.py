# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Executable contract-coverage harness — the engine.

Every operation the capability register calls *supported* must be proven
callable: invoked from a clean candidate through each channel its status claims
(UI-bound server route, MCP/CLI route, gateway route) with a valid fixture that
returns a schema-conforming success, and with an invalid fixture that is
refused. Callability is unproven until demonstrated; the predicates here are
fail-closed, so a generic error, an unknown-op reply, a swallowed exception, a
help/catalogue echo, or an unrelated-handler hit counts as NOT callable.

This module is the engine (channels, predicates, disposable workspaces, the
fixture contract). The per-operation fixtures live in ``coverage_fixtures.py``.
``test_capability_coverage.py`` drives the matrix and reconciles it to the
register and the live catalogue.
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))


# --------------------------------------------------------------------------
# Channels — invoke one (facade, op) through a named transport
# --------------------------------------------------------------------------

def _invoke_mcp(facade: str, op, params: dict) -> Any:
    import workspaces.mcp_server as M
    fn = getattr(M, facade)
    return fn(op, params) if op is not None else fn(**params)


def _invoke_gateway(facade: str, op, params: dict) -> Any:
    from workspaces import gateway as GW
    if op is None:                       # standalone tool exposed on the gateway
        return getattr(GW, facade)(**params)
    return GW._dispatch(facade, op, params)


def _invoke_ui(facade: str, op, params: dict) -> Any:
    import serve
    args = {"op": op, "params": params} if op is not None else dict(params)
    return serve._facade_call(facade, args)


CHANNELS: dict[str, Callable[[str, Any, dict], Any]] = {
    "mcp": _invoke_mcp,
    "gateway": _invoke_gateway,
    "ui": _invoke_ui,
}


# --------------------------------------------------------------------------
# Fail-closed verdicts on a response
# --------------------------------------------------------------------------

_UNKNOWN_MARKERS = ("unknown op", "not available over the gateway", "missing param")


def is_success(resp: Any) -> bool:
    """A real success: a dict without an error, not ok=False, not an unknown-op
    or missing-param reply, not a bare help/catalogue echo; OR a list/tuple
    result (some read ops return a sequence). Anything else is not a success."""
    if isinstance(resp, (list, tuple)):
        return True                       # a sequence is a legitimate result shape
    if not isinstance(resp, dict):
        return False
    if resp.get("ok") is False:
        return False
    err = resp.get("error")
    if err:
        return False
    if "valid_ops" in resp:
        return False
    # a bare catalogue echo (only ops / gateway_meta keys) is not a real op result
    if set(resp) - {"gateway_meta"} == {"ops"}:
        return False
    return True


def is_refusal(resp: Any) -> bool:
    """A controlled refusal: a dict carrying an error or ok=False. A raw
    exception is handled by the caller (a validation exception also refuses);
    a success is never a refusal."""
    if not isinstance(resp, dict):
        return False
    return bool(resp.get("error")) or resp.get("ok") is False


# --------------------------------------------------------------------------
# Disposable workspace — every fixture gets a throwaway folder + keys + log
# --------------------------------------------------------------------------

@dataclass
class WS:
    root: Path
    folder: str
    log_root: str


def new_workspace(register: bool = True) -> WS:
    tmp = Path(tempfile.mkdtemp(prefix="cov_"))
    os.environ["WORKSPACE_KEY_DIR"] = str(tmp / "keys")
    os.environ["WORKSPACE_L0_LOG_ROOT"] = str(tmp / "log")
    folder = tmp / "ws"
    folder.mkdir()
    log_root = str(tmp / "log")
    if register:
        try:
            from workspaces.workspace_registry import add_known_workspace
            add_known_workspace(folder, log_root=Path(log_root))
        except Exception:
            pass
    return WS(root=tmp, folder=str(folder), log_root=log_root)


def with_parties(ws: WS, agents=("svc-bot",), humans=("operator",)) -> WS:
    from workspaces.parties import register_party
    for a in agents:
        register_party(ws.folder, a, "agent", log_root=ws.log_root)
    for h in humans:
        register_party(ws.folder, h, "human", log_root=ws.log_root)
    return ws


# --------------------------------------------------------------------------
# Fixture contract
# --------------------------------------------------------------------------

@dataclass
class Fixture:
    """How to drive one (facade, op).

    setup(ws) prepares the disposable workspace and returns a context dict.
    valid(ws, ctx) -> params reaching a schema-conforming success.
    invalid(ws, ctx) -> params that must be refused (bad/absent required input,
    unauthorized actor, wrong scope). check(resp) optionally tightens success to
    named keys. invalid_check(resp) tightens the refusal — the default demands an
    error/ok=False dict, but a global read's refusal is "an unmapped principal
    sees nothing", so such ops set invalid_unmapped=True (the harness drives the
    invalid call under an unresolved principal) and an invalid_check that accepts
    an empty result. This scope refusal is a property of the serving surface
    (the console /tool bridge, the gateway), not of the trusted in-process facade
    — the raw facade read has no refusal path — so the harness asserts it on the
    ui/gateway channels and skips it on the mcp channel when a surface channel
    already proves it. mutating marks ops that must run only in a disposable ws."""
    valid: Callable[[WS, dict], dict]
    invalid: Callable[[WS, dict], dict] | None = None
    setup: Callable[[WS], dict] = lambda ws: {}
    check: Callable[[Any], bool] = lambda r: True
    invalid_check: Callable[[Any], bool] | None = None
    invalid_unmapped: bool = False
    no_invalid_domain: bool = False
    mutating: bool = False


def as_unmapped_principal():
    """Context manager: run under an unresolved request principal (a name that is
    not a registered party), so folder-scoped reads must return nothing."""
    from contextlib import contextmanager
    from workspaces.mcp_serving import set_request_principal, clear_request_principal

    @contextmanager
    def _ctx():
        set_request_principal("stranger\x40nowhere.example", None)
        try:
            yield
        finally:
            clear_request_principal()
    return _ctx()


def empty_result(resp: Any) -> bool:
    """A fail-closed 'sees nothing' refusal for a global read: an empty list, or
    a dict whose collection fields are all empty."""
    if isinstance(resp, (list, tuple)):
        return len(resp) == 0
    if isinstance(resp, dict):
        if resp.get("error") or resp.get("ok") is False:
            return True
        cols = [v for v in resp.values() if isinstance(v, (list, tuple))]
        return bool(cols) and all(len(v) == 0 for v in cols)
    return False
