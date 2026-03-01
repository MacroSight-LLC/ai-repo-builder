"""
MCP Direct Tool Provider

Creates LangChain StructuredTool objects that call MCP tools directly
through the MCPManager's live client connections, bypassing the registry HTTP layer.
"""

import json
from typing import List, Dict, Any, Optional
from loguru import logger
from pydantic import create_model, Field
from langchain_core.tools import StructuredTool

from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import (
    ToolProviderInterface,
    AppDefinition,
)


def create_tool_from_mcp_manager(
    tool_name: str,
    tool_def: Dict[str, Any],
    app_name: str,
    mcp_manager: MCPManager,
) -> StructuredTool:
    """Create a StructuredTool that calls an MCP tool directly via MCPManager.call_tool().

    Args:
        tool_name: Prefixed tool name (e.g. "filesystem_read_file")
        tool_def: Tool definition dict from MCPManager.get_apis_for_application()
        app_name: Server/app name (e.g. "filesystem")
        mcp_manager: The live MCPManager instance with connected transports

    Returns:
        StructuredTool that invokes the MCP tool directly (no HTTP proxy)
    """
    description = tool_def.get('description', '')
    parameters = tool_def.get('parameters', {})
    response_schemas = tool_def.get('response_schemas', {})

    # Convert OpenAPI parameter list format to JSON schema if needed
    if isinstance(parameters, list):
        parameters = _convert_params_to_json_schema(parameters)

    # Build pydantic model for the tool's input schema
    field_definitions = {}
    if isinstance(parameters, dict) and 'properties' in parameters:
        props = parameters['properties']
        required = parameters.get('required', [])
        for param_name, param_schema in props.items():
            param_type = param_schema.get('type', 'string')
            param_desc = param_schema.get('description', '')

            if isinstance(param_type, list):
                param_type = next((t for t in param_type if t != 'null'), 'string')

            type_mapping = {
                'string': str,
                'integer': int,
                'number': float,
                'boolean': bool,
                'array': list,
                'object': dict,
            }
            python_type = type_mapping.get(param_type, str)

            if param_name in required:
                field_definitions[param_name] = (python_type, Field(..., description=param_desc))
            else:
                default_val = param_schema.get('default', None)
                if isinstance(default_val, list):
                    default_val = None
                field_definitions[param_name] = (
                    python_type,
                    Field(default=default_val, description=param_desc),
                )

    if field_definitions:
        InputModel = create_model(f"{tool_name}Input", **field_definitions)
    else:
        InputModel = create_model(f"{tool_name}Input")

    # Capture references in closure
    _tool_name = tool_name
    _app_name = app_name
    _manager = mcp_manager

    async def tool_func(**kwargs):
        """Call the MCP tool directly through MCPManager.call_tool()."""
        try:
            result = await _manager.call_tool(_tool_name, kwargs)
            # call_tool returns List[TextContent]; extract text
            if isinstance(result, list) and len(result) > 0:
                text = result[0].text if hasattr(result[0], 'text') else str(result[0])
                # Try to parse as JSON for structured output
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return text
            return str(result)
        except Exception as e:
            error_msg = f"Error calling {_tool_name}: {str(e)}"
            logger.error(error_msg)
            return {"error": error_msg}

    tool_func.__name__ = tool_name
    tool_func.__doc__ = description

    tool = StructuredTool.from_function(
        func=tool_func,
        name=tool_name,
        description=description,
        args_schema=InputModel,
        coroutine=tool_func,
    )

    # Attach func for CodeAct compatibility
    tool.func = tool_func

    if response_schemas:
        tool.func._response_schemas = response_schemas

    tool.func._app_name = app_name

    return tool


def create_all_tools_from_mcp_manager(
    mcp_manager: MCPManager,
    include_response_schema: bool = False,
) -> List[StructuredTool]:
    """Create LangChain StructuredTool objects for ALL tools in the MCPManager.

    Args:
        mcp_manager: The live MCPManager instance with connected transports
        include_response_schema: Whether to include response schemas in tool metadata

    Returns:
        List of StructuredTool objects that call MCP tools directly
    """
    tools = []
    all_apis = mcp_manager.get_all_apis(include_response_schema=include_response_schema)

    for app_name, api_dicts in all_apis.items():
        if not isinstance(api_dicts, dict):
            continue
        for tool_name, tool_def in api_dicts.items():
            try:
                tool = create_tool_from_mcp_manager(
                    tool_name=tool_name,
                    tool_def=tool_def,
                    app_name=app_name,
                    mcp_manager=mcp_manager,
                )
                tools.append(tool)
                logger.debug(f"Created direct MCP tool: {tool_name}")
            except Exception as e:
                logger.warning(f"Failed to create tool '{tool_name}': {e}")

    return tools


def _convert_params_to_json_schema(parameters: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Convert OpenAPI parameter list format to JSON schema format."""
    if not isinstance(parameters, list):
        return parameters

    properties = {}
    required = []

    for param in parameters:
        param_name = param.get('name', '')
        if not param_name:
            continue
        properties[param_name] = {
            'type': param.get('type', 'string'),
            'description': param.get('description', ''),
        }
        default_val = param.get('default')
        if default_val is not None:
            properties[param_name]['default'] = default_val
        if param.get('required', False):
            required.append(param_name)

    return {'properties': properties, 'required': required}


class MCPDirectToolProvider(ToolProviderInterface):
    """
    Tool provider that creates LangChain tools backed by MCPManager.call_tool() directly.

    No HTTP registry server needed — tools call through the live MCP client connections.

    Usage:
        services = load_service_configs(yaml_path)
        manager = MCPManager(config=services)
        registry = ApiRegistry(client=manager)
        await registry.start_servers()

        provider = MCPDirectToolProvider(mcp_manager=manager)
        await provider.initialize()
        tools = await provider.get_all_tools()
    """

    def __init__(self, mcp_manager: MCPManager):
        self.mcp_manager = mcp_manager
        self.initialized = False
        self._tools: List[StructuredTool] = []
        self._tools_by_app: Dict[str, List[StructuredTool]] = {}

    async def initialize(self):
        """Build StructuredTool objects from all connected MCP servers."""
        all_apis = self.mcp_manager.get_all_apis(include_response_schema=True)

        for app_name, api_dicts in all_apis.items():
            if not isinstance(api_dicts, dict):
                continue
            app_tools = []
            for tool_name, tool_def in api_dicts.items():
                try:
                    tool = create_tool_from_mcp_manager(
                        tool_name=tool_name,
                        tool_def=tool_def,
                        app_name=app_name,
                        mcp_manager=self.mcp_manager,
                    )
                    app_tools.append(tool)
                    self._tools.append(tool)
                except Exception as e:
                    logger.warning(f"Failed to create direct tool '{tool_name}': {e}")

            self._tools_by_app[app_name] = app_tools

        self.initialized = True
        logger.info(
            f"MCPDirectToolProvider initialized with {len(self._tools)} tools "
            f"from {len(self._tools_by_app)} apps"
        )

    async def get_apps(self) -> List[AppDefinition]:
        if not self.initialized:
            await self.initialize()
        return [
            AppDefinition(
                name=name,
                url=None,
                description=f"MCP server ({len(tools)} tools)",
                type="mcp_direct",
            )
            for name, tools in self._tools_by_app.items()
        ]

    async def get_tools(self, app_name: str) -> List[StructuredTool]:
        if not self.initialized:
            await self.initialize()
        return self._tools_by_app.get(app_name, [])

    async def get_all_tools(self) -> List[StructuredTool]:
        if not self.initialized:
            await self.initialize()
        return self._tools
