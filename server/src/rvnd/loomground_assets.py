# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Compatibility facade for authoritative ``loomground-governance`` artifacts.

No Loomground vocabulary or conformance data is owned by RVND.  These helpers retain
the old RVND call surface while resolving every artifact from the installed language kit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loomground_governance import artifact_path, conformance_manifest, language_version
from loomground_governance import vocabulary as _vocabulary


def vocabulary(name: str) -> dict:
    return _vocabulary(name)


def governance_language_version() -> str:
    """The installed ``loomground-governance`` language version."""
    return language_version()


def bundle_vocabulary_dir() -> Path:
    """Legacy name for the installed package's authoritative vocabulary directory."""
    return Path(str(artifact_path("vocabulary")))


def live_root() -> Optional[Path]:
    """Return the installed authoritative artifact root (legacy compatibility hook)."""
    return Path(str(artifact_path()))


def conformance_dir() -> Path:
    return Path(str(artifact_path("conformance")))


def grounder_gold_path(template: bool = False) -> Path:
    """The grounder support gold corpus (or its unlabelled template) from the
    installed language kit's conformance data."""
    name = ("grounder-support-gold.template.jsonl" if template
            else "grounder-support-gold.jsonl")
    return Path(str(artifact_path("conformance", "grounder", name)))


def manifest() -> dict:
    return conformance_manifest()


def llms_txt() -> str:
    """The governance language guide (``llms.txt``), consumed byte-for-byte from
    loomground-governance through this seam — never copied, so it cannot drift.
    The front door hands this to an agent (the ``governance://llms.txt`` resource
    and ``GET /llms.txt``)."""
    return Path(str(artifact_path("llms.txt"))).read_text(encoding="utf-8")
