# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Shared storage defaults with no runtime-module dependencies."""

from pathlib import Path


LOG_ROOT_DEFAULT = Path.home() / ".workspace" / "log"
"""Default global log root; each workspace receives its own subdirectory."""
