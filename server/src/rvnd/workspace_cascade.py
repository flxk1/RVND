# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Governed local-first cascade for any workspace — host-callable.

Wraps the cascade engine (``cascade.run_cascade``) so any MCP client (Claude in
Cowork, Cline, the Workspaces app) can run a prompt local-first and escalate to cloud
only if a local tier can't answer — with the cloud hop Shield-gated and the
whole exchange recorded on the workspace's signed chain.

The token saving is real only when a local tier is configured and answers; with
no tier configured this returns a loud, actionable error rather than silently
doing nothing (same discipline as the installer postflight).

Tiers come from the workspace's environment (kept simple and explicit):
- local : WORKSPACE_LOCAL_LLM_URL + WORKSPACE_LOCAL_LLM_MODEL
- cloud : WORKSPACE_CLOUD_LLM_URL + WORKSPACE_CLOUD_LLM_MODEL
Cloud is the last rung; credentials are resolved from the active track's
``credential_ref`` and injected only by the governed egress broker.
"""
from __future__ import annotations

import hashlib
import json
import os
import warnings
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlsplit

from .cascade import Tier, nonempty_verifier, run_cascade
from .mutation_log import LogEvent, MutationLog

LOCAL_URL_ENV = "WORKSPACE_LOCAL_LLM_URL"
LOCAL_MODEL_ENV = "WORKSPACE_LOCAL_LLM_MODEL"
CLOUD_URL_ENV = "WORKSPACE_CLOUD_LLM_URL"
CLOUD_MODEL_ENV = "WORKSPACE_CLOUD_LLM_MODEL"
CLOUD_KEY_ENV = "WORKSPACE_CLOUD_API_KEY"
CLOUD_PRICE_ENV = "WORKSPACE_CLOUD_PRICE_PER_1K"

CONFIG_PATH_ENV = "WORKSPACE_LLM_CONFIG"     # override the config-file location


def config_path() -> Path:
    """Where the installer writes the one-time local-model config.

    ``$WORKSPACE_LLM_CONFIG`` if set, else ``$XDG_CONFIG_HOME/workspace/local-llm.json``,
    else ``~/.config/workspace/local-llm.json``. One file per user, so the workspace
    cascade works in every shell and every host without per-shell envs.
    """
    override = os.environ.get(CONFIG_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "workspace" / "local-llm.json"


def _local_config() -> dict[str, Any]:
    """Read the installer-written config (``{local:{url,model}, cloud:{...}}``).

    A one-time file so users don't export envs every shell; envs still win over
    it. Missing or unreadable file -> empty (no tier from config, not an error).
    """
    p = config_path()
    try:
        raw = json.loads(p.read_text()) if p.exists() else {}
        return _sanitise_llm_config(raw)[0]
    except Exception:
        return {}


def _sanitise_llm_config(raw: Any) -> tuple[dict[str, Any], bool]:
    """Remove unsupported legacy raw credentials without exposing values."""
    if not isinstance(raw, dict):
        return {}, False
    cfg = deepcopy(raw)
    removed = False
    cloud = cfg.get("cloud")
    if isinstance(cloud, dict) and "api_key" in cloud:
        cloud.pop("api_key", None)
        removed = True
    return cfg, removed


_SENSITIVE_QUERY_KEYS = frozenset({
    "api_key", "apikey", "key", "token", "access_token", "secret",
    "password", "authorization",
})


def _validate_endpoint_url(value: str, field: str) -> None:
    """Reject endpoint URLs that embed credentials."""
    if not value:
        return
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field} must not contain embedded credentials")
    query_keys = {key.lower() for key, _ in parse_qsl(
        parsed.query, keep_blank_values=True
    )}
    if query_keys & _SENSITIVE_QUERY_KEYS:
        raise ValueError(f"{field} must not contain credential query parameters")


def write_local_config(*,
                       local_url: str = "",
                       local_model: str = "",
                       local_models: list[dict[str, Any]] | None = None,
                       cloud_url: str = "",
                       cloud_model: str = "",
                       cloud_api_key: str = "",
                       cloud_price_per_1k: float | None = None,
                       merge: bool = True) -> Path:
    """Write the one-time local-model config the cascade reads. The installer
    calls this after pulling models so every shell and host is configured
    without per-shell envs. ``merge=True`` keeps existing keys not passed.

    ``local_models`` is the ordered list (cheap -> capable) of the pulled local
    tiers, e.g. ``[{"model": "phi-3.5-mini-q4"}, {"model": "qwen-2.5-coder-7b-q4"}]``
    — an entry with no ``url`` runs in-process from its GGUF. It takes precedence
    over the single ``local_url``/``local_model`` form. Raw cloud API keys are
    deprecated and ignored; use an egress connector ``credential_ref``. A write
    also removes any legacy ``cloud.api_key`` from the stored config.
    """
    _validate_endpoint_url(local_url, "local_url")
    _validate_endpoint_url(cloud_url, "cloud_url")
    if cloud_api_key:
        warnings.warn(
            "cloud_api_key is deprecated and ignored; configure an egress "
            "connector credential_ref",
            DeprecationWarning,
            stacklevel=2,
        )
    p = config_path()
    cfg: dict[str, Any] = _local_config() if merge else {}
    if local_models is not None:
        cfg["local"] = [e for e in local_models if isinstance(e, dict) and e.get("model")]
    else:
        local = cfg.get("local")
        local = dict(local) if isinstance(local, dict) else {}
        if local_url:
            local["url"] = local_url
        if local_model:
            local["model"] = local_model
        if local:
            cfg["local"] = local
    cloud = dict(cfg.get("cloud") or {})
    if cloud_url:
        cloud["url"] = cloud_url
    if cloud_model:
        cloud["model"] = cloud_model
    if cloud_price_per_1k is not None:
        cloud["price_per_1k"] = cloud_price_per_1k
    if cloud:
        cfg["cloud"] = cloud
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2) + "\n")
    try:
        p.chmod(0o600)   # minimise accidental disclosure of local preferences
    except Exception:
        pass
    return p


def canonical_local_model() -> str:
    """The workspace's local-model name: ``WORKSPACE_LOCAL_LLM_MODEL`` env wins, else the
    installer/BYOK config's ``local`` entry. "" if neither is set."""
    m = os.environ.get(LOCAL_MODEL_ENV, "").strip()
    if m:
        return m
    raw_local = _local_config().get("local")
    if isinstance(raw_local, list):          # ordered tiers -> first model name
        first = next((e for e in raw_local if isinstance(e, dict) and e.get("model")), None)
        return str(first.get("model", "")).strip() if first else ""
    if isinstance(raw_local, dict):
        return str(raw_local.get("model", "")).strip()
    return ""


