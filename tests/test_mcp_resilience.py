"""Tests for the MCP resilience module."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.mcp_resilience import (
    HealthReport,
    ServerHealth,
    _get_session,
    health_check_servers,
    reconnect_failed_servers,
    wrap_tools_with_retry,
)

# ── ServerHealth / HealthReport dataclass tests ────────────────


class TestServerHealth:
    """Tests for the ServerHealth dataclass."""

    def test_healthy_server(self) -> None:
        h = ServerHealth(name="fs", healthy=True, tool_count=5, latency_ms=42.3)
        assert h.name == "fs"
        assert h.healthy is True
        assert h.tool_count == 5
        assert h.latency_ms == 42.3
        assert h.error is None

    def test_unhealthy_server(self) -> None:
        h = ServerHealth(name="git", healthy=False, error="Connection refused")
        assert h.healthy is False
        assert h.tool_count == 0
        assert h.latency_ms == -1
        assert h.error == "Connection refused"

    def test_defaults(self) -> None:
        h = ServerHealth(name="x", healthy=True)
        assert h.tool_count == 0
        assert h.latency_ms == -1
        assert h.error is None


class TestHealthReport:
    """Tests for the HealthReport dataclass."""

    def test_empty_report(self) -> None:
        r = HealthReport()
        assert r.servers == []
        assert r.checked_at == 0.0
        assert r.all_healthy is False  # empty → not "all healthy"
        assert r.any_unhealthy is False
        assert r.healthy_count == 0
        assert r.unhealthy_names == []

    def test_all_healthy(self) -> None:
        r = HealthReport(
            servers=[
                ServerHealth(name="a", healthy=True),
                ServerHealth(name="b", healthy=True),
            ],
            checked_at=time.time(),
        )
        assert r.all_healthy is True
        assert r.any_unhealthy is False
        assert r.healthy_count == 2
        assert r.unhealthy_names == []

    def test_mixed_health(self) -> None:
        r = HealthReport(
            servers=[
                ServerHealth(name="a", healthy=True),
                ServerHealth(name="b", healthy=False, error="dead"),
                ServerHealth(name="c", healthy=True),
            ],
        )
        assert r.all_healthy is False
        assert r.any_unhealthy is True
        assert r.healthy_count == 2
        assert r.unhealthy_names == ["b"]

    def test_all_unhealthy(self) -> None:
        r = HealthReport(
            servers=[
                ServerHealth(name="x", healthy=False),
                ServerHealth(name="y", healthy=False),
            ],
        )
        assert r.all_healthy is False
        assert r.any_unhealthy is True
        assert r.healthy_count == 0
        assert r.unhealthy_names == ["x", "y"]

    def test_single_server_healthy(self) -> None:
        r = HealthReport(servers=[ServerHealth(name="solo", healthy=True)])
        assert r.all_healthy is True
        assert r.any_unhealthy is False

    def test_single_server_unhealthy(self) -> None:
        r = HealthReport(servers=[ServerHealth(name="solo", healthy=False)])
        assert r.all_healthy is False
        assert r.any_unhealthy is True


# ── _get_session helper tests ──────────────────────────────────


class TestGetSession:
    """Tests for the _get_session helper."""

    def test_registry_based_manager(self) -> None:
        """Session found via _servers dict."""
        fake_session = MagicMock(name="session")
        server_obj = MagicMock()
        server_obj.session = fake_session
        manager = MagicMock()
        manager._servers = {"fs": server_obj}
        manager._clients = {}
        assert _get_session(manager, "fs") is fake_session

    def test_standalone_manager_dict(self) -> None:
        """Session found via _clients dict with dict values."""
        fake_session = MagicMock(name="session")
        manager = MagicMock()
        manager._servers = {}
        manager._clients = {"git": {"session": fake_session}}
        assert _get_session(manager, "git") is fake_session

    def test_standalone_manager_object(self) -> None:
        """Session found via _clients dict with object values."""
        fake_session = MagicMock(name="session")
        client_obj = MagicMock()
        client_obj.session = fake_session
        manager = MagicMock()
        manager._servers = {}
        manager._clients = {"git": client_obj}
        assert _get_session(manager, "git") is fake_session

    def test_not_found(self) -> None:
        manager = MagicMock()
        manager._servers = {}
        manager._clients = {}
        assert _get_session(manager, "missing") is None

    def test_no_dicts_at_all(self) -> None:
        """Manager without _servers or _clients attrs."""
        manager = object()  # bare object, no attributes
        assert _get_session(manager, "anything") is None

    def test_registry_server_no_session_attr(self) -> None:
        """Server object exists but has no session attribute."""
        server_obj = MagicMock(spec=[])  # no attributes at all
        manager = MagicMock()
        manager._servers = {"fs": server_obj}
        manager._clients = {}
        assert _get_session(manager, "fs") is None


# ── health_check_servers tests ─────────────────────────────────


@dataclass
class FakeToolsResult:
    """Mimics the result from session.list_tools()."""

    tools: list[str] = field(default_factory=list)


class TestHealthCheckServers:
    """Tests for health_check_servers()."""

    @pytest.mark.asyncio()
    async def test_no_servers(self) -> None:
        """Manager with empty tools_by_server returns empty report."""
        manager = MagicMock()
        manager.tools_by_server = {}
        report = await health_check_servers(manager)
        assert report.servers == []
        assert report.checked_at > 0

    @pytest.mark.asyncio()
    async def test_all_healthy(self) -> None:
        """All servers respond to list_tools."""
        session = AsyncMock()
        session.list_tools.return_value = FakeToolsResult(tools=["a", "b"])

        manager = MagicMock()
        manager.tools_by_server = {"fs": ["a", "b"]}
        manager._servers = {}
        manager._clients = {"fs": {"session": session}}

        report = await health_check_servers(manager, timeout=5.0)

        assert len(report.servers) == 1
        assert report.servers[0].healthy is True
        assert report.servers[0].tool_count == 2
        assert report.servers[0].latency_ms >= 0
        assert report.all_healthy is True

    @pytest.mark.asyncio()
    async def test_timeout_server(self) -> None:
        """Server that times out is marked unhealthy."""
        session = AsyncMock()
        session.list_tools.side_effect = TimeoutError()

        manager = MagicMock()
        manager.tools_by_server = {"slow": ["x"]}
        manager._servers = {}
        manager._clients = {"slow": {"session": session}}

        report = await health_check_servers(manager, timeout=0.1)

        assert len(report.servers) == 1
        assert report.servers[0].healthy is False
        assert "imeout" in (report.servers[0].error or "")

    @pytest.mark.asyncio()
    async def test_exception_server(self) -> None:
        """Server that raises an exception is marked unhealthy."""
        session = AsyncMock()
        session.list_tools.side_effect = ConnectionError("refused")

        manager = MagicMock()
        manager.tools_by_server = {"broken": ["x"]}
        manager._servers = {}
        manager._clients = {"broken": {"session": session}}

        report = await health_check_servers(manager, timeout=5.0)

        assert report.servers[0].healthy is False
        assert report.servers[0].error == "refused"

    @pytest.mark.asyncio()
    async def test_no_session_found(self) -> None:
        """Server listed in tools_by_server but no session → unhealthy."""
        manager = MagicMock()
        manager.tools_by_server = {"ghost": ["x"]}
        manager._servers = {}
        manager._clients = {}

        report = await health_check_servers(manager, timeout=5.0)

        assert report.servers[0].healthy is False
        assert "No session" in (report.servers[0].error or "")

    @pytest.mark.asyncio()
    async def test_multiple_servers_mixed(self) -> None:
        """Mix of healthy and unhealthy servers."""
        good_session = AsyncMock()
        good_session.list_tools.return_value = FakeToolsResult(tools=["a"])

        bad_session = AsyncMock()
        bad_session.list_tools.side_effect = OSError("pipe broken")

        manager = MagicMock()
        manager.tools_by_server = {"good": ["a"], "bad": ["b"]}
        manager._servers = {}
        manager._clients = {
            "good": {"session": good_session},
            "bad": {"session": bad_session},
        }

        report = await health_check_servers(manager, timeout=5.0)

        assert report.healthy_count == 1
        assert report.any_unhealthy is True
        names = {s.name for s in report.servers}
        assert names == {"good", "bad"}


# ── reconnect_failed_servers tests ─────────────────────────────


class TestReconnectFailedServers:
    """Tests for reconnect_failed_servers()."""

    @pytest.mark.asyncio()
    async def test_no_servers_to_reconnect(self) -> None:
        """No errors → nothing to reconnect."""
        manager = MagicMock()
        manager.initialization_errors = {}
        registry = MagicMock()
        result = await reconnect_failed_servers(manager, registry)
        assert result == []

    @pytest.mark.asyncio()
    async def test_explicit_empty_server_names(self) -> None:
        """Explicit empty list → nothing to reconnect."""
        manager = MagicMock()
        registry = MagicMock()
        result = await reconnect_failed_servers(manager, registry, server_names=[])
        assert result == []

    @pytest.mark.asyncio()
    async def test_successful_reconnect_via_restart_server(self) -> None:
        """Successfully reconnects a server using restart_server."""
        session = AsyncMock()
        session.list_tools.return_value = FakeToolsResult(tools=["a"])

        manager = MagicMock()
        manager.initialization_errors = {"broken": {"error": "timeout"}}
        manager._servers = {"broken": MagicMock(session=session)}
        manager._clients = {}

        registry = AsyncMock()
        registry.restart_server = AsyncMock()

        result = await reconnect_failed_servers(
            manager, registry, server_names=["broken"], max_retries=1,
        )

        assert result == ["broken"]
        registry.restart_server.assert_awaited_once_with("broken")
        assert "broken" not in manager.initialization_errors

    @pytest.mark.asyncio()
    async def test_fallback_to_start_servers(self) -> None:
        """Falls back to start_servers() when restart_server is absent."""
        session = AsyncMock()
        session.list_tools.return_value = FakeToolsResult(tools=["a"])

        manager = MagicMock()
        manager.initialization_errors = {"dead": {"error": "gone"}}
        manager._servers = {"dead": MagicMock(session=session)}
        manager._clients = {}

        # Registry without restart_server or start_server attrs
        registry = AsyncMock(spec=["start_servers"])
        registry.start_servers = AsyncMock()

        result = await reconnect_failed_servers(
            manager, registry, server_names=["dead"], max_retries=1,
        )

        assert result == ["dead"]
        registry.start_servers.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_reconnect_fails_all_retries(self) -> None:
        """Server that fails all reconnect attempts is not in result."""
        manager = MagicMock()
        manager.initialization_errors = {"dead": {"error": "gone"}}
        manager._servers = {}
        manager._clients = {}

        registry = AsyncMock()
        registry.restart_server = AsyncMock(side_effect=ConnectionError("nope"))

        result = await reconnect_failed_servers(
            manager, registry, server_names=["dead"], max_retries=2, backoff_base=0.01,
        )

        assert result == []
        assert registry.restart_server.await_count == 2

    @pytest.mark.asyncio()
    async def test_reconnect_uses_initialization_errors_when_no_names(self) -> None:
        """Uses initialization_errors keys when server_names is None."""
        session = AsyncMock()
        session.list_tools.return_value = FakeToolsResult(tools=["a"])

        manager = MagicMock()
        manager.initialization_errors = {"srv1": {"error": "x"}}
        manager._servers = {"srv1": MagicMock(session=session)}
        manager._clients = {}

        registry = AsyncMock()
        registry.restart_server = AsyncMock()

        result = await reconnect_failed_servers(manager, registry, max_retries=1)

        assert "srv1" in result


# ── wrap_tools_with_retry tests ────────────────────────────────


class FakeTool:
    """Minimal tool for testing retry wrapper."""

    def __init__(self, name: str = "test_tool") -> None:
        self.name = name
        self._call_count = 0
        self._acall_count = 0

    def _run(self, *args: Any, **kwargs: Any) -> str:
        self._call_count += 1
        return f"ok-{self._call_count}"

    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        self._acall_count += 1
        return f"async-ok-{self._acall_count}"


class FailThenSucceedTool:
    """Tool that fails N times then succeeds."""

    def __init__(self, fail_count: int = 1) -> None:
        self.name = "flaky"
        self._call_count = 0
        self._acall_count = 0
        self._fail_count = fail_count

    def _run(self, *args: Any, **kwargs: Any) -> str:
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise ConnectionError(f"fail #{self._call_count}")
        return "success"

    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        self._acall_count += 1
        if self._acall_count <= self._fail_count:
            raise ConnectionError(f"async fail #{self._acall_count}")
        return "async-success"


class TestWrapToolsWithRetry:
    """Tests for wrap_tools_with_retry()."""

    def test_returns_same_list(self) -> None:
        """Returns the input list (mutated in-place)."""
        tools: list[Any] = [FakeTool()]
        result = wrap_tools_with_retry(tools, max_retries=1)
        assert result is tools

    def test_no_retry_on_success(self) -> None:
        """Successful call doesn't trigger retries."""
        tool = FakeTool()
        wrap_tools_with_retry([tool], max_retries=2, retry_delay=0.01)
        assert tool._run("hello") == "ok-1"
        assert tool._call_count == 1

    @pytest.mark.asyncio()
    async def test_no_retry_on_async_success(self) -> None:
        """Successful async call doesn't trigger retries."""
        tool = FakeTool()
        wrap_tools_with_retry([tool], max_retries=2, retry_delay=0.01)
        result = await tool._arun("hello")
        assert result == "async-ok-1"
        assert tool._acall_count == 1

    def test_sync_retry_on_connection_error(self) -> None:
        """Sync call retries on ConnectionError then succeeds."""
        tool = FailThenSucceedTool(fail_count=1)
        wrap_tools_with_retry([tool], max_retries=2, retry_delay=0.01)
        result = tool._run()
        assert result == "success"
        assert tool._call_count == 2

    @pytest.mark.asyncio()
    async def test_async_retry_on_connection_error(self) -> None:
        """Async call retries on ConnectionError then succeeds."""
        tool = FailThenSucceedTool(fail_count=1)
        wrap_tools_with_retry([tool], max_retries=2, retry_delay=0.01)
        result = await tool._arun()
        assert result == "async-success"
        assert tool._acall_count == 2

    def test_sync_exhausts_retries(self) -> None:
        """Sync call raises after all retries exhausted."""
        tool = FailThenSucceedTool(fail_count=10)
        wrap_tools_with_retry([tool], max_retries=2, retry_delay=0.01)
        with pytest.raises(ConnectionError, match="fail #3"):
            tool._run()
        assert tool._call_count == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio()
    async def test_async_exhausts_retries(self) -> None:
        """Async call raises after all retries exhausted."""
        tool = FailThenSucceedTool(fail_count=10)
        wrap_tools_with_retry([tool], max_retries=2, retry_delay=0.01)
        with pytest.raises(ConnectionError, match="async fail #3"):
            await tool._arun()
        assert tool._acall_count == 3

    def test_non_retryable_error_not_retried(self) -> None:
        """Non-retryable errors are raised immediately."""

        class BadTool:
            name = "bad"

            def _run(self) -> str:
                raise ValueError("not retryable")

        tool = BadTool()
        wrap_tools_with_retry([tool], max_retries=3, retry_delay=0.01)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="not retryable"):
            tool._run()

    def test_custom_retryable_errors(self) -> None:
        """Custom retryable error tuple is respected."""

        class CustomError(Exception):
            pass

        class CustomFailTool:
            name = "custom"
            call_count = 0

            def _run(self) -> str:
                self.call_count += 1
                if self.call_count <= 1:
                    raise CustomError("custom fail")
                return "ok"

        tool = CustomFailTool()
        wrap_tools_with_retry(
            [tool],  # type: ignore[arg-type]
            max_retries=2,
            retry_delay=0.01,
            retryable_errors=(CustomError,),
        )
        assert tool._run() == "ok"
        assert tool.call_count == 2

    def test_timeout_error_is_retryable(self) -> None:
        """TimeoutError is in the default retryable set."""

        class TimeoutTool:
            name = "timeout"
            call_count = 0

            def _run(self) -> str:
                self.call_count += 1
                if self.call_count <= 1:
                    raise TimeoutError("timed out")
                return "ok"

        tool = TimeoutTool()
        wrap_tools_with_retry([tool], max_retries=1, retry_delay=0.01)  # type: ignore[arg-type]
        assert tool._run() == "ok"

    def test_os_error_is_retryable(self) -> None:
        """OSError is in the default retryable set."""

        class OSTool:
            name = "os"
            call_count = 0

            def _run(self) -> str:
                self.call_count += 1
                if self.call_count <= 1:
                    raise OSError("broken pipe")
                return "ok"

        tool = OSTool()
        wrap_tools_with_retry([tool], max_retries=1, retry_delay=0.01)  # type: ignore[arg-type]
        assert tool._run() == "ok"

    def test_empty_tools_list(self) -> None:
        """Empty list is a no-op."""
        result = wrap_tools_with_retry([], max_retries=2)
        assert result == []

    def test_multiple_tools_all_wrapped(self) -> None:
        """Multiple tools are all wrapped."""
        t1 = FakeTool("t1")
        t2 = FakeTool("t2")
        wrap_tools_with_retry([t1, t2], max_retries=1, retry_delay=0.01)
        # Both should work fine
        assert t1._run() == "ok-1"
        assert t2._run() == "ok-1"

    def test_tool_without_run_methods(self) -> None:
        """Tool without _run/_arun is not wrapped (no crash)."""

        class BareObject:
            name = "bare"

        obj = BareObject()
        wrap_tools_with_retry([obj], max_retries=1)  # type: ignore[arg-type]
        # Should complete without error


