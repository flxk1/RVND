# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Core middleware: modes, detectors, capability tokens, audit log, egress/ingress orchestration.

Compact v0. All four detector tiers live here for clarity at this scale. Tier C is stubbed.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Literal


# ===========================================================================
# Modes
# ===========================================================================


class Mode(Enum):
    STANDARD = "standard"      # strip + warn
    STRICT = "strict"           # block on violation
    PERMISSIVE = "permissive"   # detect only, never block or strip
    AUDIT_ONLY = "audit_only"   # record everything, mutate nothing


# ===========================================================================
# Data structures
# ===========================================================================


@dataclass
class CapabilityToken:
    """Capability claims plus an optional detached Ed25519 signature."""

    iss: str
    sub: str
    aud: str            # the tool this token authorises
    iat: int            # issued-at (unix seconds)
    exp: int            # expiry (unix seconds)
    scope: dict         # {regions, identifier_classes, retention_class, fields, purpose}
    controller: str
    task_id: str
    signature: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "CapabilityToken":
        claims = {k: d[k] for k in cls.__dataclass_fields__ if k != "signature"}
        return cls(**claims, signature=d.get("signature", ""))

    def signed_bytes(self) -> bytes:
        """Return the stable UTF-8 payload covered by ``signature``."""
        claims = asdict(self)
        claims.pop("signature", None)
        return json.dumps(
            claims, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")

    def sign(self, private_key: Any) -> None:
        """Attach a hex-encoded Ed25519 signature from an issuer key."""
        self.signature = private_key.sign(self.signed_bytes()).hex()


@dataclass
class ToolCall:
    tool: str
    arguments: dict
    capability_token: CapabilityToken | None = None


@dataclass
class ToolResponse:
    payload: dict


@dataclass
class RemediationAction:
    """A single user-actionable remediation attached to a Finding (0.6.8 B3).

    Surfaces (CLI / chat / generic MCP) read the canonical action set off the
    Finding and render each option in their native idiom. The runtime owns the
    *what* (the three canonical actions and their payloads); surfaces own the
    *how* (button vs numbered choice vs JSON dict).

    ``kind`` is one of:
      - ``redact_and_retry``: payload carries a ``redacted_text`` the caller
        may re-submit in place of the original.
      - ``bypass_once``: payload sets ``acknowledgement_required = True`` so
        the surface knows to demand an explicit per-prompt ack before resend.
      - ``disable_lock``: payload carries the CLI invocation, MCP tool name,
        and the canonical disclaimer URL the user must read before opting out.
    """

    kind: Literal["redact_and_retry", "bypass_once", "disable_lock"]
    label: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Finding:
    tier: str               # "A" | "B" | "C"
    type: str               # "over_collection" | "pii_in_argument" | "pii_in_response" | "token_invalid" | "schema_violation"
    severity: str           # "low" | "medium" | "high"
    field: str | None       # field name or None for whole-message findings
    detail: str
    confidence: float = 1.0
    remediation_actions: list[RemediationAction] = field(default_factory=list)

    @property
    def finding_id(self) -> str:
        """Stable identity, derived from what the finding says.

        An ``OversightDecision`` records what a user decided about ONE finding.
        Every producer used to fill its ``finding_id`` with a fresh uuid4, so the
        decision pointed at nothing and the audit could not answer "which finding
        did the operator accept?" -- the one question that record exists for.

        Content-derived rather than allocated, so it needs no new state and so
        the same finding carries the same id across processes and reviews.
        ``remediation_actions`` is excluded: it is advice about the finding, not
        part of what the finding IS, and it may be enriched later.
        """
        import hashlib
        parts = (self.tier, self.type, self.severity, self.field or "", self.detail)
        return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]
    """Canonical set of user-actionable next steps for this finding (0.6.8 B3).

    Populated by Tier B / Tier B+ when the finding represents PII that the
    user can plausibly redact, acknowledge, or opt out of detecting. Empty
    for findings where no remediation makes sense (e.g. schema-level Tier A
    over-collection — handled by the egress decider, not the user).
    """


@dataclass
class EgressDecision:
    action: str             # "allow" | "strip" | "refuse"
    findings: list[Finding] = field(default_factory=list)
    modified_call: ToolCall | None = None
    stripped_fields: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class IngressDecision:
    action: str             # "allow" | "redact"
    findings: list[Finding] = field(default_factory=list)
    redacted_payload: dict | None = None
    reason: str = ""


@dataclass
class TextDecision:
    """Approval decision for a single text/document/triple about to leave the local boundary.

    Used by lock_text() for the document- and KG-triple-approval surface.
    Action semantics:
        - "allow"    : pass to cloud as-is
        - "minimise" : pass `redacted_text` to cloud (regex-matched spans replaced)
        - "refuse"   : do not send; caller must escalate to user or abort
    """

    action: str             # "allow" | "minimise" | "refuse"
    findings: list[Finding] = field(default_factory=list)
    redacted_text: str | None = None
    reason: str = ""
    source: str = "document"   # "document" | "triple" | "freeform" — diagnostic only


# ===========================================================================
# Tier A — Schema-level minimisation
# ===========================================================================


def tier_a_check_arguments(arguments: dict, task_scope: set[str]) -> list[Finding]:
    """Tier A on egress — flag arguments that request fields not in the task scope."""
    findings: list[Finding] = []
    requested_fields = _flatten_argument_fields(arguments)
    over_collected = requested_fields - task_scope
    for f in sorted(over_collected):
        findings.append(
            Finding(
                tier="A",
                type="over_collection",
                severity="medium",
                field=f,
                detail=f"argument requests field '{f}' not in declared task scope",
            )
        )
    return findings


def tier_a_check_response(payload: dict, task_scope: set[str]) -> list[Finding]:
    """Tier A on ingress — flag response fields not in the task scope."""
    findings: list[Finding] = []
    returned_fields = set(payload.keys())
    over_returned = returned_fields - task_scope
    for f in sorted(over_returned):
        findings.append(
            Finding(
                tier="A",
                type="over_collection",
                severity="medium",
                field=f,
                detail=f"response field '{f}' not in declared task scope",
            )
        )
    return findings


def _flatten_argument_fields(arguments: dict, prefix: str = "") -> set[str]:
    """Return the set of dotted-path field names requested. Conservative: nested dicts expanded."""
    fields: set[str] = set()
    for k, v in arguments.items():
        path = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        fields.add(path)
        if isinstance(v, dict):
            fields |= _flatten_argument_fields(v, path)
    return fields


# ===========================================================================
# Tier B — Regex / dictionary detection
# ===========================================================================


# Conservative regexes. Tuned for low false positives on workplace-agent data.
# Pattern set ported from an earlier local lock implementation — covers
# 12 PII shapes including Luhn-validated credit-card and high-entropy API
# keys / bearer tokens.

