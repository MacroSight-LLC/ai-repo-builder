"""
Build Loop — In-process build→validate→feedback→retry orchestration.

Replaces the original bash-level "Ralph Wiggum" loop (``one-click.sh``)
with a Python-level loop that keeps the agent warm, feeds validation
errors back as context, and records each iteration to the build catalog.

Usage (programmatic)::

    from cuga.build_loop import BuildLoop, BuildLoopConfig

    loop = BuildLoop(
        spec=spec_dict,
        agent=cuga_agent,
        project_dir=Path("output/my-project"),
        config=BuildLoopConfig(max_iterations=5),
    )
    result = await loop.run()
    if result.passed:
        print("Build passed on iteration", result.iteration)

Usage (CLI)::

    python -m cuga.build_loop --spec specs/example-spec.yaml \\
        --tools mcp_servers_local.yaml --output output
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

__all__ = [
    "BuildLoop",
    "BuildLoopConfig",
    "BuildResult",
    "IterationRecord",
]


# ── Configuration ──────────────────────────────────────────────


@dataclass(frozen=True)
class BuildLoopConfig:
    """Tunable parameters for the build loop.

    Attributes:
        max_iterations: Maximum build→validate→retry cycles before giving up.
        max_syntax_errors: Build passes only if syntax errors ≤ this.
        max_error_smells: Build passes only if error-severity smells ≤ this.
        require_lint_pass: If True, ruff must exit 0 for a pass.
        require_all_spec_files: If True, every spec file must exist on disk.
        record_to_catalog: If True, record each iteration to build_catalog.
        feedback_max_errors: Max individual errors to feed back to the agent
            per retry (avoids prompt bloat).
    """

    max_iterations: int = 5
    max_syntax_errors: int = 0
    max_error_smells: int = 0
    require_lint_pass: bool = False
    require_all_spec_files: bool = True
    record_to_catalog: bool = True
    feedback_max_errors: int = 20


# ── Result types ───────────────────────────────────────────────


@dataclass
class IterationRecord:
    """Snapshot of a single build→validate pass.

    Attributes:
        iteration: 1-based iteration number.
        elapsed_seconds: Wall-clock time for this iteration.
        validation: The raw validation report from ``post_build.validate_project``.
        passed: Whether this iteration met the quality gate.
        feedback_prompt: The error-feedback prompt that was (or would be)
            sent back to the agent.  ``None`` on the final iteration.
    """

    iteration: int
    elapsed_seconds: float
    validation: dict[str, Any]
    passed: bool
    feedback_prompt: str | None = None


@dataclass
class BuildResult:
    """Aggregate result returned by ``BuildLoop.run()``.

    Attributes:
        passed: Whether the build ultimately passed validation.
        iteration: The iteration on which the loop stopped (1-based).
        total_elapsed: Cumulative wall-clock time across all iterations.
        iterations: Per-iteration detail records.
        project_dir: Absolute path to the generated project.
        final_validation: The validation report from the last iteration.
    """

    passed: bool
    iteration: int
    total_elapsed: float
    iterations: list[IterationRecord] = field(default_factory=list)
    project_dir: Path | None = None
    final_validation: dict[str, Any] = field(default_factory=dict)


# ── Feedback prompt builder ────────────────────────────────────


def _build_feedback_prompt(
    validation: dict[str, Any],
    iteration: int,
    max_errors: int,
) -> str:
    """Construct the error-feedback message injected before the next agent run.

    Args:
        validation: Report dict from ``post_build.validate_project``.
        iteration: Current 1-based iteration number.
        max_errors: Cap on the number of individual errors listed.

    Returns:
        A markdown-formatted prompt string the agent can act on.
    """
    lines: list[str] = [
        f"## Build Iteration {iteration} — Validation FAILED",
        "",
        "Fix **every** issue listed below, then regenerate the affected files.",
        "Do NOT rewrite files that are already correct.",
        "",
    ]

    count = 0

    # Syntax errors
    syntax_errors = validation.get("syntax_errors", [])
    if syntax_errors:
        lines.append("### Python Syntax Errors")
        for err in syntax_errors[:max_errors]:
            if isinstance(err, dict):
                lines.append(
                    f"- `{err.get('file', '?')}:{err.get('line', '?')}` — {err.get('issue', '?')}"
                )
            else:
                lines.append(f"- {err}")
            count += 1
        lines.append("")

    # Lint issues
    if not validation.get("lint_passed", True) and validation.get("lint_output"):
        lines.append("### Lint Issues (ruff)")
        for lint_line in validation["lint_output"].splitlines()[: max_errors - count]:
            lines.append(f"- {lint_line}")
            count += 1
        lines.append("")

    # Error-severity code smells
    smells = validation.get("smells", [])
    error_smells = [s for s in smells if s.get("severity") == "error"]
    if error_smells:
        lines.append("### Code Quality Errors")
        for s in error_smells[: max_errors - count]:
            lines.append(
                f"- `{s.get('file', '?')}:{s.get('line', '?')}` — "
                f"{s.get('issue', '?')} → `{s.get('code', '')}`"
            )
            count += 1
        lines.append("")

    # Missing files
    missing_spec = validation.get("missing_spec_files", [])
    if missing_spec:
        lines.append("### Missing Spec Files")
        for m in missing_spec[: max_errors - count]:
            lines.append(f"- `{m}` — create this file")
            count += 1
        lines.append("")

    missing_req = validation.get("missing_required", [])
    if missing_req:
        lines.append("### Missing Required Files")
        for m in missing_req[: max_errors - count]:
            lines.append(f"- `{m}`")
            count += 1
        lines.append("")

    # Frontend failures
    frontend = validation.get("frontend", {})
    if frontend.get("has_frontend"):
        if frontend.get("install_ok") is False:
            lines.append("### Frontend Install Failed")
            output = str(frontend.get("install_output", ""))[-500:]
            lines.append(f"```\n{output}\n```")
            lines.append("")
        elif frontend.get("build_ok") is False:
            lines.append("### Frontend Build Failed")
            output = str(frontend.get("build_output", ""))[-500:]
            lines.append(f"```\n{output}\n```")
            lines.append("")

    # Docker build failures
    docker = validation.get("docker", {})
    if docker.get("has_dockerfile") and docker.get("build_ok") is False:
        lines.append("### Docker Build Failed")
        output = str(docker.get("build_output", ""))[-500:]
        lines.append(f"```\n{output}\n```")
        lines.append("")

    # Missing migrations
    migrations = validation.get("migrations", {})
    if migrations.get("has_orm") and not migrations.get("has_migrations"):
        orm_type = migrations.get("orm_type", "unknown")
        lines.append(f"### Missing Database Migrations ({orm_type})")
        lines.append("- ORM detected but no migration files found. Generate initial migrations.")
        lines.append("")

    lines.append(
        "Fix ALL of the above issues. Write corrected files using the filesystem tools. "
        "Do NOT skip any file."
    )
    return "\n".join(lines)


def _check_quality_gate(
    validation: dict[str, Any],
    config: BuildLoopConfig,
) -> bool:
    """Determine if a validation report meets the configured quality bar.

    Delegates to :class:`cuga.quality_gate.QualityGate` for the actual
    evaluation, mapping ``BuildLoopConfig`` fields to ``GateConfig``.

    Args:
        validation: Report dict from ``post_build.validate_project``.
        config: The loop configuration with thresholds.

    Returns:
        ``True`` if the build passes the gate.
    """
    from cuga.quality_gate import GateConfig, QualityGate

    gate_cfg = GateConfig(
        max_syntax_errors=config.max_syntax_errors,
        max_error_smells=config.max_error_smells,
        require_lint_pass=config.require_lint_pass,
        require_all_spec_files=config.require_all_spec_files,
    )
    verdict = QualityGate(gate_cfg).evaluate(validation)
    return verdict.passed


# ── The Loop ───────────────────────────────────────────────────


class BuildLoop:
    """In-process build→validate→feedback→retry orchestrator.

    Unlike the original bash ``one-click.sh`` which cold-restarts the
    agent on every iteration, BuildLoop keeps the agent alive and feeds
    validation errors back as additional messages so the LLM can fix
    only what's broken.

    Args:
        spec: The project spec dict.
        agent: A ``CugaAgent`` instance (already wired with tools).
        project_dir: Where the agent writes output files.
        config: Tunable loop parameters.
        policy_text: Optional coding policy to include in prompts.
        workspace_root: The filesystem root the agent writes to
            (defaults to ``str(project_dir)``).
    """

    def __init__(
        self,
        spec: dict[str, Any],
        agent: Any,  # CugaAgent — avoid circular import
        project_dir: Path,
        config: BuildLoopConfig | None = None,
        policy_text: str | None = None,
        workspace_root: str | None = None,
        mcp_manager: Any | None = None,
        mcp_registry: Any | None = None,
    ) -> None:
        self.spec = spec
        self.agent = agent
        self.project_dir = project_dir
        self.config = config or BuildLoopConfig()
        self.policy_text = policy_text
        self.workspace_root = workspace_root or str(project_dir.resolve())
        self._mcp_manager = mcp_manager
        self._mcp_registry = mcp_registry
        self._iterations: list[IterationRecord] = []

    # ── Public API ─────────────────────────────────────────────

    async def run(self) -> BuildResult:
        """Execute the build loop.

        Returns:
            A ``BuildResult`` summarising the outcome.
        """
        from cuga.post_build import validate_project
        from cuga.spec_to_prompt import spec_to_prompt

        total_t0 = time.time()

        # Build the initial prompt (first iteration only)
        initial_prompt = spec_to_prompt(
            self.spec,
            self.policy_text,
            workspace_root=self.workspace_root,
        )

        thread_id: str | None = None
        last_validation: dict[str, Any] = {}

        for iteration in range(1, self.config.max_iterations + 1):
            logger.info(
                "━━━ Build Loop — iteration {}/{} ━━━",
                iteration,
                self.config.max_iterations,
            )

            # Decide what to send the agent
            if iteration == 1:
                prompt = initial_prompt
            else:
                # Feed the error summary from the previous validation
                prompt = _build_feedback_prompt(
                    last_validation,
                    iteration - 1,
                    self.config.feedback_max_errors,
                )

            # ── Invoke ──────────────────────────────────────────
            iter_t0 = time.time()
            try:
                result = await self.agent.invoke(
                    prompt,
                    thread_id=thread_id,
                )
                # Preserve thread so follow-ups reuse conversation memory
                if result.thread_id:
                    thread_id = result.thread_id
            except Exception as exc:
                logger.error("Agent raised on iteration {}: {}", iteration, exc)
                rec = IterationRecord(
                    iteration=iteration,
                    elapsed_seconds=time.time() - iter_t0,
                    validation={},
                    passed=False,
                    feedback_prompt=str(exc),
                )
                self._iterations.append(rec)
                continue

            iter_elapsed = time.time() - iter_t0

            # ── Validate ────────────────────────────────────────
            last_validation = validate_project(self.project_dir, self.spec)
            passed = _check_quality_gate(last_validation, self.config)

            feedback = (
                None
                if passed
                else _build_feedback_prompt(
                    last_validation,
                    iteration,
                    self.config.feedback_max_errors,
                )
            )

            rec = IterationRecord(
                iteration=iteration,
                elapsed_seconds=iter_elapsed,
                validation=last_validation,
                passed=passed,
                feedback_prompt=feedback,
            )
            self._iterations.append(rec)

            # ── Catalog recording ───────────────────────────────
            if self.config.record_to_catalog:
                self._record_iteration(rec)

            # ── Log status ──────────────────────────────────────
            files = last_validation.get("files_total", 0)
            lines = last_validation.get("lines_total", 0)
            syn = len(last_validation.get("syntax_errors", []))
            smells_count = len(last_validation.get("smells", []))

            if passed:
                logger.info(
                    "✅ Iteration {} PASSED — {} files, {:,} lines, "
                    "{} syntax errors, {} smells in {:.1f}s",
                    iteration,
                    files,
                    lines,
                    syn,
                    smells_count,
                    iter_elapsed,
                )
                break
            else:
                logger.warning(
                    "❌ Iteration {} FAILED — {} files, {:,} lines, "
                    "{} syntax errors, {} smells in {:.1f}s — retrying…",
                    iteration,
                    files,
                    lines,
                    syn,
                    smells_count,
                    iter_elapsed,
                )

                # ── MCP health check before retry ──────────────
                await self._health_check_between_iterations()

        total_elapsed = time.time() - total_t0

        final_passed = bool(self._iterations and self._iterations[-1].passed)
        final_iter = self._iterations[-1].iteration if self._iterations else 0

        # ── Auto-mine catalog lessons (best-effort) ─────────
        if self.config.record_to_catalog:
            self._mine_catalog_lessons()

        logger.info(
            "Build loop finished: {} after {} iteration(s) in {:.1f}s",
            "PASSED" if final_passed else "FAILED",
            final_iter,
            total_elapsed,
        )

        return BuildResult(
            passed=final_passed,
            iteration=final_iter,
            total_elapsed=total_elapsed,
            iterations=list(self._iterations),
            project_dir=self.project_dir,
            final_validation=last_validation,
        )

    # ── Internals ──────────────────────────────────────────────

    def _record_iteration(self, rec: IterationRecord) -> None:
        """Record a single iteration to the build catalog (best-effort).

        Args:
            rec: The iteration record to persist.
        """
        try:
            from cuga.build_catalog import record_build

            record_build(self.spec, rec.validation, rec.elapsed_seconds)
        except Exception:
            logger.debug("Catalog recording skipped (non-critical)", exc_info=True)

    def _mine_catalog_lessons(self) -> None:
        """Run lesson mining after the loop completes (best-effort).

        Mines recurring failures and auto-adds lessons to
        ``catalog/optimizations.yaml`` so future builds benefit.
        """
        try:
            from cuga.build_catalog import mine_lessons

            new_lessons = mine_lessons()
            if new_lessons:
                logger.info("Auto-mined {} new catalog lessons", len(new_lessons))
        except Exception:
            logger.debug("Catalog lesson mining skipped (non-critical)", exc_info=True)

    async def _health_check_between_iterations(self) -> None:
        """Run MCP health checks between failed iterations (best-effort).

        If the manager and registry are available, checks server health
        and attempts to reconnect any dead servers before the next retry.
        """
        if self._mcp_manager is None:
            return

        try:
            from cuga.mcp_resilience import (
                health_check_servers,
                reconnect_failed_servers,
            )

            report = await health_check_servers(self._mcp_manager)
            if report.any_unhealthy and self._mcp_registry is not None:
                await reconnect_failed_servers(
                    self._mcp_manager,
                    self._mcp_registry,
                    server_names=report.unhealthy_names,
                )
        except Exception:
            logger.debug("MCP health check skipped (non-critical)", exc_info=True)


# ── CLI entry point (replaces one-click.sh logic) ─────────────


async def _cli_main(argv: list[str] | None = None) -> None:
    """Run the build loop from the command line.

    Equivalent to the old ``one-click.sh`` but in-process with
    error feedback.

    Args:
        argv: Explicit argument list (defaults to sys.argv).
    """
    import argparse
    import json
    import sys
    from datetime import UTC, datetime

    import yaml

    # Load .env
    try:
        from dotenv import find_dotenv, load_dotenv

        _env = find_dotenv(usecwd=True) or find_dotenv(usecwd=False)
        if _env:
            load_dotenv(_env, override=False)
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="Run the CUGA build loop (build→validate→feedback→retry).",
    )
    parser.add_argument("--spec", required=True, help="Path to the YAML spec.")
    parser.add_argument(
        "--tools",
        default="mcp_servers_local.yaml",
        help="Path to MCP servers config.",
    )
    parser.add_argument("--policy", default=None, help="Path to coding policy YAML.")
    parser.add_argument("--output", default="output", help="Output directory.")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=5,
        help="Max build→validate cycles (default: 5).",
    )
    parser.add_argument(
        "--require-lint",
        action="store_true",
        help="Require ruff lint to pass.",
    )
    args = parser.parse_args(argv)

    # ── Load spec + policy ──────────────────────────────────────
    with open(args.spec, encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    policy_text: str | None = None
    if args.policy and Path(args.policy).is_file():
        policy_text = Path(args.policy).read_text(encoding="utf-8")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = str(output_dir.resolve())

    project_name = spec.get("name", "project")
    project_dir = output_dir / project_name

    # ── Bootstrap MCP tools + CugaAgent via shared module ─────
    from cuga.mcp_bootstrap import bootstrap_mcp
    from cuga.mcp_resilience import wrap_tools_with_retry
    from cuga.sdk import CugaAgent
    from cuga.supervisor_strategy import create_build_supervisor, is_supervisor_enabled

    mcp_result = await bootstrap_mcp(
        mcp_servers_path=args.tools,
        workspace_root=workspace_root,
    )

    wrap_tools_with_retry(mcp_result.tools, max_retries=2)
    logger.info("Loaded {} tools", len(mcp_result.tools))

    if is_supervisor_enabled():
        logger.info("Supervisor mode enabled — creating multi-agent build supervisor")
        agent = create_build_supervisor(tools=mcp_result.tools)
    else:
        agent = CugaAgent(tools=mcp_result.tools)

    # ── Run the loop ────────────────────────────────────────────
    loop_config = BuildLoopConfig(
        max_iterations=args.max_iterations,
        require_lint_pass=args.require_lint,
    )

    build_loop = BuildLoop(
        spec=spec,
        agent=agent,
        project_dir=project_dir,
        config=loop_config,
        policy_text=policy_text,
        workspace_root=workspace_root,
        mcp_manager=mcp_result.manager,
        mcp_registry=mcp_result.registry,
    )

    result = await build_loop.run()

    # ── Write summary ───────────────────────────────────────────
    summary = {
        "passed": result.passed,
        "iterations": result.iteration,
        "total_elapsed_seconds": round(result.total_elapsed, 1),
        "files_total": result.final_validation.get("files_total", 0),
        "lines_total": result.final_validation.get("lines_total", 0),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    summary_file = output_dir / "build_loop_result.json"
    summary_file.write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("Build loop result written to {}", summary_file)

    if not result.passed:
        logger.error(
            "Build loop FAILED after {} iterations",
            result.iteration,
        )
        sys.exit(1)

    logger.info("🎉 Build loop PASSED on iteration {}", result.iteration)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for the build loop.

    Args:
        argv: Explicit argument list (defaults to sys.argv).
    """
    import asyncio

    asyncio.run(_cli_main(argv))


if __name__ == "__main__":
    main()
