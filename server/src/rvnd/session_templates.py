# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Starter templates — a fresh governed environment, materialized in-process (S14).

A template is a declarative recipe (plain data: workspaces with their parties /
connectors / use cases, draft seeds, rail) — never a shipped ``.rvnd`` fixture.
The verify trust model forces this: a pre-built fixture would carry the
packager's signing key, and under decision B a foreign-key session is
view-only (``REFUSAL_FOREIGN_KEY`` on restore/adopt) — so a static fixture
could never satisfy S14's "loadable as a fresh environment", and the only way
around that would be a "trusted template" carve-out in the S6 checks, which
are not overridable by design. So instantiation, not distribution: the recipe
is materialized here through the same governed write paths authoring uses
(``register_party`` / ``register_connector`` / ``register_use_case``; drafts
via ``draft_store.save``, so caps and the surface whitelist hold), and every
chain is signed with the local key at materialization time. The result
verifies, is continuable, and its provenance is local — created here, now.

Fail-closed edges: an unknown template, an unsafe workspace id, or a
destination folder that already holds anything (a template never writes into
an existing environment — I3) refuse in words; the built bundle is
self-verified (``verify_full`` + ``continuation_check``) before it is handed
over.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from . import connectors as _connectors
from . import draft_store
from . import parties as _parties
from . import session_io as S
from . import use_case as _use_case
from .mutation_log import MutationLog

#: The built-in recipes defined by the starter-environment contract.
#: Everything in a recipe is plain data — nothing here is signed, so there is
#: nothing to trust until the local engine materializes and signs it.
TEMPLATES: dict[str, dict[str, Any]] = {
    "kids-ai": {
        "name": "Govern a kid's AI",
        "description": (
            "One workspace governing a child's AI companion: the guardian "
            "holds approval, the companion agent is scoped to homework help "
            "over a single chat channel, and a starter policy draft is "
            "seeded for the guardian to adapt."),
        "rail": {"order": ["kids-ai"], "focused": "kids-ai"},
        "workspaces": [{
            "id": "kids-ai",
            "name": "Kid's AI",
            "use_cases": [{
                "use_case_id": "homework-help",
                "name": "Homework help",
                "fingerprint": {"audience": "minor", "data": ["conversation"]},
                "risk": "medium",
                "allowed_agents": ["companion"],
            }],
            "parties": [
                {"party_id": "guardian", "kind": "human", "name": "Guardian",
                 "role": "guardian", "competences": ["approve", "review"]},
                {"party_id": "companion", "kind": "agent",
                 "name": "AI companion", "owner": "guardian",
                 "purpose": "homework help under guardian oversight",
                 "channels": ["chat"]},
            ],
            "connectors": [{
                "connector_id": "chat", "role": "ingress",
                "channel": "message", "name": "Chat",
                "use_cases": ["homework-help"],
            }],
            "drafts": {"policy_paste": {"text": (
                "Starter policy — kid's AI\n"
                "- The companion answers homework questions only.\n"
                "- No personal data leaves the workspace.\n"
                "- Anything beyond homework help is held for the guardian.\n")}},
        }],
    },
    "enterprise-baseline": {
        "name": "Enterprise baseline",
        "description": (
            "Two workspaces as a starting rail: an operations desk with a "
            "triage agent on the mail channel, and a compliance desk "
            "holding the oversight line."),
        "rail": {"order": ["operations", "compliance"], "focused": "operations"},
        "workspaces": [
            {"id": "operations", "name": "Operations",
             "use_cases": [{
                 "use_case_id": "email-triage", "name": "Email triage",
                 "fingerprint": {"data": ["email"]}, "risk": "low",
                 "allowed_agents": ["triage-bot"]}],
             "parties": [
                 {"party_id": "ops-lead", "kind": "human",
                  "name": "Operations lead", "role": "operations",
                  "competences": ["approve"]},
                 {"party_id": "triage-bot", "kind": "agent",
                  "name": "Triage agent", "owner": "ops-lead",
                  "purpose": "sort inbound email", "channels": ["mail"]}],
             "connectors": [{
                 "connector_id": "mail", "role": "ingress", "channel": "email",
                 "name": "Mail", "use_cases": ["email-triage"]}],
             "drafts": {"policy_paste": {"text": (
                 "Starter policy — enterprise baseline\n"
                 "- Agents act only inside registered use cases.\n"
                 "- High-risk acts are reserved to a named role.\n")}},
             },
            {"id": "compliance", "name": "Compliance",
             "use_cases": [],
             "parties": [
                 {"party_id": "compliance-officer", "kind": "human",
                  "name": "Compliance officer", "role": "compliance",
                  "competences": ["audit", "approve"],
                  "channels": ["oversight-line"]}],
             "connectors": [{
                 "connector_id": "oversight-line", "role": "oversight",
                 "channel": "message", "name": "Oversight line",
                 "use_cases": []}],
             "drafts": {},
             },
        ],
    },
}


