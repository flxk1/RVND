<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Host egress lock

These installers are an optional Tier 3 control. They enforce identity-wide
containment without provider address lists: the dedicated Linux host is
default-drop with only the proxy UID permitted, while shared macOS/Windows
hosts block the agent identity from all non-loopback egress. It is required
for deployments that claim preventive egress enforcement. This covers
TCP, UDP/QUIC, relay hosts, and future provider addresses.

Run the plan or dry-run mode first. Applying or removing rules requires host
administrator authority and must be done in a recovery-capable maintenance
window. Keep a second administrative session open and verify that unrelated
network traffic still works.

## Linux

```sh
deploy/firewall/apply-egress-lock.sh --dry-run --proxy-user rvnd-egress
sudo deploy/firewall/apply-egress-lock.sh --apply --proxy-user rvnd-egress
sudo deploy/firewall/apply-egress-lock.sh --remove
```

## macOS

```sh
deploy/firewall/apply-egress-lock-macos.sh --dry-run --agent-user rvnd-agent
sudo deploy/firewall/apply-egress-lock-macos.sh --apply --agent-user rvnd-agent
```

For persistence, add these lines to the host-managed `/etc/pf.conf`; do not
replace that file with the RVND rules:

```text
anchor "com.rvnd.egress-lock"
load anchor "com.rvnd.egress-lock" from "/etc/pf.anchors/com.rvnd.egress-lock"
```

Remove the live anchor with `sudo
deploy/firewall/apply-egress-lock-macos.sh --remove`. Remove the two host
`pf.conf` lines separately if persistence is no longer wanted.

## Windows

In an elevated PowerShell, `Plan` is the default and makes no changes:

```powershell
.\deploy\firewall\windows-firewall.ps1 `
  -AgentUser 'rvnd-agent'

.\deploy\firewall\windows-firewall.ps1 -Mode Apply `
  -AgentUser 'rvnd-agent'

.\deploy\firewall\windows-firewall.ps1 -Mode Remove `
  -AgentUser 'rvnd-agent'
```

There is no DNS refresh schedule because no provider addresses are embedded.
Run `scripts/verify_egress_lock.py` after every apply. Static rule verification
is not behavioral proof: also confirm TCP and UDP fail as the agent identity,
while the proxy identity can reach its configured upstream.