def _local_tiers(cfg: dict[str, Any]) -> list[Tier]:
    """Build the ordered local rungs (cheap -> capable), all before cloud.

    Precedence:
    - if ``WORKSPACE_LOCAL_LLM_URL`` + a model are set in the env, that is THE local
      tier (one explicit override);
    - otherwise read ``cfg["local"]``: a single ``{url, model}`` dict, OR a list
      of ``{model, url?}`` entries kept in order. An entry with no ``url`` (or
      ``url: "inproc"``) runs in-process from the pulled GGUF — no daemon; an
      entry with an http(s) ``url`` uses the OpenAI-compatible transport. This is
      how the 2-3 pulled local models become a real local cascade.
    """
    from .workspace_local_inproc import INPROC_SENTINEL
    env_url = os.environ.get(LOCAL_URL_ENV, "").strip()
    env_model = canonical_local_model()
    if env_url and env_model:
        return [Tier(name="local", url=env_url, model=env_model,
                     is_cloud=False, price_per_1k=0.0)]

    raw = cfg.get("local")
    entries: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        entries = [raw]
    elif isinstance(raw, list):
        entries = [e for e in raw if isinstance(e, dict)]

    out: list[Tier] = []
    for i, e in enumerate(entries):
        model = str(e.get("model", "")).strip()
        if not model:
            continue
        url = str(e.get("url", "")).strip() or INPROC_SENTINEL
        # name from the model id (a path -> its filename stem); keep dots, they
        # are part of model names like "phi-3.5-mini-q4"
        label = Path(model).name if ("/" in model or model.endswith(".gguf")) else model
        if label.endswith(".gguf"):
            label = label[:-5]
        name = f"local-{label or i + 1}" if len(entries) > 1 else "local"
        out.append(Tier(name=name, url=url, model=model,
                        is_cloud=False, price_per_1k=0.0))
    if out:
        return out
    # local-first by default: nothing explicit (no env, no config), so
    # auto-discover any registered local GGUFs as in-process tiers (cheap ->
    # capable by file size). This makes local the default rung whenever a model
    # is on disk — cloud is reached only when no local model exists at all.
    return _registry_local_tiers()


