#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Run the Step 2 Build journey through the real loopback HTTP bridge."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="rvnd_build_agent_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))

import serve  # noqa: E402

folder = os.path.join(tmp, "release-workspace")


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()
    server = serve.make_server(port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.2)
    try:
        result = subprocess.run(
            ["node", str(HERE / "console_build_agent.mjs"), str(port), folder],
            capture_output=True,
            text=True,
            timeout=45,
        )
    finally:
        server.shutdown()
    print((result.stdout + result.stderr).strip())
    return 0 if result.returncode == 0 and "PASS:" in result.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
