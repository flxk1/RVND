# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Portable oversight-certificate export for RVND's human-oversight acts.

RVND already records every human oversight act as a signed event in the folder's
mutation-log chain (see ``signing.py`` + ``mutation_log.py``). That chain is
*internal*: re-checking it means running RVND's own verifier over the folder.
This module emits the **portable** counterpart — an *oversight certificate* (its
own package, ``github.com/flxk1/oversight-certificate``, MIT) that a third party
re-checks **offline**, from the DSSE envelope and a public key alone, with no
RVND in the loop.

RVND is a *consumer* here and owns none of the certificate semantic: it maps its
own human-oversight vocabulary onto the certificate's three dispositions and
calls the package's ``issue`` / ``verify``. The certificate composes the FOSS
primitives it needs — RFC 8785 canonical bytes, Ed25519 signatures, the in-toto
DSSE envelope; this adapter only supplies RVND's data and, optionally, RVND's own
signing key.

Optional dependency — the base runtime does not need it. Install with::

    pip install 'rvnd[oversight-cert]'

which pulls ``oversight-certificate`` and the ``rfc8785`` canonicaliser. So that a
base install (without the extra) is unaffected, nothing here is imported by
``workspaces/__init__``; consumers import ``workspaces.oversight_cert`` explicitly,
and the extra's packages are imported lazily inside the functions below.

The mapping — a HELD action, once a human touches it:

  * a qualified human RULES (permit or deny)   -> ``Disposition.DECIDED``
  * still routed to a human, pending           -> ``Disposition.ESCALATED``
  * recorded, no decision forced               -> ``Disposition.ABSTAINED``

The certificate proves *that a qualified human took responsibility on the shown
evidence under a named legal obligation* — not which way they ruled. The ruling
itself (permit/deny) rides RVND's internal signed chain; the portable certificate
binds the action, the evidence, the human, and the basis.

