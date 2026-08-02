# Grounding — what makes a skill proposal Loomground-grounded

A skill is not policy. It is an interaction procedure. The skill guides the conversation and the
tool sequence; **Loomground** supplies the formal governance meaning; **RVND** validates,
authorizes, and records; the **Solver** observes and produces the ordered trace. A skill is
grounded only when every governance meaning it proposes is traceable from human source to a
validated policy graph and a signed outcome — and everything not expressible stays visibly
unresolved.

The skill never writes `.lg` and assumes it is correct. It assembles intent, scope, and bindings,
asks RVND to generate or validate the constructs, and presents the server's result. The typed
object that carries this is the proposal envelope (`schemas/proposal.schema.json`), whose
`loomground.constructs` are restricted to the real vocabulary (`vocabulary.md`).

## The grounding chain

```
user language + selected context
  -> skill identifies intent
  -> RVND resolves stable objects
  -> skill requests a proposal
  -> proposal: Loomground constructs + Versum scope + residual + provenance + versions
  -> Solver validates and evaluates (canonical observation + ordered trace)
  -> skill presents interpretation
  -> human confirms when required
  -> RVND applies and records (signed)
```

## The six grounding links

A proposal is grounded when it provides all six. A proposal missing any of them is not grounded
and must not be presented as if it were.

1. **Source** — which user words, selection, or gesture produced each element. The surface lets a
   person move from a formal declaration back to the exact phrase. ("Maria approves" →
   `reserve external-publication by accountable-publisher`.)
2. **Object** — which stable RVND/Versum identities the names denote, not display names.
   ("editorial-agent" → actor `actor_18`; "Press Kit" → Versum collection `collection_31`.)
3. **Language** — which Loomground primitive expresses the meaning, from the real vocabulary only.
   If no current primitive expresses it, it is **residual**, not approximated.
4. **Runtime** — which RVND capability implements the operational consequence. A `reservation`
   becomes an RVND decision workflow; a `prohibition` an action gate with no release path; an
   `egress-obligation` a policy enforcement point. Loomground describes the governed graph; RVND
   supplies the runtime behaviour.
5. **Evidence** — which policy source, span, and claim back the declaration, plus the construct's
   legal grounding. The evidence chain (policy sentence → source fingerprint → span → claim →
   declaration) lives in Versum and is referenced by the proposal. The **legal grounding** is not
   invented — it is read from the language's own `grounding.json` annex (see `vocabulary.md`:
   reservation → AIA Art. 14/26 + GDPR Art. 22(3), prohibition → AIA Art. 5, and so on).
6. **Version** — language version, vocabulary version, Solver version, RVND command-contract
   version, policy revision, source fingerprints, boundary revision, runtime catalogue. Otherwise
   the same input could mean something different later without explanation.

## The residual ledger — the most important mechanism

Given: *"Let the editorial agent use the files responsibly and make sure the campaign feels
culturally appropriate."*

Expressed formally: `editorial-agent` (actor), authority to use the selected sources. **Not yet
expressed:** "responsibly" and "culturally appropriate" — neither is a defined `kind`, a guard
condition, an evidence requirement, a human role, or a decision criterion.

The skill shows both columns. It may ask: should this become a human `reservation`? Is there a
checklist or policy source? Who is competent to decide it? Does it prohibit release or merely
require evidence (an `egress-obligation`)? It **must not** invent a `prohibition` or an approval
rule to make the residual go away.

There is a hard, language-level reason a condition like "responsibly" cannot be smuggled into a
`when <guard>`: a guard ranges only over `{kind, risk, party, tags}` with `{=, >=, contains}` and
**never computes** (`vocabulary.md`). A meaning that would require judgement or computation is
outside the guard domain by construction, so it stays residual until a person defines it as a real
construct (a `kind` with a policy source, a role, a reservation).

## Equivalence — same meaning, same canonical observation

These inputs must resolve to the same proposal:

- Chat: "Maria must approve external publication."
- CLI: `rvnd propose policy --reserve external-publication --role accountable-publisher`
- Sheet: circle boundary → add approval layer → external-publication → accountable-publisher
- `.lg`: `reserve external-publication by accountable-publisher`

All four must produce the same **canonical observation** (the language's conformance anchor,
enforced as "canonical observation mismatch"), the same validation result, the same impact
preview, and the same confirmation requirement. This is also how the canonical-verb → live-op
mapping (`catalogue.md`) is checked: if two surfaces diverge, the mapping or the grounding is
wrong, not the language.

## The deterministic adapter — orchestrate, never invent

RVND today does not emit the whole proposal envelope from one call. So the plugin assembles the
envelope by orchestrating the real `workspace_*` ops (`catalogue.md`) with a **deterministic
adapter**, and it fills each field only from a real result:

- `intent` / `scope` from the host request and resolved identities;
- `loomground.patch` / `deltas` from `governance_netlist` plus the typed delta the host built;
- `validation` from `patch_validate` (the server's `ValidationResult`, including
  `canonical_observation`);
- `evidence` from `workspace_grounder`; the signed outcome from `patch_apply` +
  `workspace_audit(op="verify_chain")`.

Any field the server cannot supply is listed in `validation.unavailable_fields` and the proposal
stays **unapplied**. The adapter never asks a model to invent a construct, a validation result, a
binding, an evidence span, or a canonical observation to complete the envelope. A missing field is
reported as missing — it is not filled with a plausible guess. If the server later adds a single op
that emits the versioned, validated envelope, the adapter collapses to that one call; until then it
is an explicit, auditable composition, not a fabrication.

## Never present requested state as granted

Grounding does not loosen the core rule. A grounded proposal is still a **request** until RVND
applies it and returns a signed receipt. The `residual` ledger, the `validation.verdict_preview`,
and the `confirmation.required` flag are all rendered as pending, never as an outcome.
