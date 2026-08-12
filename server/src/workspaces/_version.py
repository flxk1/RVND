# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Single version source. Both build configs (pyproject.toml and
server/pyproject.toml) resolve their [project] version from this attribute,
and the package exports it as workspaces.__version__ — one artifact, one
label, nothing to drift.

Internal by design: a version constant, not a surface.
"""

__version__ = "0.6.9.9"