def _registry_local_tiers() -> list[Tier]:
    """The workspace's ONE standard local model as the default local tier.

    The workspace has a single standard model (the model registered under the ``workspace``
    role); a companion's model lives under its own role (e.g. ``code-fix``) and is
    NOT folded into the workspace cascade. So this returns exactly one in-process tier: the
    ``workspace``-role model if set, else the single smallest registered GGUF. Extra
    local rungs are opt-in — a user who wants a multi-model local cascade lists
    them in the config ``local`` array (handled before we reach here)."""
    from .workspace_local_inproc import INPROC_SENTINEL
    try:
        from . import models_registry

        def _gguf(mid: str) -> str:
            for e in models_registry.list_models():
                if e.id == mid and e.artifact_path:
                    p = Path(e.artifact_path).expanduser()
                    if p.suffix == ".gguf" and p.exists():
                        return str(p)
            return ""

        # 1) the designated workspace-standard model
        for mid in models_registry.models_for_role("workspace"):
            path = _gguf(mid)
            if path:
                return [Tier(name="local", url=INPROC_SENTINEL, model=path,
                             is_cloud=False, price_per_1k=0.0)]
        # 2) no standard designated: the single smallest registered GGUF
        found: list[tuple[int, str]] = []
        seen: set[str] = set()
        for e in models_registry.list_models():
            if not e.artifact_path:
                continue
            p = Path(e.artifact_path).expanduser()
            if p.suffix == ".gguf" and p.exists() and str(p) not in seen:
                seen.add(str(p))
                found.append((p.stat().st_size, str(p)))
        if found:
            found.sort(key=lambda t: t[0])
            return [Tier(name="local", url=INPROC_SENTINEL, model=found[0][1],
                         is_cloud=False, price_per_1k=0.0)]
    except Exception:
        return []
    return []


def tiers_for_workspace(
    *,
    capability_token: str = "",
    track_id: str = "",
) -> list[Tier]:
    """Assemble the cascade tiers: local rung(s) first, cloud last. Each value
    resolves from the env first, then the installer config — so a fresh install
    with pulled models works with no env set."""
    cfg = _local_config()
    tiers: list[Tier] = list(_local_tiers(cfg))
    cfg_cloud = cfg.get("cloud") or {}
    cu = os.environ.get(CLOUD_URL_ENV, "").strip() or str(cfg_cloud.get("url", "")).strip()
    cm = os.environ.get(CLOUD_MODEL_ENV, "").strip() or str(cfg_cloud.get("model", "")).strip()
    proxy_url = os.environ.get(
        "RVND_EGRESS_PROXY_URL", "http://127.0.0.1:8443/v1"
    ).strip()
    if cu and cm:
        try:
            price = float(os.environ.get(CLOUD_PRICE_ENV, "") or cfg_cloud.get("price_per_1k", 0.30))
        except (ValueError, TypeError):
            price = 0.30
        tiers.append(Tier(
            name="cloud",
            url=cu,
            model=cm,
            is_cloud=True,
            price_per_1k=price,
            proxy_url=proxy_url,
            capability_token=capability_token,
            track_id=track_id,
        ))
    return tiers


