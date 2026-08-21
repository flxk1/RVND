# audit-the-ai — reference

## What it drives

The RVND console's read surface, in chat. The console (`app/src/index.html` + `app/src/panels/*.js`
+ `app/src/shell/*.js`) renders a governance board from ~26 facades; this skill reproduces that board
as text, in two versions on one skeleton, using only projection ops. It drives nothing else — no
grant, no apply, no sign. See `console-surface.md` for the grounded panel → op → field map.

## Why two versions

The same 18-to-22-row skeleton, rendered twice, is a **controlled comparison**: hold the rows constant
and remove exactly one variable — the engine. The empty rows in the *without* render sit directly above
the filled rows in the *with* render, so the reader's eye does the argument. Break the skeleton (a tidy
short list without, a rich one with) and it just looks like two different tools. **The gaps are the
product, and gaps only read as gaps against a fixed frame.**

The deeper difference is epistemic, and the output should name it:

- **Without RVND → declaration.** The AI describes itself. Unverifiable, no adversary assumed.
- **With RVND → adjudication + proof.** Written policy judged each routed action (verdict + the rule
  behind it), a signed chain witnessed it, reconciliation compared claimed against observed.

## The state model (badge every row)

- **host** — declarable with no engine: model + card (`ai`), tools available (`help`), declared tokens
  (`lens`), coarse sources (`data`/`grounder`).
- **blind** — the engine is absent from this path. The row cannot be seen or asked. Reason required.
- **idle** — the engine is *installed and present* but this session/folder is not routed through it.
  This is the **loudest** state: "the watchtower is built and switched off." Detect it by: `rvnd`
  importable / MCP facade reachable, but the folder has no governed session, policy, or `.versum`.
- **live** — governed folder; filled from the signed board with its receipt.

Never collapse blind and idle. Idle is a choice and reads as an indictment; blind is a limitation.

## The spine — reconciliation as the unaskable gap

`reconciliation.observed_not_authorised` is the gap between authorised and observed — the alarm.
Computing it needs an adjudicated **authorised-set** (policy + grant/lease ledger) and a signed
**observed-set** (the effect chain). Without the engine you have neither, so the gap is **uncomputable**,
not merely unreported. In the *without* render, the reconciliation row says, in words,
**"uncomputable — no authorised-set, no observed-set; cannot ask whether anything overstepped."**
Never `0`, never `—`.

The three outcomes, weighted:
- `matched` — permitted and happened (fine).
- `authorised_not_observed` — permitted, didn't happen (benign; unused authority).
- `observed_not_authorised` — happened, not permitted (**the worst**; `unauthorised_rate` is its rate).

## Rendering shape (chat)

1. **Header** — folder, session id, timestamp, engine-present? (+ which without-state), and the scope
   line: *RVND governs only what is routed through its MCP / `operate()` path — not the agent's every
   file, tool, or network call.*
2. **Without RVND** — fill the `host` rows; every engine row shows its badge + one-clause reason. Lead
   with the reconciliation row (uncomputable) so the reader sees the unaskable gap first.
3. **With RVND** — the full board. **Red lights + reconciliation first** (#8/#13): denials with the
   exact `action_gate` rule, breaker tripwires, escalations, `observed_not_authorised`. Then human
   oversight (#9: held actions, certificates, quorum m-of-n, the oversight dial, all-stop). Then proof
   (#14: `verify_chain{ok, total_events, broken_links, signature_failures}`, `audit_id`s). Every filled
   claim carries its receipt (chain `seq`/`hash`, versum `snapshot` digest, or `audit_id`).
4. **Honesty footer** — restate omit-don't-fake / strictest-wins / fail-closed / blank-is-blind, and
   RVND's own stated proof limits: tamper-evidence against a key-directory adversary holds **only** with
   encrypted keys at rest + genesis key pinning + the log shipped off-host; erasure is a signed tombstone
   that cannot recall copies already past the boundary.

If RVND is not in the loop, render only the *without* board and say why — do **not** fabricate a full
*with* column. On request, a **counterfactual with-RVND column** may be rendered for the same real events
(what `operate()` *would* have captured), clearly labelled as counterfactual.

## Discipline (the lessons that back this skill)

- **Read the code AND run the op** before asserting a shape. Running `governance_live` revealed a shape
  reading alone missed; running `check_case` gave the exact `ContractReport` fields.
- **Manifest-completeness beats a hand-picked dimension list.** The console declares 22 panels in
  `pack.json` and fail-closes on a missing one. A "top-N dimensions" shortcut silently drops real
  surfaces (`federation`, `lens`, `bringin`). Enumerate the manifest; assert a row per id; fail loud on
  a gap.
- **Don't shrink RVND to one board.** ~26 facades, 200+ modules. The board is a view; name the surfaces.
- **Pure-read, and prove it.** Use only projection ops. Where the console's read self-records
  (`workspace_lock op=audit_query` writes the chain it reads), substitute the pure-read equivalents
  (`threshold_get`, `setup_status`). This audit perturbs nothing — that is part of its honesty.

## Pairing

- `verify-a-receipt` verifies one row deeply (the signed chain, receipt-by-receipt) and appends an
  audit-of-audit event; this skill reports the *whole* board and appends nothing. Call `verify-a-receipt`
  when a proof row needs receipt-level verification.
- `revoke-or-erase` owns the response when the board turns up an `observed_not_authorised` or a broken
  chain. This skill surfaces; the incident skill acts.
- `govern-an-action` / `sign-off` produce the receipts this board reads.