_EMAIL_RE   = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE   = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}")
_IBAN_RE    = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{1,30}\b")
_US_SSN_RE  = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Back-compat alias — older callers used _NATIONAL_ID_RE before the rename
# to us_ssn. Keep as an alias so any external callers continue to resolve.
_NATIONAL_ID_RE = _US_SSN_RE

# URL with embedded credentials (scheme://user:pass@host).
_URL_CREDS_RE = re.compile(
    r"(?P<scheme>[a-z]{2,10})://[^/\s:@]+:[^/\s@]+@[^\s]+"
)

# Bearer tokens (after literal "Bearer " keyword).
_BEARER_RE = re.compile(
    r"(?<=\bBearer )[A-Za-z0-9._\-/+=]{16,}"
)

# Known API-key prefixes (Stripe, GitHub, OpenAI, AWS, Google, Slack, HF).
_API_KEY_RE = re.compile(
    r"\b(?:sk_(?:live|test)_|sk-|ghp_|github_pat_|xox[baprs]-|"
    r"AIza|AKIA|ASIA|EAA|hf_)[A-Za-z0-9_\-]{10,}\b"
)

# IP addresses.
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b")

# UK NINO (National Insurance Number).
_UK_NINO_RE = re.compile(
    r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b"
)

# DE Personalausweis (ID-card serial; new format).
_DE_PERS_RE = re.compile(r"\b[CFGHJK]\w{8}\b")

# Credit-card candidate — accepted only if Luhn checksum validates.
_CC_CANDIDATE_RE = re.compile(r"\b(?:\d[ \-]?){13,19}\b")

# ---------------------------------------------------------------------------
# B8.1 (0.6.8): four additional pattern groups commonly missed by the
# original Tier B set. Each pattern is documented with what it matches +
# the typical false-positive shape it tolerates.
# ---------------------------------------------------------------------------

# 1) Names in possessive context — title (Mr/Mrs/Ms/Mx/Dr/Prof/Sir/Lady/etc.)
#    + a capitalised name + apostrophe-s. The title gate keeps false
#    positives (random English possessives) low. Matches multi-word names
#    up to four words.
#    Examples caught: "Mr. Smith's", "Dr. Jane Doe's", "Prof Müller's"
_NAME_POSSESSIVE_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Mx|Dr|Prof|Professor|Sir|Madam|Lady|Lord|Rev|Fr|"
    r"Herr|Frau|Sr|Sra|Sgt|Capt|Lt|Col|Gen|Hon)\.?\s+"
    r"(?:[A-ZÄÖÜÅÉÈÊÁÀÆØ][\w'’\-]+\s*){1,4}"
    r"[’']s\b"
)

# 2) Government ID formats.
#
# 2a) German Steuer-ID — 11 digits, often spaced as 12 345 678 902 or
#     unspaced. We accept either, anchored to word boundaries with optional
#     internal whitespace.
_DE_STEUER_ID_RE = re.compile(r"\b\d{2}\s?\d{3}\s?\d{3}\s?\d{3}\b")

# 2b) German Personalausweis number (new format, alphanumeric, 10 chars
#     starting with one of the standard letters). The existing
#     ``_DE_PERS_RE`` covers a related shape (9 chars after a letter); this
#     one covers the alternate 10-char layout.
_DE_PERSONALAUSWEIS_10_RE = re.compile(
    r"\b[CFGHJKLMNPRTVWXYZ][0-9A-Z]{9}\b"
)

# 2c) Spanish DNI / NIE — 8 digits + check letter (DNI), or letter X/Y/Z +
#     7 digits + check letter (NIE). Both anchored on word boundaries.
_ES_DNI_NIE_RE = re.compile(
    r"\b(?:\d{8}|[XYZ]\d{7})[A-HJ-NP-TV-Z]\b"
)

# 2d) French INSEE / SSN — 15 digits with the documented structure:
#     1 digit (sex) + 2 digits (year) + 2 digits (month) + 2-3 digits +
#     3 digits + 3 digits + 2 digits (control). Forgiving on internal
#     whitespace.
_FR_SSN_RE = re.compile(
    r"\b[12]\s?\d{2}\s?(?:0\d|1[0-2])\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b"
)

# 3) Medical / health identifiers.
#
# 3a) Patient/case ID: "patient #N", "case ID 12345", "case number 12345".
_PATIENT_CASE_ID_RE = re.compile(
    r"\b(?:patient|case|client)\s*(?:#|id|number|no\.?)\s*\d{3,10}\b",
    re.IGNORECASE,
)

# 3b) Medical Record Number — "MRN: ...", "MRN# ...", "MRN ...".
_MRN_RE = re.compile(
    r"\bMRN\s*[:#-]?\s*[A-Z0-9-]{4,15}\b",
    re.IGNORECASE,
)

# 3c) ICD-10 codes — letter + 2 digits + optional .NN extension.
_ICD10_RE = re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d{1,3})?\b")

# 4) IBAN with country prefix + checksum — broader than the existing
#    IBAN regex (which is permissive: AA00 + 1–30 chars). This one
#    enforces the per-country length set documented by the EBA so
#    we catch IBANs that span the full structure without admitting
#    arbitrary AA00... strings.
#    Covered country codes (most EU + a few neighbours).
_IBAN_FULL_RE = re.compile(
    r"\b(?:"
    r"AT\d{2}[0-9]{16}|"          # Austria
    r"BE\d{2}[0-9]{12}|"          # Belgium
    r"BG\d{2}[A-Z]{4}[0-9]{6}[A-Z0-9]{8}|"  # Bulgaria
    r"CH\d{2}[0-9]{5}[A-Z0-9]{12}|"          # Switzerland
    r"CZ\d{2}[0-9]{20}|"          # Czechia
    r"DE\d{2}[0-9]{18}|"          # Germany
    r"DK\d{2}[0-9]{14}|"          # Denmark
    r"ES\d{2}[0-9]{20}|"          # Spain
    r"FI\d{2}[0-9]{14}|"          # Finland
    r"FR\d{2}[0-9]{10}[A-Z0-9]{11}[0-9]{2}|"  # France
    r"GB\d{2}[A-Z]{4}[0-9]{14}|"  # United Kingdom
    r"IE\d{2}[A-Z]{4}[0-9]{14}|"  # Ireland
    r"IT\d{2}[A-Z][0-9]{10}[A-Z0-9]{12}|"     # Italy
    r"LU\d{2}[0-9]{3}[A-Z0-9]{13}|"            # Luxembourg
    r"NL\d{2}[A-Z]{4}[0-9]{10}|"  # Netherlands
    r"PL\d{2}[0-9]{24}|"          # Poland
    r"PT\d{2}[0-9]{21}|"          # Portugal
    r"SE\d{2}[0-9]{20}"           # Sweden
    r")\b"
)


