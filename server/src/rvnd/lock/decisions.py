# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Persisted user-approval store for Privacy Lock.

When Privacy Lock refuses a piece of outgoing text but the user wants to
approve it (e.g. "yes, send this to the cloud LLM — sending it to the cloud is fine here"),
the decision is persisted here so the next occurrence doesn't prompt again.

Schema (JSONL at ``~/.config/agent-tool-lock/decisions.jsonl``):

.. code-block:: json

    {
      "ts": 1716130000,
      "pattern_hash": "sha256:...",
      "pattern_preview": "first 80 chars of the matched text",
      "decision": "allow" | "block",
      "scope": "once" | "session" | "always",
      "reason": "free-text user-supplied reason",
      "session_id": "uuid for session-scoped decisions"
    }

Privacy notes:

- Only ``pattern_hash`` is load-bearing for lookups; ``pattern_preview`` is for
  audit readability and is truncated to 80 chars.
- The store NEVER persists the full text. Hash + preview only.
- The store is local-only; no cloud sync.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_PATH = Path.home() / ".config" / "agent-tool-lock" / "decisions.jsonl"


def _hash_text(text: str) -> str:
    """Stable SHA-256 hash of the normalised text. Used as the lookup key.

    Normalisation: Unicode NFC + strip + lowercase. NFC collapses precomposed
    and decomposed forms ("café" with U+00E9 vs "cafe" + U+0301) to one
    representation, so visually-identical text produces the same hash.
    Without NFC, an attacker could bypass "always" decisions by submitting a
    decomposed variant of an approved string.
    """
    import unicodedata
    normalised = unicodedata.normalize("NFC", text).strip().lower().encode("utf-8")
    return "sha256:" + hashlib.sha256(normalised).hexdigest()


def _preview(text: str, n: int = 80) -> str:
    """First N chars of the text with newlines collapsed."""
    one_line = " ".join(text.split())
    return one_line[:n]


def _safe_preview(text: str, n: int = 80) -> str:
    """An audit-readable preview that is safe to PERSIST: the flagged text with
    secrets/PII redacted, THEN truncated to N chars (CL4). Redaction runs on the
    FULL text before truncation — truncating first could split a secret so the
    80-char window holds a partial token the redactor no longer matches, leaking
    it into decisions.jsonl. If redaction is unavailable, DROP the preview rather
    than risk persisting raw flagged text (fail-closed).

    redact_for_capture is BEST-EFFORT (it documents residual gaps: opaque
    high-entropy tokens, unenumerated vendor key formats, split-line secrets), so
    a preview is not a guarantee of zero residue — :meth:`erase_subject` /
    :meth:`erase` scrub it on a GDPR purge, and the load-bearing lookup never
    depends on the preview (only on the hash)."""
    try:
        from .core import redact_for_capture
        redacted = redact_for_capture(text)
    except Exception:
        return ""
    return " ".join(redacted.split())[:n]


@dataclass
class StoredDecision:
    """One persisted user decision."""

    ts: float
    pattern_hash: str
    pattern_preview: str
    decision: str          # "allow" | "block"
    scope: str             # "once" | "session" | "always"
    reason: str
    session_id: str = ""
    actor: str = ""        # CL3: who recorded this clearance (identity, not a label)

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "ts": self.ts,
                "pattern_hash": self.pattern_hash,
                "pattern_preview": self.pattern_preview,
                "decision": self.decision,
                "scope": self.scope,
                "reason": self.reason,
                "session_id": self.session_id,
                "actor": self.actor,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, d: dict) -> "StoredDecision":
        return cls(
            ts=float(d.get("ts", 0.0)),
            pattern_hash=str(d.get("pattern_hash", "")),
            pattern_preview=str(d.get("pattern_preview", "")),
            decision=str(d.get("decision", "block")),
            scope=str(d.get("scope", "once")),
            reason=str(d.get("reason", "")),
            session_id=str(d.get("session_id", "")),
            actor=str(d.get("actor", "")),
        )