# ── BuildLoop health check integration test ────────────────────


class TestBuildLoopHealthCheck:
    """Tests for BuildLoop._health_check_between_iterations."""

    @pytest.mark.asyncio()
    async def test_health_check_skipped_without_manager(self) -> None:
        """No manager → health check silently skips."""
        from cuga.build_loop import BuildLoop

        loop = BuildLoop(
            spec={"name": "test"},
            agent=MagicMock(),
            project_dir=Path("/tmp/test"),
        )
        # Should not raise
        await loop._health_check_between_iterations()

    @pytest.mark.asyncio()
    async def test_health_check_calls_resilience(self) -> None:
        """With manager + registry, health check calls resilience functions."""
        from cuga.build_loop import BuildLoop

        manager = MagicMock()
        manager.tools_by_server = {"fs": ["a"]}
        registry = MagicMock()

        loop = BuildLoop(
            spec={"name": "test"},
            agent=MagicMock(),
            project_dir=Path("/tmp/test"),
            mcp_manager=manager,
            mcp_registry=registry,
        )

        mock_report = HealthReport(
            servers=[ServerHealth(name="fs", healthy=True)],
        )

        with patch(
            "cuga.mcp_resilience.health_check_servers",
            new_callable=AsyncMock,
            return_value=mock_report,
        ) as mock_hc:
            await loop._health_check_between_iterations()
            mock_hc.assert_awaited_once_with(manager)

    @pytest.mark.asyncio()
    async def test_health_check_reconnects_unhealthy(self) -> None:
        """Unhealthy servers trigger reconnect attempt."""
        from cuga.build_loop import BuildLoop

        manager = MagicMock()
        manager.tools_by_server = {"dead": ["x"]}
        registry = MagicMock()

        loop = BuildLoop(
            spec={"name": "test"},
            agent=MagicMock(),
            project_dir=Path("/tmp/test"),
            mcp_manager=manager,
            mcp_registry=registry,
        )

        mock_report = HealthReport(
            servers=[ServerHealth(name="dead", healthy=False, error="gone")],
        )

        with (
            patch(
                "cuga.mcp_resilience.health_check_servers",
                new_callable=AsyncMock,
                return_value=mock_report,
            ),
            patch(
                "cuga.mcp_resilience.reconnect_failed_servers",
                new_callable=AsyncMock,
                return_value=["dead"],
            ) as mock_reconnect,
        ):
            await loop._health_check_between_iterations()
            mock_reconnect.assert_awaited_once_with(
                manager,
                registry,
                server_names=["dead"],
            )

    @pytest.mark.asyncio()
    async def test_health_check_exception_is_swallowed(self) -> None:
        """Exceptions during health check don't propagate."""
        from cuga.build_loop import BuildLoop

        manager = MagicMock()
        manager.tools_by_server = {"fs": []}
        registry = MagicMock()

        loop = BuildLoop(
            spec={"name": "test"},
            agent=MagicMock(),
            project_dir=Path("/tmp/test"),
            mcp_manager=manager,
            mcp_registry=registry,
        )

        with patch(
            "cuga.mcp_resilience.health_check_servers",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            # Should NOT raise
            await loop._health_check_between_iterations()
