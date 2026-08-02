# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Config persistence for the onboarding wizard."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Config:
    """Persisted user configuration."""

    backend_spec: str = "mock"
    audit_log_path: str = ""        # empty → no audit
    default_mode: str = "standard"  # standard | strict | permissive | audit_only
    default_oversight: str = "approve"  # autonomous | notify | review | approve | supervised | manual
    model_dir: str = ""             # where bundled/downloaded models live
    setup_completed_at: str = ""    # ISO-8601 timestamp


def default_config_path() -> Path:
    """Standard config location: ~/.config/agent-tool-lock/config.json."""
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "agent-tool-lock" / "config.json"


def load_config(path: Path | str | None = None) -> Config:
    """Load config from disk; returns Config() defaults if file doesn't exist."""
    path = Path(path) if path else default_config_path()
    if not path.exists():
        return Config()
    try:
        data = json.loads(path.read_text())
        return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return Config()


def save_config(config: Config, path: Path | str | None = None) -> Path:
    """Persist config. Returns the path it was written to."""
    path = Path(path) if path else default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2))
    return path


def apply_config_to_env(config: Config) -> None:
    """Set runtime env vars from a config so the rest of the package sees them."""
    if config.backend_spec:
        os.environ["AGENT_TOOL_LOCK_LLM_BACKEND"] = config.backend_spec
    if config.audit_log_path:
        os.environ["AGENT_TOOL_LOCK_AUDIT_LOG"] = config.audit_log_path
    if config.default_mode:
        os.environ["AGENT_TOOL_LOCK_DEFAULT_MODE"] = config.default_mode
