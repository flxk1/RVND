# Receipt card

Renders the **display** step: the outcome after the server applied a change or ran a governed
action. It is the audit made human-readable, and it renders the result as exactly what it was.

## Source

Built from `apply` + `verify`: the server's applied outcome and its entry in the per-folder
Ed25519-signed hash chain. The receipt is verified against the chain before it is shown.

## Shows

- What actually happened: applied / held / denied / revoked / transferred — the server's result,
  not the request.
- The rule that governed it, the approver (for a loosening), and the signing key.
- For a **revoke**: that erasure is a signed tombstone which purges this folder's record and
  blocks re-ingestion, and that it cannot recall copies that already left the boundary.
- The chain position and verification status of the entry.

## Rendering rules

- Render the outcome as it stands in the chain. A hold is a hold; a denial is a denial; a request
  that was denied is never shown as a partial success.
- Attributed, not asserted: key, rule, and approver accompany the outcome. A receipt without its
  attribution is incomplete and should read as unverified.
- If chain verification fails, render the receipt as **unverified** and treat downstream reliance
  as fail-closed.
- No scores. The verification status is a discrete lamp: verified or not.
