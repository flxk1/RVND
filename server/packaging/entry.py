# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""PyInstaller entry shim — the binary boots the package properly.
(cli.py uses relative imports; freezing it directly breaks them.)"""
import sys
from rvnd.cli import main

if __name__ == "__main__":
    sys.exit(main())
