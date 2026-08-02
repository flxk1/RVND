<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Bring your identity provider

Rvnd never runs its own identity system. A fronting proxy you already trust
verifies who is calling and passes one header; Rvnd resolves that principal
against the party registry and records it on every governed action. Three
fronts, one contract.

## The contract

Set on the Rvnd host:

    WORKSPACE_PRINCIPAL_HEADER=X-Auth-Request-Email     # the header to believe
    WORKSPACE_PRINCIPAL_GROUPS_HEADER=X-Auth-Request-Groups   # optional
    WORKSPACE_IDENTITY_MAP=/etc/rvnd/identity-map.yml          # optional

Rules the bridge enforces: without `WORKSPACE_PRINCIPAL_HEADER`, identity
headers are ignored entirely (local single-operator mode is unchanged). With
it, only that header is believed — a request without it is refused, a
principal that matches no registered party gets reads but no governed
operation, and the client can no longer choose its own actor: the proxy's
principal is injected. Recorded writes carry `auth_rung: proxy-verified`.

Expose the bridge to the proxy only — keep it bound to localhost or a
private interface; the proxy is the sole caller.

## Front A — Tailscale (no IdP at all)

`tailscale serve` fronts the bridge and injects the caller's tailnet login:

    tailscale serve --bg --set-path / http://127.0.0.1:8799
    WORKSPACE_PRINCIPAL_HEADER=Tailscale-User-Login

Register each teammate as a party whose `party_id` is their tailnet login.
Ten people, ten registrations, done.

## Front B — oauth2-proxy with Google (or any OIDC IdP)

    oauth2-proxy --provider=google --upstream=http://127.0.0.1:8799 \
      --email-domain=corp.example --set-xauthrequest=true
    WORKSPACE_PRINCIPAL_HEADER=X-Auth-Request-Email
    WORKSPACE_PRINCIPAL_GROUPS_HEADER=X-Auth-Request-Groups

The same two lines cover Entra, Okta and Keycloak — change only the
oauth2-proxy provider flags; nothing IdP-specific reaches Rvnd.

## Front C — an existing SSO gateway (Entra App Proxy, nginx+SSO)

Any gateway that authenticates the user and can add a header serves:
configure it to set the principal (usually the UPN or email) and point
`WORKSPACE_PRINCIPAL_HEADER` at it.

## Groups become competences (optional)

With a groups header and a declared map, a verified principal auto-registers
as a party on first contact — recorded like any registration:

    groups:
      sg-dpo-team:
        competences: [data-protection]
      sg-engineering:
        competences: [engineering]
    channel: "email:{principal}"

No mapped group, no registration: unmapped principals stay read-only until
someone registers them deliberately.

## Resolving competences live from the map (optional)

Auto-registration snapshots a principal's competences at first contact. A
deployment whose groups source can be asked at query time — a directory
lookup, the front's groups claim — may instead install the shipped
`GroupMapResolver` on the PartyResolver port:

    from workspaces.party_resolver import GroupMapResolver, set_resolver
    set_resolver(GroupMapResolver(groups_for=lookup_groups))

`resolve_competences` then answers from the declared map on every call, so a
map edit takes effect on the next resolution with no re-registration. No map,
unknown principal, or unmapped group resolves to nothing — fail closed.
Roster reads stay with the local registry and the adapter registers nothing.
Nothing changes unless you install it: the local resolver is the default.
