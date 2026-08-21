# The offline floor (`bin/`)

The nine skills drive the RVND server. The `bin/` tools are the other half: a
**zero-install floor** that works before the server exists and gets out of the
way honestly once it does. Everything here is standard-library Python — no
install, no service, no network — so the plugin is useful on a machine where the
AGPL-3.0 `rvnd` engine has not been (and may never be) installed.

When Claude Code activates the plugin, `bin/` is added to the Bash tool's
`PATH`, so the tools below are callable by name.

## Two invariants (why an advisory floor is safe to ship)

1. **Never silently upgrade.** The floor is advisory. It never emits an enforced
   or granted verdict, never signs, never grants. Every result carries
   `"authoritative": false` and a stderr `mode:` line. When the real engine is
   present the floor routes you to the governed cycle rather than re-deciding.
2. **Never silently downgrade.** When a check could not run, the tool says so.
   It does not report a pass it did not earn.

These are enforced by `tests/test_floor_tools.py`, which asserts them on foreign
and adversarial input.

## The tools

| Command | Offline (floor) | With real RVND installed |
|---|---|---|
| `rvnd-probe` | reports engine present/absent/compatible | same — this is the detector the others use |
| `rvnd-lint` | complete stdlib structural validation | + full JSON-Schema validation (`jsonschema`) |
| `rvnd-preview` | advisory *would-be* decision | mode line routes you to the governed cycle for the binding decision |
| `rvnd-verify` | hash-chain **linkage** check | points you at `workspaces audit-tail` for authoritative signed verification |

### `rvnd-probe`
`rvnd-probe [--json]` — prints whether `rvnd` is importable and within the
plugin's compatible range (kept in step with `package.json`'s
`runtime.requires`). Exit is always 0; presence is information, not failure.

### `rvnd-lint`
`rvnd-lint [FILE|-]` — a thin launcher over the canonical linter in
`skills/rvnd-build-surface/scripts/lint_surface.py` (one implementation, no
duplication). The structural floor always runs and is fail-closed; `jsonschema`,
if present, adds full schema validation. Validation is never silently downgraded.

### `rvnd-preview`
`rvnd-preview [FILE|-]` — an advisory preview of how a governed action would be
judged, applying only the rules that are unambiguous without the governance
grammar: grade-never-increases, action-allowlist, and scope-presence. Grades are
ranked **only** when integer ranks are supplied (the ISO/IEC 0–6 ladder RVND
consumes); the grade lattice lives in the governance plane and is **not**
reinvented here — unrankable grades yield `hold`, never a fabricated `allow`.
Advisory exit codes: `0` allow, `3` hold, `4` deny, `2` malformed (fail-closed).

Input:
```json
{
  "lane":    {"grade_rank": 2, "actions": ["read","summarise"],
              "scope": {"purpose": null, "dataset": null}},
  "request": {"grade_rank": 2, "action": "summarise",
              "scope": {"purpose": "research", "dataset": "corpus-A"}}
}
```
The lane's `scope` keys name the **required** scope fields (values are ignored).

### `rvnd-verify`
`rvnd-verify [FILE|-]` — an offline linkage check of an audit chain / receipt.
Full verification (recomputing content hashes under RFC 8785 and checking Ed25519
signatures) needs the libraries the engine ships. This floor verifies what can be
verified honestly offline:

- **linkage contiguity** — always. Each entry's prev-hash must equal the previous
  entry's hash; a broken link is tamper-evidence and fails the tool (exit `5`).
  Every entry must carry a hash, or the input is malformed (exit `2`).
- **signature** — only if `cryptography` is importable *and* an entry carries
  `public_key` + `signature` + a signed body; and even then the tool states that
  its body bytes may not match the signer's RFC 8785 canonical form, so a pass is
  indicative, not authoritative. Otherwise: reported as "not checked", never as
  passed.

Field names are read tolerantly: `hash|entry_hash`, `prev_hash|prev`,
`signature|sig`, `public_key|pubkey`, `body|payload`.

## Shared code

`scripts/rvnd_floor_lib.py` holds the engine probe, the compatible-version range,
the mode-line text, and I/O helpers. The `bin/` tools import it by path so they
work regardless of how `PATH` is arranged.
