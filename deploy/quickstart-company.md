<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Quickstart — a company with an IdP (compose bundle)

One command stands up Rvnd behind oauth2-proxy; your IdP (Google, Entra,
Okta, Keycloak) verifies people, your directory groups become competences.

## 1. Register an OAuth client with your IdP

Any OIDC provider works. Redirect URL: `https://<your-host>:4180/oauth2/callback`.
Note the client id and secret.

## 2. Configure

    cd deploy
    cp .env.example .env        # then fill:
    # OAUTH2_PROXY_PROVIDER=google        (or oidc / azure / keycloak-oidc)
    # OAUTH2_PROXY_CLIENT_ID=…
    # OAUTH2_PROXY_CLIENT_SECRET=…
    # OAUTH2_PROXY_COOKIE_SECRET=$(openssl rand -base64 32 | head -c 32)
    # OAUTH2_PROXY_EMAIL_DOMAINS=corp.example
    # RVND_PROXY_SHARED_SECRET=$(openssl rand -hex 32)

Map directory groups to competences (this file is the whole design
artifact — no group mapped, no registration):

    # docker volume: rvnd-data:/data/identity-map.yml
    groups:
      sg-dpo-team:
        competences: [data-protection]
      sg-engineering:
        competences: [engineering]
    channel: "email:{principal}"

## 3. Start

    docker compose up -d

Open `http://localhost:4180` (or your fronting hostname). After the IdP
login, the console shows who you are; the first visit of a mapped person
registers them as a party, recorded like any registration.

## 4. What is enforced, without configuration

- Rvnd publishes no port of its own. The authentication proxy sends requests
  through a private header-injecting hop, and Rvnd refuses a non-loopback
  deployment unless both the principal header and proxy shared secret are
  configured. Requests without the matching proof are rejected before an
  identity header is read.
- The proxy's principal overrides whatever a browser claims to be.
- Unmapped, unregistered people cannot read a workspace or perform governed
  operations until someone registers them deliberately.
- Every recorded decision carries `auth_rung: proxy-verified`.
- Chat post-backs (`POST /decision/respond`) pass the proxy without an IdP
  session — Teams and Slack servers cannot log in; on that one route the
  single-use action-link token inside the post-back is the credential.

Local models: `docker compose --profile ollama up -d` adds an Ollama
service on the compose network (`http://ollama:11434`).

## 5. Egress lock (required for preventive egress claims)

The host lock blocks direct outbound to the four cloud-LLM providers from
every process on the host except the proxy user — the proxy, which scans
and audits every prompt, becomes the only path to the cloud. From the
repo root (`cd ..` if step 2 left you in `deploy/`):

    deploy/firewall/apply-egress-lock.sh --dry-run --proxy-user rvnd-egress
    sudo deploy/firewall/apply-egress-lock.sh --apply --proxy-user rvnd-egress

It resolves the provider IPs, loads the nftables ruleset atomically, and
ends in one plain line: lock in force or not. Providers sit behind CDNs —
re-run on a schedule (cron) to keep the IPs fresh. Verify any time:

    sudo env PYTHONPATH=server/src python3 scripts/verify_egress_lock.py

Linux hosts use nftables; macOS and Windows use the installers documented in
`deploy/firewall/README.md`. Installation is an explicit privileged deployment
step. If it is omitted, RVND remains an audit and policy layer, but the deployment
must not claim that RVND prevents processes or external tools from bypassing it.