def _luhn_ok(digits_only: str) -> bool:
    """Standard Luhn checksum. Gates credit_card matches so 13-19 digit
    strings that aren't actually card numbers (timestamps, order IDs)
    don't trigger a redaction."""
    if not digits_only.isdigit() or not (13 <= len(digits_only) <= 19):
        return False
    total = 0
    parity = len(digits_only) % 2
    for i, ch in enumerate(digits_only):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _cc_search(text: str):
    """Find a credit-card candidate that passes Luhn. Returns the Match or
    None (mimics re.search return shape for the caller's convenience)."""
    for m in _CC_CANDIDATE_RE.finditer(text):
        digits = re.sub(r"[ \-]", "", m.group(0))
        if _luhn_ok(digits):
            return m
    return None


# Pattern table — order = match priority. More specific patterns first so
# (e.g.) an api_key beats a generic-looking phone-number false-positive,
# and a UK NINO (very specific structure) is detected before the broader
# IBAN regex (any AA00... shape) can swallow it.
_TIER_B_PATTERNS: list[tuple[str, "object", float]] = [
    # (label, matcher, confidence). Matcher is either a compiled regex
    # with .search(text), or a callable that returns Match-or-None.
    ("url_with_creds",      _URL_CREDS_RE,            0.98),
    ("api_key",             _API_KEY_RE,              0.95),
    ("bearer_token",        _BEARER_RE,               0.92),
    ("email",               _EMAIL_RE,                0.90),
    ("us_ssn",              _US_SSN_RE,               0.95),
    ("uk_nino",             _UK_NINO_RE,              0.95),   # ← before iban (more specific)
    # B8.1: 4 additional pattern groups added in 0.6.8 to close gaps in
    # the privacy-bench.  All severity=high.
    ("iban_full",           _IBAN_FULL_RE,            0.97),   # ← before iban (strict per-country)
    ("fr_ssn",              _FR_SSN_RE,               0.95),   # before phone (more specific)
    ("de_steuer_id",        _DE_STEUER_ID_RE,         0.90),   # before phone
    ("es_dni_nie",          _ES_DNI_NIE_RE,           0.93),
    ("de_personalausweis_10", _DE_PERSONALAUSWEIS_10_RE, 0.85),
    ("de_personnummer",     _DE_PERS_RE,              0.85),
    ("iban",                _IBAN_RE,                 0.95),
    ("credit_card",         _cc_search,               0.95),   # callable, Luhn-gated
    ("ipv6",                _IPV6_RE,                 0.90),
    ("ipv4",                _IPV4_RE,                 0.85),
    ("mrn",                 _MRN_RE,                  0.92),   # medical record number
    ("patient_case_id",     _PATIENT_CASE_ID_RE,      0.85),
    ("icd10",               _ICD10_RE,                0.75),
    ("name_possessive",     _NAME_POSSESSIVE_RE,      0.80),
    ("phone",               _PHONE_RE,                0.85),
]


# Per-pattern redactor map — used by remediation builder to compose a
# ``redacted_text`` that swaps the matched span(s) for a typed placeholder.
# Each entry is (label, callable taking text -> redacted_text). Keep in sync
# with the labels in _TIER_B_PATTERNS above.
def _redact_for_label(text: str, label: str, placeholder: str | None = None) -> str:
    """Replace matches of the named pattern with ``[REDACTED:<label>]``.

    Pattern selection is by label so the remediation block can offer a
    surgical "redact just this PII type and try again" without nuking other
    text the user wrote. credit_card is special-cased via the Luhn-gated
    callable; everything else is a compiled regex. ``placeholder`` overrides
    the substituted marker (the minimise path uses ``[REDACTED-<LABEL>]``).
    """
    if placeholder is None:
        placeholder = f"[REDACTED:{label}]"
    if label == "credit_card":
        # Repeatedly find + replace Luhn-valid candidates until none remain.
        out = text
        while True:
            m = _cc_search(out)
            if m is None:
                break
            out = out[:m.start()] + placeholder + out[m.end():]
        return out
    regex_map = {
        "url_with_creds":         _URL_CREDS_RE,
        "api_key":                _API_KEY_RE,
        "bearer_token":           _BEARER_RE,
        "email":                  _EMAIL_RE,
        "us_ssn":                 _US_SSN_RE,
        "uk_nino":                _UK_NINO_RE,
        "de_personnummer":        _DE_PERS_RE,
        "iban":                   _IBAN_RE,
        "iban_full":              _IBAN_FULL_RE,
        "fr_ssn":                 _FR_SSN_RE,
        "de_steuer_id":           _DE_STEUER_ID_RE,
        "es_dni_nie":             _ES_DNI_NIE_RE,
        "de_personalausweis_10":  _DE_PERSONALAUSWEIS_10_RE,
        "mrn":                    _MRN_RE,
        "patient_case_id":        _PATIENT_CASE_ID_RE,
        "icd10":                  _ICD10_RE,
        "name_possessive":        _NAME_POSSESSIVE_RE,
        "ipv6":                   _IPV6_RE,
        "ipv4":                   _IPV4_RE,
        "phone":                  _PHONE_RE,
    }
    rx = regex_map.get(label)
    if rx is None:
        return text
    return rx.sub(placeholder, text)


def _build_remediation_actions(
    finding: Finding,
    original_text: str,
) -> list[RemediationAction]:
    """Build the canonical three-action remediation block for a Finding (B3).

    Order is stable across surfaces so an end-user persona who learns "option
    1 is redact, option 2 is send anyway, option 3 is disable" sees the same
    affordances every time. Surfaces render them differently but cannot
    re-order or substitute.
    """
    # Derive the pattern label from the finding's detail string. Tier B
    # populates ``detail="regex matched pattern: <label>"``; B+ populates
    # ``detail="Confusable-Unicode bypass detected: ..."`` and we fall back
    # to a generic redaction in that case.
    label = ""
    detail = finding.detail or ""
    marker = "regex matched pattern: "
    if marker in detail:
        label = detail.split(marker, 1)[1].strip()

    if label:
        redacted = _redact_for_label(original_text, label)
    else:
        # B+ / unknown-label fallback: apply the broad redaction sweep so the
        # user still has something to retry with.
        redacted = _redact_text_with_regex(original_text)

    return [
        RemediationAction(
            kind="redact_and_retry",
            label="Redact this and try again",
            payload={"redacted_text": redacted},
        ),
        RemediationAction(
            kind="bypass_once",
            label="Send anyway (this one prompt, this session)",
            payload={"acknowledgement_required": True},
        ),
        RemediationAction(
            kind="disable_lock",
            label="Disable lock for this folder (requires disclaimer)",
            payload={
                "cli": (
                    "workspaces policy disable-lock --folder X "
                    "--i-accept-the-risk --reason '...'"
                ),
                "mcp": "policy_disable_lock",
                "disclaimer_url": "https://rvnd.dev/disclaimer",
            },
        ),
    ]


