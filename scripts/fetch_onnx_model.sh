#!/usr/bin/env bash
# Fetch a Tier C local model as an ONNX Runtime GenAI directory.
#
# Rvnd endorses no model. This example downloads Microsoft's prebuilt
# Phi-3.5-mini-instruct ONNX build (MIT-licensed) — a ready directory with
# genai_config.json + weights + tokenizer, the layout the onnx_genai backend
# loads. No model builder, no torch. Idempotent: skips when the target already
# holds a genai_config.json.
#
# Override via env for any other ONNX GenAI model: MODEL_ID, SUBDIR (the build
# variant subfolder), OUT.
#
# See docs/concepts/local-models.md for wiring the backend once this completes.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL_ID="${MODEL_ID:-microsoft/Phi-3.5-mini-instruct-onnx}"
SUBDIR="${SUBDIR:-cpu_and_mobile/cpu-int4-awq-block-128-acc-level-4}"
OUT="${OUT:-$REPO_ROOT/models/phi-3.5-mini-instruct-onnx}"

if [ -f "$OUT/genai_config.json" ]; then
  echo "model already present at $OUT (genai_config.json found) — nothing to do"
  exit 0
fi

# The Hugging Face downloader: `hf` is the current name, `huggingface-cli` the
# legacy alias. Either works; both take the same download arguments.
if command -v hf >/dev/null 2>&1; then
  HF=(hf download)
elif command -v huggingface-cli >/dev/null 2>&1; then
  HF=(huggingface-cli download)
else
  echo "Hugging Face CLI not found on PATH." >&2
  echo "  pip install huggingface_hub" >&2
  exit 1
fi

DL="$OUT/.hf-dl"
mkdir -p "$OUT"
echo "downloading $MODEL_ID / $SUBDIR -> $OUT ..."
"${HF[@]}" "$MODEL_ID" --include "$SUBDIR/*" --local-dir "$DL"

# Flatten the build-variant subfolder up to OUT so the backend sees
# genai_config.json directly at the spec path.
mv "$DL/$SUBDIR/"* "$OUT/"
rm -rf "$DL"

echo
echo "done. install the runtime and wire the backend up:"
echo "  pip install onnxruntime-genai"
echo "  export AGENT_TOOL_LOCK_LLM_BACKEND=onnx_genai:$OUT"
