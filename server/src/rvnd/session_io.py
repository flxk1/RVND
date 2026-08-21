# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Session I/O — the .rvnd environment bundle: serialize + verify core (S1 + S6).

A session is the WHOLE environment (all workspaces in the rail — a Live Set of
tracks). This module is the un-gated core: pure serialize/verify with no MCP
wiring and no UI. Contract: docs/concepts/session-io-schema-v1.md.

Load-bearing rules implemented here:

- **Configs are chain projections** — a workspace document embeds its signed
  chain VERBATIM (byte-preserved lines); configs are never re-serialized.
  Re-canonicalizing signed events would break their signatures, so the chain
  log is exempt from canonicalization; only off-chain parts (presentation,
  drafts, rail, meta) are hashed in canonical form.
- **Three ordered integrity checks, short-circuit, fail-closed, NOT overridable:**
  (1) every workspace chain verifies (hash links + per-event Ed25519 against
  the embedded public key), (2) the manifest matches the content, (3) the
  bundle signature over the manifest verifies. First failure refuses the load;
  later checks report "not reached". There is no "open anyway".
- **Fail-closed on write, not on looking** — ``read_session_forensic`` gives a
  read-only view of a refused bundle (which workspaces verify, what's
  salvageable) and applies nothing.
- **No-id wall** — meta carries ``origin_role``; accountability (the human
  signer) rides the signature, not the language.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import draft_store
from .mutation_log import GENESIS_HASH, MutationLog, _canonical_event_hash, _signed_bytes
from .signing import (ensure_keypair, public_key_fingerprint, public_key_pem,
                      sign_bytes, verify_signature)

FORMAT = "rvnd-session"
SCHEMA_VERSION = "1.0"

#: Refusal taxonomy (S6). Every refusal names exactly one of these.
REFUSAL_NOT_A_SESSION = "not_a_session"
REFUSAL_UNKNOWN_SCHEMA = "unknown_schema_version"
REFUSAL_BROKEN_CHAIN = "broken_chain"
REFUSAL_ALTERED_CONTENT = "altered_content"
REFUSAL_INVALID_SIGNATURE = "invalid_signature"
REFUSAL_DANGLING_REF = "dangling_reference"
REFUSAL_FOREIGN_KEY = "foreign_key"
REFUSAL_UNSAFE_ID = "unsafe_workspace_id"

#: A workspace id must be a safe RELATIVE token — it becomes a path segment
#: (dest_root/<id>) on restore. An absolute or ``..``-bearing id would escape
#: the restore root (path traversal from an untrusted bundle). Fail-closed.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class SessionIntegrityError(Exception):
    """A session bundle failed verification. Fail-closed: the load is refused.

    Carries the full verify report on ``.report`` so the surface can render
    the located reason ("billing ✗ broken at event #63") — never a generic
    "invalid file".
    """

    def __init__(self, report: dict[str, Any]):
        self.report = report
        refusal = report.get("refusal") or {}
        super().__init__(refusal.get("detail") or refusal.get("reason") or "session refused")


# ---------------------------------------------------------------------------
# canonical form — OFF-CHAIN parts only (chain lines are byte-preserved)
# ---------------------------------------------------------------------------

def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _hash_obj(obj: Any) -> str:
    return _sha256(_canonical_bytes(obj))


