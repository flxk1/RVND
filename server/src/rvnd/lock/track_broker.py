# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""track_broker — the request→track binding for the egress proxy.

A call that leaves through the egress proxy must declare which egress track
(a `Connector` with ``role="egress"``) is driving it, via the request header::

    X-Lock-Track: <connector_id>

Agents set it once with their SDK's default-headers mechanism; the proxy
(bound to a workspace folder — broker mode) resolves the declaration against
the folder's signed chain and injects the track's credential. The agent never
holds the key.

``bind_track`` is the whole binding, fail-closed at every step: the track must
be declared, exist, be an egress track, not carry ``floor="deny"``, carry a
``credential_ref``, and that reference must resolve. Each refusal reason maps
to a channel-strip lamp state: no cable /
unplugged / barred. ``floor="hold"`` does not refuse — it marks the binding so
the proxy consults a person on every forward, whatever the content scan says.

The resolved secret rides only in ``TrackBinding.secret`` (repr-suppressed),
for immediate injection at the forward boundary; it is never logged, audited,
or echoed in a refusal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

TRACK_HEADER = "X-Lock-Track"


@dataclass
class TrackBinding:
    """The outcome of binding one proxied request to its declared egress track."""

    ok: bool
    reason: str                            # secret-free; refusal reason when not ok
    connector_id: Optional[str] = None
    credential_ref: Optional[str] = None
    floor: str = "permit"
    hold: bool = False                     # floor="hold" — a person gates every forward
    secret: Optional[str] = field(default=None, repr=False)


def _refuse(reason: str, **kw) -> TrackBinding:
    return TrackBinding(ok=False, reason=reason, **kw)


def _display(cid: str) -> str:
    """A header-supplied id, made safe to echo in a refusal reason that rides in
    an HTTP status line: printable ASCII only, bounded length."""
    safe = "".join(ch if " " <= ch <= "~" else "?" for ch in cid)
    return safe[:64] + ("..." if len(safe) > 64 else "")


def bind_track(folder_context: str, connector_id: Optional[str], *,
               log_root: Optional[str] = None) -> TrackBinding:
    """Resolve a declared track against the folder's chain, fail-closed.

    Returns an ``ok`` binding carrying the resolved secret for immediate
    injection, or a refusal whose ``reason`` names the first failed check.
    Never raises on a bad declaration; never puts the secret in ``reason``.
    """
    cid = (connector_id or "").strip()
    if not cid:
        return _refuse(f"no track declared ({TRACK_HEADER} header required in broker mode)")

    disp = _display(cid)
    from . import host_deps
    host_deps.ensure_wired()
    if host_deps.list_connectors is None:
        return _refuse("track broker unavailable (host connectors hook not wired)")
    track = None
    for c in host_deps.list_connectors(folder_context, log_root=log_root):
        if c.get("connector_id") == cid:
            track = c
            break
    if track is None:
        return _refuse(f"unknown track '{disp}'", connector_id=cid)
    if track.get("role") != "egress":
        return _refuse(f"track '{disp}' is not an egress track", connector_id=cid)

    floor = (track.get("floor") or "permit").strip().lower()
    if floor == "deny":
        return _refuse(f"track '{disp}' floor is deny; egress barred",
                       connector_id=cid, floor=floor)

    ref = track.get("credential_ref")
    if not (ref or "").strip():
        return _refuse(f"track '{disp}' has no cable; cannot reach outside",
                       connector_id=cid, floor=floor)

    from .credential_resolver import resolve_secret
    secret = resolve_secret(ref)
    if secret is None:
        return _refuse(f"track '{disp}' is unplugged; credential does not resolve",
                       connector_id=cid, credential_ref=ref, floor=floor)

    return TrackBinding(ok=True, reason="", connector_id=cid, credential_ref=ref,
                        floor=floor, hold=(floor == "hold"), secret=secret)
