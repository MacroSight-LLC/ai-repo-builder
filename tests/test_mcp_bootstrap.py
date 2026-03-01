"""Tests for the MCP bootstrap module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.mcp_bootstrap import MCPBootstrapResult, bootstrap_mcp

# ── MCPBootstrapResult tests ──────────────────────────────────


class TestMCPBootstrapResult:
    """Tests for the MCPBootstrapResult container."""

    def test_construction(self) -> None:
        tools = [MagicMock(name="tool1"), MagicMock(name="tool2")]
        manager = MagicMock()
        registry = MagicMock()

        result = MCPBootstrapResult(
            tools=tools,
            manager=manager,
            registry=registry,
            live_servers=["fs", "git"],
            failed_servers=["broken"],
        )

        assert result.tools is tools
        assert len(result.tools) == 2
        assert result.manager is manager
        assert result.registry is registry
        assert result.live_servers == ["fs", "git"]
        assert result.failed_servers == ["broken"]

    def test_empty_construction(self) -> None:
        result = MCPBootstrapResult(
            tools=[],
            manager=None,
            registry=None,
            live_servers=[],
            failed_servers=[],
        )
        assert result.tools == []
        assert result.live_servers == []
        assert result.failed_servers == []

    def test_mutable_tools_list(self) -> None:
        """Tools list can be modified after creation."""
        result = MCPBootstrapResult(
            tools=[],
            manager=None,
            registry=None,
            live_servers=[],
            failed_servers=[],
        )
        new_tool = MagicMock()
        result.tools.append(new_tool)
        assert len(result.tools) == 1


# ── bootstrap_mcp tests ───────────────────────────────────────


class TestBootstrapMcp:
    """Tests for the bootstrap_mcp() function.

    Note: bootstrap_mcp uses lazy imports inside the function body,
    so we patch the original module paths that get imported at call time.
    """

    _LOADER = "cuga.backend.tools_env.registry.config.config_loader.load_service_configs"
    _MANAGER = "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.MCPManager"
    _REGISTRY = "cuga.backend.tools_env.registry.registry.api_registry.ApiRegistry"
    _CREATE_TOOLS = "cuga.mcp_direct_tools.create_tools_from_mcp_manager"
    _SHELL_TOOL = "cuga.shell_tool.create_shell_tool"

    @pytest.mark.asyncio()
    async def test_no_live_servers_raises_connection_error(self, tmp_path: Path) -> None:
        """When no servers start, raises ConnectionError if exit_on_failure=False."""
        config_file = tmp_path / "mcp_servers.yaml"
        config_file.write_text("servers: {}")

        mock_manager = MagicMock()
        mock_manager.tools_by_server = {}
        mock_manager.initialization_errors = {}

        mock_registry = AsyncMock()

        with (
            patch(self._LOADER, return_value={}),
            patch(self._MANAGER, return_value=mock_manager),
            patch(self._REGISTRY, return_value=mock_registry),
            pytest.raises(ConnectionError, match="No MCP servers connected"),
        ):
            await bootstrap_mcp(
                config_file,
                tmp_path / "workspace",
                exit_on_failure=False,
            )

    @pytest.mark.asyncio()
    async def test_successful_bootstrap(self, tmp_path: Path) -> None:
        """Happy path — servers start, tools created, result returned."""
        config_file = tmp_path / "mcp_servers.yaml"
        config_file.write_text("servers: {}")
        workspace = tmp_path / "workspace"

        mock_tool = MagicMock(name="fs_tool")
        mock_shell = MagicMock(name="shell_tool")

        mock_manager = MagicMock()
        mock_manager.tools_by_server = {"fs": ["read", "write"]}
        mock_manager.initialization_errors = {}

        mock_registry = AsyncMock()

        with (
            patch(
                self._LOADER,
                return_value={"fs": MagicMock(auth=None, args=None)},
            ),
            patch(self._MANAGER, return_value=mock_manager),
            patch(self._REGISTRY, return_value=mock_registry),
            patch(self._CREATE_TOOLS, return_value=[mock_tool]),
            patch(self._SHELL_TOOL, return_value=mock_shell),
        ):
            result = await bootstrap_mcp(
                config_file,
                workspace,
                include_shell=True,
                exit_on_failure=False,
            )

        assert isinstance(result, MCPBootstrapResult)
        assert result.live_servers == ["fs"]
        assert result.failed_servers == []
        assert mock_tool in result.tools
        assert mock_shell in result.tools

    @pytest.mark.asyncio()
    async def test_bootstrap_without_shell(self, tmp_path: Path) -> None:
        """include_shell=False omits the shell tool."""
        config_file = tmp_path / "mcp_servers.yaml"
        config_file.write_text("servers: {}")
        workspace = tmp_path / "workspace"

        mock_tool = MagicMock(name="fs_tool")

        mock_manager = MagicMock()
        mock_manager.tools_by_server = {"fs": ["read"]}
        mock_manager.initialization_errors = {}

        mock_registry = AsyncMock()

        with (
            patch(
                self._LOADER,
                return_value={"fs": MagicMock(auth=None, args=None)},
            ),
            patch(self._MANAGER, return_value=mock_manager),
            patch(self._REGISTRY, return_value=mock_registry),
            patch(self._CREATE_TOOLS, return_value=[mock_tool]),
        ):
            result = await bootstrap_mcp(
                config_file,
                workspace,
                include_shell=False,
                exit_on_failure=False,
            )

        assert len(result.tools) == 1
        assert result.tools[0] is mock_tool

    @pytest.mark.asyncio()
    async def test_env_auth_resolution(self, tmp_path: Path) -> None:
        """ENV: auth references are resolved from environment variables."""
        config_file = tmp_path / "mcp_servers.yaml"
        config_file.write_text("servers: {}")

        auth_mock = MagicMock()
        auth_mock.value = "ENV:MY_SECRET_TOKEN"
        svc_mock = MagicMock()
        svc_mock.auth = auth_mock
        svc_mock.name = "git"
        svc_mock.args = None

        mock_manager = MagicMock()
        mock_manager.tools_by_server = {"git": ["clone"]}
        mock_manager.initialization_errors = {}

        mock_registry = AsyncMock()

        with (
            patch.dict(os.environ, {"MY_SECRET_TOKEN": "secret123"}),
            patch(self._LOADER, return_value={"git": svc_mock}),
            patch(self._MANAGER, return_value=mock_manager),
            patch(self._REGISTRY, return_value=mock_registry),
            patch(self._CREATE_TOOLS, return_value=[MagicMock()]),
            patch(self._SHELL_TOOL, return_value=MagicMock()),
        ):
            await bootstrap_mcp(
                config_file,
                tmp_path / "ws",
                exit_on_failure=False,
            )

        assert auth_mock.value == "secret123"

    @pytest.mark.asyncio()
    async def test_filesystem_scoping(self, tmp_path: Path) -> None:
        """Filesystem MCP args are scoped to workspace root."""
        config_file = tmp_path / "mcp_servers.yaml"
        config_file.write_text("servers: {}")
        workspace = tmp_path / "workspace"

        fs_svc = MagicMock()
        fs_svc.auth = None
        fs_svc.args = ["read", "/original/path"]
        fs_svc.name = "filesystem"

        mock_manager = MagicMock()
        mock_manager.tools_by_server = {"filesystem": ["read"]}
        mock_manager.initialization_errors = {}

        mock_registry = AsyncMock()

        with (
            patch(self._LOADER, return_value={"filesystem": fs_svc}),
            patch(self._MANAGER, return_value=mock_manager),
            patch(self._REGISTRY, return_value=mock_registry),
            patch(self._CREATE_TOOLS, return_value=[MagicMock()]),
            patch(self._SHELL_TOOL, return_value=MagicMock()),
        ):
            await bootstrap_mcp(
                config_file,
                workspace,
                exit_on_failure=False,
            )

        assert fs_svc.args[-1] == str(workspace.resolve())

    @pytest.mark.asyncio()
    async def test_failed_servers_listed(self, tmp_path: Path) -> None:
        """Failed servers appear in the result."""
        config_file = tmp_path / "mcp_servers.yaml"
        config_file.write_text("servers: {}")

        mock_manager = MagicMock()
        mock_manager.tools_by_server = {"fs": ["read"]}
        mock_manager.initialization_errors = {"broken": {"error": "timeout"}}

        mock_registry = AsyncMock()

        with (
            patch(
                self._LOADER,
                return_value={"fs": MagicMock(auth=None, args=None)},
            ),
            patch(self._MANAGER, return_value=mock_manager),
            patch(self._REGISTRY, return_value=mock_registry),
            patch(self._CREATE_TOOLS, return_value=[MagicMock()]),
            patch(self._SHELL_TOOL, return_value=MagicMock()),
        ):
            result = await bootstrap_mcp(
                config_file,
                tmp_path / "ws",
                exit_on_failure=False,
            )

        assert result.failed_servers == ["broken"]
        assert result.live_servers == ["fs"]

    @pytest.mark.asyncio()
    async def test_registry_start_failure_propagates(self, tmp_path: Path) -> None:
        """If registry.start_servers() raises, the error propagates."""
        config_file = tmp_path / "mcp_servers.yaml"
        config_file.write_text("servers: {}")

        mock_manager = MagicMock()
        mock_registry = AsyncMock()
        mock_registry.start_servers = AsyncMock(side_effect=RuntimeError("Docker not running"))

        with (
            patch(self._LOADER, return_value={}),
            patch(self._MANAGER, return_value=mock_manager),
            patch(self._REGISTRY, return_value=mock_registry),
            pytest.raises(RuntimeError, match="Docker not running"),
        ):
            await bootstrap_mcp(
                config_file,
                tmp_path / "ws",
                exit_on_failure=False,
            )