def tier_b_scan_text(text: str) -> list[Finding]:
    """Tier B regex scan. Returns findings keyed by pattern type.

    Pattern set covers 12 PII shapes; credit_card matches are Luhn-gated
    to avoid false-positives on arbitrary digit strings.

    Each finding carries a ``remediation_actions`` block (B3) the surface
    can render verbatim — redact-and-retry, bypass-once, disable-lock.

    NOTE: this is the canonical-form regex tier ONLY. Obfuscated PII (homoglyphs,
    zero-width splits, spacing) is deliberately NOT normalised away here — the
    classifier ESCALATES such input to tier_b_plus (more oversight) rather than
    folding it into a clean match, which would DOWNGRADE the oversight signal.
    """
    findings: list[Finding] = []
    for label, matcher, conf in _TIER_B_PATTERNS:
        hit = matcher.search(text) if hasattr(matcher, "search") else matcher(text)
        if hit:
            f = Finding(
                tier="B",
                type="pii_in_argument",   # reused for both directions
                severity="high",
                field=None,
                detail=f"regex matched pattern: {label}",
                confidence=conf,
            )
            f.remediation_actions = _build_remediation_actions(f, text)
            findings.append(f)
    return findings


def tier_b_scan_dict(data: dict) -> list[Finding]:
    """Recursively scan free-text values in a dict."""
    findings: list[Finding] = []
    for key, value in data.items():
        if isinstance(value, str):
            for f in tier_b_scan_text(value):
                f.field = key
                findings.append(f)
        elif isinstance(value, dict):
            findings.extend(tier_b_scan_dict(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    findings.extend(tier_b_scan_dict(item))
                elif isinstance(item, str):
                    for f in tier_b_scan_text(item):
                        f.field = key
                        findings.append(f)
    return findings


# ===========================================================================
# Capability-token validation
# ===========================================================================


@dataclass
class TokenValidation:
    valid: bool
    findings: list[Finding] = field(default_factory=list)


def validate_token(token: CapabilityToken | None, call: ToolCall) -> TokenValidation:
    """Validate capability-token claims against the call.

    Semantic validation (exp, aud, scope) is always enforced here. When
    ``LOCK_BETA_STRICT_TOKEN_SIG=1``, the detached Ed25519 signature must
    verify against the token issuer's key in ``LOCK_CAPABILITY_TRUST_STORE``.
    The default remains semantic-only for compatibility.
    """
    findings: list[Finding] = []
    if token is None:
        findings.append(
            Finding(
                tier="A",
                type="token_invalid",
                severity="low",
                field=None,
                detail="no capability token attached; falling back to inferred scope",
            )
        )
        return TokenValidation(valid=False, findings=findings)

    if os.environ.get("LOCK_BETA_STRICT_TOKEN_SIG") == "1":
        verified, reason = _verify_capability_signature(token)
        if not verified:
            findings.append(Finding(
                tier="A", type="token_invalid", severity="high", field=None,
                detail=f"strict-token-sig mode: {reason}",
            ))
            return TokenValidation(valid=False, findings=findings)

    now = int(time.time())
    if token.exp <= now:
        findings.append(
            Finding(
                tier="A",
                type="token_invalid",
                severity="high",
                field=None,
                detail=f"token expired at {token.exp}; now {now}",
            )
        )
        return TokenValidation(valid=False, findings=findings)

    if token.aud != call.tool:
        findings.append(
            Finding(
                tier="A",
                type="token_invalid",
                severity="high",
                field=None,
                detail=f"token audience '{token.aud}' does not match tool '{call.tool}'",
            )
        )
        return TokenValidation(valid=False, findings=findings)

    return TokenValidation(valid=True)


def _verify_capability_signature(token: CapabilityToken) -> tuple[bool, str]:
    """Verify a token using the operator-owned issuer trust store."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    store_path = os.environ.get("LOCK_CAPABILITY_TRUST_STORE")
    if not store_path:
        return False, "LOCK_CAPABILITY_TRUST_STORE is not configured"
    try:
        store = json.loads(Path(store_path).read_text(encoding="utf-8"))
        pem = store[token.iss]
        if not isinstance(pem, str):
            raise TypeError("issuer key is not text")
        public_key = serialization.load_pem_public_key(pem.encode("utf-8"))
        if not isinstance(public_key, Ed25519PublicKey):
            return False, f"trusted key for issuer '{token.iss}' is not Ed25519"
        public_key.verify(bytes.fromhex(token.signature), token.signed_bytes())
        return True, ""
    except KeyError:
        return False, f"issuer '{token.iss}' is not trusted"
    except FileNotFoundError:
        return False, "capability trust store does not exist"
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return False, "capability trust store or signature is malformed"
    except InvalidSignature:
        return False, "capability signature is invalid"


# ===========================================================================
# Audit log
# ===========================================================================


class AuditLog:
    """JSONL appender. Records schemas + decisions, NOT raw values."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write_egress(
        self,
        call: ToolCall,
        decision: EgressDecision,
        mode: Mode,
        task_id: str | None = None,
    ):
        entry = {
            "ts": time.time(),
            "kind": "egress",
            "tool": call.tool,
            "argument_schema": sorted(_flatten_argument_fields(call.arguments)),
            "action": decision.action,
            "findings_count": len(decision.findings),
            "findings": [_finding_summary(f) for f in decision.findings],
            "stripped_fields": decision.stripped_fields,
            "mode": mode.value,
            "task_id": task_id,
            "audit_id": str(uuid.uuid4()),
        }
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def write_ingress(
        self,
        response: ToolResponse,
        decision: IngressDecision,
        mode: Mode,
        task_id: str | None = None,
    ):
        entry = {
            "ts": time.time(),
            "kind": "ingress",
            "response_schema": sorted(response.payload.keys()),
            "action": decision.action,
            "findings_count": len(decision.findings),
            "findings": [_finding_summary(f) for f in decision.findings],
            "mode": mode.value,
            "task_id": task_id,
            "audit_id": str(uuid.uuid4()),
        }
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def write_text(
        self,
        text_length: int,
        decision: "TextDecision",
        mode: Mode,
        task_id: str | None = None,
    ):
        """Audit a lock_text() decision. Records length + decision, NEVER raw text."""
        entry = {
            "ts": time.time(),
            "kind": "text",
            "source": decision.source,
            "text_length": text_length,
            "action": decision.action,
            "findings_count": len(decision.findings),
            "findings": [_finding_summary(f) for f in decision.findings],
            "mode": mode.value,
            "task_id": task_id,
            "audit_id": str(uuid.uuid4()),
        }
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def write_bypass(
        self,
        would_have: str,
        decision: "TextDecision",
        *,
        oversight: Any = None,
        final_action: str = "",
        source: str = "document",
        reason: str = "",
        task_id: str | None = None,
    ):
        """Audit a Privacy-Lock-OFF bypass: the gate would have ``would_have``
        ('refuse'/'minimise') but the folder has lock disabled. Records what
        protection was in effect, the oversight level, and the final action
        (ask_user vs allow) so a reviewer can reconstruct the bypass — off
        disables enforcement, never the audit trail (CL2). NEVER raw text."""
        if would_have not in ("refuse", "minimise"):
            raise ValueError(
                f"would_have must be 'refuse' or 'minimise', got: {would_have!r}")
        entry = {
            "ts": time.time(),
            "kind": "lock_bypass",
            "source": source,
            "would_have": would_have,
            "oversight": getattr(oversight, "label", None) or (
                str(oversight) if oversight is not None else ""),
            "final_action": final_action,
            "findings_count": len(decision.findings),
            "findings": [_finding_summary(f) for f in decision.findings],
            "reason": reason,
            "task_id": task_id,
            "audit_id": str(uuid.uuid4()),
        }
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")


def _finding_summary(f: Finding) -> dict:
    return {
        "tier": f.tier,
        "type": f.type,
        "severity": f.severity,
        "field": f.field,
        "confidence": f.confidence,
    }


# ===========================================================================
# Egress / Ingress orchestration
# ===========================================================================


def egress(
    call: ToolCall,
    task_scope: set[str],
    tool_schema: dict | None = None,
    mode: Mode = Mode.STANDARD,
    audit: AuditLog | None = None,
) -> EgressDecision:
    """Pre-call middleware. Returns EgressDecision describing allow/strip/refuse + findings."""
    all_findings: list[Finding] = []

    # Tier A — schema check on arguments
    tier_a = tier_a_check_arguments(call.arguments, task_scope)
    all_findings.extend(tier_a)

    # Tier B — regex on argument values
    tier_b = tier_b_scan_dict(call.arguments)
    all_findings.extend(tier_b)

    # Capability token validation
    token_check = validate_token(call.capability_token, call)
    all_findings.extend(token_check.findings)

    decision = _decide_egress(call, task_scope, tier_a, all_findings, mode)

    if audit is not None:
        task_id = call.capability_token.task_id if call.capability_token else None
        audit.write_egress(call, decision, mode, task_id=task_id)

    return decision


def _decide_egress(
    call: ToolCall,
    task_scope: set[str],
    over_collection_findings: list[Finding],
    all_findings: list[Finding],
    mode: Mode,
) -> EgressDecision:
    has_high = any(f.severity == "high" for f in all_findings)
    has_over_collection = bool(over_collection_findings)

    if mode == Mode.AUDIT_ONLY:
        return EgressDecision(
            action="allow",
            findings=all_findings,
            modified_call=None,
            reason="audit-only mode: detect, do not mutate",
        )

    if mode == Mode.PERMISSIVE:
        return EgressDecision(
            action="allow",
            findings=all_findings,
            modified_call=None,
            reason="permissive mode: warn but allow",
        )

    if mode == Mode.STRICT:
        if has_high or has_over_collection:
            return EgressDecision(
                action="refuse",
                findings=all_findings,
                modified_call=None,
                reason=f"strict mode: {len(over_collection_findings)} over-collection findings; {sum(1 for f in all_findings if f.severity=='high')} high-severity findings",
            )
        return EgressDecision(action="allow", findings=all_findings)

    # STANDARD — strip over-collection, warn on the rest
    if has_over_collection:
        stripped_fields = [f.field for f in over_collection_findings if f.field is not None]
        modified_args = {k: v for k, v in call.arguments.items() if k not in stripped_fields}
        modified_call = ToolCall(
            tool=call.tool,
            arguments=modified_args,
            capability_token=call.capability_token,
        )
        return EgressDecision(
            action="strip",
            findings=all_findings,
            modified_call=modified_call,
            stripped_fields=stripped_fields,
            reason=f"standard mode: stripped {len(stripped_fields)} over-collected field(s)",
        )

    return EgressDecision(action="allow", findings=all_findings)


def ingress(
    response: ToolResponse,
    task_scope: set[str],
    tool_schema: dict | None = None,
    mode: Mode = Mode.STANDARD,
    audit: AuditLog | None = None,
    task_id: str | None = None,
) -> IngressDecision:
    """Post-call middleware. Returns IngressDecision with optional redacted_payload."""
    all_findings: list[Finding] = []

    # Tier A — schema check on response fields
    tier_a = tier_a_check_response(response.payload, task_scope)
    all_findings.extend(tier_a)

    # Tier B — regex on response values
    tier_b = tier_b_scan_dict(response.payload)
    # relabel Tier B findings as response-side
    for f in tier_b:
        f.type = "pii_in_response"
    all_findings.extend(tier_b)

    decision = _decide_ingress(response, task_scope, tier_a, all_findings, mode)

    if audit is not None:
        audit.write_ingress(response, decision, mode, task_id=task_id)

    return decision


def _decide_ingress(
    response: ToolResponse,
    task_scope: set[str],
    over_return_findings: list[Finding],
    all_findings: list[Finding],
    mode: Mode,
) -> IngressDecision:
    if mode == Mode.AUDIT_ONLY:
        return IngressDecision(action="allow", findings=all_findings, redacted_payload=None, reason="audit-only")

    if mode == Mode.PERMISSIVE:
        return IngressDecision(action="allow", findings=all_findings, redacted_payload=None, reason="permissive")

    # STANDARD and STRICT both redact response over-returns + high-severity PII findings.
    # This captures every high-severity finding ONLY because every finding on the
    # ingress path carries a field: tier_a_check_response sets it, and
    # tier_b_scan_dict sets f.field = key for each value it scans. A high-severity
    # finding with field=None would produce no redaction and fall through to allow
    # below. test_ingress_findings_always_carry_a_field pins that.
    redacted = dict(response.payload)
    fields_to_redact = {f.field for f in over_return_findings if f.field is not None}
    fields_to_redact |= {f.field for f in all_findings if f.severity == "high" and f.field is not None}

    if fields_to_redact:
        for f in fields_to_redact:
            if f in redacted:
                redacted[f] = "[REDACTED]"
        return IngressDecision(
            action="redact",
            findings=all_findings,
            redacted_payload=redacted,
            reason=f"redacted {len(fields_to_redact)} field(s)",
        )

    return IngressDecision(action="allow", findings=all_findings, redacted_payload=None)


# ===========================================================================
# lock_text — document / KG-triple approval surface
# ===========================================================================
#
# Unlike egress()/ingress() which are ToolCall-shaped, lock_text() takes a
# bare string and routes it through Tier B (regex) + Tier C (semantic with
# confidential context from the caller's KG). Used by the cloud-LLM boundary
# for documents and KG-triples about to leave local context.


def _detect_confusable_bypass(text: str) -> list[Finding]:
    """Tier B+ — confusable-Unicode bypass detection (0.6.7+).

    Adversary tactic: replace one or more ASCII characters in a PII string
    (e.g. an email-shaped identifier) with visually-identical Unicode code points
    (e.g. Cyrillic `а` U+0430 for ASCII `a` U+0061). The original Tier B
    regex `[A-Za-z0-9._%+-]+@...` won't match — the homoglyph slips through.

    Defence: run regex against the ASCII-folded version of the text too. If
    a PII match exists in the folded version but NOT in the original, we have
    a confusable-bypass attempt — flag as a high-severity finding.

    Returns findings only for ATTEMPTED bypasses (i.e. cases where the bypass
    would have succeeded against the original Tier B alone). Legitimate
    international text (e.g. café, München) does NOT trip this because the
    folded version is searched for PII patterns, not for the differing
    characters themselves.
    """
    try:
        from anyascii import anyascii
    except ImportError:
        # `anyascii` is a REQUIRED dependency (see pyproject `dependencies`).
        # If it is missing the homoglyph-bypass control cannot run — fail
        # CLOSED rather than silently passing text through. Surface a
        # high-severity finding so lock_text escalates/refuses instead of
        # waving the text past Tier B+. The control must work when invoked.
        unavailable = Finding(
            tier="B+",
            type="confusable_control_unavailable",
            severity="high",
            field="",
            detail=("Confusable-Unicode bypass detection could not run: the "
                    "required `anyascii` dependency is missing. Failing closed "
                    "— install with `pip install anyascii>=0.3`."),
            confidence=1.0,
        )
        unavailable.remediation_actions = []
        return [unavailable]

    folded = anyascii(text)
    if folded == text:
        return []  # nothing to fold; original was already ASCII

    # Re-run Tier B on the folded text and diff against original Tier B.
    findings_original = set((f.type, f.detail) for f in tier_b_scan_text(text))
    findings_folded = tier_b_scan_text(folded)
    bypass_findings = [
        f for f in findings_folded
        if (f.type, f.detail) not in findings_original
    ]

    if not bypass_findings:
        return []

    # One aggregate finding rather than N — the signal is "bypass attempt
    # detected," not "here are all the patterns that would have matched."
    f = Finding(
        tier="B+",
        type="confusable_bypass",
        severity="high",
        field="",
        detail=(f"Confusable-Unicode bypass detected: "
                f"{len(bypass_findings)} PII pattern(s) hidden behind "
                f"homoglyph substitution. Examples: "
                + ", ".join(sorted({f.type for f in bypass_findings})[:3])),
        confidence=0.95,
    )
    # B3: B+ findings carry the same canonical three-action remediation block.
    # For confusable-bypass the "redacted" retry is the ASCII-folded version
    # with regex spans replaced — the safest text the user could resubmit.
    f.remediation_actions = _build_remediation_actions(f, text)
    # Override the redact_and_retry payload: for B+ we want the user to send
    # the ASCII-folded equivalent (so the bypass attempt itself is neutralised),
    # not the original-with-spans-replaced (which still carries the homoglyphs).
    folded_redacted = _redact_text_with_regex(folded)
    f.remediation_actions[0].payload["redacted_text"] = folded_redacted
    return [f]


def lock_text(
    text: str,
    *,
    context: str = "",
    mode: Mode = Mode.STANDARD,
    audit: AuditLog | None = None,
    source: str = "document",
    task_id: str | None = None,
    moderation_rules: dict | None = None,
) -> TextDecision:
    """Pre-cloud middleware for arbitrary text/document/triple content.

    Runs Tier B (regex) + Tier B+ (confusable-bypass detection) +
    Tier C (semantic with confidential context) + Tier M (policy moderation,
    only when ``moderation_rules`` is supplied).

    Returns a TextDecision describing whether the text may pass to the cloud
    as-is, must be minimised (regex-matched spans redacted), or must be
    refused entirely.

    Args:
        text: the content about to leave the local boundary.
        context: confidential terms from the caller's KG (newline-separated
                 list typically). Empty = PII-only check.
        mode: STANDARD (regex matches refuse; semantic confidential refuse;
              other findings minimise) | STRICT (any finding refuses) |
              PERMISSIVE (findings recorded; never blocks) | AUDIT_ONLY
              (audit but never mutates).
        audit: optional AuditLog to record the decision.
        source: tag for audit ("document" | "triple" | "freeform").
        task_id: optional task identifier for audit cross-linking.
        moderation_rules: the folder policy's Tier-M rules, or None to skip
            moderation entirely (the default for non-egress callers).

    Never raises — best-effort middleware. On any internal error the safe
    default is action="refuse" with a reason.
    """
    findings: list[Finding] = []

    # Tier B — regex on the text directly (no dict wrapper needed)
    findings.extend(tier_b_scan_text(text))

    # Tier B+ — confusable-Unicode bypass detection (0.6.7+).
    # Closes the homoglyph attack vector that Tier B's ASCII regex misses.
    findings.extend(_detect_confusable_bypass(text))

    # Tier C — semantic check with confidential context from KG.
    # tier_c_check_semantic itself fails closed (returns a high-severity
    # tier_c_unavailable finding) when a REAL backend is configured but cannot
    # run. The except below is only for a catastrophic failure of Tier C itself
    # (e.g. an import error): in that case, if a real backend is required, we
    # ALSO fail closed rather than silently downgrading to regex-only/allow (D8).
    try:
        from .tier_c import tier_c_check_semantic
        findings.extend(tier_c_check_semantic(text, context=context))
    except Exception as e:  # noqa: BLE001
        try:
            from .tier_c import tier_c_requires_real_backend, tier_c_unavailable_finding
            if tier_c_requires_real_backend():
                # class name only — never str(e) (may carry paths/scanned text).
                findings.append(tier_c_unavailable_finding(
                    f"Tier-C layer crashed ({type(e).__name__})"))
        except Exception:
            # If even the predicate import fails we cannot tell whether a real
            # backend was configured; with mock as the documented default we do
            # not block the default onboarding path on a Tier-C import failure.
            pass

    # Tier M — policy-driven moderation (no-op when the folder declares no rules).
    # Mirrors Tier C's contract: tier_m_check_moderation itself returns a
    # high-severity tier_m_unavailable finding when a required classifier backend
    # cannot run; this except only catches a catastrophic Tier-M failure (e.g. an
    # import error) and still fails closed when a real backend was required (D8).
    if moderation_rules:
        try:
            from .tier_m import tier_m_check_moderation
            findings.extend(tier_m_check_moderation(text, rules=moderation_rules))
        except Exception as e:  # noqa: BLE001
            try:
                from .tier_m import tier_m_requires_real_backend, tier_m_unavailable_finding
                if tier_m_requires_real_backend(moderation_rules):
                    findings.append(tier_m_unavailable_finding(
                        f"Tier-M layer crashed ({type(e).__name__})"))
            except Exception:
                pass

    decision = _decide_text(findings, text, mode, source)

    if audit is not None:
        audit.write_text(len(text), decision, mode, task_id=task_id)

    return decision


def _decide_text(
    findings: list[Finding],
    text: str,
    mode: Mode,
    source: str,
) -> TextDecision:
    has_high = any(f.severity == "high" for f in findings)
    has_findings = bool(findings)

    if mode == Mode.AUDIT_ONLY:
        return TextDecision(
            action="allow",
            findings=findings,
            reason="audit-only mode — recording only",
            source=source,
        )

    if mode == Mode.PERMISSIVE:
        return TextDecision(
            action="allow",
            findings=findings,
            reason="permissive mode — findings recorded, not enforced",
            source=source,
        )

    if not has_findings:
        return TextDecision(action="allow", findings=[], source=source)

    if mode == Mode.STRICT:
        return TextDecision(
            action="refuse",
            findings=findings,
            reason="strict mode: any finding blocks",
            source=source,
        )

    # Mode.STANDARD: high severity refuses (e.g. regex PII, confidential terms,
    # health/financial); medium severity minimises via regex redaction.
    if has_high:
        return TextDecision(
            action="refuse",
            findings=findings,
            reason="high-severity finding (PII regex match, confidential term, or special-category)",
            source=source,
        )

    redacted = _redact_text_with_regex(text)
    return TextDecision(
        action="minimise",
        findings=findings,
        redacted_text=redacted,
        reason="medium-severity findings; pattern-matched spans redacted",
        source=source,
    )


def _redact_text_with_regex(text: str) -> str:
    """Replace any Tier B pattern match in ``text`` with a typed placeholder.

    Applies the full ``_TIER_B_PATTERNS`` label set via
    :func:`_redact_for_label`, in table order (specific patterns before broad
    ones), so the minimise path never forwards a span the scan tiers flag.
    Placeholders are ``[REDACTED-<LABEL>]``; credit-card matches stay
    Luhn-gated. Deterministic: same input, same output.
    """
    redacted = text
    for label, _matcher, _conf in _TIER_B_PATTERNS:
        placeholder = f"[REDACTED-{label.upper().replace('_', '-')}]"
        redacted = _redact_for_label(redacted, label, placeholder=placeholder)
    return redacted


# Capture-only credential patterns (NOT the shared Tier-B regexes — keep those
# untouched so egress detection is unaffected). These widen coverage for the
# at-rest capture redactor only.
_CAPTURE_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]+")
_CAPTURE_PEM_BEGIN_RE = re.compile(
    r"-----BEGIN ([A-Z ]{0,32}PRIVATE KEY)-----")
