"""
Terminal MCP Server — Exposes shell execution as an MCP-compliant server.

Wraps the battle-tested ``shell_tool.py`` security layer (allowlist,
blocked patterns, smart truncation) so it can run as a standard MCP
server alongside GitHub, Context7, filesystem, etc.

Transport modes:
    stdio  (local):  ``python -m cuga.terminal_mcp_server``
    HTTP   (Docker):  ``python -m cuga.terminal_mcp_server --http --port 8000``
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from fastmcp import FastMCP

# Re-use the security-scoped shell implementation
from cuga.shell_tool import (
    ALLOWED_COMMANDS,
    BLOCKED_PATTERNS,
    _execute_shell,
    _validate_command,
)

__all__ = ["create_terminal_mcp"]

# ── Server definition ──────────────────────────────────────────

mcp = FastMCP(
    name="terminal",
    instructions=(
        "Secure shell execution server for the AI Repo Builder. "
        "All commands are validated against a safety allowlist before execution. "
        "Use execute_command for running build tools, tests, linters, git, etc."
    ),
)


@mcp.tool()
async def execute_command(command: str, working_dir: str = "") -> str:
    """Execute a shell command in the project output directory.

    Args:
        command: The shell command to run. Must start with an allowed command
            (e.g. python, pip, npm, git, docker, pytest, ruff, curl, ls, cat).
        working_dir: Working directory relative to the project root.
            Leave empty to use the project root.

    Returns:
        Command output (stdout + stderr) with exit code.
        Output is intelligently truncated for long results,
        preserving error-relevant lines.
    """
    return await _execute_shell(command, working_dir)


@mcp.tool()
async def validate_command(command: str) -> str:
    """Check whether a command would be allowed before executing it.

    Args:
        command: The shell command to validate.

    Returns:
        "OK" if the command is allowed, or an error message explaining
        why it would be blocked.
    """
    error = _validate_command(command)
    return error if error else "OK"


@mcp.tool()
async def list_allowed_commands() -> str:
    """Return the full list of allowed command prefixes.

    Returns:
        Sorted, comma-separated list of allowed base commands.
    """
    return ", ".join(sorted(ALLOWED_COMMANDS))


@mcp.tool()
async def list_blocked_patterns() -> str:
    """Return the list of blocked dangerous patterns.

    Returns:
        Newline-separated list of blocked shell patterns.
    """
    return "\n".join(BLOCKED_PATTERNS)


# ── Entry point ────────────────────────────────────────────────


def create_terminal_mcp() -> FastMCP:
    """Return the configured FastMCP server instance.

    Returns:
        The terminal MCP server, ready to be run with
        ``mcp.run()`` or attached to a transport.
    """
    return mcp


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the terminal MCP server.

    Args:
        argv: Command-line arguments (defaults to sys.argv).

    Returns:
        Parsed namespace with transport configuration.
    """
    parser = argparse.ArgumentParser(
        description="Terminal MCP Server — secure shell execution",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Run in HTTP mode instead of stdio (for Docker deployment)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP port (default: 8000, only used with --http)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="HTTP host (default: 0.0.0.0, only used with --http)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the terminal MCP server.

    Args:
        argv: Command-line arguments.
    """
    args = _parse_args(argv)

    if args.http:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
