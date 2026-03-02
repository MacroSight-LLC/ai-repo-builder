"""
Create LangChain StructuredTool objects from an MCPManager's live
connections, bypassing the registry HTTP proxy entirely.

Usage:
    tools = create_tools_from_mcp_manager(manager)
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import create_model


def _build_pydantic_model(tool_name: str, parameters: dict):
    """Build a Pydantic model from the MCP tool's JSON-schema parameters."""
    props = parameters.get("properties", {})
    required = set(parameters.get("required", []))
    fields: dict[str, Any] = {}
    for pname, pschema in props.items():
        ptype = pschema.get("type", "string")
        py_type = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }.get(ptype, str)
        default = ... if pname in required else pschema.get("default", None)
        fields[pname] = (py_type, default)
    if not fields:
        fields["_placeholder"] = (str, "")
    return create_model(f"{tool_name}_Input", **fields)


def create_tools_from_mcp_manager(manager) -> list[StructuredTool]:
    """
    Iterate over every tool in ``manager.tools_by_server`` and wrap each
    one in a LangChain ``StructuredTool`` whose callback invokes
    ``manager.call_tool()`` directly.
    """
    tools: list[StructuredTool] = []

    for _server_name, tool_defs in manager.tools_by_server.items():
        for tool_def in tool_defs:
            fn = tool_def.get("function", tool_def)
            name = fn["name"]
            description = fn.get("description", "") or name
            parameters = fn.get("parameters", {})

            # Sanitize name → valid Python identifier (CodeAct sandbox
            # injects tools as local variables; dashes break exec()).
            safe_name = name.replace("-", "_")

            args_schema = _build_pydantic_model(safe_name, parameters)

            # Ordered param names so we can map positional args
            param_names = list(parameters.get("properties", {}).keys())

            # Each closure captures its own `name` and `param_names`
            def _make_funcs(tool_name: str, pnames: list[str]):
                async def _call(*args, **kwargs):
                    # Map positional args to keyword args using param order
                    for i, val in enumerate(args):
                        if i < len(pnames):
                            kwargs[pnames[i]] = val
                    kwargs.pop("_placeholder", None)
                    result = await manager.call_tool(tool_name, kwargs)
                    if result and hasattr(result[0], "text"):
                        return result[0].text
                    return str(result)

                def _sync_call(*args, **kwargs):
                    for i, val in enumerate(args):
                        if i < len(pnames):
                            kwargs[pnames[i]] = val
                    kwargs.pop("_placeholder", None)
                    try:
                        asyncio.get_running_loop()
                        # Already inside an event loop — run in a thread
                        import concurrent.futures

                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            return pool.submit(asyncio.run, _call(**kwargs)).result()
                    except RuntimeError:
                        return asyncio.run(_call(**kwargs))

                return _call, _sync_call

            # _make_funcs uses the ORIGINAL name for manager.call_tool()
            # (MCP servers expect the original tool name), but StructuredTool
            # gets the sanitized safe_name for CodeAct sandbox injection.
            async_fn, sync_fn = _make_funcs(name, param_names)

            tool = StructuredTool(
                name=safe_name,
                description=description[:1024],
                args_schema=args_schema,
                func=sync_fn,
                coroutine=async_fn,
            )
            tools.append(tool)

    return tools
