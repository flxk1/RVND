# Local models for the Privacy Lock

Rvnd's Tier C semantic check runs a local model as a second pass after the regex
layer, to catch personal or confidential content a pattern match would miss.

Rvnd is model-neutral: it bundles no weights, endorses no vendor, and ships no
default beyond the model-free `mock` stub. You bring the model — any GGUF, any
ONNX Runtime GenAI directory, whatever you prefer to run. This page covers
selecting a backend and walks one licence-clean example end to end; the example
is illustrative, not a recommendation.

Note the privacy classifier runs **in-process, on your machine, by design** — the
text it inspects is exactly the text you don't want to leak, so it must not be
shipped to a third party to be checked. That is why the Tier C backends are local
(GGUF / ONNX), not a remote API. Rvnd's other, governed model uses can point at a
hosted OpenAI-compatible endpoint with your own API key or OAuth; that is a
separate path from this privacy gate.

## Backends

Tier C selects its backend from the `AGENT_TOOL_LOCK_LLM_BACKEND` env var (spec
string). Unset defaults to `mock` — a deterministic, model-free classifier used
for onboarding and tests; it never loads weights and is not a real semantic pass.

| Spec | Runtime | Bring |
|---|---|---|
| `mock` | none (default) | nothing — deterministic stub |
| `llama_cpp:<path.gguf>` | `llama-cpp-python`, in-process | one GGUF file |
| `onnx_genai:<model_dir>` | `onnxruntime-genai`, in-process | an ONNX GenAI model directory |

Both real backends load in-process — no daemon, no HTTP. They share one prompt
template and answer format, and both **fail closed**: if the dependency is
missing, the path is wrong, or inference errors, the classifier flags the text
rather than passing it unscanned — a broken validator refuses instead of
leaking.

## A worked example — ONNX Runtime GenAI (Phi-3.5-mini, MIT)

This example uses Microsoft's prebuilt **Phi-3.5-mini-instruct ONNX** build. It is
picked only because it is **MIT-licensed** (freely redistributable, commercial use
fine) and ships ready to load — so the one example this repo documents carries no
licence encumbrance. Substitute any model you like via the env overrides below.

```bash
pip install huggingface_hub onnxruntime-genai
scripts/fetch_onnx_model.sh          # downloads models/phi-3.5-mini-instruct-onnx/ (~2.7 GB, int4)
export AGENT_TOOL_LOCK_LLM_BACKEND=onnx_genai:"$PWD"/models/phi-3.5-mini-instruct-onnx
```

`fetch_onnx_model.sh` downloads the ready-built directory (`genai_config.json`
plus quantized weights and tokenizer — the exact layout the `onnx_genai` backend
loads) via the Hugging Face CLI. No model builder and no torch: the ONNX GenAI
files are prebuilt upstream. It is idempotent — it skips the download if
`genai_config.json` is already present. Override `MODEL_ID`, `SUBDIR`, or `OUT`
to fetch a different build.

> Model choice is yours; mind the licence of what *this repo* documents. The
> example above is MIT so the repo stays clean for any user, including commercial
> ones. Some capable models carry non-commercial terms — e.g. Qwen2.5-Coder-**3B**
> is under the Qwen Research License (unlike the 1.5B/7B/14B in that family, which
> are Apache-2.0). Run whatever suits you locally under its own terms; just don't
> wire a non-commercial model into what the repo ships as its example.

The `models/` directory is gitignored (weights are multi-GB and never shipped);
only `models/README.md` is tracked. Point the backend at any directory containing
a `genai_config.json` via the spec — the in-repo `models/` folder is just the
convention this script and the onboarding wizard use.

### Lighter alternative — GGUF via llama.cpp

If you'd rather not run the ONNX runtime, use a GGUF instead — a single
downloaded file loaded by llama.cpp:

```bash
pip install llama-cpp-python
export AGENT_TOOL_LOCK_LLM_BACKEND=llama_cpp:/path/to/model.gguf
```

The onboarding wizard (`workspace setup`) discovers a GGUF in `models/` or
`~/.cache/agent-tool-lock/models/` and wires this spec for you.

## Verify

```bash
AGENT_TOOL_LOCK_LLM_BACKEND=onnx_genai:"$PWD"/models/phi-3.5-mini-instruct-onnx \
PYTHONPATH="$PWD/server/src" python3 -c "
from workspaces.lock.tier_c import describe_tier_c, tier_c_check_semantic
print(describe_tier_c())
print(tier_c_check_semantic('Patient NK-44 was diagnosed with NSTEMI on June 3'))
"
```

A working backend prints its `model_dir` (not `UNAVAILABLE`) and flags the health
example as a high-severity finding. `describe_tier_c()` and the `model_capability`
op both report readiness, so a degraded backend is visible rather than silent.
