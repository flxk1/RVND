# Proposal card

Renders a **proposed** change or action before it is validated or applied. This is the surface
that must never lie: a proposal is a request, and the card states it as a request in every word it
shows.

## Source

Built from a `propose` envelope the host assembled: a draft `governance_lane_register` (new or
widened lane), a draft policy import, or an action envelope headed for the gate. Nothing here is
in effect.

## Shows

- The exact change requested, in the server's terms: which dimension moves, from what to what.
- Its **direction** — tightening or loosening — computed from the change, not the wording.
- For a loosening: the named approver and rationale it will require, shown as *required and not yet
  supplied* until confirmation.
- The principals involved, all resolved; an unresolved principal blocks the card.

## Rendering rules

- Status words are limited to **requested / proposed / pending**. The words granted, enabled,
  active, in effect are forbidden on this card.
- A request for a higher grade than the lane allows is rendered as **will be denied**, not as a
  pending grant.
- Loosening is visually distinct from tightening; the dangerous direction is never rendered as
  routine.
- No verdict appears here — validation is a separate step and a separate card.
