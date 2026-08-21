# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Connectors — the boundary ports of a Loomground patch, as chain events.

A connector is how work crosses the boundary of the patch: it comes IN through an
ingress connector, the result goes OUT through an egress connector, and a human is
reached through an oversight connector (email / ticket / message). Connectors are
first-class Loomground node kinds, registered via an Rvnd op, on the same signed
chain as parties and use cases — so the task spine (in → loop → human → out) is
governed by construction, not bolted on.

No new store: a connector is an event on the folder's chain; re-registering appends
a new version; projections replay latest-wins. Erasure/seal/tamper-evidence are
inherited from the substrate. Channels are free-text (the concrete binding —
Gmail, Jira, Slack — is an Rvnd integration via the MCP registry, and any actual
SEND is a permissioned external action, never auto).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .mutation_log import LogEvent, MutationLog

ROLES = ("ingress", "egress", "oversight")

#: Declared destination classes for egress tracks — the closed vocabulary the
#: egress board words enforcement by. Never inferred from the channel string.
DESTINATION_CLASSES = ("llm", "tool_api", "message", "file")


def _append(folder_context: str, actor: str, log_root, extra: dict) -> str:
    log = MutationLog(folder_context, log_root=log_root)
    return log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_context).expanduser().resolve()),
        pair_id=f"connector:{extra.get('connector_id', '')}",
        channel="system",
        actor=actor,
        extra=extra,
    ))


