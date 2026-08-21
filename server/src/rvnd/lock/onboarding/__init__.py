# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Onboarding — first-run wizard that sets up the local LLM and persists config.

After install, the user runs `agent-tool-lock setup` once. The wizard:

1. Detects the runtime environment (PyInstaller-bundled binary vs. pip install)
2. Checks whether a model is already bundled or installed
3. Offers to download the canonical model if missing
4. Runs a smoke test against the chosen backend
5. Persists configuration to ~/.config/agent-tool-lock/config.json

After the wizard, the runtime knows which backend to use and tier_c_check_semantic
works air-gapped.
"""

from .config import Config, load_config, save_config, default_config_path
from .wizard import run_wizard, WizardResult

__all__ = ["Config", "load_config", "save_config", "default_config_path",
           "run_wizard", "WizardResult"]
