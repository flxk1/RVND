# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Pending-erasure markers — GDPR Art. 17 erasure against a SEALED folder.

``erasure.execute`` cannot touch a sealed folder today: it has no
passphrase, ``MutationLog.purge``/``append`` raise ``SealedWriteError`` on a
sealed store, and sealed descendants are invisible to
``memory.discover_descendants`` (a sealed folder has no plaintext log dir for
that walk to find). This module closes that gap WITHOUT ever decrypting a
seal at erasure time: at erasure time, a signed PENDING-ERASURE marker is
written beside each in-scope ``<hash>.sealed`` blob; ``seal.unseal_folder``
verifies every marker for the folder being unsealed BEFORE restoring
plaintext, and — only once the plaintext is safely restored — applies the
purge/redaction the marker records.

Feature flag: the whole mechanism is OFF by default. Set
``WORKSPACE_PENDING_ERASE=1`` to enable arming (in ``erasure.execute``) and
verify+apply (in ``seal.unseal_folder``). With the flag unset, both call
sites behave exactly as before this module existed.

SECURITY MODEL (erase-injection defense)
-----------------------------------------
A marker is the single most dangerous artifact this module writes: an
attacker who could forge one could make ``unseal`` delete data the operator
never asked to erase. Two independent bindings close that:

1. **Signature.** The marker body (every field except the two signature
   fields) is signed by the operator key; when a controller key is
   registered, the controller signs FIRST and the operator signs LAST over
   ``body + controller_sig`` — the same "operator-last" discipline as the
   ``mutation_log.py`` purge tombstone, so the operator signature is the one
   binding that ultimately vouches for the whole marker including whether a
   controller co-signed. No controller key present → the marker is
   operator-signed only (weaker, L0 tamper-evidence — documented, not
   silently accepted as equivalent).
2. **Content binding.** The marker carries the *exact* sealed blob's
   ``sealed_blob_fingerprint`` (sha256 of the ``<hash>.sealed`` bytes at arm
   time) and the folder's ``folder_hash``. At unseal, both are recomputed
   against the CURRENT on-disk blob and folder and must match exactly — a
   marker moved to a different folder, or replayed against a
   re-sealed/rotated blob, is rejected.

Verification is FAIL-CLOSED and happens BEFORE any plaintext is restored: any
invalid marker (bad signature, forged/missing controller co-signature it
claims to carry, folder mismatch, or stale blob fingerprint) aborts the
WHOLE unseal — nothing is decrypted, nothing is written, the sealed blob and
every marker (valid or not) are left exactly as they were. A folder with NO
markers unseals exactly as before this feature existed (safe-by-omission).