def _chain_tip_hash(log_lines: list[str]) -> str:
    """Canonical hash of the last well-formed event — '' for an empty chain."""
    for line in reversed(log_lines):
        line = line.strip()
        if not line:
            continue
        try:
            return "sha256:" + _canonical_event_hash(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ""
    return ""


# ---------------------------------------------------------------------------
# capture (save side) — reads, never writes
# ---------------------------------------------------------------------------

def capture_workspace(
    folder_context: str,
    *,
    workspace_id: str,
    name: str = "",
    log_root: Optional[str] = None,
    presentation: Optional[dict] = None,
) -> dict[str, Any]:
    """Capture one workspace as a self-contained document.

    The chain is embedded as VERBATIM lines (byte-preserved after utf-8
    decode); the chain's public key is embedded so the document verifies on
    any machine. Presentation and drafts are off-chain and travel canonical.
    Drafts are read from the workspace's own draft store, never taken from
    the caller — nothing client-supplied lands in the signed bundle.
    """
    log = MutationLog(folder_context, log_root=log_root)
    lines: list[str] = []
    if log.log_file.exists():
        lines = [l for l in log.log_file.read_text(encoding="utf-8").splitlines()
                 if l.strip()]
    return {
        "id": workspace_id,                     # stable uuid, NOT the display name
        "name": name or workspace_id,
        "chain_mode": "full",                   # v1; "from_checkpoint" reserved (S18)
        "chain": {
            "log_lines": lines,
            "tip_hash": _chain_tip_hash(lines),
            "pubkey_pem": public_key_pem(),
        },
        "config": _capture_config_files(folder_context),
        "presentation": dict(presentation or {}),
        "drafts": _capture_drafts(folder_context, log_root),
    }


#: Off-chain config that is a FILE, not a chain projection. Discovered building
#: S2: policy (lock mode / oversight / opt-out / access control) is written by a
#: dual-write — a chain event for audit AND ``save_policy`` to a file that holds
#: the *current* state. Embedding the chain captures the audit but NOT the state,
#: so the file must travel in the bundle too. Chain-projected config (parties,
#: connectors, use_cases, reservations) is already captured by the chain embed.
def _capture_config_files(folder_context: str) -> dict[str, Any]:
    config: dict[str, Any] = {}
    try:
        from . import policy as _policy
        pol = _policy.policy_path(folder_context)
        if pol.exists():
            config["policy"] = pol.read_text(encoding="utf-8")
    except Exception:
        pass
    return config


def _capture_drafts(folder_context: str, log_root: Optional[str]) -> dict[str, Any]:
    """Drafts for the bundle's ``drafts`` slot, read from the draft store
    (server-sourced, like the policy file above). Unreadable draft files are
    omitted from a capture — ``workspace_session(op="draft_load")`` names
    them; a scratch file must not block saving the environment."""
    try:
        return draft_store.load_all(folder_context, log_root=log_root)
    except Exception:
        return {}


def restore_workspace(ws_doc: dict[str, Any], dest_folder: str,
                      *, log_root: Optional[str] = None
                      ) -> tuple[str, list[dict[str, Any]]]:
    """Reconstruct a workspace document into a fresh folder (S2/S5 helper).

    The chain is written VERBATIM (preserving signatures and the original
    ``folder_path`` inside each event — remapping it would change the signed
    bytes and break verification; projections are path-agnostic, so this is
    correct). Off-chain config files are written back so file-backed config
    (policy) is reconstructed, not just its chain audit. Drafts rehydrate
    into the destination's draft store — a file write, no chain event.
    Returns ``(dest_folder, drafts_refused)``: a draft never makes a restore
    fail, but one the store refuses (sealed destination, over-cap payload,
    unknown surface) is named with its reason, not silently dropped.
    """
    dest = Path(dest_folder).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    log = MutationLog(str(dest), log_root=log_root)
    log.log_file.parent.mkdir(parents=True, exist_ok=True)
    lines = ws_doc.get("chain", {}).get("log_lines") or []
    log.log_file.write_text(("\n".join(lines) + "\n") if lines else "",
                            encoding="utf-8")
    cfg = ws_doc.get("config") or {}
    if cfg.get("policy") is not None:
        from . import policy as _policy
        _policy.policy_path(str(dest)).write_text(cfg["policy"], encoding="utf-8")
    # Rehydrate drafts; collect refusals rather than raising or dropping.
    drafts_refused: list[dict[str, Any]] = []
    for surface, payload in (ws_doc.get("drafts") or {}).items():
        if isinstance(payload, dict) and not payload:
            continue                              # empty draft — nothing to write
        written = draft_store.save(str(dest), surface, payload, log_root=log_root)
        if not written["ok"]:
            drafts_refused.append({"surface": surface, "error": written["error"]})
    return str(dest), drafts_refused


def build_session(
    workspaces: list[dict[str, Any]],
    rail_state: dict[str, Any],
    *,
    name: str,
    created: str,
    patch_format: str = "lg",       # canonical since the standard landed .lg; "loom" stays the accepted read alias
    parent_version: Optional[str] = None,
    origin_role: str = "user",
    signed_by: str = "",
) -> dict[str, Any]:
    """Assemble + sign the environment bundle. Pure given its inputs.

    The Ed25519 signature covers the manifest; the manifest covers everything
    (chain tips, presentation/drafts/rail/meta hashes) — so one signature
    binds the whole environment and any altered byte breaks something.

    Provenance split (S10, no-id wall): ``meta.origin_role`` is ROLE-only —
    the language side never carries a name. Accountability — the human who
    saved — is ``signed_by``, and it lives in the MANIFEST (the signature /
    named-signer side), so it is forge-proof (editing it breaks check 3) yet
    never enters meta or any chain event.
    """
    meta = {
        "name": name,
        "created": created,
        "modified": created,
        "parent_version": parent_version,
        "origin_role": origin_role,     # role, never a named identity (no-id wall)
        "workspace_count": len(workspaces),
    }
    ensure_keypair()
    manifest: dict[str, Any] = {
        "meta_hash": _hash_obj(meta),
        "rail_hash": _hash_obj(rail_state),
        # S10: named accountability rides the SIGNED manifest (identity side),
        # never meta (role side) and never a chain event.
        "signer": {"label": signed_by,
                   "key_fingerprint": public_key_fingerprint()},
        "workspaces": {
            ws["id"]: {
                "chain_tip_hash": ws["chain"]["tip_hash"],
                "chain_line_count": len(ws["chain"]["log_lines"]),
                "config_hash": _hash_obj(ws.get("config") or {}),
                "presentation_hash": _hash_obj(ws["presentation"]),
                "drafts_hash": _hash_obj(ws["drafts"]),
            }
            for ws in workspaces
        },
    }
    return {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "patch_format": patch_format,
        "meta": meta,
        "workspaces": workspaces,
        "rail": rail_state,
        "manifest": manifest,
        "signature": {
            "alg": "ed25519",
            "pubkey_pem": public_key_pem(),
            "sig": sign_bytes(_canonical_bytes(manifest)),
            "covers": "manifest",
        },
    }


# ---------------------------------------------------------------------------
# versions (S9) — content-addressed; history is a DAG (fork-not-rewind)
# ---------------------------------------------------------------------------

def bundle_version(bundle: dict[str, Any]) -> str:
    """The bundle's version id = hash of its manifest. Content-addressed:
    identical environments get identical versions; any change gets a new one.
    The manifest is what the signature covers, so the version is as
    tamper-evident as the bundle itself.
    """
    return _hash_obj(bundle.get("manifest") or {})


def next_session(
    parent_bundle: dict[str, Any],
    workspaces: list[dict[str, Any]],
    rail_state: dict[str, Any],
    *,
    created: str,
    signed_by: str = "",
) -> dict[str, Any]:
    """Save-as-continuation: a child version of ``parent_bundle`` (S9).

    ``parent_version`` links child→parent, so lineage is a DAG: reloading an
    OLDER version and continuing sprouts a second child of that version —
    a branch, never a rewind. Nothing is ever overwritten.
    """
    meta = parent_bundle.get("meta") or {}
    return build_session(
        workspaces, rail_state,
        name=meta.get("name", "session"),
        created=created,
        patch_format=parent_bundle.get("patch_format", "loom"),
        parent_version=bundle_version(parent_bundle),
        origin_role=meta.get("origin_role", "user"),
        signed_by=signed_by,
    )


def describe_session(bundle: dict[str, Any]) -> dict[str, Any]:
    """The provenance card the Open dialog renders (S10) — no verification
    side-effects, no secrets: name, when, who (signer label + key fp on the
    identity side; role on the language side), lineage, and shape.
    """
    meta = bundle.get("meta") or {}
    signer = (bundle.get("manifest") or {}).get("signer") or {}
    return {
        "name": meta.get("name", ""),
        "created": meta.get("created", ""),
        "modified": meta.get("modified", ""),
        "origin_role": meta.get("origin_role", ""),
        "signed_by": signer.get("label", ""),
        "key_fingerprint": signer.get("key_fingerprint", ""),
        "version": bundle_version(bundle),
        "parent_version": meta.get("parent_version"),
        "workspace_count": meta.get("workspace_count", 0),
        "workspaces": [{"id": ws.get("id"), "name": ws.get("name"),
                        "events": len(ws.get("chain", {}).get("log_lines") or [])}
                       for ws in bundle.get("workspaces", [])],
        "patch_format": bundle.get("patch_format", ""),
        "schema_version": bundle.get("schema_version", ""),
    }


# ---------------------------------------------------------------------------
# verify (load side) — three ordered checks, short-circuit, fail-closed
# ---------------------------------------------------------------------------

def _load_pubkey(pem: str) -> Optional[Ed25519PublicKey]:
    try:
        key = serialization.load_pem_public_key(pem.encode("utf-8"))
        return key if isinstance(key, Ed25519PublicKey) else None
    except Exception:
        return None


def _pem_fingerprint(pem: str) -> str:
    """First 16 hex of sha256 of the raw pubkey — matches signing.public_key_fingerprint."""
    key = _load_pubkey(pem)
    if key is None:
        return ""
    raw = key.public_bytes(encoding=serialization.Encoding.Raw,
                           format=serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def continuation_check(bundle: dict[str, Any]) -> dict[str, Any]:
    """Decision B: continuing a restored chain requires the LOCAL key.

    A workspace chain signed on another machine verifies fine as a *bundle*
    (embedded key), but appending to it locally would sign new events with a
    DIFFERENT key — a multi-key chain that ``verify_chain`` (single local key)
    can't validate. So a foreign-key chain is **view-only**: portable to read
    and audit, not to continue. Returns ``{continuable, local_fingerprint,
    foreign: [{workspace, key_fingerprint}]}``. (Cross-machine *continue* —
    finding #1 / a bundle key-registry — is deliberately out of scope.)
    """
    local = public_key_fingerprint()
    foreign: list[dict[str, Any]] = []
    for ws in bundle.get("workspaces", []):
        fp = _pem_fingerprint(ws.get("chain", {}).get("pubkey_pem", ""))
        if fp and fp != local:
            foreign.append({"workspace": ws.get("id"), "key_fingerprint": fp})
    return {"continuable": not foreign, "local_fingerprint": local, "foreign": foreign}


def _verify_chain_lines(lines: list[str], pubkey_pem: str) -> list[dict[str, Any]]:
    """Line-level chain verification, mirroring MutationLog.verify_chain but
    over embedded lines with an embedded key (portable — no local folder, no
    local keypair). Returns located failures; [] means the chain verifies.
    """
    failures: list[dict[str, Any]] = []
    public_key = _load_pubkey(pubkey_pem)
    expected_prev = GENESIS_HASH
    prev_was_purge = False
    seen_signed = False
    for position, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            failures.append({"position": position, "reason": "malformed_json"})
            continue
        stored_prev = obj.get("prev_hash", "")
        if stored_prev and stored_prev != expected_prev:
            # B1: an authorised purge-tombstone re-link is not tampering.
            if not prev_was_purge:
                failures.append({
                    "position": position,
                    "audit_id": obj.get("audit_id"),
                    "reason": "prev_hash_mismatch",
                    "expected": expected_prev,
                    "found": stored_prev,
                })
        stored_sig = obj.get("signature", "")
        if not stored_sig:
            # D5: a stripped signature after the signing epoch is tamper.
            if seen_signed:
                failures.append({
                    "position": position,
                    "audit_id": obj.get("audit_id"),
                    "reason": "unsigned_event_after_signing_epoch",
                })
        else:
            seen_signed = True
            if public_key is None:
                failures.append({"position": position,
                                 "reason": "chain_pubkey_unreadable"})
            elif not verify_signature(
                    _signed_bytes({**obj, "signature": ""}), stored_sig, public_key):
                failures.append({
                    "position": position,
                    "audit_id": obj.get("audit_id"),
                    "reason": "ed25519_signature_invalid",
                })
        expected_prev = _canonical_event_hash(obj)
        prev_was_purge = (obj.get("extra") or {}).get("kind") == "purge_tombstone"
    return failures


def verify_session(bundle: Any) -> dict[str, Any]:
    """The S6 verify contract: gate, then three ordered checks, short-circuit.

    Returns a report::

        {ok, checks: [{name, status: "pass"|"fail"|"not_reached", detail…}],
         refusal: {reason, detail} | None}

    ``ok=False`` means the load is REFUSED — there is no override.
    """
    checks = [
        {"name": "chains_verify", "status": "not_reached"},
        {"name": "manifest_matches", "status": "not_reached"},
        {"name": "bundle_signature", "status": "not_reached"},
    ]

    def refused(reason: str, detail: str) -> dict[str, Any]:
        return {"ok": False, "checks": checks,
                "refusal": {"reason": reason, "detail": detail}}

    # Gate: is this even a session we can read? (fail-closed on format)
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        return refused(REFUSAL_NOT_A_SESSION, "not an rvnd-session bundle")
    version = str(bundle.get("schema_version", ""))
    major = version.split(".", 1)[0]
    if major != SCHEMA_VERSION.split(".", 1)[0]:
        return refused(
            REFUSAL_UNKNOWN_SCHEMA,
            f"schema_version {version or '?'} — this reader speaks {SCHEMA_VERSION}")

    # Check 1 — every workspace chain verifies (against its EMBEDDED key).
    per_workspace: dict[str, Any] = {}
    first_broken: Optional[str] = None
    for ws in bundle.get("workspaces", []):
        wid = ws.get("id", "?")
        chain = ws.get("chain") or {}
        failures = _verify_chain_lines(chain.get("log_lines") or [],
                                       chain.get("pubkey_pem") or "")
        tip = _chain_tip_hash(chain.get("log_lines") or [])
        if chain.get("tip_hash", "") != tip:
            failures.append({"position": None, "reason": "tip_hash_mismatch"})
        per_workspace[wid] = {"ok": not failures, "failures": failures,
                              "events": len(chain.get("log_lines") or [])}
        if failures and first_broken is None:
            first_broken = wid
    checks[0] = {"name": "chains_verify",
                 "status": "fail" if first_broken else "pass",
                 "workspaces": per_workspace}
    if first_broken is not None:
        f = per_workspace[first_broken]["failures"][0]
        where = f"event #{f['position']}" if f.get("position") is not None else "chain tip"
        return refused(REFUSAL_BROKEN_CHAIN,
                       f"{first_broken} ✗ broken at {where} ({f['reason']})")

    # Check 2 — the manifest matches the content (recompute every hash).
    manifest = bundle.get("manifest") or {}
    mismatches: list[str] = []
    if manifest.get("meta_hash") != _hash_obj(bundle.get("meta")):
        mismatches.append("meta")
    if manifest.get("rail_hash") != _hash_obj(bundle.get("rail")):
        mismatches.append("rail")
    listed = manifest.get("workspaces") or {}
    embedded = {ws.get("id"): ws for ws in bundle.get("workspaces", [])}
    if set(listed) != set(embedded):
        mismatches.append("workspace set")
    else:
        for wid, entry in listed.items():
            ws = embedded[wid]
            if (entry.get("chain_tip_hash") != ws["chain"].get("tip_hash")
                    or entry.get("chain_line_count") != len(ws["chain"].get("log_lines") or [])):
                mismatches.append(f"{wid}: chain")
            if entry.get("config_hash") != _hash_obj(ws.get("config") or {}):
                mismatches.append(f"{wid}: config")
            if entry.get("presentation_hash") != _hash_obj(ws.get("presentation")):
                mismatches.append(f"{wid}: presentation")
            if entry.get("drafts_hash") != _hash_obj(ws.get("drafts")):
                mismatches.append(f"{wid}: drafts")
    checks[1] = {"name": "manifest_matches",
                 "status": "fail" if mismatches else "pass",
                 "mismatches": mismatches}
    if mismatches:
        return refused(REFUSAL_ALTERED_CONTENT,
                       "manifest hash mismatch: " + ", ".join(mismatches))

    # Check 3 — the bundle signature over the manifest.
    sig = bundle.get("signature") or {}
    public_key = _load_pubkey(sig.get("pubkey_pem") or "")
    sig_ok = (sig.get("alg") == "ed25519" and public_key is not None
              and verify_signature(_canonical_bytes(manifest),
                                   sig.get("sig") or "", public_key))
    checks[2] = {"name": "bundle_signature",
                 "status": "pass" if sig_ok else "fail"}
    if not sig_ok:
        return refused(REFUSAL_INVALID_SIGNATURE,
                       "bundle signature over the manifest does not verify")

    return {"ok": True, "checks": checks, "refusal": None}


def restore_environment(
    bundle: dict[str, Any],
    dest_root: str | Path,
    *,
    log_root_for: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Reconstruct the WHOLE environment from a verified bundle (S3 + S5 core).

    Restores every workspace document (verbatim chain + config files) under
    ``dest_root/<workspace-id>/`` and returns the presentation state the
    surface applies WITHOUT any chain write (honesty split, I3):

        {"folders": {ws_id: path}, "rail": {...}, "presentation": {ws_id: {...}},
         "drafts": {ws_id: {...}}, "drafts_refused": {ws_id: [{surface, error}]}}

    ``drafts_refused`` names any bundled draft the destination's store refused
    (only workspaces with refusals appear); the restore itself still succeeds.
    Callers MUST verify first (``load_session`` does); this function assumes a
    bundle that passed the three checks + referential integrity. The governed
    rail actions (mute/isolate/All-Stop) are already ON each chain — the rail
    block here is presentation only (order, focus, global view), per S3.
    """
    # Decision B: never restore-for-continue a foreign-key chain — it would
    # produce an on-disk chain the local verify_chain can't validate. Foreign
    # bundles are view-only (use forensic_bundle / describe_session).
    chk = continuation_check(bundle)
    if not chk["continuable"]:
        ws0 = chk["foreign"][0]
        raise SessionIntegrityError({
            "ok": False, "checks": [],
            "refusal": {"reason": REFUSAL_FOREIGN_KEY,
                        "detail": f"{ws0['workspace']} was signed on another key "
                                  f"({ws0['key_fingerprint']}) — this session is "
                                  f"view-only here; continue it on the origin machine"}})
    root = Path(dest_root).expanduser()
    folders: dict[str, str] = {}
    presentation: dict[str, Any] = {}
    drafts: dict[str, Any] = {}
    drafts_refused: dict[str, list[dict[str, Any]]] = {}
    for ws in bundle.get("workspaces", []):
        wid = ws["id"]
        if not (isinstance(wid, str) and wid not in (".", "..") and _SAFE_ID.match(wid)):
            raise SessionIntegrityError({
                "ok": False, "checks": [],
                "refusal": {"reason": REFUSAL_UNSAFE_ID,
                            "detail": f"workspace id {wid!r} is not a safe relative "
                                      f"token — it would escape the restore root"}})
        lr = (log_root_for or {}).get(wid)
        folders[wid], refused = restore_workspace(ws, str(root / wid), log_root=lr)
        presentation[wid] = ws.get("presentation") or {}
        drafts[wid] = ws.get("drafts") or {}
        if refused:
            drafts_refused[wid] = refused
    return {"folders": folders, "rail": dict(bundle.get("rail") or {}),
            "presentation": presentation, "drafts": drafts,
            "drafts_refused": drafts_refused}


# ---------------------------------------------------------------------------
# referential integrity (S4) — well-formedness, distinct from tamper-integrity
# ---------------------------------------------------------------------------

def check_referential_integrity(bundle: dict[str, Any]) -> dict[str, Any]:
    """Every cross-reference resolves WITHIN the bundle (S4).

    Distinct from :func:`verify_session` (tamper): a well-formed *signed* bundle
    can still dangle if built from a partial environment (e.g. single-workspace
    export, S13). A full-environment session is complete by construction; this
    is the guard that proves it. Refs checked (from the chain events + rail):
    connector→use_case, use_case→agent(party), party→connector(channel), and
    rail order/focus → workspace. Returns ``{ok, dangling: [{workspace, from,
    ref, reason}]}`` — a located dangling ref, never a silent drop.
    """
    ws_ids = {ws.get("id") for ws in bundle.get("workspaces", [])}
    dangling: list[dict[str, Any]] = []
    for ws in bundle.get("workspaces", []):
        wid = ws.get("id")
        parties_: dict[str, dict] = {}
        connectors_: dict[str, dict] = {}
        use_cases_: dict[str, dict] = {}
        for line in ws.get("chain", {}).get("log_lines") or []:
            try:
                extra = json.loads(line).get("extra") or {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            k = extra.get("kind")
            if k == "PartyRegistered":
                parties_[extra.get("party_id")] = extra
            elif k == "ConnectorRegistered":
                connectors_[extra.get("connector_id")] = extra
            elif k == "UseCaseRegistered":
                use_cases_[extra.get("use_case_id")] = extra
        uc_ids, party_ids, conn_ids = set(use_cases_), set(parties_), set(connectors_)

        def _dangle(frm: str, ref: str, reason: str) -> None:
            dangling.append({"workspace": wid, "from": frm, "ref": ref, "reason": reason})

        for cid, c in connectors_.items():
            for uc in (c.get("use_cases") or []):
                if uc not in uc_ids:
                    _dangle(f"connector:{cid}", f"use_case:{uc}", "missing use_case")
        for uid, u in use_cases_.items():
            for a in (u.get("allowed_agents") or []):
                if a not in party_ids:
                    _dangle(f"use_case:{uid}", f"agent:{a}", "missing agent")
        for pid, p in parties_.items():
            for ch in (p.get("channels") or []):
                if ch not in conn_ids:
                    _dangle(f"party:{pid}", f"connector:{ch}", "missing connector")

    rail = bundle.get("rail") or {}
    for w in (rail.get("order") or []):
        if w not in ws_ids:
            dangling.append({"workspace": None, "from": "rail:order",
                             "ref": f"workspace:{w}", "reason": "missing workspace"})
    foc = rail.get("focused")
    if foc and foc not in ws_ids:
        dangling.append({"workspace": None, "from": "rail:focused",
                         "ref": f"workspace:{foc}", "reason": "missing workspace"})
    return {"ok": not dangling, "dangling": dangling}


def _dangle_detail(d: dict[str, Any]) -> str:
    return f"{d.get('workspace') or 'rail'}: {d['from']} → {d['ref']} ({d['reason']})"


def verify_full(bundle: Any) -> dict[str, Any]:
    """The full load gate: the three tamper checks (:func:`verify_session`) AND
    referential integrity (:func:`check_referential_integrity`), combined into
    one fail-closed report. This is what a full-environment load enforces."""
    report = verify_session(bundle)
    if not report["ok"]:
        return report
    ref = check_referential_integrity(bundle)
    if not ref["ok"]:
        return {"ok": False, "checks": report["checks"],
                "refusal": {"reason": REFUSAL_DANGLING_REF,
                            "detail": _dangle_detail(ref["dangling"][0])}}
    return {**report, "referential": ref}


# ---------------------------------------------------------------------------
# single-workspace export / import (S13) — a track between Sets
# ---------------------------------------------------------------------------

def export_workspace(bundle: dict[str, Any], workspace_id: str, *,
                     created: str, signed_by: str = "") -> dict[str, Any]:
    """Slice ONE workspace out of a session as its own portable bundle.

    The slice is a normal single-workspace session (same format, same three
    integrity checks) whose rail names only the sliced workspace — so it
    passes S4 in its own right. Lineage: parent_version = the source session,
    so a track remembers which Set it came from.
    """
    ws = next((w for w in bundle.get("workspaces", []) if w.get("id") == workspace_id), None)
    if ws is None:
        raise KeyError(f"workspace {workspace_id!r} not in session")
    meta = bundle.get("meta") or {}
    return build_session(
        [ws], {"order": [workspace_id], "focused": workspace_id},
        name=f"{meta.get('name', 'session')} · {ws.get('name', workspace_id)}",
        created=created,
        patch_format=bundle.get("patch_format", "loom"),
        parent_version=bundle_version(bundle),
        origin_role=meta.get("origin_role", "user"),
        signed_by=signed_by,
    )


def import_workspace(
    env_bundle: dict[str, Any],
    ws_bundle: dict[str, Any],
    workspace_id: str,
    *,
    created: str,
    signed_by: str = "",
) -> dict[str, Any]:
    """Import a workspace from another session into this environment (S13).

    Fail-closed on both hazards a partial slice introduces:
    - the incoming bundle must VERIFY (three checks — no tampered track), and
    - the id must not collide with an existing workspace (no silent replace;
      replacing is a deliberate S5 act, not an import side-effect).

    Returns a NEW child session (fork-not-rewind): the environment with the
    track appended to the rail, parent_version = the receiving session.
    Referential integrity of the result is the caller's load-time guard (S4)
    — and because each workspace's refs are workspace-local, a verified
    slice keeps the merged environment resolvable.
    """
    report = verify_session(ws_bundle)
    if not report["ok"]:
        raise SessionIntegrityError(report)
    incoming = next((w for w in ws_bundle.get("workspaces", [])
                     if w.get("id") == workspace_id), None)
    if incoming is None:
        raise KeyError(f"workspace {workspace_id!r} not in the imported session")
    # Decision B: importing a foreign-key track to CONTINUE it would poison the
    # merged env with a chain the local key can't re-verify. Refuse (view-only).
    fp = _pem_fingerprint(incoming.get("chain", {}).get("pubkey_pem", ""))
    if fp and fp != public_key_fingerprint():
        raise SessionIntegrityError({
            "ok": False, "checks": report["checks"],
            "refusal": {"reason": REFUSAL_FOREIGN_KEY,
                        "detail": f"track {workspace_id!r} was signed on another key "
                                  f"({fp}) — import is same-key only (view it read-only "
                                  f"instead)"}})
    existing = {w.get("id") for w in env_bundle.get("workspaces", [])}
    if workspace_id in existing:
        raise SessionIntegrityError({
            "ok": False, "checks": report["checks"],
            "refusal": {"reason": REFUSAL_DANGLING_REF,
                        "detail": f"workspace id {workspace_id!r} already exists "
                                  f"in this environment — import never replaces "
                                  f"(replace is a deliberate load action)"},
        })
    rail = dict(env_bundle.get("rail") or {})
    rail["order"] = list(rail.get("order") or []) + [workspace_id]
    meta = env_bundle.get("meta") or {}
    merged = build_session(
        list(env_bundle.get("workspaces", [])) + [incoming], rail,
        name=meta.get("name", "session"),
        created=created,
        patch_format=env_bundle.get("patch_format", "loom"),
        parent_version=bundle_version(env_bundle),
        origin_role=meta.get("origin_role", "user"),
        signed_by=signed_by,
    )
    ref = check_referential_integrity(merged)
    if not ref["ok"]:
        raise SessionIntegrityError({
            "ok": False, "checks": [],
            "refusal": {"reason": REFUSAL_DANGLING_REF,
                        "detail": _dangle_detail(ref["dangling"][0])},
        })
    return merged


# ---------------------------------------------------------------------------
# file I/O — local filesystem only (air-gap); load is fail-closed
# ---------------------------------------------------------------------------

def save_session(bundle: dict[str, Any], path: str | Path) -> Path:
    """Write the bundle as one self-contained portable file. No chain write."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bundle, ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return p


def load_session(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read + verify a session file. FAIL-CLOSED and not overridable:
    a bundle that does not verify raises SessionIntegrityError — it never
    returns a tampered environment. Returns (bundle, verify_report).
    """
    p = Path(path).expanduser()
    try:
        bundle = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SessionIntegrityError({
            "ok": False, "checks": [],
            "refusal": {"reason": REFUSAL_NOT_A_SESSION,
                        "detail": f"unreadable session file: {type(e).__name__}"},
        }) from e
    report = verify_full(bundle)          # 3 tamper checks + referential integrity
    if not report["ok"]:
        raise SessionIntegrityError(report)
    return bundle, report


def forensic_bundle(bundle: Any) -> dict[str, Any]:
    """Forensic view over an in-memory bundle (the browser holds the file).
    Never raises; applies nothing; shows which workspaces are salvageable."""
    report = verify_session(bundle)
    chains = next((c for c in report["checks"] if c["name"] == "chains_verify"), {})
    return {
        "readable": True,
        "ok": report["ok"],
        "refusal": report["refusal"],
        "meta": bundle.get("meta") if isinstance(bundle, dict) else None,
        "workspaces": {
            wid: {"salvageable": ws["ok"], "events": ws["events"],
                  "failures": ws["failures"]}
            for wid, ws in (chains.get("workspaces") or {}).items()
        },
    }


def read_session_forensic(path: str | Path) -> dict[str, Any]:
    """Read-only forensic view of a session FILE — fail-closed on WRITE, not on
    LOOKING. Delegates to :func:`forensic_bundle` after reading the bytes."""
    p = Path(path).expanduser()
    try:
        bundle = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return {"readable": False, "detail": f"unreadable file: {type(e).__name__}",
                "workspaces": {}}
    return forensic_bundle(bundle)
