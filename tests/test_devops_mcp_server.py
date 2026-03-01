"""Tests for the DevOps MCP server module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class TestDevOpsMCPServer:
    """Test Devon MCP server tool functions."""

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
            "devops_mcp",
            Path(__file__).resolve().parents[1]
            / "src"
            / "cuga"
            / "devops_mcp_server.py",
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.mod = mod

    def test_module_has_expected_functions(self) -> None:
        """Module exposes create_devops_mcp and main."""
        assert hasattr(self.mod, "create_devops_mcp")
        assert hasattr(self.mod, "main")
        assert hasattr(self.mod, "mcp")

    def test_mcp_has_tools_registered(self) -> None:
        """MCP server should have 7 tools registered."""
        assert len(self.mod.mcp.tools) == 7

    def test_api_base_default_region(self) -> None:
        """Default region should be us-south."""
        import os
        os.environ.pop("IBMCLOUD_REGION", None)
        assert "us-south" in self.mod._api_base()

    def test_api_base_custom_region(self) -> None:
        """Custom region should be reflected in URL."""
        import os
        os.environ["IBMCLOUD_REGION"] = "eu-de"
        try:
            assert "eu-de" in self.mod._api_base()
        finally:
            os.environ["IBMCLOUD_REGION"] = "us-south"

    @pytest.mark.asyncio
    async def test_get_iam_token_missing_key(self) -> None:
        """Should raise RuntimeError when IBMCLOUD_API_KEY is missing."""
        import os
        old = os.environ.pop("IBMCLOUD_API_KEY", None)
        try:
            with pytest.raises(RuntimeError, match="IBMCLOUD_API_KEY"):
                await self.mod._get_iam_token()
        finally:
            if old:
                os.environ["IBMCLOUD_API_KEY"] = old

    @pytest.mark.asyncio
    async def test_get_iam_token_caches(self) -> None:
        """IAM token should be cached after first fetch."""
        import os
        import time
        os.environ["IBMCLOUD_API_KEY"] = "test-key"

        # Pre-populate cache
        self.mod._iam_cache["token"] = "cached-token"
        self.mod._iam_cache["expires_at"] = time.time() + 3600

        try:
            token = await self.mod._get_iam_token()
            assert token == "cached-token"
        finally:
            os.environ.pop("IBMCLOUD_API_KEY", None)
            self.mod._iam_cache["token"] = ""
            self.mod._iam_cache["expires_at"] = 0.0

    @pytest.mark.asyncio
    async def test_create_toolchain_calls_api(self) -> None:
        """create_toolchain should call the API with correct params."""
        mock_response = {"id": "tc-123", "name": "test-tc"}

        with patch.object(self.mod, "_api_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response
            result = await self.mod.create_toolchain("test-tc", "rg-abc")

        assert "tc-123" in result
        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert call_args[0][0] == "POST"
        assert "toolchains" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_run_pipeline_calls_api(self) -> None:
        """run_pipeline should trigger a pipeline run."""
        mock_response = {"id": "run-456", "status": {"state": "running"}}

        with patch.object(self.mod, "_api_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response
            result = await self.mod.run_pipeline("pipe-123")

        assert "run-456" in result
        mock_req.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pipeline_run_status(self) -> None:
        """get_pipeline_run_status should return run status."""
        mock_response = {"id": "run-456", "status": {"state": "succeeded"}}

        with patch.object(self.mod, "_api_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response
            result = await self.mod.get_pipeline_run_status("pipe-123", "run-456")

        assert "succeeded" in result

    @pytest.mark.asyncio
    async def test_delete_toolchain(self) -> None:
        """delete_toolchain should call DELETE API."""
        with patch.object(self.mod, "_api_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {}
            result = await self.mod.delete_toolchain("tc-123")

        assert "deleted" in result.lower()

    def test_parse_args_defaults(self) -> None:
        """Default args should use stdio mode."""
        args = self.mod._parse_args([])
        assert not args.http
        assert args.port == 8000

    def test_format_response(self) -> None:
        """_format_response should produce valid JSON."""
        import json
        result = self.mod._format_response({"key": "value", "num": 42})
        parsed = json.loads(result)
        assert parsed["key"] == "value"
        assert parsed["num"] == 42
