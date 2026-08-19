# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over loomground-norm's obligation runtime — RVND owns neither.

RVND's parallel obligation state machine is RETIRED. The tracked, dated, gated
duty state (``pending -> due_soon -> due -> breached_candidate``, plus the
human-recorded and migration transitions) now lives in ``loomground-norm``
(``loomground_norm.obligation_runtime``); this module re-exports it behind the
historical import names (``Obligation``, ``ObligationRegistry``,
``ObligationError``, ``OPEN_STATES``, ``TERMINAL_STATES``, ``_obligor_role``)
through the ``adapters/norm`` seam.

The plane tracks obligations against an injected ``SourceInstrument`` and audits
through an injected ``AuditSink``; the seam wires RVND's ``ContractInstance`` and
its signed mutation log in, so ``ObligationRegistry(folder, log_root=...)`` keeps
its historical shape. No state-machine logic lives here. Callers are unchanged;
this shim is deleted once they migrate to the plane directly.
"""
from __future__ import annotations

from .adapters.norm import (
    Obligation,
    ObligationRegistry,
    ObligationError,
    OPEN_STATES,
    TERMINAL_STATES,
    _obligor_role,  # noqa: F401  -- re-export: `import *` skips _private names, so this line is the export
)

__all__ = ["Obligation", "ObligationRegistry", "ObligationError",
           "OPEN_STATES", "TERMINAL_STATES"]
