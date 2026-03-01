#!/usr/bin/env python3
"""
ai-repo-builder headless entry point.

Called by one-click.sh as:
    python -m cuga.main --spec <spec.yaml> --tools <mcp_servers.yaml> \
                        --policy <policy.yaml> --output <output_dir>

Reads a build spec, constructs a task prompt, and runs the CugaAgent SDK
against the configured MCP tool stack, then writes outputs to disk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import yaml
from loguru import logger

# Load .env before anything else — GitHub/WatsonX tokens needed by MCP servers
try:
    from dotenv import find_dotenv, load_dotenv

    _env = find_dotenv(usecwd=True) or find_dotenv(usecwd=False)
    if _env:
        load_dotenv(_env, override=False)
except ImportError:
    pass  # python-dotenv optional; env vars must be set manually


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the CUGA agent to build a repository from a spec file."
    )
    parser.add_argument(
        "--spec",
        required=True,
        help="Path to the YAML spec describing the repo to build.",
    )
    parser.add_argument(
        "--tools",
        default="mcp_servers.yaml",
        help="Path to the MCP servers YAML config (default: mcp_servers.yaml).",
    )
    parser.add_argument(
        "--policy",
        default=None,
        help="Path to the coding policy YAML file.",
    )
    parser.add_argument(
        "--output",
        default="output",
        help="Directory to write generated artefacts.",
    )
    return parser.parse_args(argv)


def _load_spec(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _load_policy(path: str | None) -> str | None:
    if path is None or not os.path.isfile(path):
        return None
    with open(path) as f:
        return f.read()


def _spec_to_prompt(
    spec: dict, policy_text: str | None, workspace_root: str = "/projects/workspace"
) -> str:
    """Convert a build spec + policy into a single agent prompt.

    Supports both:
    - Rich specs (from spec_generator.py): structure.files with key_contents, etc.
    - Simple specs (legacy): flat lists of features/structure strings.
    """
    # Detect rich spec format (has structure.files list of dicts)
    structure = spec.get("structure", {})
    is_rich = isinstance(structure, dict) and "files" in structure

    if is_rich:
        from cuga.spec_to_prompt import spec_to_prompt

        return spec_to_prompt(spec, policy_text, workspace_root=workspace_root)

    # ── Legacy simple-spec path (backward compat) ──────────────
    name = spec.get("name", "unnamed-project")
    desc = spec.get("description", "")
    stack = yaml.dump(spec.get("stack", {}), default_flow_style=False).strip()
    features = "\n".join(f"  - {f}" for f in spec.get("features", []))
    structure_str = "\n".join(f"  - {s}" for s in spec.get("structure", []))
    gates = "\n".join(f"  - {g}" for g in spec.get("quality_gates", []))

    prompt = textwrap.dedent(f"""\
        You are building a project called **{name}**.

        ## Description
        {desc}

        ## Tech Stack
        {stack}

        ## Required Features
        {features}

        ## Expected File Structure
        {structure_str}

        ## Quality Gates (every gate must pass)
        {gates}

        ---
        ## Available Tools
        - **filesystem**: Read/write/list files in {workspace_root}
        - **execute_command**: Run shell commands (pip, npm, pytest, ruff, docker, git)
        - **context7**: Look up library/framework documentation
        - **brave-search**: Search the web for solutions and examples
        - **postgres**: Execute SQL against the dev database
        - **memory**: Store/retrieve decisions for cross-file consistency
        - **sequential-thinking**: Plan complex multi-step reasoning
        - **puppeteer**: Headless browser for frontend verification

        ## Instructions
        1. Use context7 to look up docs for the chosen stack.
        2. Generate all files for this project under {workspace_root}.
        3. Write each file ONE AT A TIME using:
            result = await filesystem_write_file(path="{workspace_root}/<file>", content="...")
        4. CRITICAL: Call tools DIRECTLY with await — never use asyncio.to_thread, asyncio.run, or lambda wrappers.
        5. After writing code, use execute_command to run linting and tests. Fix any errors.
        6. Verify at least 3 key files by calling filesystem_read_file.
        7. Provide a summary of all files created and test results.
    """)

    if policy_text:
        prompt += f"\n## Coding Policy\n```yaml\n{policy_text}\n```\n"

    return prompt


async def _run_setup(args: argparse.Namespace) -> tuple:
    """Bootstrap MCP tools and CugaAgent from CLI args.

    Shared by ``_run()`` and ``generate.build_project()``.

    Args:
        args: Parsed CLI arguments (needs ``tools``, ``output``).

    Returns:
        Tuple of ``(agent, workspace_root, mcp_result)`` where *agent* is a
        ready-to-use ``CugaAgent``, *workspace_root* is the resolved output
        directory path string, and *mcp_result* is the bootstrap result
        containing the manager/registry for health checks.
    """
    from cuga.mcp_bootstrap import bootstrap_mcp
    from cuga.mcp_resilience import wrap_tools_with_retry
    from cuga.sdk import CugaAgent

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = str(output_dir.resolve())

    mcp_result = await bootstrap_mcp(
        mcp_servers_path=args.tools,
        workspace_root=workspace_root,
    )

    # Wrap tools with retry logic for resilience against transient failures
    wrap_tools_with_retry(mcp_result.tools, max_retries=2)

    # Optionally create a multi-agent supervisor instead of a single agent
    from cuga.supervisor_strategy import create_build_supervisor, is_supervisor_enabled

    if is_supervisor_enabled():
        logger.info("Supervisor mode enabled — creating multi-agent build supervisor")
        agent = create_build_supervisor(tools=mcp_result.tools)
    else:
        agent = CugaAgent(tools=mcp_result.tools)

    return agent, workspace_root, mcp_result


async def _run(args: argparse.Namespace) -> None:
    spec = _load_spec(args.spec)
    policy_text = _load_policy(args.policy)

    logger.info("Spec:   {}", args.spec)
    logger.info("Tools:  {}", args.tools)
    logger.info("Policy: {}", args.policy)
    logger.info("Output: {}", Path(args.output).resolve())

    agent, workspace_root, mcp_result = await _run_setup(args)
    output_dir = Path(args.output)

    # ── Run the build loop (build→validate→feedback→retry) ─────
    from cuga.build_loop import BuildLoop, BuildLoopConfig

    project_name = spec.get("name", "project")
    project_dir = output_dir / project_name

    loop_config = BuildLoopConfig(
        max_iterations=int(os.environ.get("CUGA_MAX_ITERATIONS", "5")),
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

    build_result = await build_loop.run()

    # Persist results
    result_payload = {
        "spec": args.spec,
        "passed": build_result.passed,
        "iterations": build_result.iteration,
        "total_elapsed_seconds": round(build_result.total_elapsed, 1),
        "files_total": build_result.final_validation.get("files_total", 0),
        "lines_total": build_result.final_validation.get("lines_total", 0),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    result_file = output_dir / "result.json"
    result_file.write_text(
        json.dumps(result_payload, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("Result written to {}", result_file)

    if build_result.passed:
        fv = build_result.final_validation
        logger.info(
            "✅ Build PASSED on iteration {} — {} files, {:,} lines in {:.1f}s",
            build_result.iteration,
            fv.get("files_total", 0),
            fv.get("lines_total", 0),
            build_result.total_elapsed,
        )
    else:
        logger.error(
            "❌ Build FAILED after {} iteration(s) in {:.1f}s",
            build_result.iteration,
            build_result.total_elapsed,
        )
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
