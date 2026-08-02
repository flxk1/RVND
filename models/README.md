# models/

Local model weights for the Privacy Lock's Tier C semantic check live here. This
directory is gitignored except for this README — weights are multi-GB and are
never committed or shipped. Cloning the repo gives you an empty `models/`; you
supply the model. Rvnd endorses no vendor — bring any GGUF or ONNX Runtime GenAI
model you prefer to run.

As a licence-clean worked example, this fetches Microsoft's prebuilt Phi-3.5-mini
ONNX build (MIT); override `MODEL_ID` / `SUBDIR` / `OUT` for any other:

```bash
pip install huggingface_hub onnxruntime-genai
scripts/fetch_onnx_model.sh
export AGENT_TOOL_LOCK_LLM_BACKEND=onnx_genai:"$PWD"/models/phi-3.5-mini-instruct-onnx
```

The backend loads any directory containing a `genai_config.json`; the in-repo
path is only the convention the fetch script and onboarding wizard use. Full
setup — backends, a lighter GGUF alternative, hosted-model notes, the licence
note on model choice, and how to verify — is in
[docs/concepts/local-models.md](../docs/concepts/local-models.md).