_CAPTURE_BEARER_CI_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-/+=]{8,}")
_CAPTURE_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(?:password|passwd|secret|secret[_-]?access[_-]?key|api[_-]?key|token|access[_-]?token|refresh[_-]?token)\b"
    r"\s*[=:]\s*"
    r"(?:\"[^\"]{4,}\"|'[^']{4,}'|[^\s\"',;}]{4,})")  # quoted (JSON/YAML/.env) OR bare value

#: Tier-B labels worth redacting from at-rest captured text (credentials + PII).
#: Meta/Tier-A labels (object, search, high, pii_in_argument) are excluded.
_CAPTURE_REDACT_LABELS = (
    "url_with_creds", "api_key", "bearer_token", "credit_card", "email",
    "iban_full", "iban", "us_ssn", "uk_nino", "fr_ssn", "de_steuer_id",
    "es_dni_nie", "de_personnummer", "de_personalausweis_10", "mrn",
    "patient_case_id", "icd10", "name_possessive", "phone",
)


def _redact_pem_private_keys(text: str) -> str:
    """Redact complete PEM key blocks with a linear boundary scan.

    Matching the body with a repeated wildcard made runtime polynomial on
    attacker-controlled input. Search for a bounded BEGIN label, then use the
    exact corresponding END marker. Unclosed pseudo-blocks are left for the
    remaining credential detectors rather than consuming the rest of input.
    """
    parts: list[str] = []
    cursor = 0
    while match := _CAPTURE_PEM_BEGIN_RE.search(text, cursor):
        end_marker = f"-----END {match.group(1)}-----"
        end = text.find(end_marker, match.end())
        if end < 0:
            break
        parts.append(text[cursor:match.start()])
        parts.append("[REDACTED-PRIVATE-KEY]")
        cursor = end + len(end_marker)
    if not parts:
        return text
    parts.append(text[cursor:])
    return "".join(parts)


