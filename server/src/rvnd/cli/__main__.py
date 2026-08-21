# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Package entry for ``python -m rvnd.cli`` — same surface as the
``workspaces`` console script (rvnd.cli:main)."""

from . import main

if __name__ == "__main__":
    raise SystemExit(main())
