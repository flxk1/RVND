<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Quickstart — a small team on Tailscale (no IdP, no container)

Ten minutes from a laptop to a team console: Tailscale verifies who is
calling, Rvnd records who decided. Nothing to operate but the two of them.

## 1. Install and start Rvnd

    pip install rvnd            # or: pip install -e . from a checkout
    python app/serve.py --no-open

The console is now on `http://127.0.0.1:8799` for you alone.

## 2. Put Tailscale in front

    tailscale serve --bg --set-path / http://127.0.0.1:8799

Tell Rvnd to believe Tailscale's identity header and restart:

    export WORKSPACE_PRINCIPAL_HEADER=Tailscale-User-Login
    python app/serve.py --no-open

Teammates on the tailnet open `https://<machine>.<tailnet>.ts.net` and the
console's top bar shows who Tailscale says they are. Rvnd itself stays on
loopback — Tailscale is the only way in.

## 3. Register the team

Each teammate becomes a party whose id is their tailnet login; the channels
are where decisions reach them:

    workspace_party register
      party_id: human-operator          kind: human
      competences: [data-protection]
      channels: ["slack:https://hooks.slack.com/services/T000/B000/xxxx"]

A Slack incoming webhook per person (or one per channel) is the whole
"Slack watcher": when an escalation opens, every holder gets the minimised
notification — title and personal action link, never the content — and one
click lands them in the workbench as themselves.

## 4. Prove it

Open a test decision and watch it arrive:

    workspace_dispatch decision_open
      surface: {…}   raised_by: crm-bot   competence: data-protection

The record of whoever decides carries `auth_rung` — the chain states how
strongly the system knew who acted.
