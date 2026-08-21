# Loomground vocabulary — pinned

The skills speak governance in **Loomground**, and only in constructs the language actually
defines. This file pins those constructs to a real version so the manifests and the proposal
envelope cannot drift into invented primitives. Anything a user means that is not on this list
stays **residual** (see `grounding.md`) — it is never approximated into a construct that looks
close.

- **Language package:** `loomground-governance` **0.8.2**
- **Concrete grammar:** the `.lg` netlist, syntax specification **v0.7**
- **Vocabulary set:** the `standard/vocabulary/*.json` shipped with 0.8.2
- **Conformance:** an implementation is conformant when it produces the language's **canonical
  observation** for a program (the anchor for the equivalence test).

## Node classes

| node | role |
|------|------|
| `actor` | principal that may be granted authority and propose an action; outlet is an authority cord to a gate |
| `human` | a person named by a role; a reserved token is referred to it, or it roots a principal chain as delegator. Not graph-connected; never a cord endpoint |
| `gate` | governed checkpoint where an actor acts and a verdict is produced |
| `master` | the single sink; exactly one; the policy enforcement point attaches here |

## Cords (the only legal edges)

| cord | from → to | inlet | legal when |
|------|-----------|-------|-----------|
| `authority` | actor → gate | configuring | the gate grants that actor |
| `pipe` | gate → gate | activating | the pipe relation is acyclic |
| `egress` | gate → master | activating | always; lands only on the master |

A human is never a cord endpoint; an actor never connects directly to the master.

## Declarations

| declaration | form | effect |
|-------------|------|--------|
| `reservation` | `reserve <kind> by <target> [when <guard>] [duration <d>:<on-elapse>]` | verdict **reserved**; the action is referred to a human role |
| `quorum` | `target = role \| role and role \| <m> of { roles }` | requires **distinct** parties (separation of duty) |
| `prohibition` | `prohibit <kind> [when <guard>]` | verdict **prohibited**; never released, never discharged; overrides any grant |
| `temporal` | `duration <d>:<halt\|proceed>` on a reserved action | a deadline/window/expiry and its on-elapse resolution |
| `egress-obligation` | `obligation <id> on <gate>` | the master acts only if the obligation is attached |
| `redress` | `redress <kind> by <role> [overturn] [within <duration>]` | a released decision is contestable; a fresh re-examination is owed and recorded |
| `party` | `party <id>` on an actor or gate | sets the party currently responsible |
| `delegation` | `on-behalf-of <actor\|human>` on an authority cord | delegate acts for delegator; acyclic principal chain; no-amplification invariant |
| `autonomy-grade` | `grade <level>` on an actor (granted) or source gate (required) | gates the auto/human disposition; configuration, not a token field, never guardable |

## Verdicts

Alphabet, least → most restrictive: **`auto` → `human` → `refused` → `reserved` → `prohibited`**.

- The join over a path is the **maximum** under that order (most restrictive input wins).
- Only **`auto`** releases at the master. `human`, `refused`, `reserved`, `prohibited` all withhold.
- `refused` is the fail-safe: a denial for want of an authorizing grant. Absence of a grant is a
  withhold, never a pass.
- `inactive` is the status of a non-activated gate; it is not in the alphabet and contributes no
  term to a join.

## Grades

`L0 < L1 < L2 < L3 < L4`. Granted on an actor; required on a source gate. The language owns only
the comparison rule; the ladder's meanings are policy. Grade is a configuration attribute — never
a token property, never guardable.

## Guard domain — the wall

A `when <guard>` clause is `<field> <op> <value>` and ranges **only** over:

| field | operators |
|-------|-----------|
| `kind` | `=` |
| `party` | `=` |
| `risk` | `>=`, `=` |
| `tags` | `contains` |

Forbidden in a guard: `id`, `provenance` (except the quorum distinctness predicate), `grade`, and
**any computed value**. A guard states a condition over declared token properties; it never
computes. This is the wall between an expressive notation and a programming language — and it is
the concrete reason a meaning like "responsibly" or "culturally appropriate" cannot be encoded as
a guard and must stay residual.

## Named egress obligations

`ai-interaction-disclosure`, `synthetic-content-marking`, `emotion-or-biometric-disclosure`,
`deepfake-disclosure`, `data-minimisation`, or a policy-defined `id`.

## Token risk

`low < medium < high < critical` — an ordered severity on the token, a deployer choice. **Not** the
AI Act risk-tier classification.

## Legal grounding (from the language's own annex)

Every construct carries its established source in `grounding.json`. The skills cite these rather
than inventing authority:

- `reservation` → AIA Art. 14, Art. 26; GDPR Art. 22(3)
- `prohibition` → AIA Art. 5
- `egress-obligation` → AIA Art. 50; GDPR Art. 5(1)(c)
- `redress` → GDPR Art. 22(3), Art. 77-79; AIA Art. 14(4); Charter Art. 47
- `quorum` → separation of duties (SoD), Clark-Wilson
- `delegation` / attenuation → OCAP, RFC 8693 (no-amplification)
- `party` → GDPR Art. 4/28; AIA Art. 3
- `autonomy-grade` → Sheridan 1978, PSW00, ISO 22989 (axis); ladder is policy
- `authority cord` / no ambient authority → OCAP, least privilege, NIST 800-53 AC-3/AC-6
- `refused` / `prohibited` verdicts → XACML explicit-deny / deny-biased indeterminate