class DecisionsStore:
    """Append-only JSONL of user decisions.

    Reads on construction (full file, in-memory index by ``pattern_hash``).
    Appends on every ``remember()`` call. The full-file read keeps the
    implementation trivial; at the scale of "a human approves a few hundred
    confidential terms in a year", load time is negligible.

    Session-scoped decisions are only matched within the same ``session_id``;
    once a new session starts, prior session-scoped decisions are ignored.
    """

    #: default time-to-live for ALLOW clearances (90 days). A once-approved
    #: string should be re-confirmed periodically, not trusted forever (CL5);
    #: BLOCK decisions never expire (letting a block lapse would fail OPEN).
    _DEFAULT_TTL_SECONDS = 90 * 24 * 3600

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        session_id: str | None = None,
        ttl_seconds: float | None = None,
    ):
        self.path = Path(path) if path else _DEFAULT_PATH
        self.session_id = session_id or os.environ.get("AGENT_TOOL_LOCK_SESSION_ID") or str(uuid.uuid4())
        # TTL for allow clearances: explicit arg > env > default. <=0 disables expiry.
        if ttl_seconds is None:
            env_ttl = os.environ.get("AGENT_TOOL_LOCK_DECISION_TTL_SECONDS", "").strip()
            try:
                ttl_seconds = float(env_ttl) if env_ttl else self._DEFAULT_TTL_SECONDS
            except ValueError:
                ttl_seconds = self._DEFAULT_TTL_SECONDS
        self.ttl_seconds = ttl_seconds
        self._by_hash: dict[str, list[StoredDecision]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                d = StoredDecision.from_dict(obj)
                self._by_hash.setdefault(d.pattern_hash, []).append(d)
        except OSError:
            # Best-effort. A broken decisions file should never block the runtime.
            return

    def _live(self, d: "StoredDecision", now: float) -> bool:
        """Whether a decision is still in force. BLOCK never expires (a lapsing
        block would fail OPEN, CL5); ALLOW expires past ttl_seconds (>0)."""
        if d.decision == "block":
            return True
        if self.ttl_seconds and self.ttl_seconds > 0 and (now - d.ts) > self.ttl_seconds:
            return False
        return True

    def recall(self, text: str, *, now: float | None = None) -> str | None:
        """Return ``"allow"`` / ``"block"`` if a matching prior decision applies, else ``None``.

        Scope precedence: a stronger scope wins over a weaker one when both match
        (``always > session``); ``once`` is informational only.

        Within the strongest matching scope, **block wins over allow** (CL6) —
        regardless of recency, a later "allow" can't silently override an earlier
        "block" on the same text. Expired ALLOW clearances are ignored (CL5);
        blocks never expire.
        """
        candidates = self._by_hash.get(_hash_text(text))
        if not candidates:
            return None
        now = now if now is not None else time.time()

        always: list[StoredDecision] = []
        session: list[StoredDecision] = []
        for d in candidates:
            if not self._live(d, now):
                continue
            if d.scope == "always":
                always.append(d)
            elif d.scope == "session" and d.session_id == self.session_id:
                session.append(d)
            # "once" is informational only.

        for tier in (always, session):     # strongest matching scope first
            if tier:
                # block-precedence: any block in this tier wins (safety).
                if any(d.decision == "block" for d in tier):
                    return "block"
                return tier[-1].decision   # else the most-recent allow
        return None

    def remember(
        self,
        text: str,
        decision: str,
        *,
        scope: str = "once",
        reason: str = "",
        actor: str = "",
    ) -> StoredDecision:
        """Persist a decision. Returns the stored record.

        CL3 — a durable clearance must be attributable. A persistent ``always``
        decision (especially ``allow always``, which silences a footprint at every
        future egress) requires a named ``actor``; recording one anonymously is
        rejected (fail-closed) — an unauthenticated "allow always" is exactly the
        hole this closes. ``once``/``session`` scopes may stay anonymous (they are
        ephemeral and re-prompted)."""
        if decision not in ("allow", "block"):
            raise ValueError(f"decision must be 'allow' or 'block', got: {decision!r}")
        if scope not in ("once", "session", "always"):
            raise ValueError(f"scope must be 'once'|'session'|'always', got: {scope!r}")
        if scope == "always" and not (actor or "").strip():
            raise ValueError("a persistent 'always' clearance needs a named actor (CL3)")

        record = StoredDecision(
            ts=time.time(),
            pattern_hash=_hash_text(text),
            pattern_preview=_safe_preview(text),   # redacted before persist (CL4)
            decision=decision,
            scope=scope,
            reason=reason,
            session_id=self.session_id if scope == "session" else "",
            actor=(actor or "").strip(),
        )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(record.to_jsonl() + "\n")

        self._by_hash.setdefault(record.pattern_hash, []).append(record)
        return record

    def all_decisions(self) -> list[StoredDecision]:
        """Flat list of every stored decision. For audit / debugging."""
        out: list[StoredDecision] = []
        for records in self._by_hash.values():
            out.extend(records)
        return sorted(out, key=lambda d: d.ts)

    def _rewrite_previews(self, should_blank) -> int:
        """Blank ``pattern_preview`` on every record where ``should_blank(obj)``
        is True — in memory AND on disk, atomically (tmp + replace). The hash /
        decision / scope are kept (they carry no raw content) so recalls keep
        working; only the human-readable preview — the sole place residual flagged
        text could survive — is scrubbed. Unparseable lines are PRESERVED verbatim
        (a purge must not silently drop other records). Returns the number of
        records the predicate matched."""
        matched = 0
        for records in self._by_hash.values():
            for d in records:
                if should_blank({"pattern_hash": d.pattern_hash,
                                 "pattern_preview": d.pattern_preview,
                                 "decision": d.decision, "scope": d.scope}):
                    d.pattern_preview = ""
                    matched += 1
        if not self.path.exists():
            return matched
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return matched
        out: list[str] = []
        for raw in lines:
            s = raw.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                out.append(raw)          # preserve corrupt/partial lines, don't drop
                continue
            if should_blank(obj):
                obj["pattern_preview"] = ""
            out.append(json.dumps(obj, ensure_ascii=False))
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(("\n".join(out) + "\n") if out else "", encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        return matched

    def erase(self, pattern_hash: str | None = None) -> int:
        """GDPR purge for the decisions file (CL4): blank ``pattern_preview`` for a
        given ``pattern_hash`` (or every record when None). Returns the number of
        matching records."""
        return self._rewrite_previews(
            lambda o: pattern_hash is None or o.get("pattern_hash") == pattern_hash)

    def erase_subject(self, subject: str) -> int:
        """GDPR erase-by-subject (CL4): blank any preview that still CONTAINS the
        subject text (case-insensitive). Previews are redacted at write time, so
        this targets the residue redaction can't catch — plain names / project
        terms the redactor doesn't recognise, and legacy pre-redaction rows. The
        complement to :meth:`erase`, wired into the erasure pipeline. Returns the
        number of previews scrubbed."""
        sub = (subject or "").strip().lower()
        if not sub:
            return 0
        return self._rewrite_previews(
            lambda o: sub in str(o.get("pattern_preview", "")).lower())
