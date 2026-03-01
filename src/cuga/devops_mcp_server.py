"""
IBM DevOps MCP Server — CI/CD pipeline management via IBM Cloud Toolchain API.

Exposes IBM Continuous Delivery / Tekton pipeline operations as MCP tools
so the build agent can create toolchains, pipelines, and trigger runs
after a successful build.

Required environment variables:
    IBMCLOUD_API_KEY  — IBM Cloud IAM API key
    IBMCLOUD_REGION   — Region (default: us-south)

Transport modes:
    stdio  (local):  ``python -m cuga.devops_mcp_server``
    HTTP   (Docker):  ``python -m cuga.devops_mcp_server --http --port 8000``
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Any

import httpx
from fastmcp import FastMCP
from loguru import logger

__all__ = ["create_devops_mcp"]

# ── IBM Cloud IAM token management ────────────────────────────

_iam_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}

IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"


async def _get_iam_token() -> str:
    """Obtain or refresh an IBM Cloud IAM bearer token.

    Returns:
        A valid IAM access token string.

    Raises:
        RuntimeError: If IBMCLOUD_API_KEY is not set or token request fails.
    """
    api_key = os.environ.get("IBMCLOUD_API_KEY", "")
    if not api_key:
        msg = "IBMCLOUD_API_KEY environment variable is not set"
        raise RuntimeError(msg)

    # Return cached token if still valid (with 60s buffer)
    if _iam_cache["token"] and time.time() < _iam_cache["expires_at"] - 60:
        return _iam_cache["token"]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            IAM_TOKEN_URL,
            data={
                "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                "apikey": api_key,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    _iam_cache["token"] = data["access_token"]
    _iam_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
    return _iam_cache["token"]


def _api_base() -> str:
    """Return the IBM Cloud DevOps API base URL for the configured region.

    Returns:
        The DevOps API base URL string.
    """
    region = os.environ.get("IBMCLOUD_REGION", "us-south")
    return f"https://api.{region}.devops.cloud.ibm.com"


async def _api_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Make an authenticated request to the IBM Cloud DevOps API.

    Args:
        method: HTTP method (GET, POST, DELETE, etc.).
        path: API path (appended to the base URL).
        json_body: Optional JSON request body.
        params: Optional query parameters.

    Returns:
        Parsed JSON response as a dict.

    Raises:
        httpx.HTTPStatusError: If the API returns an error status.
    """
    token = await _get_iam_token()
    url = f"{_api_base()}{path}"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(
            method,
            url,
            json=json_body,
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}


# ── MCP Server ─────────────────────────────────────────────────

mcp = FastMCP(
    name="devops",
    instructions=(
        "IBM Cloud DevOps MCP server for CI/CD pipeline management. "
        "Create toolchains, configure Tekton pipelines, trigger runs, "
        "and check pipeline status. Requires IBMCLOUD_API_KEY."
    ),
)


@mcp.tool()
async def create_toolchain(
    name: str,
    resource_group_id: str,
    description: str = "",
) -> str:
    """Create a new IBM Cloud toolchain.

    Args:
        name: Toolchain name (e.g. "my-app-toolchain").
        resource_group_id: IBM Cloud resource group ID.
        description: Optional description.

    Returns:
        JSON string with the created toolchain details including its ID.
    """
    result = await _api_request(
        "POST",
        "/toolchain/v2/toolchains",
        json_body={
            "name": name,
            "description": description or f"Toolchain for {name}",
            "resource_group_id": resource_group_id,
        },
    )
    tc_id = result.get("id", "unknown")
    logger.info("Created toolchain '{}' ({})", name, tc_id)
    return _format_response(result)


@mcp.tool()
async def list_toolchains(resource_group_id: str) -> str:
    """List all toolchains in the given resource group.

    Args:
        resource_group_id: IBM Cloud resource group ID.

    Returns:
        JSON string with the list of toolchains.
    """
    result = await _api_request(
        "GET",
        "/toolchain/v2/toolchains",
        params={"resource_group_id": resource_group_id},
    )
    return _format_response(result)


