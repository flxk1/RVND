# Vendored Loomground schemas

`observation.schema.json`, `patch.schema.json`, and `token.schema.json` are vendored verbatim from
the **loomground-governance 0.8.2** package (`standard/schema/`), which is Apache-2.0. They are the
official interchange schemas for a policy graph (patch), its projection (observation), and the token.

They are copied here so the plugin's proposal envelope and its linter can validate against the real
language shapes offline, without assuming the language package is installed. The `$id` URNs
(`urn:loomground:0.7:*`) are preserved so a host that already has the language schemas resolves the
same identities. If the language package is present, prefer its copies as the source of truth; these
are a pinned mirror. Re-sync them when the language version changes.
