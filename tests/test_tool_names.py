"""Verify all MCP tool names are valid Python identifiers after sanitization."""

import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

if os.environ.get("GITHUB_TOKEN") and not os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
    os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = os.environ["GITHUB_TOKEN"]


async def main():
    from cuga.backend.tools_env.registry.config.config_loader import load_service_configs
    from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
    from cuga.mcp_direct_tools import create_tools_from_mcp_manager

    config_path = str(Path(__file__).parent.parent / "mcp_servers_local.yaml")
    cfgs = load_service_configs(config_path)
    mgr = MCPManager(config=cfgs)
    await mgr.load_tools()
    tools = create_tools_from_mcp_manager(mgr)

    ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    bad = [t.name for t in tools if not ident_re.match(t.name)]

    print(f"Total tools: {len(tools)}")
    print(f"Invalid names: {bad}")
    if not bad:
        print("ALL TOOL NAMES ARE VALID PYTHON IDENTIFIERS")

    for t in tools:
        if "_" in t.name and any(k in t.name for k in ["context7", "brave", "sequential"]):
            print(f"  OK: {t.name}")

    # MCPManager doesn't expose a close() – processes will be cleaned up on exit
    return bad


if __name__ == "__main__":
    bad = asyncio.run(main())
    raise SystemExit(1 if bad else 0)
