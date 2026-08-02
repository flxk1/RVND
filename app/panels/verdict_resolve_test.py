#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""D10 render gate: the client renders, never decides.

Runs verdict_resolve.mjs (jsdom over the real index.html) to assert the
client-side egress verdict mapping never softens a server 'prohibited', lets the
reserved-by-law floor only tighten, and fails closed on an unknown verdict.

  python app/verdict_resolve_test.py
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
r = subprocess.run(["node", str(HERE / "verdict_resolve.mjs")], cwd=str(HERE))
sys.exit(r.returncode)
