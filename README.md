# Loomground Rvnd

Rvnd is a local-first governance system for AI agents. It is currently in beta.

The name combines *reeve*, an old word for a steward, with *nD* for
*n-dimensional*. Rvnd applies written policy across several dimensions of a
principal–agent relationship: authority, autonomy, oversight, risk and data
handling. It governs delegation and records the resulting decisions.

It runs on your own hardware. Your data, your policies, and your models stay
local by default; the cloud is opt‑in, and a folder can be sealed off from it
entirely.

## What it does

Rvnd governs actions that are routed through its MCP server or governed
`operate()` path. It does not automatically intercept an agent's other files,
tools or network calls; host integration must route those calls through RVND,
and host-wide network containment requires the optional OS-level egress lock:

- **Privacy Lock** — inspects text leaving the local boundary for secrets and
  personal data (a fast pattern pass plus an optional local‑model semantic pass)
  and refuses, redacts, or asks a person, according to your policy. Fail‑closed:
  when in doubt, it stops rather than leaks. Out of the box the pattern pass is
  the protection; the semantic pass runs only once you configure a local model
  (see [Local models](#local-models)).
- **Oversight** — each action is checked against the lowest autonomy limit set
  by the applicable rules and against a time-based stop. A task reserved for a
  person cannot run automatically.
- **Tamper‑evident audit** — decisions and runs routed through RVND's
  journalled paths are appended to a per‑folder,
  Ed25519‑signed hash chain, and erasure is performed with signed tombstones
  rather than silent deletes. Against an adversary who can also write the key
  directory, the tamper‑evidence holds only with the opt‑in key protections
  (encrypted keys at rest, genesis key pinning) and the log shipped off‑host.
  Erasure is data‑level: it purges this folder's record and blocks
  re‑ingestion; it cannot recall copies that already left the boundary.
- **Air‑gap mode** — mark a folder local‑only and its work is kept from a cloud
  model: the governance paths exclude cloud endpoints and build no network
  request. That in‑process check is the default; an OS‑level egress lock is the
  tier that binds every process on the host. See
  [docs/concepts/air-gap-enforcement.md](docs/concepts/air-gap-enforcement.md).
- **Local‑first models** — local models (via `llama.cpp` / ONNX) serve
  governance and completion; a cloud model is optional and itself governed.
- **Policy ingest** — paste a written AI policy and Rvnd drafts the governance
  graph from it for you to review, then applies it on your confirmation.

Rvnd enforces the rules its operator configures. It does not determine whether
those rules satisfy a law, regulation or organisational policy.

## The two pieces

- **A governance MCP server** — exposes the governance tools (Privacy Lock,
  oversight, the audit chain, local models, policy ingest, memory) over the Model
  Context Protocol, so any MCP‑capable agent or client can route through them.
- **The Governance Patchbay** — a browser app that shows the governance as a
  wiring diagram: agents, tasks, the people who sign off, and the boundary, with
  each egress connection coloured by the server's verdict. Users edit the
  relationships on the canvas; enforcement remains in the MCP server.

## Built on Loomground

Rvnd is built on **Loomground**, a published governance language and format — actors,
human overseers, gates, and the boundary — with a reference engine and a
conformance suite. It is a specification that Rvnd implements, not an industry
standard. See [github.com/flxk1/loomground](https://github.com/flxk1/loomground).

Its reasoning substrate is the independent
[`loomground-solver`](https://github.com/flxk1/loomground-solver) package. RVND depends on
Solver and supplies policy, corpus, custody and audit adapters; Solver has no
RVND dependency. The `workspaces.dimensions`, `reasoning`, `predicate`,
`temporal`, contract and topology modules remain as compatibility import
surfaces and contain no duplicate Solver implementation.

Its knowledge plane is the independent
[`loomground-versum`](https://github.com/flxk1/loomground-versum) package. Versum owns
span-grounded claims, concepts, 5D+nD graph storage, fingerprints and retrieval.
RVND adds authorization, custody, signed audit and governance-specific read-time
overlays. Indexed workspaces read through Versum; unindexed workspaces expose an
explicitly labelled `legacy-pair-overlay` until their data is migrated, rather than
silently pretending that overlay is Versum. Loomground vocabulary, schemas and
conformance vectors are loaded from the published
`loomground-governance` package rather than copied into RVND.

Policy ingestion uses the independent `loomground-ingest` framework and the
published `loomground-deontic` and `loomground-governance` languages. RVND owns
the host-side admission, confirmation, policy projection and audit behavior;
the ingest package does not enforce those host decisions.

These pieces form one fixed, one-way pipeline —
**Language → Ingest → Versum → Solver → Patchbay → RVND** — whose single
persistent knowledge plane is Versum: a policy written in a registered grammar
is ingested into span-grounded Versum knowledge, reasoned over by the Solver,
rendered by the Patchbay, and enforced by RVND. Reasoning reads Versum only and
fails closed on an unindexed workspace. See
[docs/concepts/knowledge-pipeline.md](docs/concepts/knowledge-pipeline.md).

## Policy model

Policy is the source of authority. Rvnd evaluates agents and their proposed
actions against individual policy items, and links each approval, hold or denial
to the rule that produced it. The projection model is described in
[docs/concepts/architecture-model.md](docs/concepts/architecture-model.md).

## Governance layer

The governance interface can ingest a policy, build views of its rules and
answer questions about the resulting configuration. Its main operations are:

- `governance_chat` — ingest policy text, complete a use-case card or answer a
  governance question.
- `governance_map` (`governance_map/v1`) — present rules by role, step and risk.
- `governance_kg` (`governance_kg/v1`) — present the same rules as a graph with
  reasoning paths.
- `loop_graph` (`rvnd/graph-of-loops/v1`) — present the same governance as a
  graph of control loops (execution, oversight, drift, recovery, improvement).
  One of several read-only projections; see [Projections](#projections).
- `security_dashboard` (`security/v1`) — report security decisions and known
  limitations.
- `officer` — preview changes that tighten oversight.
- `model_capability` — report whether the configured local model is available
  and how the system degrades without it.

Policy imports require human confirmation before they are applied. Set
`RVND_GOVERNANCE_LAYER=off` to disable this interface. See
[docs/concepts/governance-layer.md](docs/concepts/governance-layer.md) for usage and limitations.

### Projections

RVND compiles policy into one governance graph and projects it read-only in
several ways — by role, step and risk (`governance_map`), as a reasoning graph
(`governance_kg`), and as a graph of control loops (`loop_graph`); a Patchbay
view can render any of them. The graph of loops is one such view, not the model.

The projections **declare; they do not decide**, and enforcement does not depend
on rendering any of them: `workspaces.loop_graph.assess_with_drift` feeds
structural drift into the Breaker *before* the action gate runs, while behavioral
drift routes benign work to interactive review — the checks run whether or not a
graph is ever drawn.

For the `loop_graph` call, its node/edge shape, and `control_bindings`, see
[docs/concepts/graph-of-loops.md](docs/concepts/graph-of-loops.md).

Before a registered agent operates, approve a versioned governance lane. The
lane is its complete governed operating envelope; `max_grade` is only its
autonomy ceiling:

```json
{
  "op": "governance_lane_register",
  "params": {
    "folder_context": "/absolute/path/to/workspace",
    "lane_id": "lane-research-bot",
    "agent": "research-bot",
    "max_grade": "L3",
    "action_classes": ["summarise", "classify"],
    "footprints": ["personal-data"],
    "use_cases": ["research"],
    "connectors": ["local-model"],
    "policy_fingerprint": "sha256:compiled-policy",
    "approved_by": "alice",
    "rationale": "Bounded research processing"
  }
}
```

Every live action is checked against all constrained dimensions. An agent may
request its assigned grade or a lower one, but never a higher one. Missing scope
values, an unapproved action or footprint, a connector change, a policy change,
or an attempted grade increase produces a denial. Widening requires a new lane
version with a named approver and rationale. `governance_lane_list` returns the
latest lane per agent, and `loop_graph` includes the same inventory for fleet
inspection.

## Quick start

**One line, from a bare machine** (macOS or Linux) — checks prerequisites,
clones into `~/rvnd`, and installs:

```bash
curl -fsSL https://raw.githubusercontent.com/flxk1/RVND/main/bootstrap.sh | sh
```

Prefer to read before running anything piped into a shell (a good habit):

```bash
curl -fsSL https://raw.githubusercontent.com/flxk1/RVND/main/bootstrap.sh -o bootstrap.sh
less bootstrap.sh && sh bootstrap.sh
```

Set a different location with `RVND_DIR=/path sh bootstrap.sh` (or pass it as an
argument). The bootstrap is non-interactive and idempotent — re-running updates
in place — and it refuses to touch a non-empty directory that isn't already an
RVND clone. When it finishes, run `workspaces init` for the guided setup.

**Or step by step.** The following commands clone Rvnd, create an isolated
virtual environment, install it and start the local Patchbay web application on
macOS or Linux. Run them from the directory where you want the `RVND` folder to
be created. They need Python 3.10 or newer and `git` available on the PATH: five
of Rvnd's runtime dependencies — the Loomground packages — are fetched directly
from Git rather than from PyPI, so the install step clones them. To check your
machine has everything first, run `sh scripts/preflight.sh` after cloning.

```bash
git clone https://github.com/flxk1/RVND.git
cd RVND
./server/install.sh
.venv/bin/python app/serve.py
```

The server opens the application in your browser at
`http://127.0.0.1:8799/` and remains loopback-only. If you already cloned the
repository, start with `cd` into that existing `RVND` directory and omit the
`git clone` command.

The application is plain HTML and requires no frontend build or desktop shell.
It binds to the local machine only; it is not a production or multi-user
deployment.

On macOS, **double-click `app/Open Rvnd.command`** in Finder — no terminal
needed. On first run it creates the virtual environment and installs Rvnd
itself; on every run it picks a free port (so a leftover instance never blocks
startup) and opens your browser. Close the window it opens to stop Rvnd. The MCP
server and the authenticated HTTP gateway are separate processes; their setup is
documented under `docs/`.

## Connect it to your AI agent

To let an agent (Claude Code, Codex) drive RVND — registering the governance MCP
server and installing the governance skills — run:

```bash
./scripts/connect-agent-hub.sh
```

It detects your hub, wires in the MCP server (via RVND's own `.venv`), installs
the skills where scriptable, and prints the manual steps where a hub has no
install CLI. It's safe to re-run (`--dry-run` previews; `--yes` skips the
prompt). The plugin and its skills live under `plugin/rvnd-governance/`.

## Local models

The Privacy Lock's semantic check can run a local model you supply — Rvnd bundles no
weights and endorses no vendor. It defaults to a model‑free deterministic stub; a
real backend is a GGUF (`llama.cpp`) or an ONNX Runtime GenAI directory, selected
via one env var. As a licence‑clean worked example, this fetches Microsoft's
prebuilt Phi‑3.5‑mini ONNX build (MIT) — substitute any model you prefer:

```bash
.venv/bin/python -m pip install huggingface_hub onnxruntime-genai
scripts/fetch_onnx_model.sh
export AGENT_TOOL_LOCK_LLM_BACKEND=onnx_genai:"$PWD"/models/phi-3.5-mini-instruct-onnx
```

Backends, a lighter GGUF path, hosted‑model notes, and verification:
[docs/concepts/local-models.md](docs/concepts/local-models.md).

## Layout

```
rvnd/
  server/   # the MCP server + governance runtime; consumes Solver, Versum,
            # Governance, Deontic and Ingest as packages
  app/      # the Patchbay web application and its local development server
```

## Status

Rvnd is beta software under active development. Interfaces and file formats may
change. The governance runtime and Loomground conformance suite pass the
project's current tests. The local application is loopback-only. A multi-user
deployment requires the documented verified-identity proxy; governance-session
admission then binds the proxy principal to an active agent, its approved lane
and the lane's current policy fingerprint. See [`CHANGELOG.md`](CHANGELOG.md)
and the concept docs under [`docs/`](docs/).

## Installation notes

The quick-start installer installs the package in editable mode. A normal
non-editable installation from the repository root is also possible:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
```

The commands above describe a POSIX environment. Windows uses different virtual
environment paths and does not support the Finder launcher.

### Troubleshooting local development

- To check an installation, run `.venv/bin/workspaces-doctor`. It reports which
  `workspaces` console scripts are present and whether each one's interpreter
  can import the package. Exit status is 0 when every binding is sound.
- On macOS, editable installations in an iCloud-synchronised checkout may lose
  their link to the source tree. Move the checkout to a non-synchronised local
  directory if that occurs.
- An older installed package named `workspaces` can shadow this checkout. Check
  which interpreter and package path are active before using `PYTHONPATH` as a
  temporary development override.

## Authorship

Copyright in the project is held by its identified human author or authors.
Generative AI tools assisted parts of development. See [`NOTICE.md`](NOTICE.md)
for the authorship and provenance statement.

## License

Rvnd is available under the
[GNU Affero General Public License v3.0 only](LICENSES/AGPL-3.0-only.txt), or
under separate commercial terms from the copyright holder. See
[LICENSING.md](LICENSING.md) and
[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md). Third-party
components keep their own licences; see [`LICENSES/`](LICENSES/) and
[`REUSE.toml`](REUSE.toml).

Commercial offerings — signed builds, white-label branding, and licensed
design and policy content — are separate optional products; see
[`LICENSING.md`](LICENSING.md).
