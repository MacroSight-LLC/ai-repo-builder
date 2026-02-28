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
from datetime import datetime, timezone
from pathlib import Path

import yaml
from loguru import logger


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


def _spec_to_prompt(spec: dict, policy_text: str | None) -> str:
    """Convert a build spec + policy into a single agent prompt."""
    name = spec.get("name", "unnamed-project")
    desc = spec.get("description", "")
    stack = yaml.dump(spec.get("stack", {}), default_flow_style=False).strip()
    features = "\n".join(f"  - {f}" for f in spec.get("features", []))
    structure = "\n".join(f"  - {s}" for s in spec.get("structure", []))
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
        {structure}

        ## Quality Gates (every gate must pass)
        {gates}

        ---
        Generate all files for this project under the workspace root.
        Use the filesystem tool to write each file.
        After writing files, use docker to verify the build compiles.
        Run the test suite. Fix any failures before finishing.
    """)

    if policy_text:
        prompt += f"\n## Coding Policy\n```yaml\n{policy_text}\n```\n"

    return prompt


async def _run(args: argparse.Namespace) -> None:
    # Point CUGA's registry at this project's MCP servers file
    os.environ.setdefault("MCP_SERVERS_FILE", str(Path(args.tools).resolve()))

    # Lazy-import after env vars are set so Dynaconf picks them up
    from cuga.sdk import CugaAgent  # noqa: E402

    spec = _load_spec(args.spec)
    policy_text = _load_policy(args.policy)
    prompt = _spec_to_prompt(spec, policy_text)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Spec:   {args.spec}")
    logger.info(f"Tools:  {args.tools}")
    logger.info(f"Policy: {args.policy}")
    logger.info(f"Output: {output_dir.resolve()}")
    logger.info("Prompt length: {} chars", len(prompt))

    # Build the agent (no explicit tools — the MCP registry supplies them)
    agent = CugaAgent()
    result = await agent.invoke(prompt)

    # Persist results
    result_payload = {
        "spec": args.spec,
        "answer": result.answer,
        "tool_calls": [tc.dict() if hasattr(tc, "dict") else str(tc) for tc in (result.tool_calls or [])],
        "thread_id": result.thread_id,
        "error": result.error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    result_file = output_dir / "result.json"
    result_file.write_text(json.dumps(result_payload, indent=2, default=str))
    logger.info(f"Result written to {result_file}")

    if result.error:
        logger.error(f"Agent error: {result.error}")
        sys.exit(1)

    logger.info("Agent completed successfully.")


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
