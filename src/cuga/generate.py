#!/usr/bin/env python3
"""
Plain-English → Working Project  —  two-stage pipeline entry point.

Usage examples:

    # Interactive mode (prompt at terminal)
    python -m cuga.generate

    # One-liner from the command line
    python -m cuga.generate "Build a SaaS invoicing platform with Stripe integration"

    # Read description from a file
    python -m cuga.generate --from-file brief.txt

    # Only generate the spec (skip building)
    python -m cuga.generate --spec-only "A FastAPI microservice for PDF generation"

    # Non-interactive (no confirmation prompt)
    python -m cuga.generate --no-confirm "A Next.js + FastAPI task manager"

    # Build from a previously generated spec (skip Stage 1)
    python -m cuga.generate --from-spec specs/my-project-20260228-120000.yaml

Stage 1:  LLM converts plain English → structured YAML spec
Stage 2:  CUGA agent builds the spec → working code on disk
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

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

from cuga.spec_generator import (
    SPEC_SYSTEM_PROMPT,
    build_spec_prompt,
    parse_spec_response,
    save_spec,
)
from cuga.spec_validator_tool import validate_spec_yaml

__all__ = ["build_project", "generate_spec", "main"]
# ── CLI ────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the generate pipeline.

    Args:
        argv: Explicit argument list (defaults to sys.argv).

    Returns:
        Parsed namespace with all generate flags.
    """
    parser = argparse.ArgumentParser(
        description="Generate a full project from a plain-English description.",
    )
    parser.add_argument(
        "description",
        nargs="?",
        default=None,
        help="Plain-English project description (interactive if omitted).",
    )
    parser.add_argument(
        "--from-file",
        default=None,
        help="Read the project description from a text file.",
    )
    parser.add_argument(
        "--from-spec",
        default=None,
        help="Build from a previously generated spec YAML (skips Stage 1).",
    )
    parser.add_argument(
        "--spec-only",
        action="store_true",
        help="Only produce the spec YAML; do not run the CUGA agent.",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip the interactive confirmation before building.",
    )
    parser.add_argument(
        "--tools",
        default="mcp_servers_local.yaml",
        help="Path to MCP servers YAML config (default: mcp_servers_local.yaml).",
    )
    parser.add_argument(
        "--policy",
        default="policies/coding-policy.yaml",
        help="Path to coding policy YAML (default: policies/coding-policy.yaml).",
    )
    parser.add_argument(
        "--output",
        default="output",
        help="Directory for generated artefacts.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max self-correction retries for spec generation (default: 3).",
    )
    parser.add_argument(
        "--github",
        action="store_true",
        help="Create a GitHub repo and push the generated code.",
    )
    parser.add_argument(
        "--github-owner",
        default=None,
        help="GitHub owner/org for the new repo (default: $GITHUB_OWNER env var).",
    )
    parser.add_argument(
        "--private",
        action="store_true",
        default=True,
        help="Make the GitHub repo private (default: True).",
    )
    parser.add_argument(
        "--public",
        action="store_true",
        help="Make the GitHub repo public (overrides --private).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate spec and print the full agent prompt without building.",
    )
    return parser.parse_args(argv)


# ── GitHub config injection ────────────────────────────────────────


def _inject_github_config(spec: dict, args: argparse.Namespace) -> dict:
    """Add github section to spec if --github flag is set."""
    if not getattr(args, "github", False):
        return spec

    owner = args.github_owner or os.environ.get("GITHUB_OWNER", "")
    if not owner:
        logger.warning("--github set but no owner specified (use --github-owner or $GITHUB_OWNER)")

    visibility = "public" if getattr(args, "public", False) else "private"
    spec["github"] = {
        "create_repo": True,
        "owner": owner,
        "visibility": visibility,
        "branch": "main",
        "description": spec.get("description", ""),
    }
    logger.info(
        "GitHub publishing enabled → {}/{} ({})",
        owner,
        spec.get("name", "?"),
        visibility,
    )
    return spec


# ── Stage 1: NL → Spec ────────────────────────────────────────────


