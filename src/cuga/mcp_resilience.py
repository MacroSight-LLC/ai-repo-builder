"""
MCP Resilience — Retry, reconnect, and health-check middleware for MCP tools.

Provides:
- ``resilient_tool_wrapper()``: wraps a LangChain tool with retry-on-failure
- ``health_check_servers()``: pings MCP servers to verify they're responsive
- ``reconnect_failed_servers()``: attempts to reconnect dead servers

These are designed to be called between build-loop iterations so that
transient MCP server failures don't permanently break a build.

Usage::

    from cuga.mcp_resilience import health_check_servers, wrap_tools_with_retry

    # At build-loop startup
    tools = wrap_tools_with_retry(tools, max_retries=2)

    # Between iterations
    report = await health_check_servers(manager)
    if report.any_unhealthy:
        await reconnect_failed_servers(manager, registry)
"""

from __future__ import annotations

import asyncio
import functools
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

__all__ = [
    "HealthReport",
    "ServerHealth",
    "health_check_servers",
    "reconnect_failed_servers",
    "wrap_tools_with_retry",
]


# ── Health checks ──────────────────────────────────────────────


@dataclass
class ServerHealth:
    """Health status for a single MCP server.

    Attributes:
        name: Server identifier.
        healthy: Whether the server responded to a health check.
        tool_count: Number of tools the server reported (0 if unhealthy).
        latency_ms: Response time in milliseconds (-1 if unhealthy).
        error: Error message if the health check failed.
    """

    name: str
    healthy: bool
    tool_count: int = 0
    latency_ms: float = -1
    error: str | None = None


@dataclass
class HealthReport:
    """Aggregate health report for all MCP servers.

    Attributes:
        servers: Per-server health status.
        checked_at: Unix timestamp of the check.
    """

    servers: list[ServerHealth] = field(default_factory=list)
    checked_at: float = 0.0

    @property
    def any_unhealthy(self) -> bool:
        """Whether any checked server is unhealthy."""
        return any(not s.healthy for s in self.servers)

    @property
    def all_healthy(self) -> bool:
        """Whether all checked servers are healthy."""
        return all(s.healthy for s in self.servers) and len(self.servers) > 0

    @property
    def healthy_count(self) -> int:
        """Number of healthy servers."""
        return sum(1 for s in self.servers if s.healthy)

    @property
    def unhealthy_names(self) -> list[str]:
        """Names of unhealthy servers."""
        return [s.name for s in self.servers if not s.healthy]


async def health_check_servers(
    manager: Any,
    timeout: float = 10.0,
) -> HealthReport:
    """Ping all connected MCP servers to verify they're responsive.

    Uses ``list_tools()`` as a lightweight health probe — if the server
    can enumerate its tools, it's alive.

    Args:
        manager: The MCPManager instance (registry-based).
        timeout: Max seconds to wait per server.

    Returns:
        A ``HealthReport`` with per-server status.
    """
    report = HealthReport(checked_at=time.time())

    # The registry MCPManager stores sessions in tools_by_server
    server_names = list(getattr(manager, "tools_by_server", {}).keys())
    if not server_names:
        return report

    for name in server_names:
        t0 = time.time()
        try:
            session = _get_session(manager, name)
            if session is None:
                report.servers.append(
                    ServerHealth(name=name, healthy=False, error="No session found")
                )
                continue

            tools_result = await asyncio.wait_for(
                session.list_tools(),
                timeout=timeout,
            )
            tool_count = len(tools_result.tools) if hasattr(tools_result, "tools") else 0
            latency = (time.time() - t0) * 1000

            report.servers.append(
                ServerHealth(
                    name=name,
                    healthy=True,
                    tool_count=tool_count,
                    latency_ms=round(latency, 1),
                )
            )
        except TimeoutError:
            report.servers.append(
                ServerHealth(
                    name=name,
                    healthy=False,
                    error=f"Timeout after {timeout}s",
                )
            )
        except Exception as exc:
            report.servers.append(ServerHealth(name=name, healthy=False, error=str(exc)))

    healthy = report.healthy_count
    total = len(report.servers)
    if report.any_unhealthy:
        logger.warning(
            "MCP health: {}/{} healthy, unhealthy: {}",
            healthy,
            total,
            ", ".join(report.unhealthy_names),
        )
    else:
        logger.debug("MCP health: {}/{} healthy", healthy, total)

    return report


def _get_session(manager: Any, server_name: str) -> Any | None:
    """Extract the MCP client session for a named server.

    Handles both the registry-based MCPManager (mcp_transports)
    and the standalone MCPManager (_clients).

    Args:
        manager: The MCPManager instance.
        server_name: Server identifier.

    Returns:
        The client session, or None if not found.
    """
    # Registry-based MCPManager stores transports in mcp_transports dict
    transports = getattr(manager, "mcp_transports", {})
    if server_name in transports:
        transport = transports[server_name]
        # Transport objects may expose a session
        session = getattr(transport, "session", None)
        if session is not None:
            return session
        # The transport itself can be used for health pinging
        return transport

    # Also check the public .servers dict (OpenAPI-based servers)
    servers_dict = getattr(manager, "servers", {})
    if server_name in servers_dict:
        server_obj = servers_dict[server_name]
        session = getattr(server_obj, "session", None)
        if session is not None:
            return session

    # Standalone MCPManager stores in _clients dict
    clients = getattr(manager, "_clients", {})
    if server_name in clients:
        client_data = clients[server_name]
        if isinstance(client_data, dict):
            return client_data.get("session")
        return getattr(client_data, "session", None)

    return None