def redact_for_capture(text: str) -> str:
    """Redact secrets + PII from text BEFORE it is persisted to the capture
    ledger or the signed audit chain.

    Covers the full Tier-B credential + PII set (via :func:`_redact_for_label`,
    the same coverage the egress *minimise* path's :func:`_redact_text_with_regex`
    applies) plus capture-only patterns for JWTs, PEM private-key blocks,
    case-insensitive ``bearer`` carriers, and ``secret=/password=/token=``
    assignments. Credentials are redacted before/alongside PII.

    BEST-EFFORT, not a complete secret scanner. Known residual gaps: opaque
    high-entropy tokens with no recognised prefix/carrier, unenumerated vendor
    key formats, HTTP Basic / base64-encoded credential blobs, and secrets
    split across lines. Captured text is therefore redaction-LOSSY by design:
    the ledger records that an exchange occurred plus redacted content — never
    raw secrets. Identity hashes are computed over the RAW text (one-way,
    non-leaking) so distinct exchanges keep distinct ids; only stored CONTENT
    is redacted.
    """
    if not text:
        return text
    out = text
    for label in _CAPTURE_REDACT_LABELS:
        out = _redact_for_label(out, label)
    out = _redact_pem_private_keys(out)
    out = _CAPTURE_JWT_RE.sub("[REDACTED-JWT]", out)
    out = _CAPTURE_BEARER_CI_RE.sub("[REDACTED-BEARER]", out)
    out = _CAPTURE_SECRET_ASSIGN_RE.sub("[REDACTED-SECRET]", out)
    return out


