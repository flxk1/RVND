# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Per-folder pinned-skills store.

Each Workspace folder may pin a subset of skills. Storage lives at
``<log_root>/<folder_hash>/pinned-skills.json`` and is a simple list of
records:

    {
      "version": 1,
      "skills": [
        {"id": "ai-governance-watch:newsletter-research",
         "pinned_at": "2026-05-21T10:33:00Z",
         "pinned_by": "alex",
         "note": "research-only twin for AI gov work"},
        ...
      ]
    }

The orchestrator resolver (``resolve_skills_for_query``) walks the
**asymmetric hierarchy upward** — a child folder inherits pinned skills
from its ancestors, but a parent does NOT inherit pinned skills from its
children. This matches the Workspace memory rule: pairs flow UP to parents,
ancestors broadcast policy DOWN.

Skills are referenced by their fully-qualified id
(``<plugin>:<skill-name>`` or bare ``<skill-name>``). The runtime does
not validate that the skill actually exists — pinning is a declaration of
intent, and dispatch is the caller's job (typically the orchestrator
SKILL.md itself).

This module is intentionally tiny: no LLMs, no async, no lock. The
data store is local-only and survives a Cell-level swap because it lives
alongside the mutation log.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from .mutation_log import LOG_ROOT_DEFAULT, LogEvent, MutationLog, folder_hash


PINNED_SKILLS_FILE = "pinned-skills.json"
PINNED_SKILLS_VERSION = 1


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _store_path(folder_path: str | Path, log_root: Optional[Path] = None) -> Path:
    """Return ``<log_root>/<folder_hash>/pinned-skills.json``."""
    root = Path(log_root) if log_root else LOG_ROOT_DEFAULT
    fh = folder_hash(folder_path)
    return root / fh / PINNED_SKILLS_FILE


@dataclass
class PinnedSkill:
    id: str
    pinned_at: str = field(default_factory=_now_iso)
    pinned_by: str = "system"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PinnedSkill":
        return cls(
            id=str(d.get("id") or "").strip(),
            pinned_at=str(d.get("pinned_at") or _now_iso()),
            pinned_by=str(d.get("pinned_by") or "system"),
            note=str(d.get("note") or ""),
        )


@dataclass
class PinnedSkillStore:
    version: int = PINNED_SKILLS_VERSION
    skills: list[PinnedSkill] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "skills": [s.to_dict() for s in self.skills],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PinnedSkillStore":
        return cls(
            version=int(d.get("version") or PINNED_SKILLS_VERSION),
            skills=[PinnedSkill.from_dict(s) for s in (d.get("skills") or [])],
        )


def load_pinned_skills(folder_path: str | Path,
                       log_root: Optional[Path] = None) -> PinnedSkillStore:
    """Load (or initialise) the pinned-skills store for a folder.

    A missing file returns an empty store — pinning is opt-in, the
    asymmetric resolver handles the "ancestor said something" case.
    """
    path = _store_path(folder_path, log_root)
    if not path.exists():
        return PinnedSkillStore()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return PinnedSkillStore.from_dict(json.load(f))
    except (json.JSONDecodeError, OSError):
        # Corrupt file: refuse to act on it silently. Caller can decide
        # to overwrite, but we don't pretend it's empty.
        raise


