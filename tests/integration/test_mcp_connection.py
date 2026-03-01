#!/usr/bin/env python3
"""Test the actual CUGA MCPManager connection to all stdio servers."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Load .env before importing CUGA modules
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# GitHub MCP expects GITHUB_PERSONAL_ACCESS_TOKEN
if os.environ.get("GITHUB_TOKEN") and not os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
    os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = os.environ["GITHUB_TOKEN"]


async def main():
    from pathlib import Path

    from cuga.backend.tools_env.registry.config.config_loader import load_service_configs
    from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager

    config_path = str(Path(__file__).parent.parent / "mcp_servers_local.yaml")
    os.environ.setdefault("MCP_SERVERS_FILE", config_path)
    os.environ.setdefault(
        "SETTINGS_TOML_PATH", str(Path(__file__).parent.parent / "src" / "cuga" / "settings.toml")
    )

    print(f"\n{'=' * 60}")
    print(f"  Loading MCP config from: {config_path}")
    print(f"{'=' * 60}\n")

    services = load_service_configs(config_path)
    print(f"Parsed {len(services)} service configs:")
    for name, svc in services.items():
        transport = svc.transport or "auto"
        cmd = svc.command or "(none)"
        print(f"  {name}: transport={transport}, command={cmd}")

    # Resolve ENV: auth references
    for svc in services.values():
        if svc.auth and isinstance(getattr(svc.auth, "value", None), str):
            if svc.auth.value.startswith("ENV:"):
                env_key = svc.auth.value[4:]
                svc.auth.value = os.environ.get(env_key, "")

    print("\nInitializing MCPManager...")
    manager = MCPManager(config=services)

    print("Loading tools (this spawns npx processes)...\n")
    try:
        await manager.load_tools()
    except Exception as e:
        print(f"\n❌ load_tools() failed: {e}")
        import traceback

        traceback.print_exc()
        # Even if some fail, check what we got
        pass

    # Report results
    print(f"\n{'=' * 60}")
    print("  MCP Tool Loading Results")
    print(f"{'=' * 60}\n")

    total_tools = 0
    for server_name, tools in manager.tools_by_server.items():
        count = len(tools)
        total_tools += count
        tool_names = [t.get("function", t).get("name", "?") for t in tools[:5]]
        more = f" + {count - 5} more" if count > 5 else ""
        print(f"  ✅ {server_name}: {count} tools [{', '.join(tool_names)}{more}]")

    if total_tools == 0:
        print("  ❌ No tools loaded from any server!")
    else:
        print(f"\n  Total: {total_tools} tools from {len(manager.tools_by_server)} servers")

    # Now test creating LangChain StructuredTools
    from cuga.mcp_direct_tools import create_tools_from_mcp_manager

    lc_tools = create_tools_from_mcp_manager(manager)
    print(f"\n  LangChain StructuredTools created: {len(lc_tools)}")
    for t in lc_tools[:10]:
        print(f"    {t.name}: {t.description[:60]}...")
    if len(lc_tools) > 10:
        print(f"    ... and {len(lc_tools) - 10} more")

    print(f"\n{'=' * 60}\n")
    return 0 if total_tools > 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
