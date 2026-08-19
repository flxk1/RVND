# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Model-attestation domain: core (probes, attest, signatures) and runtime
(baseline, battery, admit, status).

The package path doubles as the former attestation module: core's public API
is re-exported here so existing importers of rvnd.attestation resolve
unchanged. New code imports the submodules directly.
Internal by design: the submodules are the surface.
"""
from .core import *  # noqa: F401,F403
