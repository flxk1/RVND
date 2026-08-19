# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND seam for the Solver's deterministic interpret→audit pipeline.

Internal by design, following this package's one-upstream-module rule:
``loomground_solver.interpret`` is imported here and nowhere else under
``workspaces``. ``interpret(text)`` parses the solver's fact/rule/claim
notation into ``{"facts", "rules", "candidate"}``; ``audit(interp)`` forward-
chains to a closure and reports ``{consistent, entailed, verdict, reasons}``.

Caveat that callers MUST respect (verified live 2026-08-08): ``audit`` over an
empty or unparsed fact set reports ``consistent: True`` — "nothing to check"
looks like "consistent". Fail-closed mapping of that case to OPEN is the
caller's job (see ``rvnd.reasoning_integrity``), never this seam's.
"""
from loomground_solver.interpret import audit, interpret

__all__ = ["audit", "interpret"]
