# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Identity map — directory groups become competences (identity rung 3).

One declared YAML file maps the groups a trusted fronting proxy reports onto
party competences; a verified principal with mapped groups auto-registers (or
updates) as a party in the workspace it calls into — recorded like any manual
registration. No mapping, no registration: an unmapped principal stays
unresolved and governed operations refuse. The file is the admin's single
design artifact; Rvnd never talks to the IdP itself. Internal by design:
consumed by the HTTP bridge's trust mode, not an operator surface.

    groups:
      sg-dpo-team:
        competences: [data-protection]
      sg-engineering:
        competences: [engineering]
    channel: "email:{principal}"       # optional; {principal} substituted
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

MAP_ENV = "WORKSPACE_IDENTITY_MAP"


def load_map(path: Optional[str] = None) -> Optional[dict[str, Any]]:
    p = path or os.environ.get(MAP_ENV)
    if not p or not Path(p).exists():
        return None
    import yaml
    data = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else None


def competences_for(groups: list[str], mapping: dict[str, Any]) -> list[str]:
    out: list[str] = []
    table = mapping.get("groups") or {}
    for g in groups:
        entry = table.get((g or "").strip())
        if entry:
            for c in entry.get("competences") or []:
                if c not in out:
                    out.append(c)
    return out


def ensure_party(folder: str, principal: str, groups: list[str], *,
                 mapping: Optional[dict[str, Any]] = None,
                 log_root=None) -> dict[str, Any]:
    """Auto-register/update the principal as a party when its groups map to
    competences. Registration is recorded; the registering actor names the
    mechanism, not a person. Returns what was done, or that nothing was."""
    m = mapping if mapping is not None else load_map()
    if not m:
        return {"registered": False, "reason": "no identity map declared"}
    comps = competences_for(groups, m)
    if not comps:
        return {"registered": False, "reason": "no mapped group"}
    channel = str(m.get("channel") or "email:{principal}").replace(
        "{principal}", principal)
    from .parties import list_parties, register_party
    existing = next((p for p in list_parties(
        folder, log_root=str(log_root) if log_root else None).get("parties", [])
        if p.get("party_id") == principal), None)
    if existing and sorted(existing.get("competences") or []) == sorted(comps):
        return {"registered": True, "updated": False, "competences": comps}
    register_party(folder, party_id=principal, kind="human", name=principal,
                   competences=comps, channels=[channel],
                   actor="identity-map", log_root=log_root)
    return {"registered": True, "updated": bool(existing), "competences": comps}
