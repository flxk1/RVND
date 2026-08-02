# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Package entry for ``python -m workspaces.cli`` — same surface as the
``workspaces`` console script (workspaces.cli:main)."""

from . import main

if __name__ == "__main__":
    raise SystemExit(main())
