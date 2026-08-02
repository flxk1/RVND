# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Card store — where the facts a user types actually land, durably and audited.

`fact_intake.record_standing` merges a user's *standing* answers into a
`SubjectCard`'s facets, but it is pure: it returns a dict and writes nothing. This
module is the persistence binding. It answers, concretely:

  * **Where it goes** — into the entity's `SubjectCard` (standing facts only;
    per-case answers stay ephemeral to the run).
  * **Where it is stored** — a JSON file under the *folder* on the user's machine
    (``<folder>/cards/<subject_id>.json``), plus a signed entry appended to that
    folder's **mutation log** (hash-chained, Ed25519-signed) recording who wrote
    which facets, when. So it is durable AND auditable.
  * **Is it persisted** — yes: it survives the run, reloads next time (so the
    workflow never re-asks), and every write leaves an audit trail.

Privacy posture: a card often holds personal/financial facts (tax status, IBAN,
names). It lives in the folder; the folder's **Workspace Lock** governs whether any of
it may cross to a cloud model. For a sensitive card set Lock = LOCAL_ONLY /
Privacy-Shield-on — the facts are stored locally and never auto-sent to the cloud.

Pure stdlib + the existing mutation log; no network.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from . import forgotten_subjects, seal
from .subject_card import SubjectCard
from .mutation_log import MutationLog, LogEvent
from .redaction import contains_word_ci, count_ci, redact_value, walk_strings


def _card_id(card: SubjectCard) -> str:
    return (card.subject_id or card.domain or "card").strip() or "card"


def card_path(folder: str | Path, subject_id: str) -> Path:
    return Path(folder) / "cards" / f"{subject_id}.json"


def save_card(card: SubjectCard, folder: str | Path, *,
              log_root: Optional[str | Path] = None, actor: str = "user",
              facets_written: Optional[list[str]] = None) -> dict[str, Any]:
    """Persist the card to the folder and append a signed fact-intake event.

    Returns ``{"path": ..., "subject_id": ..., "audit_id": ...}``. The audit_id is
    the mutation-log entry id — the proof the write happened and by whom."""
    folder = Path(folder)
    sid = _card_id(card)
    path = card_path(folder, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(card), ensure_ascii=False, indent=2), encoding="utf-8")

    log = MutationLog(folder, log_root=Path(log_root) if log_root else None)
    # The chain is permanent and the card id is often a person's name, so the
    # pair id carries an opaque folder-salted ref, never the id itself — a
    # purge tombstone naming this pair must not engrave the subject it erases.
    # ``extra.subject_id`` stays plaintext deliberately: the erasure sweep
    # finds card events only by text match, and purge removes the event whole.
    ref = forgotten_subjects.opaque_ref(folder, sid, domain="card-ref")[:16]
    audit_id = log.append(LogEvent(
        event="ingest", folder_path=str(folder), pair_id=f"card:{ref}",
        channel="fact", actor=actor,
        extra={"kind": "fact-intake", "subject_id": sid, "domain": card.domain,
               "facets_written": facets_written or sorted(card.facets)}))
    return {"path": str(path), "subject_id": sid, "audit_id": audit_id}