async def generate_spec(
    user_input: str,
    max_retries: int = 3,
) -> dict:
    """Use WatsonX to convert plain English into a validated YAML spec.

    Includes a self-correction loop: if the spec fails validation the
    errors are fed back and the LLM tries again (up to *max_retries*).
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_ibm import ChatWatsonx

    llm = ChatWatsonx(
        model_id=os.environ.get(
            "SPEC_MODEL",
            "meta-llama/llama-4-maverick-17b-128e-instruct-fp8",
        ),
        temperature=0.3,
        max_tokens=16_000,
        project_id=os.environ.get("WATSONX_PROJECT_ID", ""),
    )

    if not os.environ.get("WATSONX_PROJECT_ID"):
        logger.error("WATSONX_PROJECT_ID env var is not set — cannot call WatsonX")
        sys.exit(1)

    messages = [
        SystemMessage(content=SPEC_SYSTEM_PROMPT),
        HumanMessage(content=build_spec_prompt(user_input)),
    ]

    spec: dict | None = None

    for attempt in range(1, max_retries + 1):
        logger.info("Stage 1 — generating spec (attempt {}/{})", attempt, max_retries)
        response = await llm.ainvoke(messages)
        raw_text = response.content

        logger.debug("LLM response length: {} chars", len(raw_text))

        # Parse
        try:
            spec = parse_spec_response(raw_text)
        except yaml.YAMLError as exc:
            logger.warning("YAML parse error on attempt {}: {}", attempt, exc)
            messages.append(response)
            messages.append(
                HumanMessage(
                    content=(
                        f"Your YAML output had a parse error:\n{exc}\n\n"
                        "Please fix the YAML and regenerate the COMPLETE spec.  "
                        "Output ONLY the raw YAML — no markdown fences."
                    ),
                )
            )
            continue

        # Validate
        vresult = validate_spec_yaml(yaml.dump(spec, default_flow_style=False))

        if vresult["valid"]:
            logger.info(
                "Spec valid! {} files, {} features, {} entities, {} endpoints",
                vresult["stats"].get("files_planned", 0),
                vresult["stats"].get("features", 0),
                vresult["stats"].get("entities", 0),
                vresult["stats"].get("endpoints", 0),
            )
            if vresult["warnings"]:
                for w in vresult["warnings"]:
                    logger.warning("  ⚠ {}", w)
            return spec

        # Invalid — feed errors back for self-correction
        errors_str = "\n".join(f"  - {e}" for e in vresult["errors"])
        warnings_str = "\n".join(f"  - {w}" for w in vresult["warnings"])
        logger.warning(
            "Spec failed validation (attempt {}/{}):\n{}",
            attempt,
            max_retries,
            errors_str,
        )
        messages.append(response)
        messages.append(
            HumanMessage(
                content=(
                    "The spec you produced has validation errors.  "
                    "Fix ALL errors and regenerate the COMPLETE spec.\n\n"
                    f"ERRORS:\n{errors_str}\n\n"
                    f"WARNINGS:\n{warnings_str}\n\n"
                    "Output ONLY the corrected raw YAML — no markdown fences."
                ),
            )
        )

    # Exhausted retries — return best-effort spec if we got one
    logger.error("Failed to produce a valid spec after {} attempts", max_retries)
    if spec is not None:
        logger.warning("Returning last (invalid) spec for manual review")
        return spec
    sys.exit(1)


# ── Stage 2: Spec → Project ───────────────────────────────────────


def _load_post_build_settings() -> Any:
    """Load post-build MCP settings from settings.toml.

    Returns:
        A ``PostBuildSettings`` instance with values from config or defaults.
    """
    from cuga.post_build_mcp import PostBuildSettings

    try:
        from cuga.config import settings as app_settings

        mcp_cfg = getattr(app_settings, "mcp_integrations", None)
        if mcp_cfg:
            return PostBuildSettings(
                docker_verify=getattr(mcp_cfg, "docker_verify", True),
                qradar_scan=getattr(mcp_cfg, "qradar_scan", False),
                auto_deploy=getattr(mcp_cfg, "auto_deploy", False),
                instana_monitor=getattr(mcp_cfg, "instana_monitor", False),
                devops_pipeline=getattr(mcp_cfg, "devops_pipeline", False),
            )
    except Exception:
        pass

    return PostBuildSettings()


async def build_project(
    spec: dict,
    tools_path: str,
    policy_path: str | None,
    output_dir: str,
) -> bool:
    """Run the CUGA agent to build the project from a structured spec.

    Uses the BuildLoop for in-process build→validate→feedback→retry.
    Records build results to the catalog automatically.

    Returns:
        True if the build passed the quality gate, False otherwise.
    """
    from cuga.build_loop import BuildLoop, BuildLoopConfig
    from cuga.main import _load_policy, _run_setup
    from cuga.main import _parse_args as main_parse_args

    # Save spec to a working file
    spec_file = Path(output_dir) / "_active_spec.yaml"
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        yaml.dump(spec, default_flow_style=False, sort_keys=False, width=120),
        encoding="utf-8",
    )

    # Build args for main._run
    cli_args = [
        "--spec",
        str(spec_file),
        "--tools",
        tools_path,
        "--output",
        output_dir,
    ]
    if policy_path and Path(policy_path).is_file():
        cli_args.extend(["--policy", policy_path])

    args = main_parse_args(cli_args)

    # Bootstrap MCP tools + agent via the shared setup helper
    agent, workspace_root, _mcp = await _run_setup(args)

    policy_text = _load_policy(args.policy)

    project_name = spec.get("name", "project")
    project_dir = Path(output_dir) / project_name

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
        mcp_manager=_mcp.manager,
        mcp_registry=_mcp.registry,
    )

    t0 = time.time()
    result = await build_loop.run()
    elapsed = time.time() - t0

    # ── Post-build MCP actions (optional, best-effort) ─────────
    if result.passed:
        try:
            from cuga.post_build_mcp import (
                PostBuildSettings,
                run_post_build_actions,
            )

            pb_settings = _load_post_build_settings()
            pb_report = await run_post_build_actions(
                project_dir=project_dir,
                spec=spec,
                mcp_manager=_mcp.manager,
                settings=pb_settings,
            )
            if not pb_report.all_passed:
                logger.warning("Some post-build actions failed (non-blocking)")
        except Exception:
            logger.debug("Post-build MCP actions skipped", exc_info=True)

    # ── Post-build summary ─────────────────────────────────────
    if project_dir.exists():
        files_on_disk = sorted(f for f in project_dir.rglob("*") if f.is_file())
        total_bytes = sum(f.stat().st_size for f in files_on_disk)
        print(f"\n{'─' * 50}")
        status = "✅ PASSED" if result.passed else "❌ FAILED"
        print(
            f"  {status} — {len(files_on_disk)} files ({total_bytes:,} bytes) "
            f"in {elapsed:.1f}s ({result.iteration} iteration(s))"
        )
        print(f"{'─' * 50}")
        for fp in files_on_disk:
            rel = fp.relative_to(project_dir)
            size = fp.stat().st_size
            print(f"  {rel}  ({size:,} B)")
        print(f"{'─' * 50}")
    else:
        logger.warning("Expected output dir {} not found", project_dir)

    return result.passed

# ── Pretty-print helpers ──────────────────────────────────────────


def _print_spec_summary(spec: dict) -> None:
    """Print a concise summary of the generated spec.

    Args:
        spec: The parsed project spec dictionary.
    """
    name = spec.get("name", "?")
    desc = spec.get("description", "")
    stack = spec.get("stack", {})
    files = spec.get("structure", {}).get("files", [])
    features = spec.get("features", [])
    entities = spec.get("data_model", {}).get("entities", [])

    print("\n" + "=" * 60)
    print(f"  Project: {name}")
    print(f"  {desc[:100]}{'...' if len(desc) > 100 else ''}")
    print("-" * 60)
    print(
        f"  Stack:    {stack.get('language', '?')} / "
        f"{stack.get('backend', {}).get('framework', '?')} / "
        f"{stack.get('database', {}).get('primary', '?')}"
    )

    fe = stack.get("frontend", {})
    if fe and fe.get("framework") not in (None, "none"):
        print(f"  Frontend: {fe.get('framework', '?')} + {fe.get('styling', '?')}")

    print(f"  Files:    {len(files)}")
    print(f"  Features: {len(features)}")
    print(f"  Entities: {len(entities)}")
    print("-" * 60)

    if files:
        print("  Files to generate:")
        for f in files[:15]:
            print(f"    - {f.get('path', '?')}")
        if len(files) > 15:
            print(f"    ... and {len(files) - 15} more")
    print("=" * 60 + "\n")


def _print_build_result(spec: dict, output_dir: str, passed: bool) -> None:
    """Print the build-complete result message.

    Args:
        spec: The project spec.
        output_dir: Output directory path.
        passed: Whether the build passed the quality gate.
    """
    project_name = spec.get("name", "project")
    gh = spec.get("github", {})
    if passed:
        if gh.get("create_repo"):
            owner = gh.get("owner") or os.environ.get("GITHUB_OWNER", "")
            print(f"\n✅ Project built and pushed! https://github.com/{owner}/{project_name}")
        else:
            print(f"\n✅ Project built! Check: {output_dir}/{project_name}/")
    else:
        print(f"\n⚠️  Build completed with issues. Check: {output_dir}/{project_name}/")
        print("   Run validation manually: python -m cuga.post_build validate "
              f"{output_dir}/{project_name}/")


# ── Main ──────────────────────────────────────────────────────────


async def _run_pipeline(args: argparse.Namespace) -> None:
    """Execute the full generate pipeline (Stage 1 → Stage 2).

    Handles all modes: --from-spec, --from-file, --spec-only, --dry-run,
    --github, interactive, and one-liner descriptions.

    Args:
        args: Parsed CLI arguments from ``_parse_args``.
    """
    # ── From-spec shortcut (skip Stage 1) ──────────────────────
    if args.from_spec:
        spec_file = Path(args.from_spec)
        if not spec_file.is_file():
            print(f"Spec file not found: {spec_file}")
            sys.exit(1)
        spec = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
        logger.info("Loaded existing spec from {}", spec_file)

        # Inject GitHub config if --github flag is set
        spec = _inject_github_config(spec, args)

        _print_spec_summary(spec)

        if not args.no_confirm:
            answer = input("Proceed to build this project? [Y/n] ").strip().lower()
            if answer in ("n", "no"):
                print("Aborted.")
                return

        print("\n🔨 Stage 2: Building project with CUGA agent...\n")
        passed = await build_project(
            spec=spec,
            tools_path=args.tools,
            policy_path=args.policy,
            output_dir=args.output,
        )
        _print_build_result(spec, args.output, passed)
        return

    # ── Get user input ─────────────────────────────────────────
    if args.from_file:
        from_file = Path(args.from_file)
        if not from_file.is_file():
            print(f"File not found: {from_file}")
            sys.exit(1)
        user_input = from_file.read_text(encoding="utf-8").strip()
    elif args.description:
        user_input = args.description.strip()
    else:
        # Interactive mode
        print("\n🏗️  AI Repo Builder — Plain English → Working Project\n")
        print("Describe the project you want to build.")
        print("Be as detailed as you want — technology preferences, features,")
        print("database requirements, deployment needs, etc.\n")
        print("(Press Enter twice or Ctrl-D when done)\n")
        lines: list[str] = []
        try:
            while True:
                line = input("> " if not lines else "  ")
                if line == "" and lines and lines[-1] == "":
                    break
                lines.append(line)
        except EOFError:
            pass
        user_input = "\n".join(lines).strip()

    if not user_input:
        print("No description provided. Exiting.")
        sys.exit(1)

    logger.info(
        "Input description ({} chars):\n{}",
        len(user_input),
        textwrap.indent(user_input, "  "),
    )

    # ── Stage 1: Generate spec ─────────────────────────────────
    print("\n📋 Stage 1: Generating project specification...\n")
    spec = await generate_spec(user_input, max_retries=args.max_retries)

    # Inject GitHub config if --github flag is set
    spec = _inject_github_config(spec, args)

    # Save spec
    spec_path = save_spec(spec)
    logger.info("Spec saved to {}", spec_path)

    _print_spec_summary(spec)

    if args.spec_only:
        print(f"✅ Spec written to {spec_path}")
        print("   (--spec-only mode; skipping build stage)")
        return

    # ── Dry run: show prompt, skip building ─────────────────────
    if args.dry_run:
        from cuga.spec_to_prompt import spec_to_prompt as _stp

        _policy = None
        _pp = Path(args.policy)
        if _pp.is_file():
            _policy = _pp.read_text(encoding="utf-8")
        _output = str(Path(args.output).resolve())
        prompt = _stp(spec, _policy, workspace_root=_output)
        print(f"\n{'═' * 60}")
        print("  DRY RUN — Full Agent Prompt")
        print(f"{'═' * 60}")
        print(prompt)
        print(f"{'═' * 60}")
        print(f"  Prompt length: {len(prompt):,} chars")
        print(f"  Spec saved:    {spec_path}")
        print(f"{'═' * 60}")
        return

    # ── Confirmation ───────────────────────────────────────────
    if not args.no_confirm:
        answer = input("Proceed to build this project? [Y/n] ").strip().lower()
        if answer in ("n", "no"):
            print(f"Spec saved to {spec_path} — you can edit and build later with:")
            print(f"  python -m cuga.main --spec {spec_path} --tools {args.tools}")
            return

    # ── Stage 2: Build project ─────────────────────────────────
    print("\n🔨 Stage 2: Building project with CUGA agent...\n")
    passed = await build_project(
        spec=spec,
        tools_path=args.tools,
        policy_path=args.policy,
        output_dir=args.output,
    )

    _print_build_result(spec, args.output, passed)


def _run_async(coro: Any) -> Any:
    """Run async code safely — handles nested event loops (Jupyter, IDE runners).

    Args:
        coro: An awaitable coroutine to execute.

    Returns:
        The coroutine's return value.

    Raises:
        RuntimeError: If an event loop is running and nest-asyncio is not installed.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        try:
            import nest_asyncio

            nest_asyncio.apply()
            return loop.run_until_complete(coro)
        except ImportError:
            raise RuntimeError(
                "An event loop is already running. Install nest-asyncio: pip install nest-asyncio"
            ) from None
    return asyncio.run(coro)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for the generate pipeline.

    Args:
        argv: Explicit argument list (defaults to sys.argv).
    """
    args = _parse_args(argv)
    _run_async(_run_pipeline(args))


if __name__ == "__main__":
    main()