def register_connector(
    folder_context: str,
    *,
    connector_id: str,
    role: str,
    channel: str,
    use_cases: Optional[list[str]] = None,
    name: str = "",
    tags: Optional[list[str]] = None,
    floor: str = "permit",
    group: str = "",
    credential_ref: Optional[str] = None,
    destination_class: str = "",
    tool_ref: Optional[dict[str, Any]] = None,
    actor: str = "user",
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Register (or re-version) a connector. role ∈ ingress|egress|oversight;
    channel is the concrete pipe (email/ticket/message/api/drive/…); use_cases
    are the use-case ids it links to (ingress feeds them; oversight reaches a
    human for them; egress is the return off master). ``tags`` are the NEUTRAL
    data-lineage categories this connector STAMPS on tokens flowing through it
    (origin/jurisdiction/synthetic/taint) — the connector-derived half of the tag
    data-lens; a guard (`tags contains <t>`) acts on them, but WHICH tags exist and
    what they trigger is policy. Fail-closed on role."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    if not (connector_id or "").strip():
        raise ValueError("connector_id required")
    if not (channel or "").strip():
        raise ValueError("a connector needs a channel")
    # Per-channel POLICY: a self-governance floor this channel imposes on the use
    # cases it links — the minimum strictness, in the canonical tri-state. "permit"
    # (default) = no floor; "hold" = this channel always needs a person; "deny" = this
    # channel is barred. Honored strictest-wins in federated_decision. Fail-closed:
    # an unrecognised floor is rejected (never silently treated as permit).
    _floor = (floor or "permit").strip().lower()
    if _floor not in ("permit", "hold", "deny"):
        raise ValueError(f"floor must be permit|hold|deny, got {floor!r}")
    # Access binding (per-track, step 4): the egress track's credential is stored
    # as a *reference only* — never the secret.
    # It is meaningful only on an egress track (the one that crosses the wall) and
    # must be a known-scheme ref (env:/keydir:/oidc:/spiffe:). Fail-closed on both:
    # a ref on a non-egress role, or a malformed ref, is rejected — never stored as
    # a silent no-op that would read as "no cable" when the author meant to arm one.
    _cred = (credential_ref or "").strip() or None
    if _cred is not None:
        if role != "egress":
            raise ValueError("credential_ref is only valid on an egress connector")
        from .lock import is_valid_ref
        if not is_valid_ref(_cred):
            raise ValueError(
                "credential_ref must be a known-scheme reference "
                "(env:/keydir:/oidc:/spiffe:), never a raw secret")
    # Declared destination class (egress only): which side of the wall this
    # track reaches — the authored axis enforcement is worded by. A closed
    # vocabulary, never guessed from the free-text channel; unset stays
    # undeclared and is shown as such. Fail-closed on an unknown class.
    _dest = (destination_class or "").strip().lower()
    if _dest:
        if role != "egress":
            raise ValueError("destination_class is only valid on an egress connector")
        if _dest not in DESTINATION_CLASSES:
            raise ValueError(
                f"destination_class must be one of {DESTINATION_CLASSES}, got {destination_class!r}")
    # Tool binding (governance-bus round-trip): the host-invocable MCP tool this
    # connector federates, as {"tool_name": str, "arg_mapping": {...}}. RVND never
    # invokes it — tool_call_plan projects a call descriptor the HOST executes.
    # Fail-closed: a non-dict ref, a missing/empty tool_name, a non-dict
    # arg_mapping, an unknown arg_mapping key, or a non-string value is
    # rejected in words — never stored as a silent no-op.
    _tref = None
    if tool_ref is not None:
        if not isinstance(tool_ref, dict):
            raise ValueError("tool_ref must be an object with a tool_name")
        _tn = str(tool_ref.get("tool_name") or "").strip()
        if not _tn:
            raise ValueError("tool_ref needs a non-empty tool_name")
        _am = tool_ref.get("arg_mapping") or {}
        if not isinstance(_am, dict):
            raise ValueError("tool_ref arg_mapping must be an object")
        # arg_mapping renames the plannable inputs to the tool's arg names; the
        # only plannable input today is input_ref. An unknown key would never be
        # applied by tool_call_plan — refuse it instead of storing dead mapping.
        for _k, _v in _am.items():
            if _k not in ("input_ref",):
                raise ValueError(
                    f"tool_ref arg_mapping key {_k!r} is not a plannable input "
                    "(allowed: input_ref)")
            if not isinstance(_v, str) or not _v.strip():
                raise ValueError(
                    f"tool_ref arg_mapping[{_k!r}] must be a non-empty string "
                    f"tool-arg name, got {_v!r}")
        _tref = {"tool_name": _tn,
                 "arg_mapping": {k: v for k, v in _am.items()}}
    audit_id = _append(folder_context, actor, log_root, {
        "kind": "ConnectorRegistered",
        "connector_id": connector_id,
        "role": role,
        "channel": channel,
        "use_cases": list(use_cases or []),
        "name": name or connector_id,
        "tags": [str(t) for t in (tags or [])],
        "floor": _floor,
        # The GROUP this channel belongs to — typically the MCP client/tenant, which
        # sends N channels. A group is a desk group-bus: its policy floor governs ALL
        # its channels collectively, and killing the group mutes them all at once.
        "group": (group or "").strip(),
        # Access binding: the credential REFERENCE (never the secret). None on most
        # tracks — only egress tracks that cross the wall carry one.
        "credential_ref": _cred,
        # Declared destination class ("" = undeclared): the authored axis the
        # egress board words enforcement by.
        "destination_class": _dest,
        # Tool binding (None = unbound): what tool_call_plan plans against.
        "tool_ref": _tref,
    })
    return {"ok": True, "connector_id": connector_id, "audit_id": audit_id}


def egress_board(folder_context: str,
                 log_root: Optional[str] = None,
                 llm_broker: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The Egress board projection — the operator's one-glance answer to
    "which of my tracks can act outside?" (per-track-binding concept).

    One row per EGRESS connector (the tracks that cross the wall), carrying the
    floor lamp and the cable state resolved fail-closed at read time:
    ``credential`` is credential_resolver.describe() — the reference, its scheme,
    and its arm status (no_cable / armed / unplugged) — NEVER the secret.

    Enforcement is split by destination class, and the words stay honest:

    * per-track ``mode`` is ``attested`` for general egress (email / ticket /
      tool channels) — host-invoked; RVND witnesses and records, it cannot cut
      that cable. A track reads ``enforced`` only when its declared
      ``destination_class`` is ``llm`` while a bound broker gates LLM calls —
      never by guessing from the free-text channel; undeclared stays attested.
    * board-level ``llm_broker`` is the LLM-destination attestation from
      ``lock.broker_probe.probe_broker`` (pass its result in): ``bound_here``
      True means a running proxy brokers this folder's LLM egress — every LLM
      call is gated and credentialed per track, and the client may render the
      LLM destination as enforced. Absent or unreachable resolves to False —
      the client must render the server's word, not assume it.

    Read-only; resolves but never logs secrets."""
    from .lock import describe
    _broker = llm_broker or {"reachable": False, "bound_here": False}
    _bound = bool(_broker.get("bound_here"))
    tracks: list[dict[str, Any]] = []
    counts = {"armed": 0, "no_cable": 0, "unplugged": 0}
    for c in list_connectors(folder_context, log_root=log_root):
        if c.get("role") != "egress":
            continue
        cred = describe(c.get("credential_ref"))
        counts[cred["status"]] = counts.get(cred["status"], 0) + 1
        dest = (c.get("destination_class") or "").strip() or "undeclared"
        tracks.append({
            "connector_id": c["connector_id"], "name": c.get("name") or c["connector_id"],
            "channel": c.get("channel", ""), "floor": c.get("floor", "permit"),
            "group": c.get("group", ""), "use_cases": list(c.get("use_cases") or []),
            "credential": cred,
            "destination_class": dest,
            # enforced only where a declared destination has a broker on its
            # plug: destination_class llm while a bound broker gates LLM calls.
            # Everything else stays attested (witnessed, not preventable) —
            # never upgraded by guessing from the free-text channel.
            "mode": "enforced" if (dest == "llm" and _bound) else "attested",
        })
    return {"folder_context": folder_context, "tracks": tracks,
            "llm_broker": {"reachable": bool(_broker.get("reachable")),
                           "bound_here": bool(_broker.get("bound_here"))},
            "summary": {"tracks": len(tracks), **counts,
                        # the headline number: armed tracks can reach outside NOW
                        "can_act_outside": counts["armed"]}}


def list_connectors(folder_context: str,
                    log_root: Optional[str] = None) -> list[dict[str, Any]]:
    """Latest-wins projection of registered connectors, stable id order."""
    log = MutationLog(folder_context, log_root=log_root)
    recs: dict[str, dict[str, Any]] = {}
    for evt in log.replay():
        extra = evt.extra or {}
        if extra.get("kind") != "ConnectorRegistered":
            continue
        cid = extra.get("connector_id", "")
        recs[cid] = {k: extra.get(k) for k in (
            "connector_id", "role", "channel", "use_cases", "name", "tags", "floor", "group",
            "credential_ref", "destination_class", "tool_ref")}
    return [recs[k] for k in sorted(recs)]
