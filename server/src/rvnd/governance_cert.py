# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Mint a **GovernanceCertification** — the enforcement-bound oversight proof.

The one owned artifact of the ecosystem: a portable attestation that a specific
agent action was, at the moment it acted, simultaneously **grounded ∧ overseen ∧
enforced ∧ intact ∧ legitimate**. Its load-bearing property is that it is *only
minted as the byproduct of enforcement* — the PreToolUse hook records a HELD
action, and this certificate is issued only when the human then approves it (the
PostToolUse companion). Possessing a valid one is evidence the governance
happened, not a claim that it did.

**Reuse-first.** Everything mechanical is composed, not invented:
  * Ed25519 signing              ← RVND's identity key via ``oversight_cert.rvnd_ed25519_signer``
  * the DSSE envelope            ← the "dead simple" spec below (swappable for Sigstore cosign)
  * the in-toto Statement        ← in-toto Attestation Framework (predicateType + subject.digest)
  * the ``overseen`` pillar      ← the published ``oversight-certificate`` (embedded when the
                                    ``[oversight-cert]`` extra is present; disposition-only otherwise)
  * the sidecar persistence      ← ``oversight_cert._persist_certificate`` (the board already reads it)

This module owns ONLY the 5-pillar predicate shape, which is specified in
``docs/evidence/governance-certification-v1.schema.json`` and pinned by
``server/tests/test_governance_certification.py``. That path used to read
``scratchpad/...`` and pointed at a file that existed nowhere — the shape of the
one owned artifact was whatever the code happened to emit. It needs no optional
extra to mint the certificate — only ``cryptography`` (a base dependency).
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Callable, Optional

PREDICATE_TYPE = "https://loomground.org/attestations/GovernanceCertification/v1"
_INTOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
_DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"
# The action-side grounding scheme: a footprint claim rests on the evidence span
# in the action's own text (the matched command fragment) — a real, checkable
# grounding, distinct from the policy-side versum/5d+nd grounding.
ACTION_EVIDENCE_SCHEME = "https://loomground.org/grounding/action-evidence/v1"
# EU AI Act (Reg. (EU) 2024/1689) Article 14 — human oversight of high-risk AI:
# the meta-obligation the oversight requirement itself rests on.
DEFAULT_BASIS = "eu-ai-act-2024-1689-art-14"


def _legitimate_anchors(marker: dict) -> list[dict]:
    """Policy-side grounding: what the verdict was evaluated AGAINST, anchored to
    its sources — the oversight requirement's legal basis, the effective policy
    the matrix applied, and any grounded rule references (span-norm obligation
    pairs) the gate rested on."""
    anchors: list[dict] = [
        {"role": "oversight-obligation", "basis": marker.get("basis") or DEFAULT_BASIS},
        {"role": "effective-policy",
         "oversight_level": marker.get("oversight_level", ""),
         "grade": marker.get("grade", ""),
         "gate_verdict": marker.get("gate_verdict", "")},
    ]
    for op in marker.get("obligation_pairs") or []:
        anchors.append({"role": "policy-rule", "obligation_pair": str(op)})
    return anchors


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _pae(payload_type: str, body: bytes) -> bytes:
    """DSSE Pre-Authentication Encoding (the signed bytes). Spec-exact."""
    t = payload_type.encode("utf-8")
    return (b"DSSEv1 " + str(len(t)).encode() + b" " + t + b" "
            + str(len(body)).encode() + b" " + body)


