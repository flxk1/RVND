# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Attestation runtime — wires the pure probe engine to storage and the record.

Gold probes live beside the models registry (one JSON per model under
``<models_dir>/attestation/``); every outcome is a signed event on the invoking
workspace's chain, and status is projected from that record only — a status
read never runs a probe. Probe runs invoke the model through an injectable
runner (default: the local-LLM transport at temperature 0) and are governed,
recorded writes. Admitted learning enters only through an explicit declaration;
the model-file hash is a corroborating signal — the baseline captures it, a run
reports changed / unchanged / unknown, it never counts as admitted by itself.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .core import Probe, attest, signature
from ..models_registry import models_dir
from ..mutation_log import LogEvent, MutationLog

Runner = Callable[[str], str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store_path(model_id: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model_id).strip("-") or "model"
    return models_dir() / "attestation" / f"{slug}.json"


def load_store(model_id: str) -> Optional[dict[str, Any]]:
    p = _store_path(model_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _save_store(model_id: str, store: dict[str, Any]) -> None:
    p = _store_path(model_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _append(folder: str, actor: str, log_root, kind: str, extra: dict) -> str:
    log = MutationLog(Path(folder), log_root=Path(log_root) if log_root else None)
    return log.append(LogEvent(event="system", folder_path=str(folder),
                               pair_id=f"attestation:{extra.get('model_id', '?')}",
                               channel="system", actor=actor or "host",
                               extra={"kind": kind, **extra}))


def _artifact_sha(model_id: str) -> str:
    """Best-effort hash of the model artifact — empty when no local file is
    known (endpoint-only models attest by behaviour alone)."""
    try:
        from ..models_registry import list_models
        entry = next((m for m in list_models() if m.id == model_id), None)
        path = Path(entry.artifact_path) if entry and entry.artifact_path else None
        if not path or not path.exists():
            return ""
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:                                           # noqa: BLE001
        return ""


def _default_runner(model_id: str) -> Runner:
    from .. import local_llm

    def run(prompt: str) -> str:
        r = local_llm.complete(prompt, model=model_id, temperature=0.0)
        if not r.get("ok"):
            raise RuntimeError(r.get("error", "completion failed"))
        return str(r.get("response", ""))
    return run


def baseline(model_id: str, probes: list[dict], folder: str, actor: str, *,
             runner: Optional[Runner] = None, log_root=None) -> dict[str, Any]:
    """Capture the gold set: run every probe input against the model now and
    store the signatures as the baseline. A governed, recorded write — the
    baseline is the claim later runs are held against."""
    items = [p for p in (probes or []) if p.get("id") and p.get("input")]
    if not items:
        return {"ok": False, "error": "no probes — a baseline needs at least"
                                      " one {id, input} probe"}
    run = runner or _default_runner(model_id)
    gold = []
    try:
        for p in items:
            gold.append({"id": str(p["id"]), "input": str(p["input"]),
                         "baseline_signature": signature(run(str(p["input"])))})
    except Exception as e:                                      # noqa: BLE001
        return {"ok": False, "error": f"could not capture the baseline —"
                                      f" the battery must run green: {e}"}
    store = {"model_id": model_id, "baselined_at": _now(),
             "artifact_sha256": _artifact_sha(model_id),
             "probes": gold, "admitted": [], "last_attested_at": None}
    _save_store(model_id, store)
    audit_id = _append(folder, actor, log_root, "attestation.baseline",
                       {"model_id": model_id, "probe_count": len(gold),
                        "artifact_sha256": store["artifact_sha256"]})
    return {"ok": True, "model_id": model_id, "probe_count": len(gold),
            "baselined_at": store["baselined_at"], "audit_id": audit_id}


def admit(model_id: str, folder: str, actor: str, note: str, *,
          log_root=None) -> dict[str, Any]:
    """Declare a learning event (a deliberate model change) so the next run
    reconciles drift as explained instead of alarming. Requires the note —
    an unexplained admission would defeat the reconciliation."""
    if not (note or "").strip():
        return {"ok": False, "error": "an admitted-learning event needs a note"
                                      " — what changed, deliberately"}
    if not (actor or "").strip():
        return {"ok": False, "error": "an admission must name its actor"}
    store = load_store(model_id)
    if store is None:
        return {"ok": False, "error": f"no baseline for {model_id!r} — baseline first"}
    event = {"at": _now(), "actor": actor.strip(), "note": note.strip()}
    store.setdefault("admitted", []).append(event)
    _save_store(model_id, store)
    audit_id = _append(folder, actor, log_root, "attestation.admitted",
                       {"model_id": model_id, **event})
    return {"ok": True, "model_id": model_id, "admitted": event, "audit_id": audit_id}


def run_battery(model_id: str, folder: str, actor: str, *, tolerance: int = 0,
                runner: Optional[Runner] = None, log_root=None) -> dict[str, Any]:
    """Run the probe battery against the model and record the reconciled
    outcome on the workspace's chain. A runner that cannot answer a probe
    leaves it unobserved (a coverage gap, never drift); a runner that cannot
    run at all refuses without recording — nothing observed is not evidence."""
    store = load_store(model_id)
    if store is None:
        return {"ok": False, "error": f"no baseline for {model_id!r} — baseline first"}
    run = runner or _default_runner(model_id)
    gold = [Probe(id=p["id"], baseline_signature=p["baseline_signature"])
            for p in store.get("probes", [])]
    observed: dict[str, str] = {}
    failures = 0
    for p in store.get("probes", []):
        try:
            observed[p["id"]] = signature(run(str(p["input"])))
        except Exception:                                       # noqa: BLE001
            failures += 1
    if failures and not observed:
        return {"ok": False, "error": "the battery could not run at all —"
                                      " no probe answered; nothing recorded"}
    since = store.get("last_attested_at") or store.get("baselined_at") or ""
    admitted = sum(1 for a in store.get("admitted", []) if a.get("at", "") > since)
    result = attest(observed, gold, admitted_learning_events=admitted,
                    tolerance=tolerance)
    sha_now = _artifact_sha(model_id)
    sha_base = store.get("artifact_sha256", "")
    hash_state = ("unknown" if not (sha_now and sha_base)
                  else "unchanged" if sha_now == sha_base else "changed")
    payload = {"model_id": model_id, **result.to_dict(),
               "hash_state": hash_state, "probe_count": len(gold),
               "breaker": result.to_metrics(), "at": _now()}
    audit_id = _append(folder, actor, log_root, "attestation.run", payload)
    store["last_attested_at"] = payload["at"]
    _save_store(model_id, store)
    return {"ok": True, "audit_id": audit_id, **payload}


def status(folder: str, model_id: str = "", *, log_root=None) -> dict[str, Any]:
    """Read-only projection of recorded attestation state from the workspace's
    chain — the latest run per model, baseline and admission counts. Never
    runs a probe."""
    log = MutationLog(Path(folder), log_root=Path(log_root) if log_root else None)
    try:
        events = list(log.replay())
    except Exception as e:                                      # noqa: BLE001
        return {"ok": False, "error": f"could not read the record: {e}"}
    models: dict[str, dict[str, Any]] = {}
    for evt in events:
        ex = evt.extra or {}
        kind = ex.get("kind", "")
        if not kind.startswith("attestation."):
            continue
        mid = ex.get("model_id", "?")
        if model_id and mid != model_id:
            continue
        m = models.setdefault(mid, {"model_id": mid, "baselines": 0,
                                    "admissions": 0, "latest_run": None})
        if kind == "attestation.baseline":
            m["baselines"] += 1
        elif kind == "attestation.admitted":
            m["admissions"] += 1
        elif kind == "attestation.run":
            m["latest_run"] = {k: ex.get(k) for k in
                               ("verdict", "diverged", "unobserved",
                                "admitted_learning_events", "reason",
                                "hash_state", "probe_count", "at")}
    return {"ok": True, "models": sorted(models.values(),
                                         key=lambda m: m["model_id"])}