def save_pinned_skills(folder_path: str | Path, store: PinnedSkillStore,
                       log_root: Optional[Path] = None) -> Path:
    """Persist the store to disk atomically (write to .tmp then rename)."""
    path = _store_path(folder_path, log_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store.to_dict(), f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path


def pin_skill(folder_path: str | Path, skill_id: str,
              *, pinned_by: str = "system", note: str = "",
              log_root: Optional[Path] = None) -> PinnedSkillStore:
    """Pin a skill to a folder. Idempotent: re-pinning the same id is
    a no-op for the id but updates ``pinned_at``/``pinned_by``/``note``.
    """
    if not isinstance(skill_id, str):
        raise ValueError("skill_id must be a non-empty string")
    skill_id = skill_id.strip()
    if not skill_id:
        raise ValueError("skill_id must be a non-empty string")
    store = load_pinned_skills(folder_path, log_root=log_root)
    # Drop any existing entry with the same id (last-write-wins on metadata)
    store.skills = [s for s in store.skills if s.id != skill_id]
    store.skills.append(PinnedSkill(
        id=skill_id,
        pinned_at=_now_iso(),
        pinned_by=pinned_by or "system",
        note=note or "",
    ))
    save_pinned_skills(folder_path, store, log_root=log_root)
    return store


def unpin_skill(folder_path: str | Path, skill_id: str,
                log_root: Optional[Path] = None) -> tuple[PinnedSkillStore, bool]:
    """Unpin a skill from a folder.

    Returns ``(store, removed)``. ``removed`` is True iff the skill was
    actually present.
    """
    if not skill_id:
        raise ValueError("skill_id must be a non-empty string")
    skill_id = skill_id.strip()
    store = load_pinned_skills(folder_path, log_root=log_root)
    before = len(store.skills)
    store.skills = [s for s in store.skills if s.id != skill_id]
    removed = len(store.skills) != before
    if removed:
        save_pinned_skills(folder_path, store, log_root=log_root)
    return store, removed


def list_pinned(folder_path: str | Path,
                log_root: Optional[Path] = None) -> list[PinnedSkill]:
    """Return the pinned skills for THIS folder only (no ancestor walk).

    For the orchestrator-time view that includes inherited pins, use
    ``resolve_skills_for_query`` in this module.
    """
    return list(load_pinned_skills(folder_path, log_root=log_root).skills)


# ---------------------------------------------------------------------------
# Asymmetric resolver
# ---------------------------------------------------------------------------


def _ancestor_chain(folder_path: str | Path) -> list[Path]:
    """Return ``[self, parent, grandparent, ...]`` up to filesystem root."""
    p = Path(folder_path).expanduser().resolve()
    chain = [p]
    while True:
        parent = p.parent
        if parent == p:
            break
        chain.append(parent)
        p = parent
    return chain


# ---------------------------------------------------------------------------
# Companion-skill catalogue (#193)
# ---------------------------------------------------------------------------
#
# A small JSON catalogue shipped under the Workspace plugin's ``references/``
# directory groups skills into families. When the user pins skill X, we look
# up its family and surface sibling skills as companion suggestions. The
# catalogue is read-only at runtime; updates ship in the .plugin.

_COMPANION_CATALOGUE_CACHE: Optional[dict[str, Any]] = None
_COMPANION_CATALOGUE_MTIME: Optional[float] = None
_COMPANION_CATALOGUE_PATH: Optional[Path] = None
_COMPANION_CATALOGUE_LAST_INTEGRITY: Optional[Any] = None  # IntegrityResult


def _candidate_catalogue_paths() -> list[Path]:
    """Probe locations where the Workspace plugin's catalogue might live."""
    import importlib.util
    out: list[Path] = []
    try:
        spec = importlib.util.find_spec("workspaces")
        if spec and spec.origin:
            pkg_dir = Path(spec.origin).resolve().parent
            for up in [pkg_dir.parent.parent.parent, pkg_dir.parent.parent,
                       pkg_dir.parent]:
                out.append(up / "plugin" / "references" / "skill-companions.json")
    except Exception:
        pass
    return out


def _invalidate_catalogue_cache_if_stale(catalogue_path: Path) -> bool:
    """Return True if cache was invalidated (file mtime changed)."""
    global _COMPANION_CATALOGUE_CACHE, _COMPANION_CATALOGUE_MTIME
    global _COMPANION_CATALOGUE_PATH, _COMPANION_CATALOGUE_LAST_INTEGRITY
    try:
        current_mtime = catalogue_path.stat().st_mtime
    except OSError:
        return False
    if (_COMPANION_CATALOGUE_MTIME is None
            or _COMPANION_CATALOGUE_PATH != catalogue_path
            or current_mtime != _COMPANION_CATALOGUE_MTIME):
        _COMPANION_CATALOGUE_CACHE = None
        _COMPANION_CATALOGUE_LAST_INTEGRITY = None
        return True
    return False


def get_last_integrity_result() -> Any:
    """Expose the last integrity-check result for diagnostics / dashboard.

    Returns the IntegrityResult from the most recent load, or ``None`` if
    no load has happened.
    """
    return _COMPANION_CATALOGUE_LAST_INTEGRITY


def load_companion_catalogue() -> dict[str, Any]:
    """Load (and cache) the companion-skill catalogue. Returns ``{}`` if not
    found — companion-suggestion is opt-in and gracefully degrades when the
    catalogue isn't shipped.

    Integrity: the loaded catalogue is verified via ``catalogue_integrity``
    according to ``WORKSPACE_CATALOGUE_MODE``. In ``enforce`` mode, a verified
    failure causes the catalogue to be treated as empty (companion-skill
    discovery degrades to no-op). In ``warn`` mode (default), failures are
    logged but the catalogue is still returned. In ``legacy`` mode,
    verification is skipped entirely.
    """
    global _COMPANION_CATALOGUE_CACHE, _COMPANION_CATALOGUE_MTIME
    global _COMPANION_CATALOGUE_PATH, _COMPANION_CATALOGUE_LAST_INTEGRITY

    # Local import keeps the integrity module optional in environments
    # where it hasn't been installed yet.
    try:
        from . import catalogue_integrity as _ci  # type: ignore
    except Exception:
        _ci = None  # type: ignore

    for p in _candidate_catalogue_paths():
        try:
            if not p.exists():
                continue
            _invalidate_catalogue_cache_if_stale(p)
            enforce = _ci is not None and _ci.current_mode() == _ci.MODE_ENFORCE
            if (not enforce and _COMPANION_CATALOGUE_CACHE is not None
                    and _COMPANION_CATALOGUE_PATH == p):
                return _COMPANION_CATALOGUE_CACHE
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Integrity verification
            if _ci is not None:
                try:
                    result = _ci.verify_catalogue(data)
                except Exception as e:  # pragma: no cover — defensive
                    import logging as _logging
                    active_mode = _ci.current_mode()
                    _logging.getLogger(__name__).error(
                        "catalogue integrity check raised %s: %s",
                        type(e).__name__, e,
                    )
                    if active_mode == _ci.MODE_ENFORCE:
                        _COMPANION_CATALOGUE_CACHE = {}
                        _COMPANION_CATALOGUE_PATH = p
                        try:
                            _COMPANION_CATALOGUE_MTIME = p.stat().st_mtime
                        except OSError:
                            _COMPANION_CATALOGUE_MTIME = None
                        return _COMPANION_CATALOGUE_CACHE
                    result = None
                if result is not None:
                    _COMPANION_CATALOGUE_LAST_INTEGRITY = result
                    if not result.ok:
                        # enforce-mode failure: treat catalogue as empty
                        import logging as _logging
                        _logging.getLogger(__name__).error(
                            "catalogue integrity FAILED in enforce mode at %s: %s",
                            p, "; ".join(result.errors),
                        )
                        _COMPANION_CATALOGUE_CACHE = {}
                        _COMPANION_CATALOGUE_PATH = p
                        try:
                            _COMPANION_CATALOGUE_MTIME = p.stat().st_mtime
                        except OSError:
                            _COMPANION_CATALOGUE_MTIME = None
                        return _COMPANION_CATALOGUE_CACHE
                    # warn-mode: log warnings but proceed
                    if result.warnings:
                        import logging as _logging
                        for w in result.warnings:
                            _logging.getLogger(__name__).warning(
                                "catalogue integrity warning at %s: %s", p, w,
                            )
            _COMPANION_CATALOGUE_CACHE = data
            _COMPANION_CATALOGUE_PATH = p
            try:
                _COMPANION_CATALOGUE_MTIME = p.stat().st_mtime
            except OSError:
                _COMPANION_CATALOGUE_MTIME = None
            return _COMPANION_CATALOGUE_CACHE
        except (OSError, json.JSONDecodeError):
            continue
    _COMPANION_CATALOGUE_CACHE = {}
    _COMPANION_CATALOGUE_PATH = None
    _COMPANION_CATALOGUE_MTIME = None
    return _COMPANION_CATALOGUE_CACHE


# ---------------------------------------------------------------------------
# Installed-plugin discovery — the pin picker's live source of truth.
#
# Skills are not authored in RVND; each plane/plugin repo carries its own, and
# users install them via `claude plugin install` / `codex plugin install`. So
# the pinnable menu is whatever the host has ACTUALLY installed, not a static
# catalogue we have to regenerate. We read the host's install manifest and scan
# each install's skills/ dir. Everything is best-effort: a missing or corrupt
# manifest yields no families rather than an error.
# ---------------------------------------------------------------------------

def _host_plugin_manifests() -> list[Path]:
    """installed_plugins.json for each host we know about. Override the search
    roots with WORKSPACE_HOST_PLUGIN_DIRS (os.pathsep-separated) — used by tests
    and operators pointing at a non-default install location."""
    override = os.environ.get("WORKSPACE_HOST_PLUGIN_DIRS")
    if override:
        roots = [Path(p) for p in override.split(os.pathsep) if p]
    else:
        home = Path.home()
        roots = [home / ".claude" / "plugins", home / ".codex" / "plugins"]
    return [r / "installed_plugins.json" for r in roots
            if (r / "installed_plugins.json").is_file()]


def discover_installed_skills() -> dict[str, Any]:
    """Enumerate skills from host-installed marketplace plugins.

    Returns ``{plugin: {"label": "<plugin>@<marketplace>", "skills":
    ["<plugin>:<skill>", ...]}}`` — the shape the pin picker consumes. Skill ids
    are canonical ``<plugin>:<skill>``. A plugin installed from several hosts is
    merged (union of skills). Best-effort throughout."""
    families: dict[str, Any] = {}
    for manifest in _host_plugin_manifests():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key, installs in (data.get("plugins") or {}).items():
            plugin = key.split("@", 1)[0]
            if not isinstance(installs, list) or not installs:
                continue
            # newest install record wins (lastUpdated, then installedAt)
            rec = sorted(
                installs,
                key=lambda r: (r.get("lastUpdated") or r.get("installedAt") or ""),
                reverse=True,
            )[0]
            install_path = rec.get("installPath")
            if not install_path:
                continue
            skills_dir = Path(install_path) / "skills"
            if not skills_dir.is_dir():
                continue
            found = [f"{plugin}:{sd.name}" for sd in sorted(skills_dir.iterdir())
                     if (sd / "SKILL.md").is_file()]
            if not found:
                continue
            fam = families.setdefault(plugin, {"label": key, "skills": []})
            for s in found:
                if s not in fam["skills"]:
                    fam["skills"].append(s)
    return families


def discover_pinnable_families() -> dict[str, Any]:
    """The pin picker's family set: host-installed skills unioned with any
    static companion catalogue that ships (usually none). Installed plugins are
    the live source; the static catalogue is a back-compat fallback."""
    families: dict[str, Any] = dict(load_companion_catalogue().get("families") or {})
    for plugin, fam in discover_installed_skills().items():
        if plugin in families:
            merged = list(families[plugin].get("skills") or [])
            for s in fam["skills"]:
                if s not in merged:
                    merged.append(s)
            families[plugin] = {**families[plugin], "skills": merged}
        else:
            families[plugin] = fam
    return families


def suggest_companions(skill_id: str,
                       *,
                       exclude: Optional[list[str]] = None) -> dict[str, Any]:
    """Suggest companion skills for ``skill_id`` based on the catalogue.

    Strategy: look up the family by ``<plugin>:`` prefix. Return all
    siblings except the input skill itself and anything in ``exclude``.

    Returns:
        ``{
            skill_id, family, family_label, companions: [skill_id, ...]
        }``.
        If no family matches, ``family`` is ``""`` and ``companions`` is empty.
    """
    skill_id = (skill_id or "").strip()
    if not skill_id:
        return {"skill_id": "", "family": "", "family_label": "",
                "companions": []}
    excl = set(exclude or [])
    excl.add(skill_id)
    cat = load_companion_catalogue()
    families = cat.get("families") or {}
    # 1. Try direct plugin-prefix match
    plugin_prefix = skill_id.split(":", 1)[0] if ":" in skill_id else skill_id
    fam = families.get(plugin_prefix)
    # 2. Otherwise, scan families looking for a family whose skill list
    #    contains skill_id directly.
    if fam is None:
        for fname, fdata in families.items():
            if skill_id in (fdata.get("skills") or []):
                fam = fdata
                plugin_prefix = fname
                break
    if fam is None:
        return {"skill_id": skill_id, "family": "", "family_label": "",
                "companions": []}
    companions = [s for s in (fam.get("skills") or []) if s not in excl]
    return {
        "skill_id":     skill_id,
        "family":       plugin_prefix,
        "family_label": fam.get("label") or plugin_prefix,
        "companions":   companions,
    }


def record_dispatch(folder_path: str | Path,
                    skill_id: str,
                    *,
                    query: str = "",
                    chosen_via: str = "user",
                    actor: str = "system",
                    log_root: Optional[Path] = None,
                    extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Record a dispatch event in the folder's mutation log.

    This is the audit-trail side of the orchestrator. The dispatch event
    is keyed at ``pair_id='skill-dispatch'`` so future analytics can
    query "how many times has skill X been dispatched in this folder".

    Does NOT validate that the skill is in the resolved set — the caller
    (MCP wrapper) is responsible for that check. Does NOT invoke the
    skill; that's the dashboard / host's job.

    Returns the recorded event metadata.
    """
    if not skill_id or not isinstance(skill_id, str):
        raise ValueError("skill_id must be a non-empty string")
    skill_id = skill_id.strip()
    if not skill_id:
        raise ValueError("skill_id must be a non-empty string after strip")

    log = MutationLog(folder_path, log_root=log_root)
    folder_resolved = str(Path(folder_path).expanduser().resolve())
    event_extra: dict[str, Any] = {
        "dispatch": "skill",
        "skill_id":   skill_id,
        "query":      query[:200] if query else "",
        "chosen_via": chosen_via,
    }
    # Caller-supplied extras (e.g., ``batch_id`` from dispatch_skills_batch)
    # get merged in. Reserved keys are not overwritten.
    if extra:
        for k, v in extra.items():
            if k not in event_extra:
                event_extra[k] = v
    event = LogEvent(
        event="system",
        folder_path=folder_resolved,
        pair_id="skill-dispatch",
        lifecycle_state="",
        channel="system",
        actor=actor or "system",
        extra=event_extra,
    )
    audit_id = log.append(event)
    return {
        "folder_context": folder_resolved,
        "skill_id":       skill_id,
        "query":          query,
        "chosen_via":     chosen_via,
        "actor":          actor or "system",
        "dispatched_at":  _now_iso(),
        # Surface the audit_id so callers can include it in compliance
        # output (Canonical Output Section [6] Audit Trail) and look the
        # event up later via ``get_audit_event(event_id, folder_context)``.
        "audit_id":       audit_id,
    }


def resolve_skills_for_query(folder_path: str | Path,
                             query: str = "",
                             *,
                             log_root: Optional[Path] = None,
                             include_ancestors: bool = True,
                             fingerprint: Optional[dict] = None,
                             case_log_root: Optional[str] = None) -> dict[str, Any]:
    """Resolve the effective set of pinned skills at orchestration time.

    Asymmetric rule:
    - The folder itself contributes its pinned skills.
    - Each ancestor folder contributes its pinned skills too (downward
      broadcast — children inherit policy from parents).
    - Siblings and descendants do NOT contribute.

    The union is taken by skill_id; the earliest (most ancestral) pinning
    metadata wins, but the ``inherited_from`` field is populated so the
    orchestrator knows the provenance.

    ``query`` is an optional keyword filter applied as a case-insensitive
    substring match against the skill_id. Empty query = no filter.

    Evidence seam (problem-solution graph): every resolved skill is
    annotated with ``evidence`` — the count of HUMAN-CLOSED cases the
    folder's case index holds for that solver (``fingerprint`` narrows the
    count to compatible problem shapes). Skills with evidence rank first;
    with no recorded cases every count is 0 and the ordering is the
    id-sort — identical to the pre-seam behaviour by construction. The
    seam fails OPEN: an unreadable case chain means evidence 0, never a
    resolution failure.

    Returns:
        ``{
            folder_context: <resolved-path>,
            query: <query>,
            skills: [
                {id, pinned_at, pinned_by, note, inherited_from, evidence},
                ...
            ],
            chain: [<folder>, <parent>, ...],
        }``
    """
    chain = _ancestor_chain(folder_path)
    if not include_ancestors:
        chain = chain[:1]

    by_id: dict[str, dict[str, Any]] = {}
    # Walk from most-ancestral down to self, so self overrides if duplicates.
    # But we still surface the most-ancestral provenance via inherited_from.
    for ancestor in reversed(chain):
        try:
            store = load_pinned_skills(ancestor, log_root=log_root)
        except (json.JSONDecodeError, OSError):
            continue
        for s in store.skills:
            if s.id in by_id:
                # Already seen — only update the inherited_from chain.
                by_id[s.id].setdefault("inherited_from", str(ancestor))
                continue
            by_id[s.id] = {
                "id":              s.id,
                "pinned_at":       s.pinned_at,
                "pinned_by":       s.pinned_by,
                "note":            s.note,
                "inherited_from":  str(ancestor)
                                   if str(ancestor) != str(chain[0])
                                   else "",
            }

    skills = list(by_id.values())
    q = (query or "").strip().lower()
    if q:
        skills = [s for s in skills if q in s["id"].lower()]

    # Evidence seam: annotate from the case index, fail-open to 0.
    evidence: dict[str, int] = {}
    if case_log_root is None and log_root is not None:
        case_log_root = str(log_root)      # one log root unless told otherwise
    try:
        from .case_index import retrieve as _case_retrieve
        for row in _case_retrieve(str(chain[0]), fingerprint or {},
                                  log_root=case_log_root):
            sid = row["solver"]
            sid = sid[len("skill:"):] if sid.startswith("skill:") else sid
            evidence[sid] = evidence.get(sid, 0) + row["evidence"]
    except Exception:
        # evidence is an enhancement on top of resolution, never a
        # dependency of it — an unreadable chain must not break dispatch
        evidence = {}
    for s in skills:
        s["evidence"] = evidence.get(s["id"], 0)

    # Evidence first, then the stable id-sort (zero evidence everywhere
    # reproduces the pre-seam ordering exactly).
    skills.sort(key=lambda s: (-s["evidence"], s["id"]))

    return {
        "folder_context": str(chain[0]),
        "query":          query,
        "skills":         skills,
        "chain":          [str(p) for p in chain],
    }
