"""Tests for the Instana MCP server module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class TestInstanaMCPServer:
    """Test Instana MCP server tool functions."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        """Import the module with FastMCP stubbed."""
        import importlib.util
        import sys
        import types
        from pathlib import Path

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

        spec = importlib.util.spec_from_file_location(
            "instana_mcp",
            Path(__file__).resolve().parents[1]
            / "src"
            / "cuga"
            / "instana_mcp_server.py",
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.mod = mod

    def test_module_has_expected_functions(self) -> None:
        """Module exposes create_instana_mcp and main."""
        assert hasattr(self.mod, "create_instana_mcp")
        assert hasattr(self.mod, "main")
        assert hasattr(self.mod, "mcp")

    def test_mcp_has_tools_registered(self) -> None:
        """MCP server should have 7 tools registered."""
        assert len(self.mod.mcp.tools) == 7

    def test_base_url_missing(self) -> None:
        """Should raise RuntimeError when INSTANA_BASE_URL is missing."""
        import os
        os.environ.pop("INSTANA_BASE_URL", None)
        with pytest.raises(RuntimeError, match="INSTANA_BASE_URL"):
            self.mod._base_url()

    def test_base_url_strips_trailing_slash(self) -> None:
        """Should strip trailing slash from base URL."""
        import os
        os.environ["INSTANA_BASE_URL"] = "https://tenant.instana.io/"
        try:
            assert self.mod._base_url() == "https://tenant.instana.io"
        finally:
            os.environ.pop("INSTANA_BASE_URL", None)

    def test_api_token_missing(self) -> None:
        """Should raise RuntimeError when INSTANA_API_TOKEN is missing."""
        import os
        os.environ.pop("INSTANA_API_TOKEN", None)
        with pytest.raises(RuntimeError, match="INSTANA_API_TOKEN"):
            self.mod._api_token()

    @pytest.mark.asyncio
    async def test_create_application_perspective(self) -> None:
        """create_application_perspective should call API with correct params."""
        mock_response = {"id": "app-123", "label": "my-app"}

        with patch.object(self.mod, "_api_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response
            result = await self.mod.create_application_perspective("my-app")

        assert "app-123" in result
        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert call_args[0][0] == "POST"
        assert "application" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_create_application_perspective_default_match(self) -> None:
        """Default match_value should fall back to name."""
        with patch.object(self.mod, "_api_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"id": "app-1"}
            await self.mod.create_application_perspective("test-app")

        body = mock_req.call_args[1]["json_body"]
        element = body["matchSpecification"]["elements"][0]
        assert element["value"] == "test-app"
        assert element["name"] == "service.name"

    @pytest.mark.asyncio
    async def test_get_application_metrics(self) -> None:
        """get_application_metrics should call API with time window."""
        mock_response = {"items": [{"metric": "latency.mean", "value": 42.5}]}

        with patch.object(self.mod, "_api_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response
            result = await self.mod.get_application_metrics("app-123", "latency", 1800)

        assert "42.5" in result
        body = mock_req.call_args[1]["json_body"]
        assert body["applicationId"] == "app-123"
        assert body["timeFrame"]["windowSize"] == 1800000  # seconds → ms

    @pytest.mark.asyncio
    async def test_get_infrastructure_health(self) -> None:
        """get_infrastructure_health should call GET on health endpoint."""
        with patch.object(self.mod, "_api_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"healthy": 10, "unhealthy": 0}
            result = await self.mod.get_infrastructure_health()

        assert "healthy" in result
        call_args = mock_req.call_args
        assert call_args[0][0] == "GET"

    @pytest.mark.asyncio
    async def test_list_alerts_with_app_filter(self) -> None:
        """list_alerts should pass applicationId as query param."""
        with patch.object(self.mod, "_api_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"alerts": []}
            await self.mod.list_alerts(application_id="app-123")

        params = mock_req.call_args[1]["params"]
        assert params["applicationId"] == "app-123"

    @pytest.mark.asyncio
    async def test_list_alerts_no_filter(self) -> None:
        """list_alerts with no application_id should send empty params."""
        with patch.object(self.mod, "_api_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"alerts": []}
            await self.mod.list_alerts()

        params = mock_req.call_args[1]["params"]
        assert params == {}

    @pytest.mark.asyncio
    async def test_create_smart_alert(self) -> None:
        """create_smart_alert should create alert with threshold."""
        mock_response = {"id": "alert-789", "name": "High Latency"}

        with patch.object(self.mod, "_api_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response
            result = await self.mod.create_smart_alert(
                "High Latency", "app-123", "latency", 500.0, "ABOVE",
            )

        assert "alert-789" in result
        body = mock_req.call_args[1]["json_body"]
        assert body["rule"]["conditionValue"] == 500.0
        assert body["rule"]["conditionOperator"] == "ABOVE"

    @pytest.mark.asyncio
    async def test_get_service_map(self) -> None:
        """get_service_map should call the services endpoint."""
        with patch.object(self.mod, "_api_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"services": []}
            result = await self.mod.get_service_map("app-123")

        assert "services" in result
        path = mock_req.call_args[0][1]
        assert "app-123" in path

    def test_format_helper(self) -> None:
        """_format should produce valid JSON."""
        import json
        result = self.mod._format({"key": "value"})
        parsed = json.loads(result)
        assert parsed["key"] == "value"

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
