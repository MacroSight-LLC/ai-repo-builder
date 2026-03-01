"""
IBM Instana MCP Server — APM observability via Instana REST API.

Exposes Instana monitoring operations as MCP tools so the build agent
can set up application perspectives, check health, and configure alerts
after deploying a generated application.

Required environment variables:
    INSTANA_API_TOKEN — Instana API token
    INSTANA_BASE_URL  — Instana tenant URL (e.g. https://your-tenant.instana.io)

Transport modes:
    stdio  (local):  ``python -m cuga.instana_mcp_server``
    HTTP   (Docker):  ``python -m cuga.instana_mcp_server --http --port 8000``
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import httpx
from fastmcp import FastMCP
from loguru import logger

__all__ = ["create_instana_mcp"]


# ── Instana API client ────────────────────────────────────────


def _base_url() -> str:
    """Return the Instana base URL from environment.

    Returns:
        The Instana API base URL string.

    Raises:
        RuntimeError: If INSTANA_BASE_URL is not set.
    """
    url = os.environ.get("INSTANA_BASE_URL", "")
    if not url:
        msg = "INSTANA_BASE_URL environment variable is not set"
        raise RuntimeError(msg)
    return url.rstrip("/")


def _api_token() -> str:
    """Return the Instana API token from environment.

    Returns:
        The API token string.

    Raises:
        RuntimeError: If INSTANA_API_TOKEN is not set.
    """
    token = os.environ.get("INSTANA_API_TOKEN", "")
    if not token:
        msg = "INSTANA_API_TOKEN environment variable is not set"
        raise RuntimeError(msg)
    return token


async def _api_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Make an authenticated request to the Instana REST API.

    Args:
        method: HTTP method (GET, POST, PUT, DELETE).
        path: API path (appended to the base URL).
        json_body: Optional JSON request body.
        params: Optional query parameters.

    Returns:
        Parsed JSON response as a dict.

    Raises:
        httpx.HTTPStatusError: If the API returns an error status.
    """
    url = f"{_base_url()}/api{path}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            method,
            url,
            json=json_body,
            params=params,
            headers={
                "Authorization": f"apiToken {_api_token()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}


# ── MCP Server ─────────────────────────────────────────────────

mcp = FastMCP(
    name="instana",
    instructions=(
        "IBM Instana APM observability server. "
        "Monitor application health, view metrics, manage alerts, "
        "and create application perspectives. Requires INSTANA_API_TOKEN "
        "and INSTANA_BASE_URL."
    ),
)


@mcp.tool()
async def create_application_perspective(
    name: str,
    match_key: str = "service.name",
    match_value: str = "",
) -> str:
    """Create an Instana application perspective for monitoring.

    An application perspective groups related services/endpoints
    so you can view their metrics together.

    Args:
        name: Perspective name (e.g. "task-manager-app").
        match_key: Tag key to match services (default: "service.name").
        match_value: Tag value to match. Defaults to the perspective name.

    Returns:
        JSON string with the created perspective details.
    """
    value = match_value or name
    result = await _api_request(
        "POST",
        "/application-monitoring/settings/application",
        json_body={
            "label": name,
            "matchSpecification": {
                "type": "EXPRESSION",
                "logicalOperator": "AND",
                "elements": [
                    {
                        "type": "TAG_FILTER",
                        "name": match_key,
                        "operator": "EQUALS",
                        "entity": "DESTINATION",
                        "value": value,
                    }
                ],
            },
            "scope": "ALL",
        },
    )
    app_id = result.get("id", "unknown")
    logger.info("Created application perspective '{}' ({})", name, app_id)
    return _format(result)


@mcp.tool()
async def list_application_perspectives() -> str:
    """List all configured application perspectives.

    Returns:
        JSON string with all application perspectives.
    """
    result = await _api_request(
        "GET",
        "/application-monitoring/settings/application",
    )
    return _format(result)


@mcp.tool()
async def get_application_metrics(
    application_id: str,
    metric: str = "latency",
    window_seconds: int = 3600,
) -> str:
    """Get metrics for a monitored application.

    Args:
        application_id: The application perspective ID.
        metric: Metric type — "latency", "errors", "calls" (default: "latency").
        window_seconds: Time window in seconds (default: 3600 = 1 hour).

    Returns:
        JSON string with metric data points.
    """
    import time

    now_ms = int(time.time() * 1000)
    from_ms = now_ms - (window_seconds * 1000)

    metric_map = {
        "latency": "latency.mean",
        "errors": "errors",
        "calls": "calls",
    }
    metric_id = metric_map.get(metric, metric)

    result = await _api_request(
        "POST",
        "/application-monitoring/metrics/applications",
        json_body={
            "applicationId": application_id,
            "timeFrame": {"to": now_ms, "windowSize": window_seconds * 1000},
            "metrics": [{"metric": metric_id, "aggregation": "MEAN"}],
        },
    )
    return _format(result)


@mcp.tool()
async def get_infrastructure_health() -> str:
    """Get overall infrastructure health snapshot.

    Returns:
        JSON string with host/container health counts and states.
    """
    result = await _api_request("GET", "/infrastructure-monitoring/monitoring/health")
    return _format(result)


@mcp.tool()
async def list_alerts(application_id: str = "") -> str:
    """List active alerts, optionally filtered by application.

    Args:
        application_id: Optional application perspective ID to filter by.

    Returns:
        JSON string with the list of active alerts.
    """
    params = {}
    if application_id:
        params["applicationId"] = application_id

    result = await _api_request("GET", "/events/settings/alerts", params=params)
    return _format(result)


@mcp.tool()
async def create_smart_alert(
    name: str,
    application_id: str,
    metric: str = "latency",
    threshold: float = 1000.0,
    operator: str = "ABOVE",
) -> str:
    """Create a smart alert rule for an application.

    Args:
        name: Alert name (e.g. "High Latency Alert").
        application_id: Application perspective to monitor.
        metric: Metric to alert on — "latency", "errors", "calls".
        threshold: Threshold value to trigger the alert.
        operator: Comparison operator — "ABOVE" or "BELOW".

    Returns:
        JSON string with the created alert configuration.
    """
    metric_map = {
        "latency": "latency.mean",
        "errors": "errors",
        "calls": "calls",
    }
    metric_id = metric_map.get(metric, metric)

    result = await _api_request(
        "POST",
        "/events/settings/alerts",
        json_body={
            "name": name,
            "description": f"Auto-created alert: {metric} {operator.lower()} {threshold}",
            "severity": 5,
            "triggering": True,
            "enabled": True,
            "rule": {
                "ruleType": "threshold",
                "metricName": metric_id,
                "conditionOperator": operator,
                "conditionValue": threshold,
            },
            "applicationId": application_id,
            "alertChannelIds": [],
            "timeThreshold": {
                "type": "violationsInSequence",
                "timeWindow": 300000,
            },
        },
    )
    alert_id = result.get("id", "unknown")
    logger.info("Created alert '{}' ({}) for app {}", name, alert_id, application_id)
    return _format(result)


@mcp.tool()
async def get_service_map(application_id: str) -> str:
    """Get the service dependency map for an application.

    Shows how services communicate with each other, useful for
    understanding the deployed application architecture.

    Args:
        application_id: Application perspective ID.

    Returns:
        JSON string with the service dependency graph.
    """
    result = await _api_request(
        "GET",
        f"/application-monitoring/applications/{application_id}/services",
    )
    return _format(result)


# ── Helpers ────────────────────────────────────────────────────


def _format(data: Any) -> str:
    """Format API response as a readable JSON string.

    Args:
        data: Response data to format.

    Returns:
        Pretty-printed JSON string.
    """
    return json.dumps(data, indent=2, default=str)


# ── Entry point ────────────────────────────────────────────────


def create_instana_mcp() -> FastMCP:
    """Return the configured FastMCP server instance.

    Returns:
        The Instana MCP server, ready to run.
    """
    return mcp


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the Instana MCP server.

    Args:
        argv: Command-line arguments.

    Returns:
        Parsed namespace with transport configuration.
    """
    parser = argparse.ArgumentParser(
        description="IBM Instana MCP Server — APM observability",
    )
    parser.add_argument("--http", action="store_true", help="Run in HTTP mode")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the Instana MCP server.

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