def list_templates() -> list[dict[str, Any]]:
    """The catalogue the surface renders: id, name, description, and the
    workspaces a template would create — enough to choose, nothing signed."""
    return [{"id": tid,
             "name": t["name"],
             "description": t["description"],
             "workspaces": [{"id": w["id"], "name": w["name"]}
                            for w in t["workspaces"]]}
            for tid, t in TEMPLATES.items()]


def _refuse(error: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, **extra}


def _folder_in_use(folder: Path, log_root: Optional[str]) -> bool:
    """True when materializing here would touch an existing environment:
    the folder holds anything, or a chain already exists for its path."""
    if folder.exists() and any(folder.iterdir()):
        return True
    return MutationLog(str(folder), log_root=log_root).log_file.exists()


def materialize(
    template_id: str,
    dest_root: str | Path,
    *,
    created: str,
    actor: str = "user",
    signed_by: str = "",
    origin_role: str = "user",
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Instantiate a template as a fresh local environment (the S14 act).

    Creates ``dest_root/<workspace-id>/`` per recipe workspace, registers the
    recipe's use cases / parties / connectors through the real governed paths
    (chain events, signed with the local key), seeds drafts through the draft
    store (refusals are named, never silent), then captures and signs the
    environment bundle exactly as a live save would. The bundle is
    self-verified before return — a template that materializes broken is a
    refusal, not a handover. Returns::

        {ok, template, folders, rail, bundle, version, card,
         continuation, drafts_refused}
    """
    recipe = TEMPLATES.get(template_id)
    if recipe is None:
        return _refuse(f"unknown template {template_id!r} — "
                       f"one of {sorted(TEMPLATES)}")
    root = Path(dest_root).expanduser()

    # Guard first, write second: a template creates, it never joins or
    # overwrites. Refuse if any destination is in use, before touching one.
    for ws in recipe["workspaces"]:
        wid = ws["id"]
        if not (isinstance(wid, str) and wid not in (".", "..")
                and S._SAFE_ID.match(wid)):
            return _refuse(f"workspace id {wid!r} is not a safe relative token")
        if _folder_in_use(root / wid, log_root):
            return _refuse(f"{root / wid} already holds a workspace or files — "
                           f"a template only materializes into fresh folders")

    folders: dict[str, str] = {}
    drafts_refused: dict[str, list[dict[str, Any]]] = {}
    docs: list[dict[str, Any]] = []
    for ws in recipe["workspaces"]:
        wid = ws["id"]
        folder = root / wid
        folder.mkdir(parents=True, exist_ok=True)
        folders[wid] = str(folder)
        for uc in ws.get("use_cases") or []:
            _use_case.register_use_case(
                str(folder), use_case_id=uc["use_case_id"], name=uc["name"],
                fingerprint=uc.get("fingerprint") or {}, risk=uc["risk"],
                allowed_agents=list(uc.get("allowed_agents") or []),
                actor=actor, log_root=log_root)
        for p in ws.get("parties") or []:
            _parties.register_party(
                str(folder), p["party_id"], p["kind"],
                name=p.get("name", ""), role=p.get("role", ""),
                competences=p.get("competences"), channels=p.get("channels"),
                owner=p.get("owner", ""), purpose=p.get("purpose", ""),
                actor=actor, log_root=log_root)
        for c in ws.get("connectors") or []:
            _connectors.register_connector(
                str(folder), connector_id=c["connector_id"], role=c["role"],
                channel=c["channel"], use_cases=list(c.get("use_cases") or []),
                name=c.get("name", ""), actor=actor, log_root=log_root)
        refused: list[dict[str, Any]] = []
        for surface, payload in (ws.get("drafts") or {}).items():
            written = draft_store.save(str(folder), surface, payload,
                                       log_root=log_root)
            if not written["ok"]:
                refused.append({"surface": surface, "error": written["error"]})
        if refused:
            drafts_refused[wid] = refused
        docs.append(S.capture_workspace(
            str(folder), workspace_id=wid, name=ws["name"], log_root=log_root))

    bundle = S.build_session(docs, dict(recipe["rail"]), name=recipe["name"],
                             created=created, origin_role=origin_role,
                             signed_by=signed_by)
    # Self-check: the handover contract is a bundle that verifies and is
    # continuable here (same key). Anything else is a bug — refuse it.
    report = S.verify_full(bundle)
    continuation = S.continuation_check(bundle)
    if not report["ok"] or not continuation["continuable"]:
        return _refuse("materialized template failed self-verification — "
                       "refusing to hand over a broken environment",
                       report=report, continuation=continuation)
    return {"ok": True, "template": template_id, "folders": folders,
            "rail": dict(recipe["rail"]), "bundle": bundle,
            "version": S.bundle_version(bundle),
            "card": S.describe_session(bundle),
            "continuation": continuation, "drafts_refused": drafts_refused}
