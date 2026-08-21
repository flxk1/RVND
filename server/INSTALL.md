# Installing the Rvnd server

Everything here runs **from the repository root**. Rvnd is a single
distribution built from one `pyproject.toml` at the root; `server/` holds the
import package (`server/src/rvnd`) but no build config of its own, so
`pip install` pointed at `server/` fails with "neither 'setup.py' nor
'pyproject.toml' found".

## For users — one command

```bash
cd rvnd
./server/install.sh
```

That creates an isolated environment (root `.venv/`), installs the runtime and
every dependency into it (`mcp`, `cryptography`, `anyascii`, the pinned
Loomground upstreams, plus the test tools), and runs the oversight demo to
prove it works. Then:

```bash
source .venv/bin/activate     # activate it in your shell
workspaces --help             # the CLI is on PATH
python server/examples/oversight_demo.py
pytest server/tests -q        # run the suite — no PYTHONPATH needed
```

The virtual environment keeps the runtime's dependencies off your system Python.
Re-run `./server/install.sh` any time to update.

## For developers — manual

```bash
cd rvnd
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
```

`-e` is editable: the install links to the source tree, so your edits are
live. Drop `-e` for a fixed (non-editable) install.

`make venv` does the same thing and is what CI runs; `make test-fast` runs the
bounded subset of the suite.

## Notes

- **No `PYTHONPATH` needed after install.** Running raw from the tree with
  `PYTHONPATH=server/src` is only for working in it before installing. Once
  `pip install` has run, `import workspaces` resolves from anywhere.
- **The first install needs network and `git`.** Five dependencies resolve from
  `git+https://github.com/flxk1/…` at pinned commits (see `pyproject.toml`).
- **Develop outside iCloud Drive.** `pip install -e .` is unreliable from a
  path under `~/Documents` (iCloud) — the editable link is dropped
  intermittently. Use a local, non-synced checkout (see the README's
  environment caveats).
- **Python version.** 3.10+ is required; the installer prefers 3.12 (the
  most-tested) and falls back to whatever `python3` it finds. Verified on
  3.14 as well (mcp 1.27 ships cp314 wheels).
- **The `workspaces` command not on PATH?** That only happens with a non-venv
  user install; pip prints the directory it used (e.g. `~/.local/bin`). The
  venv installer avoids this — inside an activated `.venv` the command is
  always found. `import workspaces` and `pytest` are unaffected either way.
- **Optional extras** (from the root): `pip install -e ".[extractors]"` adds
  PDF/DOCX parsing; `".[llm]"` adds the local-model backend. Neither is needed
  for the oversight engine, which is pure stdlib on the decision path. There is
  no need for an `[mcp]` extra — `mcp` is a required runtime dependency and is
  always installed.
