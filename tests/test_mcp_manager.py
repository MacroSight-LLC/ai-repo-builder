"""Tests for MCP manager."""

from __future__ import annotations

import pytest

from cuga.mcp_manager import MCPManager


class TestMCPManager:
    """Tests for MCPManager initialization and properties."""

    def test_init_with_mcp_servers_key(self) -> None:
        config = {"mcpServers": {"fs": {"command": "npx", "args": []}}}
        mgr = MCPManager(config)
        assert mgr.connected_servers == []
        assert mgr.failed_servers == []
        assert mgr.is_any_connected is False

    def test_init_with_servers_key(self) -> None:
        config = {"servers": {"fs": {"command": "npx", "args": []}}}
        mgr = MCPManager(config)
        assert mgr.is_any_connected is False

    def test_init_empty_config(self) -> None:
        mgr = MCPManager({})
        assert mgr.connected_servers == []
        assert mgr.is_any_connected is False

    def test_get_session_raises_on_missing(self) -> None:
        mgr = MCPManager({})
        with pytest.raises(KeyError, match="not connected"):
            mgr.get_session("nonexistent")

    @pytest.mark.asyncio()
    async def test_disconnect_all_when_empty(self) -> None:
        mgr = MCPManager({})
        await mgr.disconnect_all()  # Should not raise

    @pytest.mark.asyncio()
    async def test_connect_all_raises_when_all_fail(self) -> None:
        config = {
            "mcpServers": {
                "fake": {"command": "nonexistent_binary_12345", "args": []},
            },
        }
        mgr = MCPManager(config)
        with pytest.raises((ConnectionError, Exception)):
            await mgr.connect_all(timeout_per_server=3.0)

    def test_failed_servers_initially_empty(self) -> None:
        mgr = MCPManager({"mcpServers": {"s": {"command": "x", "args": []}}})
        assert mgr.failed_servers == []

    def test_connected_servers_initially_empty(self) -> None:
        mgr = MCPManager({"mcpServers": {"s": {"command": "x", "args": []}}})
        assert mgr.connected_servers == []

    @pytest.mark.asyncio()
    async def test_disconnect_all_is_idempotent(self) -> None:
        mgr = MCPManager({})
        await mgr.disconnect_all()
        await mgr.disconnect_all()  # Should not raise on second call

    def test_get_session_message_lists_available(self) -> None:
        mgr = MCPManager({})
        with pytest.raises(KeyError, match="Available"):
            mgr.get_session("missing")