def cascade_for_workspace(folder: str | Path,
                     prompt: str,
                     *,
                     max_tokens: int = 512,
                     temperature: float = 0.0,
                     completer: Optional[Callable[..., dict]] = None,
                     log_root: str | Path | None = None,
                     capability_token: str = "",
                     track_id: str = "") -> dict[str, Any]:
    """Run the governed local-first cascade for ``folder`` and record it.

    Returns the cascade result dict plus ``ledger`` (tokens the cloud did NOT
    spend when served locally) and ``audit_id``. With no tier configured returns
    ``{ok: False, error, advice}`` — loud, not silent.
    """
    tiers = tiers_for_workspace(
        capability_token=capability_token,
        track_id=track_id,
    )
    # D7 air-gap: a folder set ``local_llm.mode = local-only`` must NEVER reach a
    # cloud tier. Drop every cloud rung BEFORE the cascade runs (fail-closed:
    # is_air_gapped also returns True for an unverifiable policy). If the local
    # rung then defers, run_cascade returns escalation_withheld — no cloud to
    # escalate to — which is exactly the right air-gap outcome for the default
    # on_insufficient=escalate-to-cloud: escalation cannot override the air-gap.
    from .policy import is_air_gapped
    air_gapped = is_air_gapped(folder)
    cloud_withheld = 0
    if air_gapped:
        kept = [t for t in tiers if not t.is_cloud]
        cloud_withheld = len(tiers) - len(kept)
        tiers = kept
    if not tiers:
        if air_gapped:
            # Air-gapped and nothing left to run. Don't mislead the operator into
            # configuring a cloud credential that the policy forbids — say it's the
            # air-gap. cloud_withheld distinguishes "cloud was configured but
            # withheld" (>0) from "no cloud was ever configured" (0); either way
            # the fix is a LOCAL model or lifting the air-gap, never a cloud credential.
            if cloud_withheld:
                cloud_note = (f"the {cloud_withheld} configured cloud rung(s) were "
                              f"withheld")
            else:
                cloud_note = "no cloud rung is configured (and none could be used anyway)"
            return {
                "ok": False,
                "error": "air-gapped (local-only): cloud egress is forbidden by "
                         "policy and no local model tier is configured",
                "advice": (f"this folder is air-gapped (local_llm.mode=local-only): "
                           f"{cloud_note}. Configure a LOCAL model — `workspaces models config "
                           f"--local-url <url> --local-model <id>` or {LOCAL_URL_ENV} + "
                           f"{LOCAL_MODEL_ENV} — or lift the air-gap in the folder policy. "
                           f"Config file: {config_path()}"),
                "air_gapped": True,
                "cloud_tiers_withheld": cloud_withheld,
                "served_is_cloud": False,
                "tiers": 0,
                "config_path": str(config_path()),
            }
        return {
            "ok": False,
            "error": "no model tier configured",
            "advice": (f"configure a model: `workspaces models config --local-url <url> "
                       f"--local-model <id>` for your own local endpoint (BYOK), or "
                       f"register a local model and run `workspaces models config`; set "
                       f"{CLOUD_URL_ENV} + {CLOUD_MODEL_ENV} for a cloud rung, then "
                       f"bind an egress connector credential_ref to the active track. "
                       f"Or set {LOCAL_URL_ENV} + {LOCAL_MODEL_ENV} directly. "
                       f"Config file: {config_path()}"),
            "air_gapped": air_gapped,            # False here, but carry it per spec
            "cloud_tiers_withheld": cloud_withheld,
            "served_is_cloud": False,
            "tiers": 0,
            "config_path": str(config_path()),
        }
    kw: dict[str, Any] = {"max_tokens": max_tokens, "temperature": temperature,
                          "verifier": nonempty_verifier}
    if completer is not None:
        kw["completer"] = completer
    else:
        # dispatching completer: in-process for inproc rungs, HTTP otherwise
        from .workspace_local_inproc import workspace_completer
        kw["completer"] = workspace_completer
    res = run_cascade(prompt, tiers, **kw)

    audit_id = None
    audit_dropped = None
    try:
        resolved = str(Path(folder).expanduser().resolve())
        log = MutationLog(resolved, log_root=log_root)
        ev = LogEvent(
            event="system", folder_path=resolved,
            # Hash, not the raw prompt prefix — the pair_id lands in the signed
            # chain and must not leak prompt content (which may carry secrets/PII).
            pair_id="cascade:" + hashlib.sha256(
                (prompt or "").encode("utf-8")).hexdigest()[:16],
            channel="system",
            actor="workspace-cascade",
            extra={"kind": "cascade", "served_by": res.served_by,
                   "served_is_cloud": res.served_is_cloud, "ok": res.ok,
                   "escalation_withheld": res.escalation_withheld,
                   "air_gapped": air_gapped,
                   "cloud_tiers_withheld": cloud_withheld},
        )
        audit_id = log.append(ev)
    except Exception as exc:
        from .audit_drop import record as _record_drop
        audit_dropped = _record_drop("workspace_cascade.cascade_for_workspace", exc,
                                     folder=str(folder), log_root=log_root)

    out = res.to_dict()
    out["ledger"] = res.ledger()
    out["audit_id"] = audit_id
    if audit_dropped:
        # See workspace_orchestrate: a null audit_id alone cannot say whether the
        # write failed or was never attempted.
        out["audit_dropped"] = audit_dropped["error"]
    out["air_gapped"] = air_gapped
    out["cloud_tiers_withheld"] = cloud_withheld
    return out
