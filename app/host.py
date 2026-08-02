#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Static hosting half of the rvnd app server — zero `workspaces` imports.

Serves the shell pages (console, classic patchbay, sign-off widget),
composes the classic page with its panel pack
(panel-mount-contract.md §4), and serves the self-hosted fonts and shared
units. This is the half `repo-topology.md` names as moving to
`loomground-patchbay` at extraction: nothing here imports `workspaces`, so
it can be lifted out with no code left behind. `serve.py` imports
`HostRoutes` and layers the `/tool` bridge, `/whoami`, and
`/decision/respond` — the routes that do import `workspaces` — on top.
"""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import urlparse

INDEX = Path(__file__).with_name("src") / "index.html"
# The one local panel pack (panel-mount-contract.md §3.1/§4): a directory of
# panel bundles plus one manifest, composed into the classic page at serve
# time. contract_version's major must match what this shell understands —
# an unknown major refuses the whole pack, never a silent partial mount.
PANELS_DIR = Path(__file__).with_name("src") / "panels"
PACK_MANIFEST = PANELS_DIR / "pack.json"
_SUPPORTED_PACK_MAJOR = "1"
# Shell-chrome bundles: the shell's own furniture (About, Help, the
# workspace switcher, ...), not plane-provided domain content — so unlike
# panels there is no manifest, no version gate, no refusal path. Every
# app/src/shell/*.js file is inlined unconditionally, in name order (a fixed
# order only for determinism; the functions are plain globals, so load order
# never affects correctness — everything runs after the whole page loads).
SHELL_DIR = Path(__file__).with_name("src") / "shell"
# The sign-off widget: the delegate's own page. It renders one action-link
# token's decision and posts one signature; the link token is the working
# credential (verified server-side per call), the bridge injects only the
# transport wiring every served page carries.
WIDGET = Path(__file__).with_name("src") / "signoff.html"
# the five-widget front door (new default at /); the classic console stays at
# /classic, unchanged and reachable.
CONSOLE = Path(__file__).with_name("src") / "console.html"
FONTS_DIR = Path(__file__).with_name("src") / "fonts"
# Shared front-door logic both shells import (the observable store, and later
# widget units). ES modules, served raw — no bridge injection, they carry no head.
UNITS_DIR = Path(__file__).with_name("src") / "units"

# Self-hosted webfonts, served by exact name only — no directory listing, no
# path resolution, so a crafted /fonts/ URL cannot reach anything else.
_FONT_FILES: frozenset[str] = frozenset({
    "space-grotesk-latin.woff2",
    "ibm-plex-mono-400-latin.woff2",
    "ibm-plex-mono-500-latin.woff2",
    "ibm-plex-mono-600-latin.woff2",
})

# Shared units, served by exact name only — same exact-name allowlist idiom as
# the fonts above, so a crafted /units/ URL cannot reach anything else.
_UNIT_FILES: frozenset[str] = frozenset({
    "state.mjs",
    "say.mjs",
    "verdict.mjs",
    "patchbay.mjs",
    "run.mjs",
    "matrix.mjs",
})

# Hostnames that count as "this machine". A request whose Host header or Origin
# names anything else is a cross-origin / DNS-rebinding attempt and is refused.
_LOCAL_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_loopback_ip(ip: str) -> bool:
    return ip == "::1" or ip == "::ffff:127.0.0.1" or ip.startswith("127.")


def _host_is_local(host_header: str) -> bool:
    """The Host header host part must be a loopback name (anti-DNS-rebinding:
    a rebinding attack resolves attacker.com→127.0.0.1 but the Host stays
    attacker.com)."""
    h = (host_header or "").strip()
    if h.startswith("["):                       # [::1]:port
        h = h[1:].split("]")[0]
    else:
        h = h.rsplit(":", 1)[0] if h.count(":") == 1 else h
    return h in _LOCAL_HOSTS


def _origin_is_local(origin_header: str | None) -> bool:
    """A POST whose Origin names another site is cross-origin (CSRF) and refused.
    Absent Origin (non-browser clients, some same-origin fetches) is allowed —
    the Host + loopback checks still apply."""
    if not origin_header:
        return True
    try:
        host = urlparse(origin_header).hostname
    except Exception:
        return False
    return host in _LOCAL_HOSTS


def _load_pack() -> tuple[list[dict], str | None]:
    """Read the one local panel pack. Returns (panels, refusal) — panels is
    empty and refusal names the reason when the pack fails validation
    (panel-mount-contract.md §3.1): fail-closed, never a silent partial
    mount. No manifest on disk is not a refusal — an empty pack composes to
    the shell alone."""
    if not PACK_MANIFEST.exists():
        return [], None
    try:
        manifest = json.loads(PACK_MANIFEST.read_text())
    except Exception as e:
        return [], f"pack manifest is not valid JSON: {e}"
    if manifest.get("format") != "lg-panel-pack":
        return [], "pack manifest is missing the 'lg-panel-pack' format marker"
    major = str(manifest.get("contract_version", "")).split(".")[0]
    if major != _SUPPORTED_PACK_MAJOR:
        return [], (f"pack declares contract_version major {major!r}; "
                    f"this shell understands major {_SUPPORTED_PACK_MAJOR!r}")
    return manifest.get("panels", []), None


def compose_classic() -> str:
    """The reference composition (panel-mount-contract.md §4): the shell
    document, every app/src/shell/*.js chrome bundle inlined unconditionally
    (name order), then window.__PANEL_MANIFEST__ and each pack bundle
    inlined as a <script> block before </body>, in manifest order — the
    same idiom _send_html's head_inject hook uses to let a bridge inject
    before </head>. A pack that fails validation is refused: the manifest
    and bundles are never injected, and the page shows why instead of
    mounting silently short. Shell chrome has no such refusal path — it
    isn't plane-provided, so there's nothing to validate or gate.
    """
    html = INDEX.read_text()
    if SHELL_DIR.is_dir():
        # Must land BEFORE the shell's own main <script> block, not near
        # </body> — that block's window.X=X export line runs synchronously
        # at parse time and references shell-chrome functions directly (not
        # just inside later event handlers), so they must already exist as
        # globals by the time it executes. <script> tags run in document
        # order; function declarations are hoisted only within their own
        # tag, not across separate ones.
        shell_scripts = "".join(
            "<script>\n" + f.read_text() + "\n</script>\n"
            for f in sorted(SHELL_DIR.glob("*.js"))
        )
        html = html.replace("<script>", shell_scripts + "<script>", 1)
    panels, refusal = _load_pack()
    if refusal:
        banner = ("<script>document.addEventListener('DOMContentLoaded',function(){"
                   "var b=document.createElement('div');"
                   "b.style.cssText='position:fixed;top:0;left:0;right:0;z-index:99999;"
                   "background:#5a1f1f;color:#fff;font:12px monospace;padding:8px 12px';"
                   f"b.textContent={json.dumps('panel pack refused: ' + refusal)};"
                   "document.body.prepend(b);});</script>\n")
        return html.replace("</body>", banner + "</body>", 1)
    bundle_scripts = ""
    for p in panels:
        entry = p.get("entry", "")
        # Exact-name resolution only, inside the pack directory — the same
        # allowlist idiom as /fonts and /units, so a pack entry never
        # resolves outside app/src/panels/. A skipped entry (malformed path
        # or missing file) is not injected, so its bundle never registers;
        # the shell's boot-time check (patchbayVerifyBoot) then names it in a
        # visible refusal, so the skip is surfaced, not silent (§3.1).
        if "/" in entry or "\\" in entry or ".." in entry:
            continue
        bundle_path = PANELS_DIR / entry
        if not bundle_path.is_file():
            continue
        bundle_scripts += "<script>\n" + bundle_path.read_text() + "\n</script>\n"
    inject = ("<script>window.__PANEL_MANIFEST__=" + json.dumps(panels) + ";</script>\n"
              + bundle_scripts
              + "<script>patchbayMountMenus();patchbayVerifyBoot();</script>\n")
    return html.replace("</body>", inject + "</body>", 1)


def _deployed_bind() -> str:
    """The non-loopback bind address, or empty. Set via RVND_BIND for
    container/team deployment. Fail-closed pairing (enforced by the bridge
    in make_server): leaving loopback without a declared principal header is
    refused — the console leaves the machine only behind a verified-identity
    proxy."""
    import os
    bind = (os.environ.get("RVND_BIND") or "").strip()
    if not bind or bind in _LOCAL_HOSTS or bind.startswith("127."):
        return ""
    return bind


class HostRoutes:
    """Mixin: static page hosting, request guards, response primitives.
    Zero `workspaces` import. Mixed with `http.server.BaseHTTPRequestHandler`
    by a concrete handler (serve.py's `H`), which also supplies
    `head_inject()` to add its own transport wiring — HostRoutes injects
    nothing on its own, so a consumer with no bridge at all still serves
    plain pages.
    """

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        # close each connection: this bridge is one request at a time, and a
        # kept-alive socket can leave a client's fetch (newer undici) awaiting
        # reuse that never comes. Content-Length + close = the response ends here.
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b)

    def _guard(self) -> bool:
        """Refuse anything that isn't a same-machine, same-origin request.
        Returns True if the request may proceed; otherwise sends 403 and
        returns False. Defends the local host against another host (if the
        server were ever bound non-loopback), DNS rebinding (Host header),
        and a malicious web page driving it (Origin). A deliberate
        non-loopback deployment (RVND_BIND, only grantable together with a
        declared principal header, enforced by the bridge) moves the
        boundary to the fronting proxy: the loopback and Host checks step
        aside there, the Origin check stays."""
        if not _deployed_bind():
            if not _is_loopback_ip(self.client_address[0]):
                self._send(403, {"error": "refused: non-loopback client"})
                return False
            if not _host_is_local(self.headers.get("Host", "")):
                self._send(403, {"error": "refused: non-local Host header "
                                          "(possible DNS rebinding)"})
                return False
        origin = self.headers.get("Origin")
        if _deployed_bind():
            # same-origin behind the proxy: the Origin's host must be the
            # host this request was addressed to
            ok = True
            if origin:
                try:
                    o_host = urlparse(origin).hostname or ""
                except Exception:
                    o_host = ""
                h = (self.headers.get("Host", "") or "").rsplit(":", 1)[0]
                ok = bool(o_host) and o_host == h
            if not ok:
                self._send(403, {"error": "refused: cross-origin request"})
                return False
            return True
        if not _origin_is_local(origin):
            self._send(403, {"error": "refused: cross-origin request"})
            return False
        return True

    def head_inject(self) -> str:
        """Markup a bridge (or other consumer) wants injected before
        </head> on every served page — e.g. rvnd's /tool transport wiring.
        HostRoutes itself injects nothing; this is the seam a bridge
        subclass overrides. Returning "" changes no served byte."""
        return ""

    def _send_html(self, html: str):
        """Serve a composed HTML string, with head_inject()'s markup
        spliced in before </head> if the consumer supplied any."""
        inject = self.head_inject()
        if inject:
            html = html.replace("</head>", inject + "</head>", 1)
        return self._send(200, html.encode(), "text/html; charset=utf-8")

    def _send_page(self, file: Path):
        """Serve one of the app's static pages, unchanged from disk."""
        return self._send_html(file.read_text())

    def handle_host_get(self) -> bool:
        """Serve a static GET route if `self.path` names one. Returns True
        when handled (the caller must not also respond); False for anything
        that isn't a host route, so the caller can fall through to its own
        routes (rvnd's /whoami) before 404ing."""
        if self.path in ("/", "/console", "/console.html"):
            # the new default front door; falls back to the classic console
            # until console.html exists, so the app never 404s at /.
            self._send_page(CONSOLE if CONSOLE.exists() else INDEX)
            return True
        if self.path in ("/classic", "/index.html"):
            self._send_html(compose_classic())
            return True
        if urlparse(self.path).path in ("/sign", "/signoff"):
            # the widget page reads ?token= (and ?folder=) itself; the
            # server never echoes a link token, it only serves the page
            self._send_page(WIDGET)
            return True
        if self.path.startswith("/fonts/"):
            name = self.path[len("/fonts/"):]
            if name in _FONT_FILES:
                self._send(200, (FONTS_DIR / name).read_bytes(), "font/woff2")
            else:
                self._send(404, {"error": "not found"})
            return True
        if self.path.startswith("/units/"):
            name = self.path[len("/units/"):]
            if name in _UNIT_FILES:
                self._send(200, (UNITS_DIR / name).read_bytes(),
                           "text/javascript; charset=utf-8")
            else:
                self._send(404, {"error": "not found"})
            return True
        return False
