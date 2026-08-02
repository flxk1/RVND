<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->

# RVND threat model

A prospective STRIDE pass over RVND's trust boundaries. It complements
[`red-team-findings.md`](red-team-findings.md), which is *retrospective* (specific
attacks found and fixed, each pinned by a regression); this document is the
*prospective* frame — the assets, the boundaries, and the residual risks the
mitigations do not fully close. Where a threat maps to a red-team entry it is
cross-referenced (`A1`–`A9`, `M1`–`M7`).

*Scope: RVND itself as a deployed system (v0.6.8.x). Not the customers' agents
RVND governs. Re-verify against the code on each material change — the file:line
anchors below are the contract this model is written against.*

## Assets, in priority order

1. **The signed audit chain** — the tamper-evident record of every governed
   decision. Integrity is the product. (`server/src/workspaces/mutation_log.py`)
2. **The signing keys** — identity (per-host) and controller (workspace).
   Whoever holds them can author history. (`server/src/workspaces/signing.py`)
3. **Personal data at rest** — pairs, drafts, cards, and the decisions store,
   optionally sealed (AEAD). (`seal.py`, `erasure.py`)
4. **The egress boundary** — the only sanctioned path to a cloud model; the
   thing that must never leak PII or be bypassed. (`lock/egress_proxy.py`)
5. **Availability** of the local governance service to its operator.

## Trust boundaries and STRIDE

### Boundary 1 — agent ↔ MCP server

An agent (or an MCP host acting for it) calls the tool surface
(`mcp_server.py:_DECLARED_TOOLS`, 23 folded facades). Over HTTP the guard is
`app/serve.py`: loopback bind, Host/Origin checks, proxy-identity proof, and a
fail-closed `hmac.compare_digest` on the per-session token (`serve.py:265`).

- **S (spoofing):** a same-machine process posing as the agent. *Mitigation:*
  session-token compare before any dispatch; signed session admission binds
  registry/lane/policy/folder/uid (`session_admission.py`). *Residual:* loopback
  TCP exposes no peer-UID, so co-located process isolation leans on the OS
  firewall (Boundary 4) — documented, not eliminated.
- **T (tampering):** malicious tool arguments. *Mitigation:* Tier A/B argument
  scanning; `folder_context` allowlist refuses sibling-folder escape (`A6`).
- **R (repudiation):** "the agent didn't do that." *Mitigation:* every governed
  step is a signed chain event.
- **I (information disclosure):** cross-workspace scope leak. *Mitigation:*
  scope is bound to `folder_context`, not the query string (`M1`); token echo
  through a "safe" view is refused (`M3`).
- **D (denial of service):** flooding the tool surface. *Mitigation:* loopback
  only; the egress proxy now sheds load with 503 above a concurrency cap.
- **E (elevation):** normal caller invoking a privileged op. *Mitigation:*
  capability/competence gating; a prohibited act is refused at any grade
  (`test_operate_prohibition_hard_stop`).

### Boundary 2 — server ↔ egress proxy (the cloud path)

The proxy (`lock/egress_proxy.py`) forwards to a cloud model only after
verifying `X-Rvnd-Capability` (`egress_proxy.py:771`), scanning/redacting the
prompt, and applying oversight. A refusal writes a `capability_refused` audit
entry and returns 403.

- **S:** a tokenless or forged-token request. *Mitigation:* signature +
  freshness + revocation + exact folder binding; refuse-to-start without a
  wired verifier (`egress_proxy.py:603`).
- **T:** SSRF / redirect / DNS-rebind to reach the internal network. *Mitigation:*
  allowlisted egress, per-hop revalidation, single-resolution connection binding
  (`A3`, `A4`); request-target authority-selection attacks refused.
- **I:** PII leaving in the prompt. *Mitigation:* Tier B/B+/C scan + redaction
  before forward; the recall floor is now measured and gated (RV-04). *Residual:*
  Tier C semantic recall depends on the operator's local model; space-grouped
  IBANs are a known Tier-B gap (`lock-recall-baseline.json` `known_gaps`).
- **D:** a hung/stuck upstream exhausting workers. *Mitigation:* bounded forward
  deadline (`WORKSPACE_EGRESS_TIMEOUT_SECS`) + concurrency cap shedding 503.
- **E:** the agent injecting a credential it shouldn't hold. *Mitigation:*
  credential-binding check; the track's credential is injected server-side, the
  agent never holds the key.

### Boundary 3 — console ↔ bridge

The browser console reaches the server via `POST /tool` with `X-Workspaces-Token`
(`serve.py:265`), compared timing-safe; missing/invalid → 403 before dispatch.

- **S/E:** another local process driving the bridge. *Mitigation:* per-session
  token, injected into the served page (`serve.py:196`), never persisted.
- **T (clickjacking / DOM):** *Mitigation:* frame-ancestors CSP; the console
  renders verdicts from server responses only (never recomputes a verdict), and
  the fail-closed verdict surface is asserted by the render gates.
- **I:** token disclosure. *Residual:* the token lives in the page's JS context;
  a same-origin XSS would expose it — the console is self-authored, no third-party
  script, CSP blocks external hosts, but this is the sharpest console residual.

### Boundary 4 — host ↔ OS firewall

The egress lock (`deploy/firewall/`: nftables/pf/PowerShell + `apply-egress-lock.sh`)
is the identity-scoped default-drop that makes the proxy the *only* path out.
CI proves it with a live probe (positive + negative controls).

- **T/E:** an agent process bypassing the proxy by connecting directly.
  *Mitigation:* default-drop for the agent's UID; only the proxy UID may egress.
  *Residual:* rests on correct firewall install (the README mandates dry-run +
  a second admin session) and on the OS honouring UID rules; a root-capable
  attacker on the host is out of scope (see below).

## Residual risks the model accepts (RV-23 sign-off)

These are **accepted residual risks**, recorded per Part V of the test plan —
owner **flxk1**, review on each minor release.

- **RR-1 · Key-holder chain rewrite (`A2`).** An attacker holding `identity.priv`
  can re-sign a fully rewritten chain; `verify_chain` passes. Mitigated only by
  `host_id` divergence (advisory; hard-fail under
  `WORKSPACE_STRICT_HOST_DIVERGENCE=1`) and by shipping the log off-host.
  **Not fully closable on a single self-attesting host** — this is the
  fundamental limit of a local tamper-evident log. *Closing it* needs external
  anchoring (a transparency log / third-party timestamp), which is out of scope
  for the local-first design; recorded here rather than pretended away.
- **RR-2 · Identity key is immutable for the life of a chain.** By design the
  chain has one verifying key and a non-downgradable genesis pin, so there is no
  in-place key rotation (see `deploy/rollback-and-key-lifecycle.md`). A
  compromised key means starting a new chain, not rotating in place.
- **RR-3 · Co-located process isolation** depends on the OS firewall (Boundary
  4); loopback TCP has no peer-UID primitive.
- **RR-4 · A root-capable adversary on the host** is out of scope: they can read
  keys, disable the firewall, and replace binaries. RVND defends the agent
  boundary and the audit record, not the host's own compromise.

## Out of scope

The customers' governed agents; the operator's own OS hardening; physical
access; supply-chain compromise of Python/OS packages (partially covered by the
SBOM + Trivy + dep-licence gate, but not a threat this model closes).

---