Legal basis defaults to EU AI Act (Reg. (EU) 2024/1689) Article 14 — human
oversight of high-risk AI; pass ``basis`` when another obligation governs (e.g.
GDPR Art. 22 for solely-automated decisions).
"""
from __future__ import annotations

import json
from typing import Callable, Optional, Sequence

# EU AI Act (Reg. (EU) 2024/1689) Article 14 — human oversight of high-risk AI.
DEFAULT_BASIS = "eu-ai-act-2024-1689-art-14"

# A DSSE envelope, as returned by ``Envelope.to_dict()`` — a plain JSON-able dict.
Envelope = dict


def _canonicalizer() -> Callable[[dict], bytes]:
    """RFC 8785 (JCS) canonical bytes — the portable, standard form any third
    party reproduces to re-check the signed payload. Lazy import so a base
    install (no ``oversight-cert`` extra) is unaffected."""
    try:
        import rfc8785
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        raise ModuleNotFoundError(
            "portable oversight certificates need the 'oversight-cert' extra: "
            "pip install 'rvnd[oversight-cert]'"
        ) from exc
    return rfc8785.dumps


def certify_decision(
    *,
    decision_id: str,
    action: str,
    human_id: str,
    qualification: str,
    evidence: Sequence[str],
    at: str,
    credential_not_after: Optional[str] = None,
    basis: str = DEFAULT_BASIS,
    sign: Callable[[bytes], bytes],
    keyid: str = "",
    canonicalize: Optional[Callable[[dict], bytes]] = None,
) -> Envelope:
    """A qualified human RULED on a held action → a signed DECIDED certificate,
    returned as a DSSE envelope ``dict`` ready to store or hand to an auditor.

    ``evidence`` are content hashes of exactly what the overseer was shown.
    ``at`` and ``credential_not_after`` are ISO-8601; a credential that lapses
    *after* ``at`` does not void the record (:func:`recheck` proves this).
    ``sign`` maps signed bytes → raw signature bytes (see :func:`rvnd_ed25519_signer`).
    Raises ``oversight_certificate.InvalidCertificate`` if the inputs cannot form
    a coherent certificate (e.g. empty ``evidence``)."""
    from oversight_certificate import (Disposition, Human,
                                       OversightCertificate, issue)

    cert = OversightCertificate(
        id=decision_id, action=action, disposition=Disposition.DECIDED, at=at,
        basis=basis, evidence=tuple(evidence),
        human=Human(human_id, qualification, credential_not_after),
    )
    return issue(cert, canonicalize=canonicalize or _canonicalizer(),
                 sign=sign, keyid=keyid).to_dict()


def certify_escalation(
    *,
    decision_id: str,
    action: str,
    escalated_to: str,
    evidence: Sequence[str],
    at: str,
    basis: str = DEFAULT_BASIS,
    sign: Callable[[bytes], bytes],
    keyid: str = "",
    canonicalize: Optional[Callable[[dict], bytes]] = None,
) -> Envelope:
    """The machine refused and ROUTED the action to a human — pending, with the
    evidence attached → a signed ESCALATED certificate. Carries no human decision
    (it has not happened yet); ``escalated_to`` names the queue or person it went
    to."""
    from oversight_certificate import (Disposition, OversightCertificate, issue)

    cert = OversightCertificate(
        id=decision_id, action=action, disposition=Disposition.ESCALATED, at=at,
        basis=basis, evidence=tuple(evidence), escalated_to=escalated_to,
    )
    return issue(cert, canonicalize=canonicalize or _canonicalizer(),
                 sign=sign, keyid=keyid).to_dict()


def certify_abstention(
    *,
    decision_id: str,
    action: str,
    at: str,
    evidence: Sequence[str] = (),
    basis: str = DEFAULT_BASIS,
    sign: Callable[[bytes], bytes],
    keyid: str = "",
    canonicalize: Optional[Callable[[dict], bytes]] = None,
) -> Envelope:
    """No decision was in scope — the outcome is *recorded, not forced* → a signed
    ABSTAINED certificate. The honest record of a non-decision, so a later audit
    sees the abstention rather than a silent gap."""
    from oversight_certificate import (Disposition, OversightCertificate, issue)

    cert = OversightCertificate(
        id=decision_id, action=action, disposition=Disposition.ABSTAINED, at=at,
        basis=basis, evidence=tuple(evidence),
    )
    return issue(cert, canonicalize=canonicalize or _canonicalizer(),
                 sign=sign, keyid=keyid).to_dict()


def recheck(
    envelope: Envelope,
    *,
    verify_sig: Callable[[bytes, bytes], bool],
    now: str,
    required_basis: Optional[str] = None,
    canonicalize: Optional[Callable[[dict], bytes]] = None,
):
    """Re-check a certificate from the envelope and a public verify function
    ALONE — the same call a third-party auditor runs offline. Returns the
    package's ``Report`` (``.ok`` plus ``.findings``); it locates defects but does
    not rule whether the oversight was legally *sufficient* — that stays with the
    auditor. ``verify_sig`` maps (signed bytes, signature bytes) → bool."""
    from oversight_certificate import verify

    return verify(envelope, canonicalize=canonicalize or _canonicalizer(),
                  verify_sig=verify_sig, now=now, required_basis=required_basis)


def rvnd_ed25519_signer() -> tuple[Callable[[bytes], bytes],
                                   Callable[[bytes, bytes], bool], str]:
    """Return ``(sign, verify_sig, keyid)`` backed by RVND's own identity keypair
    (``signing.ensure_keypair``), so the portable certificate is signed by the very
    key that anchors this host's audit chain — one key, two proofs (the internal
    chain and the portable certificate). ``keyid`` is RVND's public-key fingerprint,
    stamped into the DSSE envelope so a verifier knows which published key to pull.

    Honours ``WORKSPACE_KEY_DIR``; generates the keypair on first use like the rest
    of RVND's signing path."""
    from cryptography.exceptions import InvalidSignature

    from .signing import ensure_keypair, fingerprint_of

    priv, pub = ensure_keypair()

    def sign(data: bytes) -> bytes:
        return priv.sign(data)

    def verify_sig(data: bytes, sig: bytes) -> bool:
        try:
            pub.verify(sig, data)
            return True
        except (InvalidSignature, ValueError):
            return False

    return sign, verify_sig, fingerprint_of(pub)[:16]


def _qualification_for(folder_context: str, actor: str, log_root=None) -> str:
    """Best-effort qualification string for a party — ``role[:competences]`` from the
    workspace roster (the data RVND already keeps and routes approvers by). Never
    raises; returns ``"unspecified"`` when the roster can't be read or the party is
    unknown."""
    try:
        from .parties import list_parties
        roster = list_parties(str(folder_context),
                              log_root=str(log_root) if log_root else None)
        row = next((p for p in roster.get("parties", [])
                    if p.get("party_id") == str(actor)), None)
        if not row:
            return "unspecified"
        role = str(row.get("role") or "").strip()
        comps = [str(c) for c in (row.get("competences") or []) if str(c).strip()]
        return (role + (":" + ",".join(comps) if comps else "")) or "unspecified"
    except Exception:
        return "unspecified"


