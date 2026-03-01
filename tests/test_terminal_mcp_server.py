"""Tests for the Terminal MCP server module."""

from __future__ import annotations

import pytest


class TestTerminalMCPTools:
    """Test terminal MCP tool functions (sans FastMCP registration)."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        """Extract tool functions from the module."""
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "terminal_mcp",
            Path(__file__).resolve().parents[1]
            / "src"
            / "cuga"
            / "terminal_mcp_server.py",
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)

        # Stub FastMCP to avoid full import chain
        import types
        import sys

        fake_fastmcp = types.ModuleType("fastmcp")

        class FakeMCP:
            def __init__(self, **kw: object) -> None:
                self.tools: list[object] = []

            def tool(self, **kw: object):
                def decorator(fn: object):
                    self.tools.append(fn)
                    return fn
                return decorator

            def run(self, **kw: object) -> None:
                pass

        fake_fastmcp.FastMCP = FakeMCP  # type: ignore[attr-defined]
        sys.modules["fastmcp"] = fake_fastmcp

        spec.loader.exec_module(mod)
        self.mod = mod

    def test_module_has_expected_functions(self) -> None:
        """Module exposes create_terminal_mcp and main."""
        assert hasattr(self.mod, "create_terminal_mcp")
        assert hasattr(self.mod, "main")
        assert hasattr(self.mod, "mcp")

    def test_mcp_has_tools_registered(self) -> None:
        """MCP server should have 4 tools registered."""
        mcp_server = self.mod.mcp
        assert len(mcp_server.tools) == 4

    def test_execute_command_is_async(self) -> None:
        """execute_command should be an async function."""
        import asyncio
        assert asyncio.iscoroutinefunction(self.mod.execute_command)

    def test_validate_command_is_async(self) -> None:
        """validate_command should be an async function."""
        import asyncio
        assert asyncio.iscoroutinefunction(self.mod.validate_command)

    def test_list_allowed_commands_is_async(self) -> None:
        """list_allowed_commands should be an async function."""
        import asyncio
        assert asyncio.iscoroutinefunction(self.mod.list_allowed_commands)

    @pytest.mark.asyncio
    async def test_validate_command_allows_safe(self) -> None:
        """Safe commands should return OK."""
        result = await self.mod.validate_command("ls -la")
        assert result == "OK"

    @pytest.mark.asyncio
    async def test_validate_command_blocks_unsafe(self) -> None:
        """Dangerous commands should return error message."""
        result = await self.mod.validate_command("rm -rf /")
        assert "Blocked" in result

    @pytest.mark.asyncio
    async def test_validate_command_blocks_disallowed(self) -> None:
        """Commands not in allowlist should return error."""
        result = await self.mod.validate_command("shutdown -h now")
        assert "not in the allowed list" in result

    @pytest.mark.asyncio
    async def test_list_allowed_commands_returns_sorted(self) -> None:
        """list_allowed_commands should return a sorted comma-separated list."""
        result = await self.mod.list_allowed_commands()
        assert "git" in result
        assert "python" in result
        assert ", " in result
        # Verify sorted
        items = [x.strip() for x in result.split(",")]
        assert items == sorted(items)

    @pytest.mark.asyncio
    async def test_list_blocked_patterns_returns_patterns(self) -> None:
        """list_blocked_patterns should return known dangerous patterns."""
        result = await self.mod.list_blocked_patterns()
        assert "rm -rf /" in result
        assert "sudo " in result

    @pytest.mark.asyncio
    async def test_execute_command_runs_echo(self) -> None:
        """execute_command should run a simple echo command."""
        import os
        os.environ.setdefault("CUGA_OUTPUT_DIR", "/tmp")
        result = await self.mod.execute_command("echo hello_mcp_test")
        assert "hello_mcp_test" in result
        assert "Exit code: 0" in result

    @pytest.mark.asyncio
    async def test_execute_command_blocks_dangerous(self) -> None:
        """execute_command should block dangerous commands."""
        result = await self.mod.execute_command("sudo rm -rf /")
        assert "ERROR" in result

    def test_parse_args_defaults(self) -> None:
        """Default args should use stdio mode."""
        args = self.mod._parse_args([])
        assert not args.http
        assert args.port == 8000

    def test_parse_args_http(self) -> None:
        """--http flag should enable HTTP mode."""
        args = self.mod._parse_args(["--http", "--port", "9000"])
        assert args.http
        assert args.port == 9000
