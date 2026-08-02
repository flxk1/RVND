# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Declared upstream seam for Governance and Deontic language contracts.

Internal by design: callers consume the published-policy-pack boundary, while
this module solely confines dependency imports to the adapter layer.
"""
from __future__ import annotations

import deontic
import loomground_governance


def installed_policy_language_packages() -> tuple[tuple[str, object, str], ...]:
    return (
        (
            "governance",
            loomground_governance,
            "authoritative policy grammar and vocabulary",
        ),
        ("deontic", deontic, "normative classification language"),
    )
