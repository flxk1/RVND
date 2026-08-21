# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Allow ``python -m workspaces ...`` to invoke the CLI."""

from __future__ import annotations

import sys

from .cli import main


if __name__ == "__main__":
    sys.exit(main())
