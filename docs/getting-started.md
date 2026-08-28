# Getting started

A copy-pasteable path from a bare machine to your first governed, audited
action — for someone who only wants to `pip install` RVND and try it, no
clone required.

## Mental model, in four sentences

RVND governs *actions* — an agent asking a question, one workspace reading
from another — by evaluating them against your policy and returning a
**verdict**: `permit` (go ahead), `hold` (a person signs off first), or
`deny` (refused). A **workspace** is a folder you register; RVND scopes
policy to it and gives it its own signed, tamper-evident audit chain, so
every verdict on that folder has a record. RVND's own engine is assembled
from several independent, git-pinned packages it calls **planes** — a
knowledge plane (Versum) that stores what's been ingested, a reasoning plane
(Solver) that evaluates it, and others — each a separate project RVND
consumes rather than owns (see [Built on Loomground](../README.md#built-on-loomground)
in the README).

## 1. Install

Pip-only, no clone — pins to a tag for a reproducible install (drop `@v…`
for the default branch, at the risk of a moving target):

<!-- doctest: skip -->
```bash
pip install "rvnd @ git+https://github.com/flxk1/RVND@v0.6.9.11"
```

Or clone and run the guided installer, which also runs a self-check:

<!-- doctest: skip -->
```bash
git clone https://github.com/flxk1/RVND.git
cd RVND
./server/install.sh
```

Either way you get the `workspaces` command on your `PATH` (inside the
virtualenv, if you used `install.sh`). Confirm it:

```bash
workspaces --version
```

## 2. See what's there

```bash
workspaces guide
```

Prints every command, grouped by what you're trying to do. Skim it once;
the rest of this page walks one path through it.

## 3. Register a folder as a workspace

The nested, canonical form:

```bash
workspaces workspace add ~/Documents/my-project
```

A shorter top-level alias does the same thing:

```bash
workspaces add ~/Documents/my-project
```

Either registers the folder in `<log root>/known-workspaces.json` — the
allowlist RVND checks before it will scope an action to an explicit folder.
Skip this step and point a command at an unregistered folder, and you'll see
an error naming this exact command as the fix. It's idempotent: run it again
and it just confirms the folder is already registered.

## 4. Take your first governed action

Each block below re-runs the idempotent register from step 3 first, so you can
copy any one on its own:

```bash
workspaces add ~/Documents/my-project                                       # idempotent; from step 3
workspaces ask --folder ~/Documents/my-project "what is this workspace for?"
```

This is a real governed action: it resolves the folder, checks policy,
records the turn on the folder's signed audit chain, and returns a verdict —
whether or not it can actually answer. **With no model configured, the
first response reads `(no answer generated: no model tier configured)`.
That is expected, not a failure** — the governance and the audit append
still happened; only the completion step had nothing to call. A model is
optional. If you want one, `ask`'s own error tells you how:

<!-- doctest: skip -->
```bash
workspaces models config --local-url <url> --local-model <id>   # your own local endpoint (BYOK)
# or set WORKSPACE_LOCAL_LLM_URL / WORKSPACE_LOCAL_LLM_MODEL directly
# or set WORKSPACE_CLOUD_LLM_URL / WORKSPACE_CLOUD_LLM_MODEL for a cloud rung
```

See [docs/concepts/local-models.md](concepts/local-models.md) for a worked,
licence-clean local-model example (that page covers the Privacy Lock's
semantic pass specifically; the env vars above are the separate `ask`/
governed-model path).

A second kind of governed action, reading laterally between two workspaces —
the block registers a second folder, then reads across:

```bash
workspaces add ~/Documents/my-project                                       # idempotent; from step 3
workspaces add ~/Documents/other-project                                    # the second workspace
workspaces cross-workspace --folder ~/Documents/my-project --source ~/Documents/other-project
```

Prints a verdict per source (`permit`/`hold`/`deny`, shown as
`✓`/`•`/`✗`) rather than silently allowing or blocking the read.

## 5. Read the record back

```bash
workspaces add ~/Documents/my-project                                       # idempotent; from step 3
workspaces audit-tail --folder ~/Documents/my-project
workspaces status --folder ~/Documents/my-project
```

`audit-tail` lists recent signed events on that folder's chain;
`status` adds the folder's policy, pinned skills, and a chain-integrity
check (`ok=True`/`False`) in one view. Run these after step 4 and both the
`ask` and the `cross-workspace` turn show up in the tail.

## Default enforcement posture — read this before you rely on it

Installing RVND does not, by itself, make anything block. Two separate
things have to be true before a verdict has teeth:

1. **The PreToolUse hook has to be installed and given teeth.** `bootstrap.sh`
   wires it in *monitor* mode by default — it logs what it would have done and
   never blocks. To make it binding, edit its `--command` in
   `~/.claude/settings.json` and drop the `RVND_HOOK_MODE=monitor` prefix (see
   the README's [Quick start](../README.md#quick-start)).
2. **Even enforcing, the autonomy grade starts permissive.** The default
   grade is `L2` (`RVND_AUTONOMY_GRADE`), and the hook's built-in classifier
   is deliberately narrow — it flags a small, high-signal set of destructive
   or privilege-escalating shell patterns (e.g. `git push --force`,
   `rm -rf`, `sudo`) and lets everything else pass with an empty footprint.
   Content-based risk that doesn't match one of those patterns — reading a
   sensitive file and sending it somewhere, for instance — is **not** caught
   by the default classifier; that needs policy written for it (see
   [Policy model](../README.md#policy-model)) or the semantic Privacy Lock
   pass (needs a configured model, step 4).

A non-destructive illustration of an actual block, once the hook is
enforcing: attempting `git push --force` at the default `L2` grade is
refused with `blocked by governance: grade L2 below required for
'irreversible' (needs grade ≥ 3 under balanced posture)` — nothing pushes,
and the refusal itself is what you'd see. Raising `RVND_AUTONOMY_GRADE`,
adding a standing approval for that action class, or running the command
yourself are the three ways past it (the refusal names all three).

## `workspaces init` and where it writes

`workspaces init` is the interactive first-run wizard (keys, Privacy Lock,
a local model, oversight, connecting your agent). Left to its defaults it
writes real state under `~/.workspace` (signing keys, the audit-chain
directory, the init marker) — nothing above this page's steps 1–5 requires
running it. To try it without touching your real home, point it at a
throwaway root the same way every other command respects:

<!-- doctest: skip -->
```bash
workspaces --log-root /tmp/rvnd-sandbox/log init --yes
# or: RVND_LOG_ROOT=/tmp/rvnd-sandbox/log workspaces init --yes
```

`--yes` accepts recommended defaults non-interactively; add `--dry-run` to
print the plan without writing anything at all.

## Where to go next

- [README.md](../README.md) — what RVND is, the two pieces (MCP server +
  Patchbay), and the full Loomground dependency chain.
- [docs/concepts/governance-layer.md](concepts/governance-layer.md) — the
  `governance_chat` / `governance_map` / `governance_kg` interface for
  ingesting and inspecting policy.
- [docs/concepts/air-gap-enforcement.md](concepts/air-gap-enforcement.md) —
  keeping a workspace's work off the network entirely.
- [plugin/rvnd/SKILLS.md](../plugin/rvnd/SKILLS.md) — the Claude Code skill
  catalog and which parts of it are installable by anyone versus
  author-internal.
