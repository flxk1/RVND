<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# NOTICE

## Authorship

Copyright in this project is held by its identified human author(s) (flxk1).
Generative AI tools (including Claude, Anthropic) assisted parts of development;
they are not authors or copyright holders.

## License

RVND is distributed under **AGPL-3.0-only** (see [`LICENSES/`](LICENSES/)).

## Third-party components

RVND composes the following third-party open-source components, each under its own
license. Full texts live in [`LICENSES/`](LICENSES/); a machine-readable SBOM
(CycloneDX) is in [`sbom/`](sbom/).

| Component | Purpose | License |
|---|---|---|
| `cryptography` | Ed25519 signing / verification | Apache-2.0 OR BSD-3-Clause |
| `anyascii` | Unicode → ASCII transliteration | ISC |
| `mcp` (Model Context Protocol SDK) | the governance MCP server transport | MIT |
| `PyYAML` | policy / config parsing | MIT |
| `pdfplumber` (via `loomground-versum`) | PDF text extraction | MIT |
| `loomground-solver`, `-versum`, `-governance`, `-deontic`, `-ingest`, `-legal`, `-norm`, `-factual`, `-epistemic` | the Loomground engine packages | Apache-2.0 |
| bundled font(s) | UI | SIL OFL-1.1 |

Optional local-model backends (`llama.cpp` / ONNX Runtime) and any model weights you
supply carry their own licenses; RVND bundles none.
