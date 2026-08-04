# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND adapter seam over loomground-legal's connection algebra; internal by design.

The workspaces boundary rule confines every direct import of an upstream
Loomground package to the ``adapters/`` seam. This module is that seam for the
legal plane: it re-exports loomground-legal's connection algebra
(``connection_algebra`` — the solver ``RelationAlgebra`` built from the
package's ``connections.json``), plus the solver's ``ESCALATE`` sentinel and 5D
``Dimension``, so ``workspaces.legal_connection`` (the historical shim) consumes
them through here rather than reaching upstream directly.
"""
from __future__ import annotations

from loomground_solver import ESCALATE, Dimension
from loomground_legal.connection import (
    GOVERNING,
    connection_algebra,
    is_connection,
    load_connections,
)

__all__ = [
    "ESCALATE", "Dimension",
    "GOVERNING", "connection_algebra", "is_connection", "load_connections",
]
