"""
MCP Bootstrap — Shared MCP server startup and tool creation.

Consolidates the duplicated MCP setup logic from ``main.py`` and
``build_loop.py`` into a single reusable module.

Usage::

    from cuga.mcp_bootstrap import bootstrap_mcp

    tools, manager = await bootstrap_mcp(
        mcp_servers_path="mcp_servers.yaml",
        workspace_root="/path/to/output",
    )
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger

__all__ = [
    "MCPBootstrapResult",
    "bootstrap_mcp",
]


class MCPBootstrapResult:
    """Result of MCP bootstrap containing tools and manager references.

    Attributes:
        tools: List of LangChain-compatible tools created from MCP servers.
        manager: The underlying MCPManager instance.
        registry: The ApiRegistry instance.
        live_servers: Names of successfully connected servers.
        failed_servers: Names of servers that failed to connect.
    """

    def __init__(
        self,
        tools: list[Any],
        manager: Any,
        registry: Any,
        live_servers: list[str],
        failed_servers: list[str],
    ) -> None:
        self.tools = tools
        self.manager = manager
        self.registry = registry
        self.live_servers = live_servers
        self.failed_servers = failed_servers


async def bootstrap_mcp(
    mcp_servers_path: str | Path,
    workspace_root: str | Path,
    *,
    include_shell: bool = True,
    exit_on_failure: bool = True,
) -> MCPBootstrapResult:
    """Start MCP servers, create tools, and return them ready to use.

    Handles:
    - Loading and parsing the MCP servers YAML config
    - Resolving ``ENV:`` auth references from environment variables
    - Scoping the filesystem MCP server to the output directory
    - Starting all servers with graceful degradation
    - Creating LangChain-compatible tools from the connected servers
    - Optionally adding the native shell execution tool

    Args:
        mcp_servers_path: Path to the MCP servers YAML config file.
        workspace_root: Absolute path to the output/workspace directory.
        include_shell: If True, append the native shell tool.
        exit_on_failure: If True, call ``sys.exit(1)`` when no servers connect.
            If False, raise ``ConnectionError`` instead.

    Returns:
        An ``MCPBootstrapResult`` with tools and server info.

    Raises:
        ConnectionError: If no servers connect and ``exit_on_failure=False``.
    """
    mcp_path = str(Path(mcp_servers_path).resolve())
    ws_root = str(Path(workspace_root).resolve())

    os.environ.setdefault("MCP_SERVERS_FILE", mcp_path)

    # Lazy imports — only needed after env vars are set
    from cuga.backend.tools_env.registry.config.config_loader import (
        load_service_configs,
    )
    from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
    from cuga.backend.tools_env.registry.registry.api_registry import ApiRegistry
    from cuga.mcp_direct_tools import create_tools_from_mcp_manager

    services = load_service_configs(mcp_path)

    # Resolve ENV: auth references (e.g. "ENV:GITHUB_TOKEN")
    for svc in services.values():
        if (
            svc.auth
            and isinstance(svc.auth.value, str)
            and svc.auth.value.startswith("ENV:")
        ):
            env_key = svc.auth.value[4:]
            resolved = os.environ.get(env_key, "")
            if not resolved:
                logger.warning("Auth env var {} not set for {}", env_key, svc.name)
            svc.auth.value = resolved

    # Scope the filesystem MCP to the workspace directory
    if "filesystem" in services and services["filesystem"].args:
        services["filesystem"].args[-1] = ws_root
        logger.info("Filesystem MCP scoped to {}", ws_root)

    manager = MCPManager(config=services)
    registry = ApiRegistry(client=manager)

    # Start servers — individual failures are gracefully degraded
    try:
        await registry.start_servers()
    except Exception as exc:
        logger.error("Failed to start MCP servers: {}", exc)
        raise

    live = list(manager.tools_by_server.keys())
    failed = list(getattr(manager, "initialization_errors", {}).keys())

    logger.info(
        "MCP servers: {} live ({}), {} failed ({})",
        len(live),
        ", ".join(live) if live else "none",
        len(failed),
        ", ".join(failed) if failed else "none",
    )

    if not live:
        msg = "No MCP servers connected — cannot proceed"
        if exit_on_failure:
            logger.error(msg)
            sys.exit(1)
        else:
            raise ConnectionError(msg)

    # Log details of failed servers
    if hasattr(manager, "initialization_errors") and manager.initialization_errors:
        for srv_name, err in manager.initialization_errors.items():
            logger.warning(
                "MCP server '{}' unavailable: {}", srv_name, err.get("error", "unknown")
            )

    # Create tools from connected MCP servers
    all_tools: list[Any] = list(create_tools_from_mcp_manager(manager))

    # Optionally add native shell execution
    if include_shell:
        from cuga.shell_tool import create_shell_tool

        os.environ["CUGA_OUTPUT_DIR"] = ws_root
        all_tools.append(create_shell_tool())

    logger.info(
        "Loaded {} tools ({} MCP servers{})",
        len(all_tools),
        len(live),
        " + shell" if include_shell else "",
    )
    for t in all_tools:
        logger.debug("  Tool: {}", t.name)

    return MCPBootstrapResult(
        tools=all_tools,
        manager=manager,
        registry=registry,
        live_servers=live,
        failed_servers=failed,
    )