def load_card(folder: str | Path, subject_id: str) -> Optional[SubjectCard]:
    """Reload a persisted card so a later run reuses its standing facts. Returns
    None if the entity has no card yet (a first-time intake)."""
    path = card_path(folder, subject_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return SubjectCard(
        domain=data.get("domain", ""), facets=dict(data.get("facets") or {}),
        description=data.get("description", ""), notes=data.get("notes", ""),
        contact=data.get("contact", ""), attachments=list(data.get("attachments") or []),
        subject_id=data.get("subject_id", subject_id))


def list_cards(folder: str | Path) -> list[str]:
    d = Path(folder) / "cards"
    return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []


# ---------------------------------------------------------------------------
# Erasure hooks — a card carries subject data as a plain JSON file, so the
# chain purge alone leaves it behind. ``erasure.sweep`` calls scan for the
# preview, ``erasure.execute`` calls redact and records the counts on the
# composite tombstone; no per-card chain event is written here. The string
# matching lives in ``redaction``, shared with the draft store.
# ---------------------------------------------------------------------------

def _cards_dir(folder: str | Path) -> Path:
    return Path(folder) / "cards"


def _read_raw(path: Path) -> Optional[dict[str, Any]]:
    """The card file as a raw dict (rewrites must not drop unknown keys the
    dataclass loader would). None means unparseable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _is_identity_match(path: Path, data: dict[str, Any], needle: str) -> bool:
    """The card is about the subject: its id (which is the file name) or its
    ``subject_id`` field carries the subject as a whole word. Word-delimited,
    not substring — erasing "Ada" must not delete nevada-holdings.json — and
    an identity match deletes the file whole, since a rewritten id would not
    name the same card."""
    return (contains_word_ci(path.stem, needle)
            or contains_word_ci(str(data.get("subject_id", "")), needle))


def _fields_to_scan(data: dict[str, Any]) -> dict[str, Any]:
    """Everything except ``subject_id``: that field mirrors the file name, so
    it is either an identity match (file deleted whole) or it stays intact —
    a substring-rewritten id would fork the card on its next save."""
    return {k: v for k, v in data.items() if k != "subject_id"}


def scan(folder: str | Path, subject: str,
         *, log_root: Optional[str | Path] = None) -> dict[str, Any]:
    """Erasure preview over the folder's card files. ``hits`` counts subject
    occurrences per card that :func:`redact` would rewrite; ``identity`` names
    cards deleted whole (id carries the subject); ``unreadable`` names files
    deleted because they cannot be certified clean (unparseable cards, stale
    rewrite scratch). A sealed workspace cannot be inspected: ``sealed=True``
    with empty hits means the cards are unknown, not clean."""
    hits: dict[str, int] = {}
    identity: list[str] = []
    unreadable: list[str] = []
    if seal.is_sealed(folder, log_root=log_root):
        return {"hits": hits, "identity": identity,
                "unreadable": unreadable, "sealed": True}
    needle = (subject or "").strip()
    d = _cards_dir(folder)
    if not needle or not d.is_dir():
        return {"hits": hits, "identity": identity,
                "unreadable": unreadable, "sealed": False}
    for path in sorted(d.glob("*.json.tmp")):
        unreadable.append(path.name)
    for path in sorted(d.glob("*.json")):
        data = _read_raw(path)
        if data is None:
            unreadable.append(path.stem)
        elif _is_identity_match(path, data, needle):
            identity.append(path.stem)
        else:
            n = sum(count_ci(t, needle)
                    for t in walk_strings(_fields_to_scan(data)))
            if n:
                hits[path.stem] = n
    return {"hits": hits, "identity": identity,
            "unreadable": unreadable, "sealed": False}


def redact(folder: str | Path, subject: str,
           *, log_root: Optional[str | Path] = None) -> dict[str, Any]:
    """Erasure execute over the folder's card files: delete identity-matching
    cards, unparseable files, and stale rewrite scratch (none can be certified
    clean of the subject); rewrite every other occurrence to ``[REDACTED]``.
    Returns ``{ok, redacted: {card_id: count}, deleted: [card_id]}`` — deleted
    ids may themselves carry the subject, so callers redact them before
    putting them in any report. Refuses on a sealed workspace."""
    if seal.is_sealed(folder, log_root=log_root):
        return {"ok": False,
                "error": "workspace is sealed — unseal it to erase cards"}
    needle = (subject or "").strip()
    if not needle:
        return {"ok": False, "error": "erasure subject must be non-empty"}
    redacted: dict[str, int] = {}
    deleted: list[str] = []
    d = _cards_dir(folder)
    if not d.is_dir():
        return {"ok": True, "redacted": redacted, "deleted": deleted}
    for path in sorted(d.glob("*.json.tmp")):
        path.unlink()
        deleted.append(path.name)
    for path in sorted(d.glob("*.json")):
        data = _read_raw(path)
        if data is None or _is_identity_match(path, data, needle):
            path.unlink()
            deleted.append(path.stem)
            continue
        body, n = redact_value(_fields_to_scan(data), needle)
        if not n:
            continue
        if "subject_id" in data:
            body["subject_id"] = data["subject_id"]
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
        redacted[path.stem] = n
    return {"ok": True, "redacted": redacted, "deleted": deleted}