# ===========================================================================
# B8.2 + B8.3 (0.6.8): Tier C semantic ensemble with CoT prompting.
# ===========================================================================
#
# The single-model Tier C in rvnd.lock/tier_c.py defers to the configured
# backend. B8.2 adds an ensemble specifically for hard cases — when the
# user has the default models configured we run them all and only declare
# PII on unanimous agreement. Any disagreement is returned as insufficient
# so the caller can escalate (human review, cloud fallback, refuse —
# whatever the folder policy says).
#
# Requiring unanimity trades single-model recall for ensemble precision:
# a joint pii_yes is high-confidence, and every disagreement becomes a
# deliberate escalation rather than a silent miss.
#
# The default names only weights whose licences permit commercial use:
# Phi-3.5-mini is MIT; Qwen2.5-Coder-7B and Mistral-7B-Instruct are
# Apache-2.0. The 3B Qwen variant is under the Qwen Research License
# (non-commercial), so it must not be a shipped default — see
# docs/concepts/local-models.md.

ENSEMBLE_MODELS_DEFAULT = (
    "phi-3.5-mini-q4",
    "qwen-2.5-coder-7b-q4",
    "mistral-7b-instruct-q4",
)

# Role string used by tier_c_semantic_check when looking up models from the
# registry. Kept as a module constant so tests + downstream skills can refer
# to the same name.
TIER_C_ROLE = "lock-c"


