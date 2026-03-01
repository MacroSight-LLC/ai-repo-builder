"""MCP server lifecycle manager with timeouts and graceful degradation."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

__all__ = ["MCPManager"]


class MCPManager:
    """Manages connections to MCP tool servers.

    Handles startup, health checks, graceful degradation when servers
    are unavailable, and clean shutdown.

    Args:
        config: MCP server configuration dictionary.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._servers: dict[str, Any] = config.get(
            "mcpServers", config.get("servers", {})
        )
        self._clients: dict[str, Any] = {}
        self._failed: list[str] = []

    async def connect_all(
        self,
        timeout_per_server: float = 30.0,
    ) -> None:
        """Connect to all configured MCP servers.

        Servers that fail to connect are added to the failed list
        but don't block other servers from connecting.

        Args:
            timeout_per_server: Max seconds to wait per server.

        Raises:
            ConnectionError: If ALL servers fail to connect.
        """
        for name, server_config in self._servers.items():
            try:
                await asyncio.wait_for(
                    self._connect_one(name, server_config),
                    timeout=timeout_per_server,
                )
                # Verify the server responds
                await asyncio.wait_for(
                    self._ping(name),
                    timeout=10.0,
                )
                logger.info("MCP connected: {}", name)
            except TimeoutError:
                logger.warning(
                    "MCP timeout: {} ({}s) — skipping", name, timeout_per_server
                )
                self._failed.append(name)
            except Exception as exc:  # graceful degradation
                logger.warning("MCP unavailable: {} — {}", name, exc)
                self._failed.append(name)

        connected = len(self._clients)
        failed = len(self._failed)
        logger.info(
            "MCP: {}/{} connected, {} failed", connected, connected + failed, failed
        )

        if connected == 0 and len(self._servers) > 0:
            msg = (
                f"All {failed} MCP servers failed to connect. "
                f"Failed: {', '.join(self._failed)}"
            )
            raise ConnectionError(msg)

    async def _connect_one(self, name: str, config: dict[str, Any]) -> None:
        """Connect to a single MCP server.

        Args:
            name: Server identifier.
            config: Server configuration (command, args, env).
        """
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise ImportError(
                "MCP client library not installed. Run: pip install mcp"
            ) from exc

        command = config.get("command", "")
        args = config.get("args", [])
        env = config.get("env")

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
        )

        # Store the context manager itself so we can __aexit__ it on disconnect
        transport_cm = stdio_client(server_params)
        transport = await transport_cm.__aenter__()
        try:
            read, write = transport
            session = ClientSession(read, write)
            await session.__aenter__()
            try:
                await session.initialize()
            except Exception:
                await session.__aexit__(None, None, None)
                raise
        except Exception:
            await transport_cm.__aexit__(None, None, None)
            raise

        self._clients[name] = {
            "session": session,
            "transport_cm": transport_cm,
            "config": config,
        }

    async def _ping(self, name: str) -> None:
        """Verify an MCP server responds by listing its tools.

        Args:
            name: Server identifier.

        Raises:
            ConnectionError: If the server doesn't respond.
        """
        client = self._clients.get(name)
        if not client:
            msg = f"No client for server: {name}"
            raise ConnectionError(msg)

        try:
            tools = await client["session"].list_tools()
            tool_count = len(tools.tools) if hasattr(tools, "tools") else 0
            logger.debug("{}: {} tools available", name, tool_count)
        except Exception as exc:
            # Clean up the dead client's resources before removing
            client_data = self._clients.pop(name, None)
            if client_data:
                try:
                    session = client_data.get("session")
                    if session:
                        await session.__aexit__(None, None, None)
                    transport_cm = client_data.get("transport_cm")
                    if transport_cm and hasattr(transport_cm, "__aexit__"):
                        await transport_cm.__aexit__(None, None, None)
                except Exception:  # best-effort cleanup
                    pass
            msg = f"{name} connected but not responding: {exc}"
            raise ConnectionError(msg) from exc

    async def disconnect_all(self) -> None:
        """Disconnect all MCP servers gracefully."""
        for name, client in list(self._clients.items()):
            try:
                session = client.get("session")
                if session:
                    await session.__aexit__(None, None, None)
            except Exception as exc:  # best-effort cleanup
                logger.warning("Error closing session for {}: {}", name, exc)
            try:
                transport_cm = client.get("transport_cm")
                if transport_cm and hasattr(transport_cm, "__aexit__"):
                    await transport_cm.__aexit__(None, None, None)
                logger.debug("Disconnected: {}", name)
            except Exception as exc:  # best-effort cleanup
                logger.warning("Error closing transport for {}: {}", name, exc)
            finally:
                self._clients.pop(name, None)

    def get_session(self, name: str) -> Any:
        """Get an MCP client session by server name.

        Args:
            name: Server identifier.

        Returns:
            The MCP client session.

        Raises:
            KeyError: If the server is not connected.
        """
        client = self._clients.get(name)
        if not client:
            available = list(self._clients.keys())
            msg = f"MCP server '{name}' not connected. Available: {available}"
            raise KeyError(msg)
        return client["session"]

    @property
    def connected_servers(self) -> list[str]:
        """List of connected server names."""
        return list(self._clients.keys())

    @property
    def failed_servers(self) -> list[str]:
        """List of servers that failed to connect."""
        return list(self._failed)

    @property
    def is_any_connected(self) -> bool:
        """Whether at least one server is connected."""
        return len(self._clients) > 0
