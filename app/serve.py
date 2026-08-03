#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Loomground Rvnd app — universal local server (plain html/index, no build step).

Serves the HTML UI and bridges the browser to the SAME workspaces facades the MCP
server exposes, over localhost HTTP. This is what makes the html/index app work
in ANY browser, on any OS, with full read+write — independent of which MCP client
you use.

  python3 app/serve.py           # -> http://127.0.0.1:8799  (open in a browser)

Localhost-only by default. The browser POSTs {"tool","args"} to /tool; the
shim calls the in-process facade (workspace_x(op, params)) and returns its JSON.
The transport, not the logic: governance still lives in the facades.

Split with host.py (repo-topology.md, panel-mount-contract.md §"Composition"):
host.py serves pages and imports nothing from `workspaces`, so it is the half
that moves to loomground-patchbay at extraction. This file is the /tool
bridge — everything below that touches `workspaces` — and it stays in rvnd,
importing HostRoutes and layering its own routes on top.
"""
from __future__ import annotations
import hmac, json, secrets, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from host import (  # noqa: F401 — re-exported for `serve.<name>` callers/tests
    INDEX, PANELS_DIR, PACK_MANIFEST, _SUPPORTED_PACK_MAJOR, WIDGET, CONSOLE,
    FONTS_DIR, UNITS_DIR, _FONT_FILES, _UNIT_FILES, _LOCAL_HOSTS,
    _is_loopback_ip, _host_is_local, _origin_is_local, _load_pack,
    compose_classic, _deployed_bind, HostRoutes,
)

# Self-bootstrap: make `workspaces` importable no matter where serve.py is launched
# from. Prefer an installed `workspaces` (pip install rvnd/server); otherwise fall
# back to the in-tree runtime at rvnd/server/src. rvnd is self-contained: the
# runtime is vendored here (the parallel core), so this never reaches outside
# the rvnd repo.
from pathlib import Path
_HERE = Path(__file__).resolve()
try:
    import workspaces  # noqa: F401 — already installed
except ModuleNotFoundError:
    _src = _HERE.parent.parent / "server" / "src"   # rvnd layout: rvnd/server/src
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

from workspaces import mcp_server as _mcp_server  # noqa: E402 — needs the bootstrap above

# ---------------------------------------------------------------------------
# Tool allowlist. The bridge previously did getattr(mcp_server, tool) for
# ANY name — so any same-machine process (or a malicious web page via DNS
# rebinding) could invoke ANY module-level callable: internal helpers, bare
# write surfaces (write_file_to_folder), private readers (by_id, search,
# route_to_workspace). The local app is full read+write by design, so the allowlist
# is exactly the REGISTERED MCP tool surface — sourced from the canonical
# workspaces.mcp_server._DECLARED_TOOLS rather than a hand-kept copy, so it stays in
# lockstep with folds/additions: a folded tool (reachable only via its facade
# op, e.g. reason / workspace_cascade / workspace_shadow_scan) cannot be invoked directly,
# and a newly-declared tool (e.g. cross_workspace_read) is not silently unreachable.
# Governance still lives inside each facade (Lock, oversight, audit). Anything
# not registered is REFUSED (fail-closed).
# ---------------------------------------------------------------------------
ALLOWED_TOOLS: frozenset[str] = frozenset(_mcp_server._DECLARED_TOOLS)

# Trusted-front identity (rung 2): when WORKSPACE_PRINCIPAL_HEADER names a
# header, the bridge believes ONLY that header about who is calling — the
# fronting proxy (Tailscale serve, oauth2-proxy, an SSO gateway) verified it.
# Without the declared header name, incoming identity headers are ignored
# entirely (spoof-proof by default). WORKSPACE_PRINCIPAL_GROUPS_HEADER may
# name a second header carrying the principal's groups; with a declared
# identity map those groups auto-register the party (rung 3).
import os as _os


def _principal_config():
    h = (_os.environ.get("WORKSPACE_PRINCIPAL_HEADER") or "").strip()
    g = (_os.environ.get("WORKSPACE_PRINCIPAL_GROUPS_HEADER") or "").strip()
    return (h or None), (g or None)


def _proxy_proof_config():
    """Return the configured proxy proof header and shared secret.

    A secret is optional for loopback-only/local compatibility. A deployed
    bind requires it in ``make_server``. When configured, every route that
    consumes proxy identity verifies it before reading identity headers.
    """
    header = (_os.environ.get("WORKSPACE_PROXY_PROOF_HEADER")
              or "X-RVND-Proxy-Proof").strip()
    secret = (_os.environ.get("WORKSPACE_PROXY_SHARED_SECRET") or "").strip()
    return header, secret


def _proxy_proof_valid(headers) -> bool:
    _, secret = _proxy_proof_config()
    if not secret:
        return True
    header, _ = _proxy_proof_config()
    return hmac.compare_digest((headers.get(header) or "").strip(), secret)


def _resolve_principal(headers, args: dict):
    """None when trust mode is off; else (principal, party_or_None)."""
    header_name, groups_header = _principal_config()
    if not header_name:
        return None
    principal = (headers.get(header_name) or "").strip()
    if not principal:
        return ("", None)                     # declared trust, no principal
    folder = (args.get("folder_context")
              or (args.get("params") or {}).get("folder_context") or "")
    if not folder:
        return (principal, None)
    # The folder is request-controlled. Bind it to the registered workspace
    # allowlist before group auto-registration or party-log reads can touch a
    # store. Local single-operator mode never enters this proxy-only path.
    try:
        from workspaces.folder_context import resolve_folder_context
        folder = resolve_folder_context(folder)
    except Exception:
        return (principal, None)               # outside a trusted root: deny
    from workspaces.mcp_serving import _log_root
    groups = [s for s in (headers.get(groups_header) or "").replace(",", " ").split()
              if s] if groups_header else []
    try:
        if groups:
            from workspaces.identity_map import ensure_party
            ensure_party(folder, principal, groups, log_root=_log_root())
        from workspaces.parties import list_parties
        roster = list_parties(str(folder),
                              log_root=str(_log_root()) if _log_root() else None)
        # Status-aware match: a suspended or killed party (kill switch) must
        # not resolve — the proxy-verified actor privilege dies with the party.
        party = next((p.get("party_id") for p in roster.get("parties", [])
                      if p.get("party_id") == principal
                      and p.get("status", "active") == "active"), None)
    except Exception:                                   # noqa: BLE001
        party = None                                    # unresolved, never a crash
    return (principal, party)


def _facade_call(tool: str, args: dict):
    # N5: only the registered tool surface may be invoked — never arbitrary getattr.
    if tool not in ALLOWED_TOOLS:
        return {"error": f"tool {tool!r} is not callable over the local app bridge",
                "allowed": sorted(ALLOWED_TOOLS)}
    fn = getattr(_mcp_server, tool, None)
    if not callable(fn):
        # Registered name missing from the module → fail closed, don't probe.
        return {"error": f"tool {tool!r} is unavailable"}
    try:
        # Dispatch by the function's REAL signature, not by whether the caller
        # happened to send an 'op' key: op-based facades declare an ``op``
        # parameter; standalone tools (cross_workspace_read, workspace_orchestrate,
        # workspace_ask, server_info) take kwargs. This stops a stray 'op' in args
        # from misrouting a standalone tool into the (op, params) path.
        import inspect
        from workspaces.mcp_serving import apply_principal_to_params
        if "op" in inspect.signature(fn).parameters:
            # One seam for every op facade, hand-rolled ones included: under
            # a request principal, inject the resolved party as the actor and
            # refuse an unresolved principal any folder-addressed operation —
            # reads too, fail-closed. Facades that inspect per-op callables
            # (_op_call, workspace_dispatch) enforce the same rule again
            # inside; this covers the ones that dispatch by hand.
            params = args.get("params") or {}
            refused = apply_principal_to_params(None, params)
            if refused is not None:
                return refused
            return _stamp_help(fn(args.get("op"), params))
        refused = apply_principal_to_params(fn, args)
        if refused is not None:
            return refused
        return fn(**args)
    except Exception as e:  # surfaced as a clean error, never a 500 crash
        return {"error": f"{type(e).__name__}: {e}"}


def _stamp_help(result):
    """Stamp a help/catalogue response with per-op ``mutates`` so the console
    CLI knows which ops raise a confirm-card. Fail-closed in op_mutation: an
    unrecognised op is treated as a write. Non-help responses pass through."""
    if isinstance(result, dict) and isinstance(result.get("ops"), list):
        from workspaces.op_mutation import stamp
        stamp(result["ops"])
    return result


def make_handler(session_token: str):
    class H(HostRoutes, BaseHTTPRequestHandler):
        # Per-session credential for POST /tool. The served page carries it
        # (injected via head_inject below); a stray same-machine process does
        # not — so the full read+write tool surface is closed by
        # construction, not by trusting the loopback boundary alone.
        token = session_token

        def head_inject(self) -> str:
            """The bridge transport wiring HostRoutes leaves to a consumer:
            the /tool path and the per-session token this page must present
            on POST /tool."""
            return ("<script>window.__WORKSPACES_HTTP__='/tool';"
                    f"window.__WORKSPACES_TOKEN__={json.dumps(session_token)};"
                    "</script>")

        def do_GET(self):
            if not self._guard():
                return
            if self.handle_host_get():
                return
            if self.path == "/whoami" or self.path.startswith("/whoami?"):
                return self._whoami()
            return self._send(404, {"error": "not found"})

        def _whoami(self):
            """Who the server believes is calling, and which console units
            that party's role warrants (``?folder=`` names the workspace to
            resolve against). The units list drives chrome only — which
            frames the console renders; read scoping and the write gates
            stay the enforcement whatever a client draws. Fail-closed: an
            unmatched principal (or one with no mapped role) warrants no
            units; local single-operator mode warrants all of them."""
            from urllib.parse import parse_qs
            from workspaces.mcp_serving import CONSOLE_UNITS, units_for_role
            header_name, _ = _principal_config()
            if not header_name:
                return self._send(200, {"trust_mode": False, "principal": None,
                                        "party": None, "role": None,
                                        "units": list(CONSOLE_UNITS)})
            if not _proxy_proof_valid(self.headers):
                return self._send(403, {"error":
                    "refused: missing or invalid proxy identity proof"})
            principal = (self.headers.get(header_name) or "").strip()
            folder = (parse_qs(urlparse(self.path).query).get("folder")
                      or [""])[0].strip()
            party = role = None
            if principal and folder:
                try:
                    from workspaces.mcp_serving import _log_root
                    from workspaces.parties import list_parties
                    roster = list_parties(
                        str(folder),
                        log_root=str(_log_root()) if _log_root() else None)
                    row = next((p for p in roster.get("parties", [])
                                if p.get("party_id") == principal
                                and p.get("status", "active") == "active"),
                               None)
                    if row:
                        party = row.get("party_id")
                        role = row.get("role") or ""
                except Exception:                       # noqa: BLE001
                    party = role = None                 # unresolved, never a crash
            return self._send(200, {
                "trust_mode": True, "principal": principal or None,
                "party": party, "role": role,
                "units": units_for_role(role) if party else []})

        def do_POST(self):
            if not self._guard():
                return
            if self.path == "/decision/respond":
                return self._decision_respond()
            if self.path != "/tool":
                return self._send(404, {"error": "not found"})
            if _principal_config()[0] and not _proxy_proof_valid(self.headers):
                return self._send(403, {"error":
                    "refused: missing or invalid proxy identity proof"})
            # Fail-closed: the tool surface is full read+write, so a request
            # without the session token is refused before any dispatch. The
            # loopback/Host/Origin guards above stop remote and cross-origin
            # callers; the token stops a same-machine process.
            supplied = self.headers.get("X-Workspaces-Token", "")
            if not hmac.compare_digest(supplied, session_token):
                return self._send(403, {"error": "refused: missing or invalid "
                                                  "session token"})
            n = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, {"error": "bad json"})
            args = req.get("args", {})
            resolved = _resolve_principal(self.headers, args)
            if resolved is None:
                # Local single-operator mode has no identity proxy by design,
                # but the console still needs to open the short-lived session
                # capability that every governed Run requires.  The bridge's
                # unguessable session token plus loopback/Host/Origin guards
                # authenticate this local console session.  Bind only the
                # governance_open request, and only to the agent named by that
                # request; governance_open itself re-checks that the agent is
                # active and owns the approved lane.  Direct Python/MCP calls
                # remain unable to mint without an explicit request principal.
                params = args.get("params") or {}
                local_open = (req.get("tool") == "workspace_workflow"
                              and args.get("op") == "governance_open")
                party = (params.get("party") or "").strip() if local_open else ""
                if party:
                    from workspaces.mcp_serving import (
                        clear_request_principal,
                        set_request_principal,
                    )
                    set_request_principal(party, party, rung="loopback-session")
                    try:
                        return self._send(
                            200, _facade_call(req.get("tool", ""), args))
                    finally:
                        clear_request_principal()
                return self._send(200, _facade_call(req.get("tool", ""), args))
            from workspaces.mcp_serving import (clear_request_principal,
                                                set_request_principal)
            principal, party = resolved
            if not principal:
                return self._send(200, {"ok": False, "error":
                    "trust mode is declared but the request carries no"
                    " principal header — is the fronting proxy in place?"})
            set_request_principal(principal, party)
            try:
                return self._send(200, _facade_call(req.get("tool", ""), args))
            finally:
                clear_request_principal()

        def _decision_respond(self):
            """Decide-from-chat: a platform post-back (Teams Action.Submit,
            Slack interactivity, or normalised JSON) becomes the one governed
            write. The action-link token is the credential — the platform's
            own identity is never trusted; an unrecognisable or tokenless
            body is refused in words."""
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            from workspaces.decisions.projections import unwrap_response
            resp = unwrap_response(self.headers.get("Content-Type", ""), body)
            if not resp or not resp.get("link_token"):
                return self._send(200, {"ok": False, "error":
                    "unrecognisable response — a decision post-back needs its"
                    " action-link token"})
            from workspaces.mcp_impl import decision_record
            out = decision_record(
                str(resp.get("folder_context", "")),
                chosen_option_id=str(resp.get("chosen_option_id", "")),
                rationale=str(resp.get("rationale", "")),
                link_token=str(resp.get("link_token", "")),
                reconfirm_code=str(resp.get("reconfirm_code", "")))
            return self._send(200, out)

    return H


def _session_token() -> str:
    """The per-session token gating POST /tool. A caller may pin it via
    RVND_BRIDGE_TOKEN so both ends know it (test harnesses, a supervising
    parent); otherwise it is freshly generated per server."""
    return (_os.environ.get("RVND_BRIDGE_TOKEN") or "").strip() or secrets.token_urlsafe(32)


def make_server(host="127.0.0.1", port=8799):
    deployed = _deployed_bind()
    if deployed:
        import os
        if not (os.environ.get("WORKSPACE_PRINCIPAL_HEADER") or "").strip():
            raise SystemExit(
                "RVND_BIND leaves loopback but no WORKSPACE_PRINCIPAL_HEADER"
                " is declared — refusing to expose the bridge without a"
                " verified-identity proxy in front (see"
                " docs/concepts/bring-your-idp.md)")
        if not (os.environ.get("WORKSPACE_PROXY_SHARED_SECRET") or "").strip():
            raise SystemExit(
                "RVND_BIND leaves loopback but no"
                " WORKSPACE_PROXY_SHARED_SECRET is configured — refusing to"
                " trust an unauthenticated proxy identity header")
        host = deployed
    token = _session_token()
    try:
        srv = ThreadingHTTPServer((host, port), make_handler(token))
    except OSError as e:
        import errno
        if e.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"port {port} is already in use on {host} — RVND may already be"
                f" running. Free it (macOS/Linux: lsof -ti tcp:{port} | xargs"
                f" kill) or start on another port: python app/serve.py <PORT>")
        raise
    srv.session_token = token
    return srv


def _refuse_root() -> None:
    """Fail-closed startup guard — the broker never runs as uid 0 (deploy/
    Dockerfile creates the unprivileged ``rvnd-broker`` account).
    POSIX-only; a no-op where os.getuid doesn't exist (Windows doesn't have
    the uid-0 concept this guards against)."""
    getuid = getattr(_os, "getuid", None)
    if getuid is not None and getuid() == 0:
        raise SystemExit(
            "refusing to start as root (uid 0) — run as an unprivileged"
            " user (see deploy/Dockerfile's rvnd-broker user)")


def main():
    _refuse_root()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    srv = make_server(port=port)
    url = f"http://127.0.0.1:{port}"
    print(f"Rvnd app on {url}  (Ctrl-C to stop)")
    # open the browser for the user — best-effort, never fatal (headless/CI safe)
    if "--no-open" not in sys.argv:
        try:
            import webbrowser
            threading.Timer(0.7, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    srv.serve_forever()


if __name__ == "__main__":
    main()
