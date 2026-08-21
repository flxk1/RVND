# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Bind Solver's governance port to RVND policy, custody and audit; internal by design."""

from __future__ import annotations

from typing import Any, Callable, Optional

from loomground_solver.api import check as _solver_check


class RvndGovernance:
    def __init__(self, folder_path, *, policy_loader: Optional[Callable] = None,
                 lock_fn: Optional[Callable[[str], dict]] = None,
                 log_sink: Optional[Any] = None):
        self._folder = folder_path
        self._load_policy = policy_loader
        self._lock = lock_fn
        self._log = log_sink

    def _policy(self):
        """The policy IN FORCE, not the folder's declaration.

        This seam decides (RC-4 turns on its answer), and the enforcement
        posture is the deployment's. An injected ``policy_loader`` still wins
        -- tests and hosts substitute their own -- but the default has to be
        the effective policy or a deployment that disabled Oversight would
        still see folder-scoped cases pass the oversight floor.
        """
        if self._load_policy is None:
            from ...policy import effective_policy
            self._load_policy = effective_policy
        return self._load_policy(self._folder)

    def oversight_level(self) -> str:
        return self._policy().oversight_default_level

    def oversight_active(self) -> bool:
        active = self._policy().oversight_is_active
        return bool(active() if callable(active) else active)

    def classify(self, text: str) -> dict:
        if self._lock is None:
            from ...lock_classify import _lock_string
            self._lock = _lock_string
        result = self._lock(text) or {}
        findings = result.get("findings", 0)
        count = len(findings) if isinstance(findings, (list, tuple, set)) else int(findings or 0)
        return {"findings": count, "raw": result}

    def record(self, event: dict) -> None:
        if self._log is not None:
            self._log.append_raw(**event)


def check_with_rvnd_governance(
    case: dict,
    folder_path,
    **kwargs,
):
    """Live RVND→Solver policy seam used by folder-scoped contract checks."""
    return _solver_check(
        case,
        governance=RvndGovernance(folder_path),
        **kwargs,
    )
