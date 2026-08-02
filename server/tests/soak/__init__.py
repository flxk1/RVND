# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Soak-test orchestrator package.

Not part of the per-PR unit suite. Invoke via::

    python -m tests.soak.run_soak --hours 24 --output /tmp/soak.csv

Soak tests cover sustained runtime metrics and failure thresholds.
"""
