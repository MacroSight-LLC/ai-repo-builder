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
import time
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


async def _run(args: argparse.Namespace) -> None:
    mcp_servers_path = str(Path(args.tools).resolve())
    os.environ.setdefault("MCP_SERVERS_FILE", mcp_servers_path)

    # Lazy-import after env vars are set so Dynaconf picks them up
    from cuga.backend.tools_env.registry.config.config_loader import (
        load_service_configs,
    )
    from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
    from cuga.backend.tools_env.registry.registry.api_registry import ApiRegistry
    from cuga.mcp_direct_tools import create_tools_from_mcp_manager
    from cuga.sdk import CugaAgent

    spec = _load_spec(args.spec)
    policy_text = _load_policy(args.policy)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = str(output_dir.resolve())

    prompt = _spec_to_prompt(spec, policy_text, workspace_root=workspace_root)

    logger.info(f"Spec:   {args.spec}")
    logger.info(f"Tools:  {args.tools}")
    logger.info(f"Policy: {args.policy}")
    logger.info(f"Output: {output_dir.resolve()}")
    logger.info("Prompt length: {} chars", len(prompt))

    # ── Load MCP tools in-process from the YAML config ──────────────
    services = load_service_configs(mcp_servers_path)

    # Resolve ENV: references in auth values (e.g. "ENV:GITHUB_TOKEN")
    for svc in services.values():
        if svc.auth and isinstance(svc.auth.value, str) and svc.auth.value.startswith("ENV:"):
            env_key = svc.auth.value[4:]
            resolved = os.environ.get(env_key, "")
            if not resolved:
                logger.warning(f"Auth env var {env_key} not set for {svc.name}")
            svc.auth.value = resolved

    # Ensure the filesystem MCP is scoped to the resolved output directory
    if "filesystem" in services and services["filesystem"].args:
        fs_args = services["filesystem"].args
        # Replace the last arg (./output or relative path) with absolute output dir
        fs_args[-1] = workspace_root
        logger.info("Filesystem MCP scoped to {}", workspace_root)

    manager = MCPManager(config=services)
    registry = ApiRegistry(client=manager)

    # Start MCP servers — individual server failures are handled gracefully
    # by MCPManager (logged + skipped), so we only retry on catastrophic failures.
    try:
        await registry.start_servers()
    except Exception as exc:
        logger.error("Failed to start MCP servers: {}", exc)
        raise

    # Verify each server is responsive by checking it returned tools
    live_servers = list(manager.tools_by_server.keys())
    failed = list(getattr(manager, "initialization_errors", {}).keys())
    logger.info(
        "MCP servers: {} live ({}), {} failed ({})",
        len(live_servers),
        ", ".join(live_servers) if live_servers else "none",
        len(failed),
        ", ".join(failed) if failed else "none",
    )

    if not live_servers:
        logger.error("No MCP servers connected — cannot proceed")
        sys.exit(1)

    # Log any servers that failed to connect
    if hasattr(manager, "initialization_errors") and manager.initialization_errors:
        for srv_name, err in manager.initialization_errors.items():
            logger.warning("MCP server '{}' unavailable: {}", srv_name, err.get("error", "unknown"))

    # Create tools that call MCP servers *directly* via the manager,
    # bypassing the registry HTTP proxy (which isn't running locally).
    all_tools = create_tools_from_mcp_manager(manager)

    # Add native shell execution tool (replaces desktop-commander MCP)
    from cuga.shell_tool import create_shell_tool

    os.environ["CUGA_OUTPUT_DIR"] = str(output_dir.resolve())
    all_tools.append(create_shell_tool())

    logger.info(
        f"Loaded {len(all_tools)} tools ({len(manager.tools_by_server)} MCP servers + shell)"
    )
    for t in all_tools:
        logger.debug(f"  Tool: {t.name}")

    # Build the agent with the loaded MCP tools
    agent = CugaAgent(tools=all_tools)

    t0 = time.time()
    result = await agent.invoke(prompt)
    elapsed = time.time() - t0

    # Persist results
    result_payload = {
        "spec": args.spec,
        "answer": result.answer,
        "tool_calls": [
            tc.dict() if hasattr(tc, "dict") else str(tc) for tc in (result.tool_calls or [])
        ],
        "thread_id": result.thread_id,
        "error": result.error,
        "timestamp": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(elapsed, 1),
    }
    result_file = output_dir / "result.json"
    result_file.write_text(json.dumps(result_payload, indent=2, default=str))
    logger.info(f"Result written to {result_file}")

    # ── Post-build file summary ────────────────────────────────
    project_name = spec.get("name", "project")
    project_dir = output_dir / project_name
    if project_dir.exists():
        # Run post-build validation and fixes
        from cuga.post_build import post_build_validate

        pb_summary = post_build_validate(project_dir, spec)
        files_on_disk = pb_summary["file_count"]
        total_bytes = pb_summary["total_bytes"]
        logger.info("Built {} files ({:,} bytes) in {:.1f}s", files_on_disk, total_bytes, elapsed)

        if pb_summary["syntax_errors"]:
            logger.warning("{} Python syntax errors found", len(pb_summary["syntax_errors"]))
        if pb_summary["missing_files"]:
            logger.warning("{} expected files missing", len(pb_summary["missing_files"]))
    else:
        logger.info("Agent completed in {:.1f}s", elapsed)

    if result.error:
        logger.error(f"Agent error: {result.error}")
        sys.exit(1)

    logger.info("Agent completed successfully.")


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