def _models_for_role(
    role: str,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Resolve a role to the configured ensemble of model ids.

    Lookup order:
      1. Any models the user has registered under ``role`` in
         ``rvnd.models_registry``.
      2. If the registry has no entries for ``role``, return ``default``
         (the historical hard-coded ensemble, for backward compat).
      3. If the resolver itself errors (missing module, bad I/O), return
         ``default`` rather than raising — this is a best-effort lookup.

    Pure-ish: makes one registry read. Mock-friendly because the injected
    host hook resolves ``rvnd.local_llm.resolve_models_for_role`` at
    call time, so tests can monkey-patch that symbol on the module.
    """
    try:
        from . import host_deps
        host_deps.ensure_wired()
        if host_deps.models_for_role is None:
            return tuple(default)
        configured = host_deps.models_for_role(role)
    except Exception:
        return tuple(default)
    if configured:
        return tuple(configured)
    return tuple(default)

# B8.3: CoT prompt. Three guard rails forcing the model to reason about
# (1) named entity, (2) non-public information, (3) re-identifiability,
# before emitting the final 3-label classification.
TIER_C_COT_PROMPT_TEMPLATE = """\
You are a privacy classifier. Apply these rules IN ORDER:
1. Does the text mention a specific real person by name? (yes/no/unsure)
2. Does it reveal information not public about that person? (yes/no/n/a)
3. Could the combination of details identify the person? (yes/no/unsure)
Then classify: pii_yes / pii_no / insufficient.
Output ONLY the final label.

Text: {text}
"""


@dataclass
class TierCEnsembleResult:
    """Outcome of :func:`tier_c_semantic_check`.

    label is one of:
      - ``pii_yes``        — all models agreed PII is present.
      - ``pii_no``         — all models agreed PII is absent.
      - ``insufficient``   — disagreement / any model insufficient.
      - ``unavailable``    — no local models configured; fall through to
                             today's stub behaviour.
    """
    label: str
    per_model: dict[str, str]
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalise_label(raw: Any) -> str:
    """Coerce a model output to one of pii_yes / pii_no / insufficient."""
    if raw is None:
        return "insufficient"
    s = str(raw).strip().lower()
    # Strip surrounding quotes/punctuation a model might emit.
    s = s.strip("\"' .\n\t")
    if "pii_yes" in s or s in {"yes", "pii"}:
        return "pii_yes"
    if "pii_no" in s or s in {"no", "none"}:
        return "pii_no"
    return "insufficient"


def tier_c_semantic_check(
    text: str,
    folder_context: str = "",
    *,
    models: tuple[str, ...] | None = None,
) -> TierCEnsembleResult | None:
    """Ensemble Tier C semantic check using local models.

    Model-set resolution (0.6.8.1):
      1. If ``models`` is passed as a non-empty tuple, use exactly that set.
      2. If ``models`` is ``None`` or ``()`` (the default), look up the
         user's registered ensemble for the ``"lock-c"`` role in the
         local model registry (see :func:`_models_for_role`).
      3. If the registry has no entries for ``"lock-c"``, fall back to
         the built-in defaults in :data:`ENSEMBLE_MODELS_DEFAULT`. This
         preserves out-of-the-box behaviour for users who haven't run
         ``workspaces models register`` yet.

    All models must agree to return a high-confidence label. Disagreement
    or insufficiency from any model returns ``insufficient`` so the
    caller can escalate. If no local-LLM backend is configured at all,
    returns ``None`` (fall-through to today's stub behaviour).

    The :func:`local_llm_classify` MCP tool is the ensemble's classifier
    of choice — it accepts a ``model`` argument and is already wired into
    the host's model registry. We import it lazily so this module does
    not pull in MCP at import time.
    """
    if not text or not text.strip():
        return TierCEnsembleResult(
            label="pii_no", per_model={},
            confidence=1.0, reason="empty input",
        )
    # Resolve the active ensemble. Explicit non-empty wins; otherwise
    # registry; otherwise the hard-coded built-in defaults.
    if models is None or len(models) == 0:
        models = _models_for_role(TIER_C_ROLE, default=ENSEMBLE_MODELS_DEFAULT)

    from . import host_deps
    host_deps.ensure_wired()
    if host_deps.llm_classify is None:
        return None
    local_llm_classify = host_deps.llm_classify

    per_model: dict[str, str] = {}
    any_unavailable = False
    for model in models:
        prompt = TIER_C_COT_PROMPT_TEMPLATE.format(text=text)
        try:
            result = local_llm_classify(
                text=prompt,
                categories=["pii_yes", "pii_no", "insufficient"],
                folder_context=folder_context,
                model=model,
            )
        except Exception:
            any_unavailable = True
            per_model[model] = "insufficient"
            continue
        if not isinstance(result, dict):
            any_unavailable = True
            per_model[model] = "insufficient"
            continue
        if not result.get("ok", True):
            any_unavailable = True
            per_model[model] = "insufficient"
            continue
        label = _normalise_label(result.get("category"))
        per_model[model] = label

    # If every model reported unavailable, treat the whole call as
    # unconfigured (callers fall back to today's stub behaviour).
    if any_unavailable and all(v == "insufficient" for v in per_model.values()) \
       and len(per_model) == len(models):
        return None

    labels = set(per_model.values())
    if labels == {"pii_yes"}:
        return TierCEnsembleResult(
            label="pii_yes", per_model=per_model,
            confidence=0.9, reason="ensemble agreement (pii_yes)",
        )
    if labels == {"pii_no"}:
        return TierCEnsembleResult(
            label="pii_no", per_model=per_model,
            confidence=0.9, reason="ensemble agreement (pii_no)",
        )
    return TierCEnsembleResult(
        label="insufficient", per_model=per_model,
        confidence=0.5,
        reason="ensemble disagreement or insufficient — escalate per policy",
    )
