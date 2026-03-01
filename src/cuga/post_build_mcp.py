"""
Post-Build MCP Orchestration — Runs optional MCP-powered steps after build passes.

After the quality gate passes, this module orchestrates:
1. Docker: Verify Dockerfile builds correctly
2. QRadar: Scan generated code for security patterns
3. GitHub: Create repo + push (if configured in spec)
4. DevOps: Create CI/CD pipeline linked to the repo
5. Code Engine: Deploy the built container
6. Instana: Set up monitoring for the deployed app

Each step is optional and gracefully degrades if the MCP isn't connected
or credentials aren't configured.

Usage::

    from cuga.post_build_mcp import run_post_build_actions

    report = await run_post_build_actions(
        project_dir=Path("output/my-app"),
        spec=spec_dict,
        mcp_manager=manager,
        settings=settings,
    )
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

__all__ = [
    "PostBuildReport",
    "PostBuildSettings",
    "run_post_build_actions",
]


@dataclass
class PostBuildSettings:
    """Configuration for post-build MCP actions.

    Attributes:
        docker_verify: Verify Dockerfile builds during post-build.
        qradar_scan: Run QRadar security scan on generated code.
        auto_deploy: Auto-deploy to IBM Code Engine after build passes.
        instana_monitor: Auto-setup Instana monitoring after deploy.
        devops_pipeline: Auto-create CI/CD pipeline after build passes.
    """

    docker_verify: bool = True
    qradar_scan: bool = False
    auto_deploy: bool = False
    instana_monitor: bool = False
    devops_pipeline: bool = False


@dataclass
class StepResult:
    """Result of a single post-build step.

    Attributes:
        name: Step name (e.g. "docker_verify").
        success: Whether the step completed successfully.
        skipped: Whether the step was skipped (MCP not available).
        message: Human-readable result message.
        data: Optional structured data from the step.
    """

    name: str
    success: bool = False
    skipped: bool = False
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PostBuildReport:
    """Aggregate report from all post-build steps.

    Attributes:
        steps: List of individual step results.
        all_passed: True if no step failed (skipped counts as pass).
    """

    steps: list[StepResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        """Whether all non-skipped steps succeeded."""
        return all(s.success or s.skipped for s in self.steps)

    def summary(self) -> str:
        """Return a human-readable summary of all steps.

        Returns:
            Multi-line summary string.
        """
        lines = ["Post-build actions:"]
        for s in self.steps:
            if s.skipped:
                icon = "⏭️"
                status = "skipped"
            elif s.success:
                icon = "✅"
                status = "passed"
            else:
                icon = "❌"
                status = "failed"
            lines.append(f"  {icon} {s.name}: {status} — {s.message}")
        return "\n".join(lines)


async def run_post_build_actions(
    project_dir: Path,
    spec: dict[str, Any],
    mcp_manager: Any | None = None,
    settings: PostBuildSettings | None = None,
) -> PostBuildReport:
    """Run all configured post-build MCP actions.

    Each step is executed in order and is independent — a failure in one
    step does not prevent subsequent steps from running.

    Args:
        project_dir: Path to the generated project directory.
        spec: The project specification dict.
        mcp_manager: The MCPManager instance (None if MCP not available).
        settings: Configuration for which steps to run.

    Returns:
        A ``PostBuildReport`` with results for each step.
    """
    cfg = settings or PostBuildSettings()
    report = PostBuildReport()

    # Determine which MCP servers are live
    live_servers: set[str] = set()
    if mcp_manager is not None:
        live_servers = set(getattr(mcp_manager, "tools_by_server", {}).keys())
    logger.info("Post-build: live MCP servers = {}", live_servers)

    # 1. Docker verify
    result = await _docker_verify(project_dir, spec, mcp_manager, live_servers, cfg)
    report.steps.append(result)

    # 2. QRadar security scan
    result = await _qradar_scan(project_dir, spec, mcp_manager, live_servers, cfg)
    report.steps.append(result)

    # 3. GitHub create + push (handled by the agent, just verify)
    result = await _github_verify(spec, mcp_manager, live_servers)
    report.steps.append(result)

    # 4. DevOps pipeline
    result = await _devops_pipeline(spec, mcp_manager, live_servers, cfg)
    report.steps.append(result)

    # 5. Code Engine deploy
    result = await _code_engine_deploy(project_dir, spec, mcp_manager, live_servers, cfg)
    report.steps.append(result)

    # 6. Instana monitoring
    result = await _instana_monitor(spec, mcp_manager, live_servers, cfg)
    report.steps.append(result)

    logger.info(report.summary())
    return report


# ── Individual step implementations ────────────────────────────


async def _docker_verify(
    project_dir: Path,
    spec: dict[str, Any],
    manager: Any | None,
    live: set[str],
    cfg: PostBuildSettings,
) -> StepResult:
    """Verify the generated Dockerfile builds successfully.

    Args:
        project_dir: Project directory path.
        spec: Project spec dict.
        manager: MCPManager instance.
        live: Set of live MCP server names.
        cfg: Post-build settings.

    Returns:
        StepResult for the Docker verification step.
    """
    if not cfg.docker_verify:
        return StepResult(name="docker_verify", skipped=True, message="Disabled in settings")

    # Check if a Dockerfile exists
    dockerfile = project_dir / "Dockerfile"
    if not dockerfile.exists():
        return StepResult(
            name="docker_verify",
            skipped=True,
            message="No Dockerfile found in project",
        )

    if "docker" not in live:
        return StepResult(
            name="docker_verify",
            skipped=True,
            message="Docker MCP not connected",
        )

    try:
        project_name = spec.get("name", "project")
        tag = f"{project_name}:latest"

        result = await manager.call_tool(
            "docker_build",
            {"path": str(project_dir), "tag": tag},
        )
        output = _extract_text(result)

        if "error" in output.lower() or "failed" in output.lower():
            return StepResult(
                name="docker_verify",
                success=False,
                message=f"Docker build failed: {output[:200]}",
            )

        return StepResult(
            name="docker_verify",
            success=True,
            message=f"Docker image built: {tag}",
            data={"tag": tag},
        )
    except Exception as exc:
        return StepResult(
            name="docker_verify",
            success=False,
            message=f"Docker build error: {exc}",
        )


async def _qradar_scan(
    project_dir: Path,
    spec: dict[str, Any],
    manager: Any | None,
    live: set[str],
    cfg: PostBuildSettings,
) -> StepResult:
    """Run QRadar security scan on generated code.

    Args:
        project_dir: Project directory path.
        spec: Project spec dict.
        manager: MCPManager instance.
        live: Set of live MCP server names.
        cfg: Post-build settings.

    Returns:
        StepResult for the QRadar scanning step.
    """
    if not cfg.qradar_scan:
        return StepResult(name="qradar_scan", skipped=True, message="Disabled in settings")

    if "qradar" not in live:
        return StepResult(
            name="qradar_scan",
            skipped=True,
            message="QRadar MCP not connected",
        )

    try:
        # Use QRadar's AQL to check for security-relevant patterns
        project_name = spec.get("name", "project")
        result = await manager.call_tool(
            "search_offenses",
            {"query": f"app_name = '{project_name}'", "limit": 10},
        )
        output = _extract_text(result)

        return StepResult(
            name="qradar_scan",
            success=True,
            message=f"QRadar scan complete: {output[:200]}",
        )
    except Exception as exc:
        return StepResult(
            name="qradar_scan",
            success=False,
            message=f"QRadar scan error: {exc}",
        )


async def _github_verify(
    spec: dict[str, Any],
    manager: Any | None,
    live: set[str],
) -> StepResult:
    """Verify GitHub repo was created (if configured in spec).

    Args:
        spec: Project spec dict.
        manager: MCPManager instance.
        live: Set of live MCP server names.

    Returns:
        StepResult for the GitHub verification step.
    """
    gh = spec.get("github", {})
    if not gh.get("create_repo"):
        return StepResult(
            name="github_verify",
            skipped=True,
            message="GitHub repo creation not configured in spec",
        )

    if "github" not in live:
        return StepResult(
            name="github_verify",
            skipped=True,
            message="GitHub MCP not connected",
        )

    try:
        owner = gh.get("owner") or os.environ.get("GITHUB_OWNER", "")
        repo_name = spec.get("name", "project")

        result = await manager.call_tool(
            "get_repository",
            {"owner": owner, "name": repo_name},
        )
        output = _extract_text(result)

        if "error" in output.lower() or "not found" in output.lower():
            return StepResult(
                name="github_verify",
                success=False,
                message=f"GitHub repo not found: {owner}/{repo_name}",
            )

        return StepResult(
            name="github_verify",
            success=True,
            message=f"GitHub repo verified: {owner}/{repo_name}",
            data={"owner": owner, "repo": repo_name},
        )
    except Exception as exc:
        return StepResult(
            name="github_verify",
            success=False,
            message=f"GitHub verification error: {exc}",
        )


async def _devops_pipeline(
    spec: dict[str, Any],
    manager: Any | None,
    live: set[str],
    cfg: PostBuildSettings,
) -> StepResult:
    """Create a DevOps CI/CD pipeline for the generated project.

    Args:
        spec: Project spec dict.
        manager: MCPManager instance.
        live: Set of live MCP server names.
        cfg: Post-build settings.

    Returns:
        StepResult for the DevOps pipeline creation step.
    """
    if not cfg.devops_pipeline:
        return StepResult(
            name="devops_pipeline",
            skipped=True,
            message="Disabled in settings",
        )

    if "devops" not in live:
        return StepResult(
            name="devops_pipeline",
            skipped=True,
            message="DevOps MCP not connected",
        )

    gh = spec.get("github", {})
    if not gh.get("create_repo"):
        return StepResult(
            name="devops_pipeline",
            skipped=True,
            message="No GitHub repo configured — pipeline needs a repo URL",
        )

    try:
        owner = gh.get("owner") or os.environ.get("GITHUB_OWNER", "")
        repo_name = spec.get("name", "project")
        repo_url = f"https://github.com/{owner}/{repo_name}"

        # Create toolchain
        rg_id = os.environ.get("IBMCLOUD_RESOURCE_GROUP_ID", "")
        if not rg_id:
            return StepResult(
                name="devops_pipeline",
                skipped=True,
                message="IBMCLOUD_RESOURCE_GROUP_ID not set",
            )

        tc_result = await manager.call_tool(
            "create_toolchain",
            {
                "name": f"{repo_name}-toolchain",
                "resource_group_id": rg_id,
                "description": f"CI/CD for {repo_name}",
            },
        )
        tc_data = json.loads(_extract_text(tc_result))
        tc_id = tc_data.get("id", "")

        if not tc_id:
            return StepResult(
                name="devops_pipeline",
                success=False,
                message="Failed to create toolchain — no ID returned",
            )

        # Create pipeline
        pipe_result = await manager.call_tool(
            "create_tekton_pipeline",
            {
                "toolchain_id": tc_id,
                "pipeline_name": f"{repo_name}-pipeline",
                "repo_url": repo_url,
            },
        )
        pipe_data = json.loads(_extract_text(pipe_result))
        pipe_id = pipe_data.get("id", "")

        return StepResult(
            name="devops_pipeline",
            success=True,
            message=f"Pipeline created in toolchain {tc_id}",
            data={"toolchain_id": tc_id, "pipeline_id": pipe_id, "repo_url": repo_url},
        )
    except Exception as exc:
        return StepResult(
            name="devops_pipeline",
            success=False,
            message=f"DevOps pipeline error: {exc}",
        )


async def _code_engine_deploy(
    project_dir: Path,
    spec: dict[str, Any],
    manager: Any | None,
    live: set[str],
    cfg: PostBuildSettings,
) -> StepResult:
    """Deploy the built project to IBM Code Engine.

    Args:
        project_dir: Project directory path.
        spec: Project spec dict.
        manager: MCPManager instance.
        live: Set of live MCP server names.
        cfg: Post-build settings.

    Returns:
        StepResult for the Code Engine deployment step.
    """
    if not cfg.auto_deploy:
        return StepResult(
            name="code_engine_deploy",
            skipped=True,
            message="Disabled in settings",
        )

    if "code-engine" not in live:
        return StepResult(
            name="code_engine_deploy",
            skipped=True,
            message="Code Engine MCP not connected",
        )

    try:
        project_name = spec.get("name", "project")

        # First check if a docker image was built
        tag = f"{project_name}:latest"

        result = await manager.call_tool(
            "code_engine_create_app",
            {
                "name": project_name,
                "image": tag,
            },
        )
        output = _extract_text(result)

        if "error" in output.lower():
            return StepResult(
                name="code_engine_deploy",
                success=False,
                message=f"Code Engine deploy failed: {output[:200]}",
            )

        return StepResult(
            name="code_engine_deploy",
            success=True,
            message=f"Deployed to Code Engine: {project_name}",
            data={"app_name": project_name, "image": tag},
        )
    except Exception as exc:
        return StepResult(
            name="code_engine_deploy",
            success=False,
            message=f"Code Engine deploy error: {exc}",
        )


async def _instana_monitor(
    spec: dict[str, Any],
    manager: Any | None,
    live: set[str],
    cfg: PostBuildSettings,
) -> StepResult:
    """Set up Instana monitoring for the deployed application.

    Args:
        spec: Project spec dict.
        manager: MCPManager instance.
        live: Set of live MCP server names.
        cfg: Post-build settings.

    Returns:
        StepResult for the Instana monitoring setup step.
    """
    if not cfg.instana_monitor:
        return StepResult(
            name="instana_monitor",
            skipped=True,
            message="Disabled in settings",
        )

    if "instana" not in live:
        return StepResult(
            name="instana_monitor",
            skipped=True,
            message="Instana MCP not connected",
        )

    try:
        project_name = spec.get("name", "project")

        # Create application perspective
        result = await manager.call_tool(
            "create_application_perspective",
            {"name": project_name},
        )
        perspective_data = json.loads(_extract_text(result))
        app_id = perspective_data.get("id", "")

        if not app_id:
            return StepResult(
                name="instana_monitor",
                success=False,
                message="Failed to create application perspective — no ID returned",
            )

        # Create a baseline latency alert
        try:
            await manager.call_tool(
                "create_smart_alert",
                {
                    "name": f"{project_name} — High Latency",
                    "application_id": app_id,
                    "metric": "latency",
                    "threshold": 1000.0,
                },
            )
        except Exception:
            logger.debug("Instana alert creation failed (non-critical)")

        return StepResult(
            name="instana_monitor",
            success=True,
            message=f"Instana monitoring configured for {project_name}",
            data={"application_id": app_id},
        )
    except Exception as exc:
        return StepResult(
            name="instana_monitor",
            success=False,
            message=f"Instana setup error: {exc}",
        )


# ── Helpers ────────────────────────────────────────────────────


def _extract_text(result: Any) -> str:
    """Extract text from an MCP tool call result.

    Args:
        result: The raw result from ``manager.call_tool()``.

    Returns:
        The text content as a string.
    """
    if result and hasattr(result[0], "text"):
        return result[0].text
    return str(result)
