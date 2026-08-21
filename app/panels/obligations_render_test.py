#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for the Obligations board (Pending section, read-only).

Seeds one contract with two obligations — one ticked past its deadline to
breach candidate, one whose relative deadline cannot resolve — boots serve.py,
then runs obligations_render.mjs against it.

  python3 app/obligations_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="obligations_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                          # noqa: E402
from rvnd.contracts.instance import ContractInstance, ContractRegistry, PartyRef  # noqa: E402
from rvnd.obligation_runtime import ObligationRegistry  # noqa: E402
from rvnd.obligation_scheduler import ObligationScheduler  # noqa: E402
from rvnd.predicate import parse_condition  # noqa: E402
from rvnd.temporal import Date  # noqa: E402

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)
LOG = os.environ["WORKSPACE_L0_LOG_ROOT"]


def seed() -> None:
    contract = ContractInstance(
        contract_id="dpa-acme", version=1, contract_type="dpa",
        parties=(PartyRef(entity_code="acme", role="processor"),
                 PartyRef(entity_code="kunde", role="controller")),
        effective_date=Date("2026-07-01"),
        events={"signing": Date("2026-06-15"),
                "personal_data_breach": Date("2026-08-10")},
        document_hash=f"sha256:{'a' * 32}", language="en")
    ContractRegistry(F, log_root=LOG).register(contract)
    ObligationRegistry(F, log_root=LOG).instantiate(contract, [
        {"id": "rule:notify72",
         "norm": {"modal": "obligation", "subject": "processor",
                  "action": "notify the controller of a personal data breach",
                  "condition": "no later than 72 hours after the personal data breach",
                  "condition_struct": parse_condition(
                      "no later than 72 hours after the personal data breach").to_dict()}},
        {"id": "rule:delete",
         "norm": {"modal": "obligation", "subject": "processor",
                  "action": "delete all personal data upon termination",
                  "condition": ""}},
    ])
    ObligationScheduler(F, log_root=LOG).tick(Date("2026-09-01"))


def main() -> int:
    seed()
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "obligations_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