# ── Reconnect ──────────────────────────────────────────────────


async def reconnect_failed_servers(
    manager: Any,
    registry: Any,
    server_names: list[str] | None = None,
    max_retries: int = 2,
    backoff_base: float = 2.0,
) -> list[str]:
    """Attempt to restart failed MCP servers.

    Uses exponential backoff between retry attempts. Only restarts
    servers that are currently dead — healthy servers are not touched.

    Args:
        manager: The MCPManager instance.
        registry: The ApiRegistry instance.
        server_names: Specific servers to restart. If None, restarts
            all servers from the manager's initialization_errors.
        max_retries: Maximum reconnection attempts per server.
        backoff_base: Base delay in seconds (doubled each retry).

    Returns:
        List of server names that were successfully reconnected.
    """
    if server_names is None:
        errors = getattr(manager, "initialization_errors", {})
        server_names = list(errors.keys())

    if not server_names:
        return []

    reconnected: list[str] = []

    for name in server_names:
        delay = backoff_base
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    "Reconnecting MCP server '{}' (attempt {}/{})",
                    name,
                    attempt,
                    max_retries,
                )

                # Use registry's per-server restart if available
                if hasattr(registry, "restart_server"):
                    await registry.restart_server(name)
                elif hasattr(registry, "start_server"):
                    await registry.start_server(name)
                else:
                    # Fallback: restart all (less ideal but works)
                    await registry.start_servers()

                # Verify the server is now responsive
                session = _get_session(manager, name)
                if session is not None:
                    await asyncio.wait_for(session.list_tools(), timeout=10.0)

                logger.info("Reconnected MCP server '{}'", name)
                reconnected.append(name)

                # Clear from initialization_errors
                errors = getattr(manager, "initialization_errors", {})
                errors.pop(name, None)
                break

            except Exception as exc:
                logger.warning(
                    "Reconnect attempt {}/{} for '{}' failed: {}",
                    attempt,
                    max_retries,
                    name,
                    exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2  # Exponential backoff

    if reconnected:
        logger.info("Reconnected {} MCP server(s): {}", len(reconnected), ", ".join(reconnected))

    return reconnected


# ── Tool retry wrapper ─────────────────────────────────────────


def wrap_tools_with_retry(
    tools: list[Any],
    max_retries: int = 2,
    retry_delay: float = 1.0,
    retryable_errors: tuple[type[Exception], ...] | None = None,
) -> list[Any]:
    """Wrap each tool's invoke/ainvoke with retry-on-failure logic.

    When a tool call fails with a retryable error (connection closed,
    timeout, etc.), the wrapper retries with exponential backoff before
    giving up. This makes MCP tool calls resilient to transient server
    failures.

    Args:
        tools: List of LangChain-compatible tools.
        max_retries: Maximum retry attempts per tool call.
        retry_delay: Initial delay between retries in seconds.
        retryable_errors: Tuple of exception types to retry on.
            Defaults to common connection/timeout errors.

    Returns:
        The same tools list with retry wrappers applied (mutates in-place).
    """
    if retryable_errors is None:
        retryable_errors = (
            ConnectionError,
            TimeoutError,
            OSError,
        )
        # Add anyio/trio ClosedResourceError if available
        try:
            from anyio import ClosedResourceError

            retryable_errors = (*retryable_errors, ClosedResourceError)
        except ImportError:
            pass

    for tool in tools:
        _wrap_single_tool(tool, max_retries, retry_delay, retryable_errors)

    return tools


def _wrap_single_tool(
    tool: Any,
    max_retries: int,
    retry_delay: float,
    retryable_errors: tuple[type[Exception], ...],
) -> None:
    """Wrap a single tool's invoke methods with retry logic.

    Args:
        tool: A LangChain-compatible tool.
        max_retries: Maximum retry attempts.
        retry_delay: Initial delay between retries.
        retryable_errors: Exception types that trigger a retry.
    """
    # Wrap _run (sync invoke)
    if hasattr(tool, "_run"):
        original_run = tool._run

        @functools.wraps(original_run)
        def retrying_run(*args: Any, **kwargs: Any) -> Any:
            delay = retry_delay
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return original_run(*args, **kwargs)
                except retryable_errors as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        logger.warning(
                            "Tool '{}' failed (attempt {}/{}): {} — retrying in {:.1f}s",
                            getattr(tool, "name", "?"),
                            attempt + 1,
                            max_retries + 1,
                            exc,
                            delay,
                        )
                        time.sleep(delay)
                        delay *= 2
                    else:
                        raise
            raise last_exc  # type: ignore[misc]  # pragma: no cover

        tool._run = retrying_run

    # Wrap _arun (async invoke)
    if hasattr(tool, "_arun"):
        original_arun = tool._arun

        @functools.wraps(original_arun)
        async def retrying_arun(*args: Any, **kwargs: Any) -> Any:
            delay = retry_delay
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await original_arun(*args, **kwargs)
                except retryable_errors as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        logger.warning(
                            "Tool '{}' failed (attempt {}/{}): {} — retrying in {:.1f}s",
                            getattr(tool, "name", "?"),
                            attempt + 1,
                            max_retries + 1,
                            exc,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        delay *= 2
                    else:
                        raise
            raise last_exc  # type: ignore[misc]  # pragma: no cover

        tool._arun = retrying_arun