@mcp.tool()
async def create_tekton_pipeline(
    toolchain_id: str,
    pipeline_name: str,
    repo_url: str,
    branch: str = "main",
) -> str:
    """Create a Tekton pipeline in an existing toolchain.

    Args:
        toolchain_id: ID of the toolchain to add the pipeline to.
        pipeline_name: Name for the pipeline.
        repo_url: Git repository URL to link.
        branch: Branch to build from (default: "main").

    Returns:
        JSON string with the created pipeline details including its ID.
    """
    # Step 1: Create the pipeline definition
    result = await _api_request(
        "POST",
        "/pipeline/v2/tekton_pipelines",
        json_body={
            "name": pipeline_name,
            "worker": {"type": "private"},
            "resource_group": {"id": toolchain_id},
        },
    )
    pipeline_id = result.get("id", "unknown")

    # Step 2: Add Git trigger
    try:
        await _api_request(
            "POST",
            f"/pipeline/v2/tekton_pipelines/{pipeline_id}/triggers",
            json_body={
                "type": "scm",
                "name": f"{pipeline_name}-git-trigger",
                "event_listener": "listener",
                "source": {
                    "type": "git",
                    "properties": {
                        "url": repo_url,
                        "branch": branch,
                    },
                },
                "events": ["push", "pull_request"],
            },
        )
    except httpx.HTTPStatusError:
        logger.warning("Could not add Git trigger to pipeline {}", pipeline_id)

    logger.info("Created Tekton pipeline '{}' ({})", pipeline_name, pipeline_id)
    return _format_response(result)


@mcp.tool()
async def run_pipeline(pipeline_id: str, branch: str = "main") -> str:
    """Trigger a manual pipeline run.

    Args:
        pipeline_id: ID of the Tekton pipeline run.
        branch: Branch to build (default: "main").

    Returns:
        JSON string with the pipeline run details including run ID and status.
    """
    result = await _api_request(
        "POST",
        f"/pipeline/v2/tekton_pipelines/{pipeline_id}/pipeline_runs",
        json_body={
            "trigger": {
                "name": "Manual Trigger",
                "properties": {"branch": branch},
            },
        },
    )
    run_id = result.get("id", "unknown")
    logger.info("Triggered pipeline run {} on branch '{}'", run_id, branch)
    return _format_response(result)


@mcp.tool()
async def get_pipeline_run_status(pipeline_id: str, run_id: str) -> str:
    """Get the status of a pipeline run.

    Args:
        pipeline_id: ID of the Tekton pipeline.
        run_id: ID of the specific pipeline run.

    Returns:
        JSON string with the run status, start time, duration, and result.
    """
    result = await _api_request(
        "GET",
        f"/pipeline/v2/tekton_pipelines/{pipeline_id}/pipeline_runs/{run_id}",
    )
    status = result.get("status", {}).get("state", "unknown")
    logger.info("Pipeline run {} status: {}", run_id, status)
    return _format_response(result)


@mcp.tool()
async def list_pipeline_runs(pipeline_id: str, limit: int = 10) -> str:
    """List recent pipeline runs.

    Args:
        pipeline_id: ID of the Tekton pipeline.
        limit: Maximum number of runs to return (default: 10).

    Returns:
        JSON string with the list of recent pipeline runs.
    """
    result = await _api_request(
        "GET",
        f"/pipeline/v2/tekton_pipelines/{pipeline_id}/pipeline_runs",
        params={"limit": str(limit)},
    )
    return _format_response(result)


@mcp.tool()
async def delete_toolchain(toolchain_id: str) -> str:
    """Delete a toolchain and all its integrations.

    Args:
        toolchain_id: ID of the toolchain to delete.

    Returns:
        Confirmation message.
    """
    await _api_request("DELETE", f"/toolchain/v2/toolchains/{toolchain_id}")
    logger.info("Deleted toolchain {}", toolchain_id)
    return f"Toolchain {toolchain_id} deleted successfully."


# ── Helpers ────────────────────────────────────────────────────


def _format_response(data: dict[str, Any]) -> str:
    """Format API response as a readable string.

    Args:
        data: The API response dictionary.

    Returns:
        Formatted string representation.
    """
    import json

    return json.dumps(data, indent=2, default=str)


# ── Entry point ────────────────────────────────────────────────


def create_devops_mcp() -> FastMCP:
    """Return the configured FastMCP server instance.

    Returns:
        The DevOps MCP server, ready to run.
    """
    return mcp


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the DevOps MCP server.

    Args:
        argv: Command-line arguments.

    Returns:
        Parsed namespace with transport configuration.
    """
    parser = argparse.ArgumentParser(
        description="IBM DevOps MCP Server — CI/CD pipeline management",
    )
    parser.add_argument("--http", action="store_true", help="Run in HTTP mode")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the DevOps MCP server.

    Args:
        argv: Command-line arguments.
    """
    args = _parse_args(argv)

    if args.http:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
