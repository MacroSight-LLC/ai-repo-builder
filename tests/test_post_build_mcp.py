"""Tests for the post-build MCP orchestration module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.post_build_mcp import (
    PostBuildReport,
    PostBuildSettings,
    StepResult,
    run_post_build_actions,
)


# ── Data-class unit tests ──────────────────────────────────────


class TestPostBuildSettings:
    """Test PostBuildSettings defaults and overrides."""

    def test_defaults(self) -> None:
        """Default settings should enable only docker_verify."""
        s = PostBuildSettings()
        assert s.docker_verify is True
        assert s.qradar_scan is False
        assert s.auto_deploy is False
        assert s.instana_monitor is False
        assert s.devops_pipeline is False

    def test_override(self) -> None:
        """Constructor kwargs should override defaults."""
        s = PostBuildSettings(qradar_scan=True, auto_deploy=True)
        assert s.qradar_scan is True
        assert s.auto_deploy is True


class TestStepResult:
    """Test StepResult dataclass."""

    def test_default_values(self) -> None:
        """Bare StepResult should default to not-success, not-skipped."""
        r = StepResult(name="test")
        assert r.success is False
        assert r.skipped is False
        assert r.message == ""
        assert r.data == {}

    def test_with_data(self) -> None:
        """StepResult should carry extra data."""
        r = StepResult(name="deploy", success=True, data={"url": "http://app"})
        assert r.data["url"] == "http://app"


class TestPostBuildReport:
    """Test PostBuildReport aggregate logic."""

    def test_empty_report_passes(self) -> None:
        """Report with no steps should count as all_passed."""
        r = PostBuildReport()
        assert r.all_passed is True

    def test_all_passed_with_skips(self) -> None:
        """Report with only skipped and success steps should pass."""
        r = PostBuildReport(steps=[
            StepResult(name="a", success=True, message="ok"),
            StepResult(name="b", skipped=True, message="skipped"),
        ])
        assert r.all_passed is True

    def test_fails_on_failure(self) -> None:
        """Report with a failed step should not pass."""
        r = PostBuildReport(steps=[
            StepResult(name="a", success=True, message="ok"),
            StepResult(name="b", success=False, message="failed"),
        ])
        assert r.all_passed is False

    def test_summary_contains_step_names(self) -> None:
        """Summary should list all step names and statuses."""
        r = PostBuildReport(steps=[
            StepResult(name="docker_verify", success=True, message="built"),
            StepResult(name="qradar_scan", skipped=True, message="off"),
            StepResult(name="github_verify", success=False, message="err"),
        ])
        summary = r.summary()
        assert "docker_verify" in summary
        assert "passed" in summary
        assert "skipped" in summary
        assert "failed" in summary


# ── Mock helpers ───────────────────────────────────────────────


def _make_manager(live_servers: set[str] | None = None) -> MagicMock:
    """Build a mock MCP manager with configurable live servers."""
    m = MagicMock()
    m.tools_by_server = {s: [] for s in (live_servers or set())}
    m.call_tool = AsyncMock()
    return m


class _FakeToolResult:
    """Fake MCP tool result with a .text attribute."""

    def __init__(self, text: str) -> None:
        self.text = text


def _tool_returns(data: dict[str, Any]) -> list[_FakeToolResult]:
    """Wrap data as the list[result] format manager.call_tool returns."""
    return [_FakeToolResult(json.dumps(data))]


# ── Step integration tests ─────────────────────────────────────


class TestDockerVerify:
    """Test _docker_verify step."""

    @pytest.mark.asyncio
    async def test_disabled_in_settings(self, tmp_path: Path) -> None:
        """Should skip if docker_verify=False."""
        cfg = PostBuildSettings(docker_verify=False)
        mgr = _make_manager({"docker"})
        report = await run_post_build_actions(tmp_path, {}, mgr, cfg)
        step = report.steps[0]
        assert step.name == "docker_verify"
        assert step.skipped is True

    @pytest.mark.asyncio
    async def test_no_dockerfile(self, tmp_path: Path) -> None:
        """Should skip when project has no Dockerfile."""
        cfg = PostBuildSettings(docker_verify=True)
        mgr = _make_manager({"docker"})
        report = await run_post_build_actions(tmp_path, {}, mgr, cfg)
        step = report.steps[0]
        assert step.skipped is True
        assert "No Dockerfile" in step.message

    @pytest.mark.asyncio
    async def test_mcp_not_connected(self, tmp_path: Path) -> None:
        """Should skip when Docker MCP is not live."""
        (tmp_path / "Dockerfile").write_text("FROM python:3.12")
        cfg = PostBuildSettings(docker_verify=True)
        mgr = _make_manager(set())
        report = await run_post_build_actions(tmp_path, {}, mgr, cfg)
        step = report.steps[0]
        assert step.skipped is True
        assert "not connected" in step.message

    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path) -> None:
        """Should pass when Docker MCP succeeds."""
        (tmp_path / "Dockerfile").write_text("FROM python:3.12")
        cfg = PostBuildSettings(docker_verify=True)
        mgr = _make_manager({"docker"})
        mgr.call_tool.return_value = _tool_returns({"status": "success"})

        report = await run_post_build_actions(tmp_path, {"name": "myapp"}, mgr, cfg)
        step = report.steps[0]
        assert step.success is True
        assert "myapp:latest" in step.message

    @pytest.mark.asyncio
    async def test_build_error(self, tmp_path: Path) -> None:
        """Should fail when build output contains 'error'."""
        (tmp_path / "Dockerfile").write_text("FROM python:3.12")
        cfg = PostBuildSettings(docker_verify=True)
        mgr = _make_manager({"docker"})
        mgr.call_tool.return_value = _tool_returns({"error": "bad FROM"})

        report = await run_post_build_actions(tmp_path, {"name": "myapp"}, mgr, cfg)
        step = report.steps[0]
        assert step.success is False


class TestQRadarScan:
    """Test _qradar_scan step."""

    @pytest.mark.asyncio
    async def test_disabled(self, tmp_path: Path) -> None:
        """Should skip if qradar_scan=False."""
        cfg = PostBuildSettings(qradar_scan=False)
        mgr = _make_manager({"qradar"})
        report = await run_post_build_actions(tmp_path, {}, mgr, cfg)
        step = next(s for s in report.steps if s.name == "qradar_scan")
        assert step.skipped is True

    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path) -> None:
        """Should succeed when QRadar returns results."""
        cfg = PostBuildSettings(qradar_scan=True)
        mgr = _make_manager({"qradar", "docker"})
        mgr.call_tool.return_value = _tool_returns({"offenses": []})

        report = await run_post_build_actions(tmp_path, {"name": "app"}, mgr, cfg)
        step = next(s for s in report.steps if s.name == "qradar_scan")
        assert step.success is True


class TestGitHubVerify:
    """Test _github_verify step."""

    @pytest.mark.asyncio
    async def test_no_repo_configured(self, tmp_path: Path) -> None:
        """Should skip if spec has no github.create_repo."""
        cfg = PostBuildSettings()
        mgr = _make_manager({"github"})
        report = await run_post_build_actions(tmp_path, {}, mgr, cfg)
        step = next(s for s in report.steps if s.name == "github_verify")
        assert step.skipped is True

    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path) -> None:
        """Should succeed when GitHub confirms repo exists."""
        spec = {"github": {"create_repo": True, "owner": "org"}, "name": "app"}
        cfg = PostBuildSettings()
        mgr = _make_manager({"github", "docker"})
        mgr.call_tool.return_value = _tool_returns({"full_name": "org/app"})

        report = await run_post_build_actions(tmp_path, spec, mgr, cfg)
        step = next(s for s in report.steps if s.name == "github_verify")
        assert step.success is True


class TestDevOpsPipeline:
    """Test _devops_pipeline step."""

    @pytest.mark.asyncio
    async def test_disabled(self, tmp_path: Path) -> None:
        """Should skip if devops_pipeline=False."""
        cfg = PostBuildSettings(devops_pipeline=False)
        mgr = _make_manager({"devops"})
        report = await run_post_build_actions(tmp_path, {}, mgr, cfg)
        step = next(s for s in report.steps if s.name == "devops_pipeline")
        assert step.skipped is True

    @pytest.mark.asyncio
    async def test_no_github_repo(self, tmp_path: Path) -> None:
        """Should skip if no GitHub repo is configured."""
        cfg = PostBuildSettings(devops_pipeline=True)
        mgr = _make_manager({"devops"})
        report = await run_post_build_actions(tmp_path, {}, mgr, cfg)
        step = next(s for s in report.steps if s.name == "devops_pipeline")
        assert step.skipped is True
        assert "No GitHub repo" in step.message


class TestCodeEngineDeploy:
    """Test _code_engine_deploy step."""

    @pytest.mark.asyncio
    async def test_disabled(self, tmp_path: Path) -> None:
        """Should skip if auto_deploy=False."""
        cfg = PostBuildSettings(auto_deploy=False)
        mgr = _make_manager({"code-engine"})
        report = await run_post_build_actions(tmp_path, {}, mgr, cfg)
        step = next(s for s in report.steps if s.name == "code_engine_deploy")
        assert step.skipped is True

    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path) -> None:
        """Should succeed when Code Engine MCP returns success."""
        cfg = PostBuildSettings(auto_deploy=True)
        mgr = _make_manager({"code-engine", "docker"})
        mgr.call_tool.return_value = _tool_returns({"status": "running"})

        report = await run_post_build_actions(tmp_path, {"name": "myapp"}, mgr, cfg)
        step = next(s for s in report.steps if s.name == "code_engine_deploy")
        assert step.success is True


class TestInstanaMonitor:
    """Test _instana_monitor step."""

    @pytest.mark.asyncio
    async def test_disabled(self, tmp_path: Path) -> None:
        """Should skip if instana_monitor=False."""
        cfg = PostBuildSettings(instana_monitor=False)
        mgr = _make_manager({"instana"})
        report = await run_post_build_actions(tmp_path, {}, mgr, cfg)
        step = next(s for s in report.steps if s.name == "instana_monitor")
        assert step.skipped is True

    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path) -> None:
        """Should succeed when perspective is created."""
        cfg = PostBuildSettings(instana_monitor=True)
        mgr = _make_manager({"instana", "docker"})
        mgr.call_tool.return_value = _tool_returns({"id": "app-123"})

        report = await run_post_build_actions(tmp_path, {"name": "myapp"}, mgr, cfg)
        step = next(s for s in report.steps if s.name == "instana_monitor")
        assert step.success is True

    @pytest.mark.asyncio
    async def test_mcp_not_connected(self, tmp_path: Path) -> None:
        """Should skip when Instana MCP is not live."""
        cfg = PostBuildSettings(instana_monitor=True)
        mgr = _make_manager(set())
        report = await run_post_build_actions(tmp_path, {}, mgr, cfg)
        step = next(s for s in report.steps if s.name == "instana_monitor")
        assert step.skipped is True


# ── Full orchestration test ────────────────────────────────────


class TestFullOrchestration:
    """Test complete run_post_build_actions flow."""

    @pytest.mark.asyncio
    async def test_no_manager(self, tmp_path: Path) -> None:
        """All steps should skip when manager is None."""
        report = await run_post_build_actions(tmp_path, {}, None, PostBuildSettings())
        # docker_verify may skip for "no Dockerfile" or "not connected"
        assert report.all_passed is True
        assert all(s.skipped for s in report.steps)

    @pytest.mark.asyncio
    async def test_all_disabled(self, tmp_path: Path) -> None:
        """When all settings are false, all steps should be skipped."""
        cfg = PostBuildSettings(
            docker_verify=False,
            qradar_scan=False,
            auto_deploy=False,
            instana_monitor=False,
            devops_pipeline=False,
        )
        mgr = _make_manager({"docker", "qradar", "github", "devops", "code-engine", "instana"})
        report = await run_post_build_actions(tmp_path, {}, mgr, cfg)
        assert report.all_passed is True
        assert len(report.steps) == 6

    @pytest.mark.asyncio
    async def test_report_has_six_steps(self, tmp_path: Path) -> None:
        """Report should always have exactly 6 steps."""
        report = await run_post_build_actions(tmp_path, {}, None, PostBuildSettings())
        assert len(report.steps) == 6
        names = [s.name for s in report.steps]
        assert names == [
            "docker_verify",
            "qradar_scan",
            "github_verify",
            "devops_pipeline",
            "code_engine_deploy",
            "instana_monitor",
        ]
