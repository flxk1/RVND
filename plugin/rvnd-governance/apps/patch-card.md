# Patch card

Renders the **preview** of what a validated change would do, before it is applied. It is the
officer preview made visible: the server's projection of the effect, not the host's guess.

## Source

Built from `validate` + `preview`: the server's verdict on the proposal, and — for anything that
tightens oversight — the `officer` preview of the tightening. For a loosening, the projection of
the widened envelope and the controls it would move.

## Shows

- The server's verdict on the proposal: allow / hold / deny, bound to the rule that produced it.
- The concrete controls that would move and in which direction, as the server projects them.
- What tightens (safe) and what loosens (needs the named approver + rationale).
- The confirmation still outstanding before apply is possible.

## Rendering rules

- The verdict is the server's, rendered as a discrete lamp. The host does not compute or soften
  it.
- A `hold` or `deny` is shown as a stop, with its rule — not paraphrased into "almost there".
- The preview is explicitly labelled *not yet applied*. Seeing the patch is not applying it.
- Every projected control change is attributed to the rule and the compiled policy behind it.
