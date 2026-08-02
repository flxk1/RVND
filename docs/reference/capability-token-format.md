<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Capability token format

The lock middleware accepts a JSON object with these claims:

`iss`, `sub`, `aud`, `iat`, `exp`, `scope`, `controller`, and `task_id`.
An issuer may also attach `signature`, a hex-encoded Ed25519 signature over
the canonical JSON encoding of those eight claims (UTF-8, keys sorted, compact
separators, and Unicode preserved).

By default RVND checks claim semantics but does not require a signature. This
compatibility mode means capability tokens are not authorization evidence by
themselves.

Set `LOCK_BETA_STRICT_TOKEN_SIG=1` to require signatures. Strict mode also
requires `LOCK_CAPABILITY_TRUST_STORE` to name a JSON file controlled by the
operator. The file maps each trusted `iss` value to an Ed25519 public key in
PEM form:

```json
{
  "https://issuer.example": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n"
}
```

Strict mode refuses unsigned tokens, invalid signatures, unknown issuers,
missing trust stores, malformed stores, and non-Ed25519 keys. A public key
carried by a token is never trusted.

Python issuers can construct a `CapabilityToken` and call its `sign(private_key)`
method. Sign only after all claims are final; changing any claim invalidates
the signature.