def dsse_wrap(statement: dict, sign: Callable[[bytes], bytes], keyid: str) -> dict:
    """Wrap an in-toto Statement in a DSSE envelope, signed. Reuse cosign to
    verify — this is the same envelope it emits."""
    body = json.dumps(statement, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = sign(_pae(_DSSE_PAYLOAD_TYPE, body))
    return {
        "payloadType": _DSSE_PAYLOAD_TYPE,
        "payload": base64.b64encode(body).decode("ascii"),
        "signatures": [{"keyid": keyid, "sig": base64.b64encode(sig).decode("ascii")}],
    }


def build_predicate(marker: dict) -> dict:
    """Assemble the 5-pillar GovernanceCertification predicate from a gate event.

    ``verdict`` is ``hold-approved`` (a held action a human then approved — the
    PreToolUse/PostToolUse loop) or ``permit`` (a governed action the gate allowed
    with no human step required — e.g. a policy-cleared egress). The ``overseen``
    pillar reflects that: a permitted action required no human oversight."""
    evidence = marker.get("evidence") or []
    verdict = marker.get("verdict") or "hold-approved"
    overseen: dict[str, Any] = {
        "required": verdict != "permit",
        "qualifier": marker.get("qualification", "unspecified"),
    }
    if verdict != "permit":
        overseen["disposition"] = "DECIDED"   # a human approved the held action
        # oversight_certificate embedded by emit_* iff the [oversight-cert] extra is present
    return {
        "verdict": verdict,
        "action_class": marker.get("action_class", ""),
        "issued_at": marker.get("at", ""),
        # grounding SIGNAL + human-facing risk traffic light. Grounding grounds the
        # POLICY (is the verdict on a grounded policy, or the bare default?); the
        # light organises risk for a human — it is not an automated decision.
        "risk": {
            "grounded": bool(marker.get("grounded")),
            "traffic_light": marker.get("traffic_light") or "amber",
        },
        # THE load-bearing pillar: the action was blocked-unless-permitted.
        "enforced": {
            "mechanism": marker.get("mechanism", "claude-code:PreToolUse"),
            "blocked_unless_permitted": True,
            "decision_ref": marker.get("audit_id", ""),
        },
        "overseen": overseen,
        "grounded": {
            "scheme": ACTION_EVIDENCE_SCHEME,
            "ref": evidence,            # the matched command spans that triggered the footprint
            "digest": {"sha256": _sha256_hex(
                json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8"))},
        },
        "intact": {
            "type": "native-chain",
            "log_id": marker.get("folder", ""),
            "entry_ref": marker.get("audit_id", ""),
            "algorithm": "ed25519+sha256",
        },
        "legitimate": {
            "policy_fingerprint": marker.get("policy_digest", ""),
            "anchors": _legitimate_anchors(marker),
        },
    }


def emit_governance_certification(folder_context: str, *, marker: dict,
                                  log_root: Any = None) -> Optional[dict]:
    """Build → sign → persist a GovernanceCertification for a just-approved held
    action. Returns the DSSE envelope, or ``None`` on any failure (a witness must
    never break the action it records)."""
    try:
        from .oversight_cert import (_persist_certificate, _qualification_for,
                                     rvnd_ed25519_signer)
        sign, _verify, keyid = rvnd_ed25519_signer()

        marker = dict(marker)
        marker.setdefault("qualification",
                          _qualification_for(str(folder_context),
                                             marker.get("agent", ""), log_root))
        predicate = build_predicate(marker)

        # overseen pillar: embed a full portable oversight-certificate when a human
        # was actually required AND the optional extra is installed; otherwise the
        # pillar carries the disposition (or, for a permit, nothing) alone.
        try:
            if not predicate["overseen"].get("required"):
                raise RuntimeError("no human step — nothing to certify")
            from .oversight_cert import certify_decision
            ev = tuple(f"span:{e.get('matched', '')}"
                       for e in (marker.get("evidence") or []) if e.get("matched")) \
                or (f"audit:{marker.get('audit_id', '')}",)
            predicate["overseen"]["oversight_certificate"] = certify_decision(
                decision_id=str(marker.get("audit_id", "") or marker.get("action_class", "")),
                action=str(marker.get("action_class", "")),
                human_id=str(marker.get("agent", "claude-code")),
                qualification=str(marker.get("qualification", "unspecified")),
                evidence=ev, at=str(marker.get("at", "")), sign=sign, keyid=keyid)
        except Exception:
            pass  # extra absent (or issue failed) → overseen carries disposition only

        statement = {
            "_type": _INTOTO_STATEMENT_TYPE,
            "subject": [{"name": marker.get("action_class", "action"),
                         "digest": {"sha256": marker.get("action_digest", "")}}],
            "predicateType": PREDICATE_TYPE,
            "predicate": predicate,
        }
        envelope = dsse_wrap(statement, sign, keyid)
        _persist_certificate(str(folder_context), str(marker.get("audit_id", "")),
                             envelope, log_root)
        return envelope
    except Exception:
        return None


def verify_governance_certification(envelope: dict, *,
                                    verify_sig: Optional[Callable[[bytes, bytes], bool]] = None
                                    ) -> dict:
    """Offline re-check: the DSSE signature over the exact stored payload, plus the
    invention's load-bearing assertion — a certification that does NOT prove
    enforcement (``enforced.blocked_unless_permitted`` true) is rejected. Returns
    ``{ok, findings, statement}``. Uses this host's identity public key unless a
    ``verify_sig`` is supplied (read-only; never generates a key)."""
    try:
        payload = base64.b64decode(envelope["payload"])
        pae = _pae(str(envelope.get("payloadType", _DSSE_PAYLOAD_TYPE)), payload)
        if verify_sig is None:
            from .oversight_cert import _rvnd_verify_sig
            verify_sig = _rvnd_verify_sig()
        if verify_sig is None:
            return {"ok": False, "findings": [{"code": "no-verifier-key",
                    "detail": "no public key available to verify the signature"}]}
        sig_ok = any(verify_sig(pae, base64.b64decode(s["sig"]))
                     for s in (envelope.get("signatures") or []))
        stmt = json.loads(payload)
        pred = stmt.get("predicate") or {}
        findings = []
        if stmt.get("predicateType") != PREDICATE_TYPE:
            findings.append({"code": "wrong-predicate-type",
                             "detail": str(stmt.get("predicateType"))})
        if (pred.get("enforced") or {}).get("blocked_unless_permitted") is not True:
            findings.append({"code": "not-enforcement-bound",
                             "detail": "enforced.blocked_unless_permitted is not true — "
                                       "this certifies nothing the invention requires"})
        if not sig_ok:
            findings.append({"code": "bad-signature",
                             "detail": "DSSE signature did not verify"})
        return {"ok": sig_ok and not findings, "findings": findings, "statement": stmt}
    except Exception as e:
        return {"ok": False, "findings": [{"code": "verify-error",
                "detail": f"{type(e).__name__}: {e}"}]}
