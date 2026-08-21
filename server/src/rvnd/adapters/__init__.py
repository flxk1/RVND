# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Adapter implementations for workspace-adapter and upstream engine ports.

Host declarations continue to resolve through the existing per-kind modules. The
``solver`` and ``versum`` subpackages are the explicit boundaries from RVND-owned
governance to those independently versioned engines.
Internal by design: only the submodules are surface.
"""
