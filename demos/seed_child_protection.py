#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seed the child-protection demo workspace — a repeatable template for
product presentations, not a one-off manual setup.

Drives the SAME public /tool bridge the console uses (no internal
`workspaces` imports), so this stays true to what a real session looks
like: a benign task a person must sign off on, a content-filter policy
pasted and applied, and the resulting prohibited task severed at the
boundary. The story: "here's a task that needs a person, here's a policy
that makes a task un-runnable regardless of who signs off."

Usage:
    python3 app/serve.py 8801 --no-open &      # start the app once
    python3 demos/seed_child_protection.py      # then seed it

Idempotent per folder: re-running against a folder that already has this
content only appends duplicate parties/tasks if run twice — point it at a
fresh WORKSPACE_DIR (default) for a clean demo each time, or pass --folder
to target an existing one deliberately.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

WORKSPACE_DIR = Path(__file__).with_name("child-protection-workspace")

POLICY_TEXT = (
    "Content that sexualizes or endangers a minor must never be generated; "
    "any request involving a minor and sexual or exploitative content is "
    "prohibited outright, severed at the boundary, no sign-off possible. "
    "Any other request that merely involves a minor (e.g. a photo caption, "
    "a school project) must be reviewed by a person before it runs."
)


def _fetch_token(base: str) -> str:
    with urllib.request.urlopen(f"{base}/") as r:
        html = r.read().decode("utf-8")
    m = re.search(r"window\.__WORKSPACES_TOKEN__=(\"[^\"]*\"|'[^']*')", html)
    if not m:
        raise RuntimeError("could not find the session token in the served page — is app/serve.py running?")
    return json.loads(m.group(1).replace("'", '"'))


def _call(base: str, token: str, tool: str, op: str, params: dict) -> dict:
    body = json.dumps({"tool": tool, "args": {"op": op, "params": params}}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/tool", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Workspaces-Token": token},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"http {e.code}: {e.read().decode('utf-8', 'ignore')}"}


def seed(base: str, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    fc = str(folder.resolve())
    token = _fetch_token(base)
    actor = "demo-seed"

    print(f"folder: {fc}")

    r = _call(base, token, "workspace_policy", "party_register",
              {"folder_context": fc, "party_id": "content-agent", "kind": "agent",
               "name": "Content agent", "actor": actor})
    print("agent:", r.get("error") or "registered")

    r = _call(base, token, "workspace_policy", "party_register",
              {"folder_context": fc, "party_id": "reviewer", "kind": "human",
               "name": "Reviewer", "actor": actor})
    print("person:", r.get("error") or "registered")

    r = _call(base, token, "workspace_workflow", "use_case_register",
              {"folder_context": fc, "use_case_id": "photo-caption", "name": "Draft a photo caption",
               "fingerprint": {"issue_type": "content_generation"}, "risk": "medium",
               "allowed_agents": ["content-agent"], "actor": actor})
    print("benign task:", r.get("error") or "registered")

    # run it once so it shows a live "needs a person" disposition rather than
    # sitting unfired — the contrast is the point of the demo: one task waits
    # on a person, the other is severed at the boundary outright.
    import time as _time
    r = _call(base, token, "workspace_workflow", "operate",
              {"folder_context": fc, "use_case_id": "photo-caption", "agent_id": "content-agent",
               "issues": [{"issue_id": "i-demo1", "issue_type": "content_generation", "completeness": "high"}],
               "now_epoch": int(_time.time())})
    print("benign task run:", r.get("error") or r.get("final"))

    r = _call(base, token, "workspace_workflow", "governance_chat",
              {"folder_context": fc, "text": POLICY_TEXT, "policy_text": "", "intent": "policy"})
    if r.get("error") or r.get("kind") != "twin":
        print("policy classification failed:", r.get("error") or r)
        sys.exit(1)
    twin = r["result"]
    if twin.get("ok") is False:
        print("policy could not be resolved:", twin.get("errors"))
        sys.exit(1)

    v = _call(base, token, "workspace_workflow", "patch_validate",
              {"folder_context": fc, "netlist": twin.get("netlist")})
    if not v.get("ok"):
        print("patch failed validation:", v)
        sys.exit(1)

    a = _call(base, token, "workspace_workflow", "patch_apply",
              {"folder_context": fc, "actor": actor, "netlist": twin.get("netlist")})
    if a.get("error") or a.get("ok") is False:
        print("apply failed:", a.get("error") or a.get("errors"))
        sys.exit(1)
    print("policy applied — the prohibition is now on the chain")

    r = _call(base, token, "workspace_workspace", "add",
              {"folder_context": fc, "label": "Child Protection — Product Demo"})
    print("registered in the workspace picker:", r.get("error") or r.get("path"))
    print()
    print("done. open the console, select this workspace, and check Build —")
    print("the prohibited task's cord to the boundary should be red; the")
    print("benign task's cord should be orange (needs a person).")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default="http://127.0.0.1:8801", help="the running app/serve.py base URL")
    p.add_argument("--folder", default=str(WORKSPACE_DIR), help="target workspace folder (default: demos/child-protection-workspace)")
    args = p.parse_args()
    seed(args.base, Path(args.folder))
