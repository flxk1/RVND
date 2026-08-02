# Loomground Rvnd — app

The UI is a single static file, `src/index.html` (the Governance Patchbay), served
by `serve.py`. **No build step, no Rust, no desktop shell** — the agreed runtime is
html/index over a local HTTP shim.

## Run it

```bash
cd rvnd
PYTHONPATH="$PWD/server/src" python3 app/serve.py
# → http://127.0.0.1:8799   (loopback only)
```

`serve.py` serves `src/index.html` at `/` and exposes the MCP tools at `POST /tool`
on the same origin (it injects `window.__WORKSPACES_HTTP__='/tool'` into the page). The
page's bridge then routes every `tool(name, args)` call to that endpoint.

## The bridge

`src/index.html` picks its transport at load (see the `bridge` block):

- **HTTP shim** — `window.__WORKSPACES_HTTP__` (`/tool`), the path `serve.py` uses. This
  is the universal local path.
- **Cowork** — `window.cowork.callMcpTool`, when the page is hosted inside the
  Cowork plugin.

If neither is present the app runs against its built-in demo (read-only).

## Verify

The UI is gated by render tests that boot `serve.py` and drive the real ops in
jsdom — run them all with:

```bash
python3 scripts/verify_completeness.py    # UI render gates
python3 scripts/verify_completeness.py --server   # + full server suite
```
