# Session presence — trust boundary

How much a live-presence claim can be trusted, and where the boundary is.

## The two identities

- **Witnessed** — the chain actor: the per-session id the PreToolUse hook records
  on the signed chain when a session takes a governed action. Chain-proven.
- **Live presence** — a connected process asserting, at MCP connect, that it *is* a
  given session (via `session_id`). This is a JOIN, matched by string equality
  against the witnessed actor. It answers "which physical process is this actor
  right now" — the signal an overseer uses during incident triage.

The board keeps these tiered and never fuses them (`identity_tier: witnessed` on the
chain actor; the join is labelled and flagged, see below).

## The threat

`session_id` comes from `CLAUDE_CODE_SESSION_ID`, an environment variable readable
by any same-user process (`ps eww`). So a same-host, same-user process can set it to
an already-admitted actor's id and surface as that actor's live presence. It does
**not** forge chain authority (the lane verdict still keys off the signed-chain
actor), but it forges *presence* — the "which process is this actor" signal.

## What is enforced (RVND-side, no host support required)

Defence in depth, each partial:

1. **Collision flag** — when more than one live connection claims one `session_id`,
   the projection sets `presence_ambiguous` and the board shows a caveat; the join
   never silently trusts one arbitrary process.
2. **Honest label** — the live join is shown as a string-equality match on an
   unauthenticated host env var, explicitly "not chain-proven".
3. **Process start-time binding** — a record is trusted only when its pid is alive
   AND the live process's start-time matches the value pinned at register
   (`pid_start`, `ps -o lstart=`). This rejects a recycled pid and a record
   hand-injected into the registry dir naming a live pid it never owned. Fails open
   to liveness-only for a pre-upgrade record or an unreadable start-time (never a
   false drop of a genuine agent).

## The residual gap — requires host support

None of the above closes the core vector: a process that legitimately calls
`register_connection` while *claiming* a victim's `session_id` (the env var is
readable, so the claim is cheap). Closing it needs the id to stop being a plain
readable value:

- **Host-minted credential (the real fix).** The host (Claude Code) mints a
  short-lived, per-session secret/signed token that only the genuine session holds,
  presented at connect and verified by RVND. RVND already has an Ed25519 host key
  (`signing.py`) it could verify against, but the *minting* side is a host
  capability RVND cannot supply for it — this is a capability request against the
  host, not RVND-side work.
- **RVND-signed records (a lesser, RVND-only step).** Sign each connection record
  with the host key so the registry cannot be edited or a record injected without
  detection. This closes direct-registry tampering but not the env-var claim (a
  claiming registration is signed like any other), and it uses the operator signing
  key — a key-material decision reserved to the operator.

Until a host-minted credential exists, a live-presence match is an operational
convenience with the trust boundary stated above, not an authentication.
