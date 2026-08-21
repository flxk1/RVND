# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Single CLI entry point: agent-tool-lock <subcommand>.

Subcommands:
  setup    — run the onboarding wizard (first-run config + model setup)
  mcp      — run the MCP server over stdio (after setup)
  review   — interactive PII-review CLI
  doctor   — print runtime status (backend, models found, config path)
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(_usage(), file=sys.stderr)
        return 1

    cmd, rest = argv[0], argv[1:]

    if cmd in ("setup", "init", "wizard"):
        from rvnd.lock.onboarding.wizard import main as wizard_main
        return wizard_main()

    if cmd in ("mcp", "serve"):
        from rvnd.lock.mcp_server import main as mcp_main
        mcp_main()
        return 0

    if cmd in ("review", "interactive"):
        from rvnd.lock.interactive import interactive_cli
        return interactive_cli(argv=rest)

    if cmd in ("proxy", "egress"):
        from rvnd.lock.egress_proxy import main as proxy_main
        return proxy_main(rest)

    if cmd == "doctor":
        return _doctor()

    if cmd in ("-h", "--help", "help"):
        print(_usage())
        return 0

    print(f"unknown subcommand: {cmd}\n\n{_usage()}", file=sys.stderr)
    return 1


def _usage() -> str:
    return (
        "Usage: agent-tool-lock <subcommand>\n\n"
        "Subcommands:\n"
        "  setup      Run the onboarding wizard (first-run setup)\n"
        "  mcp        Run the MCP server over stdio\n"
        "  proxy      Run the egress proxy — enforced gate between agent and cloud LLM\n"
        "  review     Interactive PII-review CLI\n"
        "  doctor     Print runtime status\n"
        "  help       Show this message\n"
    )


def _doctor() -> int:
    """Print runtime status. Useful for debugging install issues."""
    import os
    from rvnd.lock.onboarding.config import load_config, default_config_path, apply_config_to_env
    from rvnd.lock.tier_c import describe_tier_c, is_tier_c_available

    print("━━ agent-tool-lock doctor ━━\n")

    cfg_path = default_config_path()
    print(f"  config path:     {cfg_path}")
    print(f"  config exists:   {cfg_path.exists()}")
    cfg = load_config()
    apply_config_to_env(cfg)
    print(f"  backend spec:    {cfg.backend_spec}")
    print(f"  default mode:    {cfg.default_mode}")
    print(f"  audit log:       {cfg.audit_log_path or '(not set)'}")
    print()

    print("  env vars:")
    for k in ("AGENT_TOOL_LOCK_LLM_BACKEND", "AGENT_TOOL_LOCK_DEFAULT_MODE",
              "AGENT_TOOL_LOCK_AUDIT_LOG", "OLLAMA_HOST", "OLLAMA_MODEL"):
        v = os.environ.get(k, "")
        print(f"    {k:<35} = {v or '(unset)'}")
    print()

    print("  Tier C status:")
    print(f"    backend:       {describe_tier_c()}")
    print(f"    available:     {is_tier_c_available()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