RECALL GAP (documented, not hidden)
------------------------------------
Subject matching at apply time is an N-GRAM / phrase match, never substring
— see ``_select_matching_pairs``. The marker records ``subject_token_count``
(the subject's token length — a harmless integer, not PII) at arm time;
at unseal, each restored record's haystack is tokenised the same way and
every CONTIGUOUS window of that width is hashed and compared to the
marker's ``subject_hash``. A multi-word subject like "Jane Doe" IS now
recalled from "...please contact Jane Doe at the address below..." (the
bigram "jane doe" is a contiguous window) — the earlier design's gap, where
a multi-word subject only matched if it was the ENTIRE per-event haystack
verbatim, is closed.

What is NOT recalled, by design (never a substring/fuzzy matcher, and
correctly so — it must not over-erase):

  - non-contiguous mentions ("Jane ... (see below) ... Doe" spanning
    unrelated tokens) — the window is contiguous, so this is not the phrase
    "Jane Doe" and must not match it;
  - a single token that is part of a multi-word subject appearing alone
    ("Jane called today" when the erasure subject is "Jane Doe") — matching
    on a shared PARTIAL token would risk erasing an unrelated "Jane" that is
    not the data subject at all;
  - a subject embedded mid-word (e.g. "...JaneDoeCase123...") — tokenisation
    (Unicode word-boundary) does not split that into separate tokens, so no
    window of the right width is ever formed;
  - case/punctuation-INSENSITIVE but still phrase-shaped: "Jane, Doe" and
    "Jane Doe" both tokenise to the same 2-token window and match; genuine
    paraphrase or transliteration does not.

This is the same trade-off ``forgotten_subjects.check`` already accepts for
the live ingest guard (which is UNCHANGED by this module — see
``_select_matching_pairs`` for why the two paths use separate tokenising
helpers rather than sharing one).

OUT OF SCOPE (documented, not hidden)
--------------------------------------
An UNREGISTERED sealed folder (never passed to
``adapters.workspace.add_known_workspace``) is invisible to the discovery
walk this module uses to find in-scope sealed descendants during
``erasure.execute``, exactly as it is invisible to
``memory.discover_descendants`` today. It is never armed, never flagged as a
failure, and never silently claimed as "clean" — it is simply not reached,
the same posture the codebase already takes toward unregistered folders
elsewhere (see ``adapters/workspace.py``).
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from . import forgotten_subjects, seal, signing
from .mutation_log import _file_lock

PENDING_ERASE_ENV = "WORKSPACE_PENDING_ERASE"

_MARKER_SUFFIX = ".pending-erase.jsonl"


class PendingEraseError(RuntimeError):
    """A pending-erase marker could not be armed, verified, or applied."""


def feature_enabled() -> bool:
    """True iff the pending-erase-on-unseal feature is switched on.

    Default OFF (unset / any value other than ``"1"``) — staged rollout, see
    module docstring. When off, ``erasure.execute`` arms no markers and
    ``seal.unseal_folder`` neither looks for nor applies any."""
    import os
    return os.environ.get(PENDING_ERASE_ENV) == "1"


# ---------------------------------------------------------------------------
# Canonical signing bytes — same discipline as mutation_log._signed_bytes:
# sort_keys, compact separators, ensure_ascii=False. Deterministic across
# machines and Python versions; NOT the same helper (mutation_log's variant
# binds a hash-chain prev_hash this marker has no equivalent of), but the
# same canonicalisation rule, so a verifier re-derives byte-identical input.
# ---------------------------------------------------------------------------


def _canonical_bytes(d: dict[str, Any]) -> bytes:
    return json.dumps(
        d, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")


_BODY_FIELDS = (
    "folder_hash", "sealed_blob_fingerprint", "salt", "subject_hash",
    "subject_token_count", "request_id", "legal_basis", "requester_ref",
    "reason_safe", "created_at", "marker_id", "controller_keyid",
    "operator_keyid",
)


def _body_of(marker: dict[str, Any]) -> dict[str, Any]:
    """Every marker field EXCEPT the two signatures — what both signatures
    are computed over (the operator signature additionally covers
    ``controller_sig``, see :func:`arm_marker`)."""
    return {k: marker.get(k) for k in _BODY_FIELDS}


# ---------------------------------------------------------------------------
# Ledger location + locked read/write — one JSONL ledger per sealed folder,
# living BESIDE that folder's ``<hash>.sealed`` blob (same directory, same
# hash-derived name), so a marker travels with the blob it is bound to.
# ---------------------------------------------------------------------------


def _resolve_sealed_paths(folder: str | Path, log_root: str | Path | None) -> tuple[Path, Path]:
    """Return ``(log_dir, sealed_blob_path)`` using the EXACT same hash
    resolution ``seal.is_sealed``/``seal.unseal_folder`` use — so arming and
    verifying always agree on which blob a marker is bound to, including
    whatever the pre-existing primary/legacy resolution does."""
    log_dir = seal._resolve_log_dir(folder, log_root)
    return log_dir, seal._sealed_path(log_dir)


def _marker_ledger_path(folder: str | Path, log_root: str | Path | None) -> Path:
    log_dir, _sealed = _resolve_sealed_paths(folder, log_root)
    return log_dir.parent / (log_dir.name + _MARKER_SUFFIX)


def _read_markers(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _write_markers(path: Path, markers: list[dict[str, Any]]) -> None:
    payload = "".join(
        json.dumps(m, ensure_ascii=False) + "\n" for m in markers
    )
    forgotten_subjects._atomic_write_private(path, payload)


def _locked(path: Path):
    """Hold the same OS-level lock discipline ``mutation_log``/``seal`` use
    (``_file_lock`` over an open file handle) around a ledger read-modify-
    write, so a concurrent arm and a concurrent apply (unseal) never
    interleave a torn read or a lost write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / ("." + path.name + ".lock")
    fh = open(lock_path, "a+", encoding="utf-8")
    return fh, _file_lock(fh, exclusive=True)


# ---------------------------------------------------------------------------
# Arm — write one signed marker for one sealed folder.
# ---------------------------------------------------------------------------


def arm_marker(
    folder: str | Path,
    *,
    subject_norm: str,
    request_id: str,
    legal_basis: str,
    requester_ref: str,
    reason_safe: str,
    log_root: str | Path | None = None,
) -> dict[str, Any]:
    """Arm one signed pending-erasure marker for a SEALED folder.

    Raises :class:`PendingEraseError` if ``folder`` is not currently sealed.
    Registers ``subject_norm`` in THIS folder's own forgotten-subjects ledger
    (creating its salt on first use) so the marker embeds the exact
    ``{salt, subject_hash}`` pair the unseal matcher re-derives from —
    each folder has its own salt, so a marker for a descendant is bound to
    that descendant's own ledger, never the root's.

    Also embeds ``subject_token_count`` — the number of whitespace/
    Unicode-word tokens in the normalised subject (a harmless integer, not
    PII: it reveals nothing about the subject's identity, only its shape).
    Inside the SIGNED body, so tampering it is caught the same way tampering
    any other field is. The unseal-time matcher uses it as the sliding
    n-gram window width — see :func:`_select_matching_pairs`.

    Deduplicates: if a marker already exists for this exact
    ``(folder_hash, subject_hash, sealed_blob_fingerprint)`` triple — a
    repeat ``execute()`` call against the SAME still-sealed blob for the
    SAME subject — the existing marker is returned unchanged rather than
    appending a redundant entry. The sealed blob cannot change while sealed
    (no passphrase, no write path), so a second call describes exactly the
    same pending action; without this, a poll-happy caller (or an
    at-least-once delivery retry) would grow the ledger unboundedly and pay
    O(markers × events) at unseal for zero additional effect.

    Returns the armed (or existing, on dedup) marker dict — also the shape
    recorded in ``ExecutionReport.pending_markers``, trimmed to
    ``{folder, marker_id, controller_keyid}`` by the caller.
    """
    log_dir, sealed_path = _resolve_sealed_paths(folder, log_root)
    if not sealed_path.exists():
        raise PendingEraseError(f"cannot arm a pending-erase marker: {folder} is not sealed")

    subject_hash, _added = forgotten_subjects.ensure(
        folder, subject_norm, request_id=request_id)
    salt = forgotten_subjects.salt_for(folder)
    blob_fingerprint = _sha256_hex(sealed_path.read_bytes())
    # Same normalisation the unseal-time matcher applies to a haystack
    # before tokenising (lowercase, then Unicode word-boundary tokens) —
    # arm and unseal MUST tokenise identically or the n-gram hashes never
    # line up. See _select_matching_pairs.
    subject_token_count = max(
        1, len(forgotten_subjects._TOKEN_RE.findall(subject_norm.strip().lower())))

    has_controller = signing.public_controller_key_fingerprint() is not None

    ledger_path = _marker_ledger_path(folder, log_root)
    fh, lock_cm = _locked(ledger_path)
    try:
        with lock_cm:
            markers = _read_markers(ledger_path)
            for existing in markers:
                if (existing.get("folder_hash") == log_dir.name
                        and existing.get("subject_hash") == subject_hash
                        and existing.get("sealed_blob_fingerprint") == blob_fingerprint):
                    return existing

            body: dict[str, Any] = {
                "folder_hash":              log_dir.name,
                "sealed_blob_fingerprint":  blob_fingerprint,
                "salt":                     salt,
                "subject_hash":             subject_hash,
                "subject_token_count":      subject_token_count,
                "request_id":               request_id,
                "legal_basis":              legal_basis,
                "requester_ref":            requester_ref,
                "reason_safe":              reason_safe,
                "created_at":               time.time(),
                "marker_id":                "pending-erase:" + uuid.uuid4().hex[:16],
                "controller_keyid":         (signing.public_controller_key_fingerprint()
                                              if has_controller else None),
                "operator_keyid":           signing.public_key_fingerprint(),
            }

            # Operator-last signing (mirrors mutation_log.py's purge
            # tombstone): the controller signs the body FIRST (if a
            # controller key was deliberately initialised — never
            # auto-created here, matching MutationLog.purge's opt-in
            # discipline); the operator signs LAST, over body +
            # controller_sig, so the operator signature is the one that
            # vouches for the whole marker including whether a controller
            # co-signed AND the subject_token_count window width.
            if has_controller:
                try:
                    controller_sig = signing.sign_with_controller(_canonical_bytes(body))
                except Exception:
                    controller_sig = ""
            else:
                controller_sig = ""

            op_payload = dict(body)
            op_payload["controller_sig"] = controller_sig
            operator_sig = signing.sign_bytes(_canonical_bytes(op_payload))

            marker: dict[str, Any] = dict(body)
            marker["controller_sig"] = controller_sig
            marker["operator_sig"] = operator_sig

            markers.append(marker)
            _write_markers(ledger_path, markers)
    finally:
        fh.close()

    return marker


def _sha256_hex(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Discovery — in-scope SEALED folders reachable through the registered-
# workspace list (the only way to find a sealed DESCENDANT: it has no
# plaintext log dir for memory.discover_descendants to find).
# ---------------------------------------------------------------------------


def discover_sealed_in_scope(
    folder_context: str,
    *,
    cascade: bool,
    log_root: str | Path | None = None,
) -> list[str]:
    """Every SEALED folder in scope of an erasure against ``folder_context``.

    Always includes ``folder_context`` itself if sealed (root is always in
    scope). When ``cascade`` is True, also includes every REGISTERED
    workspace that is a descendant of ``folder_context`` under the same
    asymmetric path-prefix rule ``memory.discover_descendants`` uses, and is
    currently sealed. An unregistered sealed descendant is invisible here —
    documented out-of-scope, see module docstring.
    """
    from .adapters.workspace import list_known_workspaces

    ctx = str(Path(folder_context).expanduser().resolve())
    out: set[str] = set()
    if seal.is_sealed(ctx, log_root=log_root):
        out.add(ctx)

    if cascade:
        ctx_prefix = ctx if ctx.endswith("/") else ctx + "/"
        try:
            rows = list_known_workspaces(log_root)
        except Exception:
            rows = []
        for row in rows:
            fp = str(row.get("path") or "")
            if not fp:
                continue
            if fp == ctx or fp.startswith(ctx_prefix):
                try:
                    if seal.is_sealed(fp, log_root=log_root):
                        out.add(fp)
                except Exception:
                    # Can't determine seal state for this candidate — leave
                    # it out of the armed set; the caller's pre-existing
                    # versum_sealed / blind-spot bookkeeping is unaffected,
                    # this discovery step just can't add a marker for it.
                    continue
    return sorted(out)


# ---------------------------------------------------------------------------
# Verify — fail-closed, BEFORE any plaintext restore.
# ---------------------------------------------------------------------------


def verify_markers_for_unseal(
    folder: str | Path,
    *,
    log_dir: Path,
    sealed_path: Path,
    log_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Read + verify every pending-erase marker armed for ``folder``.

    Called by ``seal.unseal_folder`` BEFORE any decryption or plaintext
    write. Returns the verified markers (``[]`` if there are none — safe by
    omission). Raises :class:`seal.SealError` on the FIRST invalid marker —
    a forged, moved, replayed, or partially-stripped-then-reattached marker
    aborts the WHOLE unseal; nothing is decrypted, nothing is written,
    nothing is deleted (not even the bad marker itself, so the operator can
    inspect it).
    """
    ledger_path = log_dir.parent / (log_dir.name + _MARKER_SUFFIX)
    markers = _read_markers(ledger_path)
    if not markers:
        return []

    current_blob_fingerprint = _sha256_hex(sealed_path.read_bytes())

    for marker in markers:
        body = _body_of(marker)
        controller_sig = str(marker.get("controller_sig") or "")
        operator_sig = str(marker.get("operator_sig") or "")

        # 1) Operator signature — required on every marker.
        pub = signing.identity_public_key_or_none()
        if pub is None:
            raise seal.SealError(
                "pending-erase marker verification failed: no operator "
                "identity key registered — refusing to unseal")
        op_payload = dict(body)
        op_payload["controller_sig"] = controller_sig
        if not signing.verify_signature(_canonical_bytes(op_payload), operator_sig, pub):
            raise seal.SealError(
                "pending-erase marker verification failed: operator "
                "signature invalid — refusing to unseal (nothing restored)")

        # 2) Controller co-signature — required ONLY if the marker claims
        # one (controller_keyid is not None). A marker armed at L0 (no
        # controller key at arm time) carries controller_keyid=None and is
        # accepted on the operator signature alone (documented weaker
        # tamper-evidence — see module docstring).
        if body.get("controller_keyid") is not None:
            if not controller_sig or not signing.verify_controller_signature_strict(
                _canonical_bytes(body), controller_sig
            ):
                raise seal.SealError(
                    "pending-erase marker verification failed: claimed "
                    "controller co-signature is missing or invalid — "
                    "refusing to unseal (nothing restored)")

        # 3) Folder binding — a marker armed for a different folder must not
        # apply here (moved marker).
        if body.get("folder_hash") != log_dir.name:
            raise seal.SealError(
                "pending-erase marker verification failed: folder_hash "
                "does not match the folder being unsealed — refusing to "
                "unseal (nothing restored)")

        # 4) Sealed-blob binding — a marker armed against an earlier/
        # different sealed blob must not apply to THIS one (stale/replayed
        # marker after a reseal or a moved blob).
        if body.get("sealed_blob_fingerprint") != current_blob_fingerprint:
            raise seal.SealError(
                "pending-erase marker verification failed: sealed blob "
                "fingerprint mismatch — refusing to unseal (nothing "
                "restored)")

    return markers


# ---------------------------------------------------------------------------
# Matcher — sliding-window N-GRAM subject recovery. Deliberately its OWN
# tokenising path, not a call into forgotten_subjects.check()/
# _candidate_strings(): those stay single-token-or-whole-text (the live
# ingest guard's contract, unchanged by this feature); this matcher needs a
# CONTIGUOUS window sized to the subject's own token count, which is a
# different match shape, not a superset/subset of the ingest guard's. Both
# reuse the SAME primitives underneath (``forgotten_subjects._TOKEN_RE`` for
# tokenising, ``forgotten_subjects._hash_subject`` for the salted hash) so
# tokenisation and hashing never independently drift, even though the two
# call sites compose them differently.
# ---------------------------------------------------------------------------


def _select_matching_pairs(
    folder: str,
    salt: str,
    subject_hash: str,
    subject_token_count: int,
    *,
    log_root: str | Path | None,
) -> tuple[set[str], set[str]]:
    """Every ``pair_id`` in ``folder``'s (now-restored) log whose text
    haystack contains a CONTIGUOUS run of ``subject_token_count`` tokens
    that hashes (under ``salt``) to ``subject_hash`` — see the module
    docstring's RECALL GAP section for exactly what this does and does not
    recall. ``subject_token_count == 1`` degrades to the same single-token
    match the pre-n-gram matcher used. Returns ``(pair_ids,
    matched_candidate_strings)``; the candidates (the matched n-gram phrases
    themselves, recovered from the folder's own now-plaintext data — never
    from the marker, which never carries the subject) are what
    ``apply_markers`` feeds to ``draft_store.redact``/``card_store.redact``
    for parity.
    """
    from .erasure import _event_text_haystack
    from .memory import _pair_from_event
    from .mutation_log import MutationLog

    log = MutationLog(folder, log_root=log_root)
    try:
        from .adapters.versum import read_disk_versum_records
        versum_bodies = {
            b["id"]: b for b in
            (r.get("properties", {}).get("record") for r in
             read_disk_versum_records(folder))
            if isinstance(b, dict) and isinstance(b.get("id"), str)
        }
    except Exception:
        versum_bodies = {}

    n = max(1, int(subject_token_count) or 1)
    pair_ids: set[str] = set()
    matched: set[str] = set()
    for evt in log.replay():
        pair = _pair_from_event(evt) or versum_bodies.get(evt.pair_id)
        haystack = _event_text_haystack(evt, pair)
        if not haystack:
            continue
        tokens = forgotten_subjects._TOKEN_RE.findall(haystack.strip().lower())
        if len(tokens) < n:
            continue
        for i in range(len(tokens) - n + 1):
            window = " ".join(tokens[i:i + n])
            if forgotten_subjects._hash_subject(salt, window) == subject_hash:
                pair_ids.add(evt.pair_id)
                matched.add(window)
    return pair_ids, matched


# ---------------------------------------------------------------------------
# Apply — purge + versum erase + draft/card parity + composite; idempotent.
# ---------------------------------------------------------------------------


def apply_markers(
    folder: str | Path,
    markers: list[dict[str, Any]],
    *,
    log_root: str | Path | None = None,
    actor: str = "system:pending-erase",
) -> dict[str, Any]:
    """Apply every VERIFIED marker to ``folder`` once its plaintext has been
    restored by ``seal.unseal_folder``. Never called on unverified input —
    the caller (``unseal_folder``) only reaches here after every marker in
    the batch passed :func:`verify_markers_for_unseal`.

    Idempotent by construction: purging a pair already purged (its events
    are gone from the chain) matches nothing on a re-run, so a marker that
    somehow survives to be applied twice converges to zero additional
    effect. A marker is removed from the ledger ONLY after its own apply
    fully succeeds — a mid-apply failure leaves it for the next unseal to
    retry, rather than silently dropping a still-pending erasure.
    """
    from . import card_store, draft_store
    from .erasure import _erase_versum_mirror
    from .mutation_log import LogEvent, MutationLog

    folder_str = str(folder)
    ledger_path = _marker_ledger_path(folder, log_root)
    applied_marker_ids: list[str] = []
    total_purged_pairs = 0
    total_purged_events = 0
    errors: list[dict[str, Any]] = []

    applied_ids_this_call: set[str] = set()

    for marker in markers:
        marker_id = str(marker.get("marker_id", ""))
        try:
            pair_ids, candidates = _select_matching_pairs(
                folder_str, str(marker.get("salt", "")),
                str(marker.get("subject_hash", "")),
                int(marker.get("subject_token_count", 1) or 1),
                log_root=log_root)

            log = MutationLog(folder_str, log_root=log_root)
            purged_this_marker: set[str] = set()
            purged_events_this_marker = 0
            for pid in sorted(pair_ids):
                n = log.purge(
                    pid,
                    legal_basis=str(marker.get("legal_basis", "")),
                    requester_ref=str(marker.get("requester_ref", "")),
                    reason=(f"[erase-req:{marker.get('request_id', '')}] "
                            f"{marker.get('reason_safe', '')}"),
                )
                if n:
                    purged_this_marker.add(pid)
                    purged_events_this_marker += int(n)

            if purged_this_marker:
                versum_errors, _sealed_here = _erase_versum_mirror(
                    folder_str, purged_this_marker, physical=True,
                    reason=(f"[erase-req:{marker.get('request_id', '')}] "
                            f"{marker.get('reason_safe', '')}"),
                    actor=actor, log_root=log_root,
                )
                for verr in versum_errors:
                    errors.append({"marker_id": marker_id,
                                    "versum_purge": f"{type(verr).__name__}: {verr}"})

            # Draft/card parity: the marker never carries the plaintext
            # subject, so redaction here targets the MATCHED candidate
            # strings recovered from this folder's own now-plaintext data —
            # not a re-derivation of secret material, the folder is already
            # unsealed at this point.
            for cand in candidates:
                if not cand:
                    continue
                try:
                    draft_store.redact(folder_str, cand, log_root=log_root)
                except Exception as e:  # pragma: no cover - best-effort parity
                    errors.append({"marker_id": marker_id, "drafts": f"{type(e).__name__}: {e}"})
                try:
                    card_store.redact(folder_str, cand, log_root=log_root)
                except Exception as e:  # pragma: no cover - best-effort parity
                    errors.append({"marker_id": marker_id, "cards": f"{type(e).__name__}: {e}"})

            # Composite breadcrumb on the now-restored, now-plaintext log —
            # written even when nothing new matched (idempotent convergence
            # is still a documented outcome, not silence).
            log.append(LogEvent(
                event="system",
                folder_path=folder_str,
                pair_id=f"pending-erase-applied:{marker_id}",
                channel="system",
                actor=actor,
                extra={
                    "kind":                "erasure_pending_applied",
                    "request_id":          str(marker.get("request_id", "")),
                    "marker_id":           marker_id,
                    "legal_basis":         str(marker.get("legal_basis", "")),
                    "requester_ref":       str(marker.get("requester_ref", "")),
                    "reason":              str(marker.get("reason_safe", "")),
                    "subject_preview":     "[REDACTED]",
                    "affected_pair_count": len(purged_this_marker),
                    "purged_event_count":  purged_events_this_marker,
                },
            ))

            total_purged_pairs += len(purged_this_marker)
            total_purged_events += purged_events_this_marker
            applied_marker_ids.append(marker_id)
            applied_ids_this_call.add(marker_id)
        except Exception as e:  # noqa: BLE001 - one bad marker must not sink the rest
            errors.append({"marker_id": marker_id, "error": f"{type(e).__name__}: {e}"})
            from .audit_drop import record as _record_drop
            _record_drop("pending_erase.apply_markers", e,
                          request_id=str(marker.get("request_id", "")), log_root=log_root)

    # Remove only the markers that fully succeeded — anything else (a
    # marker not in this call's batch, or one that raised) is left for the
    # next unseal to retry.
    if applied_ids_this_call:
        fh, lock_cm = _locked(ledger_path)
        try:
            with lock_cm:
                # Re-read under lock in case of a concurrent arm since the
                # unlocked read above; only drop markers we actually applied.
                current = _read_markers(ledger_path)
                survivors = [m for m in current
                             if str(m.get("marker_id", "")) not in applied_ids_this_call]
                _write_markers(ledger_path, survivors)
        finally:
            fh.close()

    return {
        "applied_marker_ids":  applied_marker_ids,
        "purged_pair_count":   total_purged_pairs,
        "purged_event_count":  total_purged_events,
        "errors":              errors,
    }


__all__ = [
    "PENDING_ERASE_ENV",
    "PendingEraseError",
    "feature_enabled",
    "arm_marker",
    "discover_sealed_in_scope",
    "verify_markers_for_unseal",
    "apply_markers",
]
