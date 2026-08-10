# Air-gap enforcement tiers

A folder whose policy sets `local_llm.mode = local-only` is air-gapped: no text
from that workspace is to leave the machine for a cloud LLM. That protection is
enforced at three tiers of increasing strength. Each tier catches what the one
below cannot; only the strongest — the OS-level egress lock — binds every
process on the host, so the protection is as strong as the highest tier you
actually enable.

## Tier 1 — code-path enforcement (weakest)

`is_air_gapped()` in `server/src/workspaces/policy.py` reads the folder's
`local_llm.mode` and fails closed: a policy file that is present but unreadable,
malformed, or carries an unrecognised mode counts as air-gapped, so a corrupt
policy can never silently re-open cloud egress. `workspace_cascade.py` consults
it before the model cascade runs and drops every cloud rung; if the local rung
then defers, the escalation is withheld rather than routed to the cloud.

This tier is advisory in a precise sense: only code paths that consult
`is_air_gapped()` are covered. A module that opened its own connection to a
provider would never ask.

## Tier 2 — CI-time import guard

`scripts/egress_import_guard.py` is a static AST lint that fails CI when any
module outside the sanctioned allowlist (the egress proxy itself) imports a
cloud-LLM SDK, instantiates one of its clients, dynamically imports one via a
string literal, or hardcodes a provider base URL. It keeps Tier 1's assumption
true — that no code path reaches a provider without consulting policy.

Documented blind spots: dynamic imports through computed strings and hostnames
built by string concatenation are beyond static analysis and are not caught.
The guard also protects the shipped codebase, not a machine — it says nothing
about other software running on the host.

## Tier 3 — OS-level firewall (strongest)

The templates in `deploy/firewall/` block direct outbound traffic to the four
cloud-LLM provider hosts from every local process except the egress proxy:

* `nftables.conf` (Linux) — applied in one command by
  `deploy/firewall/apply-egress-lock.sh`, which resolves the provider hosts,
  loads the ruleset atomically as table `inet rvnd_egress_lock`, and runs
  `scripts/verify_egress_lock.py`.
* `pf.conf` (macOS) and `windows-firewall.ps1` (Windows) — operator-applied
  through the dry-run-first commands in `deploy/firewall/README.md`
  from the template headers; the apply script is Linux-only.

This is the only tier that binds every process on the host, including software
that never imports this codebase.

Staleness caveat (from the `nftables.conf` header): the firewall matches on IP,
not hostname, and an empty address set enforces nothing. The provider hosts sit
behind CDNs, so the resolved IPs go stale. Re-run `apply-egress-lock.sh` on a
schedule — cron or a systemd timer; no timer unit is shipped — or front the
providers with your own resolver/proxy.

## What a default install has

Tier 1 only. The import guard runs in this repository's CI, not on your
machine, and no firewall is applied for you — the `deploy/firewall/` templates
take effect only when an operator applies them. If a workspace's air-gap must
hold against arbitrary local processes, apply Tier 3. `workspaces doctor`
reports when a registered folder is air-gapped but no OS-level lock is
detectable.