def _persist_certificate(folder_context: str, audit_id: str, envelope: dict,
                         log_root=None) -> None:
    """Append the certificate to the folder's ``oversight_certs.jsonl`` sidecar,
    beside the signed chain — never mutating the chain (so ``verify_chain`` is
    untouched). Refuses to write beside a SEALED store. Best-effort; never raises."""
    try:
        from pathlib import Path as _Path

        from .mutation_log import MutationLog
        ml = MutationLog(_Path(folder_context),
                         log_root=_Path(log_root) if log_root else None)
        if getattr(ml, "_is_sealed", lambda: False)():
            return
        path = ml.log_dir / "oversight_certs.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"audit_id": audit_id, "certificate": envelope},
                                separators=(",", ":")) + "\n")
    except Exception:
        pass


def emit_decision_certificate(folder_context: str, *, actor: str, action: str,
                              evidence_refs: Sequence[str], at: str,
                              audit_id: str = "", basis: str = DEFAULT_BASIS,
                              log_root=None) -> Optional[dict]:
    """Emit + persist the portable oversight certificate for a just-recorded human
    DECISION, returning the DSSE envelope dict — or ``None`` on any failure (a
    witness must never break the decision it records, so every failure is
    swallowed). Signed by RVND's own identity key.

    ``credential_not_after`` is ``None`` in this phase: RVND does not yet model a
    human's credential-validity window, so the certificate proves *a qualified party
    decided on the shown evidence under a named law*, and the valid-at-decision-time
    check stays dormant until that window is modelled (phase b)."""
    try:
        ev = tuple(str(e) for e in (evidence_refs or []) if str(e).strip())
        if not ev and audit_id:
            ev = (f"audit:{audit_id}",)          # the chain event as the minimal anchor
        if not ev:
            return None                          # a DECIDED certificate must carry evidence
        sign, _verify, keyid = rvnd_ed25519_signer()
        env = certify_decision(
            decision_id=str(audit_id or action), action=str(action),
            human_id=str(actor),
            qualification=_qualification_for(folder_context, actor, log_root),
            evidence=ev, at=str(at), credential_not_after=None,
            basis=basis, sign=sign, keyid=keyid)
        _persist_certificate(folder_context, str(audit_id or ""), env, log_root)
        return env
    except Exception:
        return None


def _rvnd_verify_sig():
    """Read-only Ed25519 verify closure from this host's identity public key, or
    ``None`` if no keypair exists. Never generates a key — verification must not
    write."""
    try:
        from cryptography.exceptions import InvalidSignature

        from .signing import identity_public_key_or_none
        pub = identity_public_key_or_none()
        if pub is None:
            return None

        def verify_sig(data: bytes, sig: bytes) -> bool:
            try:
                pub.verify(sig, data)
                return True
            except (InvalidSignature, ValueError):
                return False
        return verify_sig
    except Exception:
        return None


def _verify_sig_from_pem(pem):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    pub = load_pem_public_key(pem.encode("utf-8") if isinstance(pem, str) else pem)

    def verify_sig(data: bytes, sig: bytes) -> bool:
        try:
            pub.verify(sig, data)
            return True
        except (InvalidSignature, ValueError):
            return False
    return verify_sig


def verify_certificate(envelope, *, now: str, required_basis: Optional[str] = None,
                       public_key_pem=None) -> dict:
    """Re-check a certificate and return ``{ok, findings:[{code, detail}]}`` — the
    offline third-party check, exposed for the app (the ``oversight_cert_verify``
    op). Verifies against a supplied PEM public key when given, else this host's
    identity public key (read-only; never generates one)."""
    try:
        verify_sig = (_verify_sig_from_pem(public_key_pem) if public_key_pem
                      else _rvnd_verify_sig())
        if verify_sig is None:
            return {"ok": False, "findings": [{"code": "no-verifier-key",
                    "detail": "no public key available to verify the signature"}]}
        rep = recheck(envelope if isinstance(envelope, dict) else {},
                      verify_sig=verify_sig, now=str(now),
                      required_basis=required_basis)
        return {"ok": bool(rep.ok),
                "findings": [{"code": f.code, "detail": f.detail}
                             for f in rep.findings]}
    except ModuleNotFoundError as e:
        return {"ok": False, "findings": [{"code": "extra-missing",
                "detail": str(e)}]}
    except Exception as e:
        return {"ok": False, "findings": [{"code": "verify-error",
                "detail": f"{type(e).__name__}: {e}"}]}
