"""
Supervisor Build Strategy — Multi-agent builds via CugaSupervisor.

Provides a factory that — when supervisor mode is enabled — creates a
``CugaSupervisor`` with specialised sub-agents instead of a single
``CugaAgent``.  The supervisor delegates tasks, each sub-agent brings
its own system-prompt personality, and the results are aggregated.

The supervisor is *invoke-compatible* with ``CugaAgent``, so the
``BuildLoop`` can use it as a drop-in replacement.

Usage::

    from cuga.supervisor_strategy import create_build_supervisor

    supervisor = create_build_supervisor(tools=mcp_tools)
    result = await supervisor.invoke("Build a FastAPI task manager")
"""

from __future__ import annotations

from typing import Any

from loguru import logger

__all__ = [
    "create_build_supervisor",
    "is_supervisor_enabled",
]


# ── Special instructions for each sub-agent ────────────────────

_ARCHITECT_INSTRUCTIONS = """\
You are the **Architect** agent.  Your job is to:
1. Analyse the project specification and break it into implementation tasks.
2. Design the directory structure and file layout.
3. Create every file with a proper skeleton (imports, class/function stubs with real parameter names).
4. Write configuration files (pyproject.toml, Dockerfile, docker-compose, .env.example, .gitignore).
5. Hand off to the Coder by writing a clear task list of remaining implementation work.

Rules:
- ALWAYS create files using the filesystem tools — never just describe them.
- Every Python file starts with ``from __future__ import annotations``.
- Use pathlib.Path, f-strings, and Google-style docstrings.
- Never leave TODO/FIXME comments or stub functions with ``pass``.
"""

_CODER_INSTRUCTIONS = """\
You are the **Coder** agent.  Your job is to:
1. Take the architect's scaffolding and implement ALL business logic.
2. Fill in every function body — no stubs, no placeholders, no TODOs.
3. Write comprehensive tests (pytest) with fixtures and edge cases.
4. Run ``ruff check`` and ``ruff format`` via the shell tool and fix any issues.

Rules:
- ALWAYS use the filesystem tools to write/edit files — never just describe code.
- Every function must have full type hints and a docstring.
- Never hardcode secrets — use environment variables.
- Write at least 10 tests covering happy path, edge cases, and error handling.
"""

_REVIEWER_INSTRUCTIONS = """\
You are the **Reviewer** agent.  Your job is to:
1. Read every file in the project using the filesystem tools.
2. Check for: syntax errors, missing imports, stub functions, bare excepts,
   hardcoded secrets, TODO comments, missing tests.
3. Fix EVERY issue you find by writing corrected files.
4. Run ``ruff check . --fix`` and ``ruff format .`` via the shell tool.
5. Run ``python -m pytest tests/ -v`` and fix any test failures.

Rules:
- Do NOT just report issues — FIX them by rewriting the files.
- After fixing, re-run ruff and pytest to verify.
- The project must have zero syntax errors and zero ruff violations.
"""


def is_supervisor_enabled() -> bool:
    """Check whether supervisor mode is enabled in settings.

    Returns:
        True if ``settings.supervisor.enabled`` is True.
    """
    try:
        from cuga.config import settings

        return bool(getattr(settings, "supervisor", {}).get("enabled", False))
    except Exception:
        return False


def create_build_supervisor(
    tools: list[Any],
    *,
    strategy: str = "sequential",
    model: Any | None = None,
) -> Any:
    """Create a CugaSupervisor with specialised build sub-agents.

    The supervisor delegates to three agents in sequence:
    1. **Architect** — designs structure, creates scaffolding
    2. **Coder** — implements all business logic and tests
    3. **Reviewer** — reviews, fixes issues, runs lint & tests

    The returned supervisor is invoke-compatible with ``CugaAgent``,
    so it can be used as a drop-in replacement in ``BuildLoop``.

    Args:
        tools: LangChain-compatible tools (MCP + shell) shared by all agents.
        strategy: Execution strategy: ``"sequential"`` (default) or
            ``"parallel"`` or ``"adaptive"``.
        model: Optional LLM model override for all agents.

    Returns:
        A ``CugaSupervisor`` instance.
    """
    from cuga.sdk import CugaAgent, CugaSupervisor

    # Each sub-agent gets the same tools but different instructions
    architect = CugaAgent(
        tools=tools,
        special_instructions=_ARCHITECT_INSTRUCTIONS,
        model=model,
    )
    coder = CugaAgent(
        tools=tools,
        special_instructions=_CODER_INSTRUCTIONS,
        model=model,
    )
    reviewer = CugaAgent(
        tools=tools,
        special_instructions=_REVIEWER_INSTRUCTIONS,
        model=model,
    )

    supervisor = CugaSupervisor(
        agents={
            "architect": architect,
            "coder": coder,
            "reviewer": reviewer,
        },
        description=(
            "Build supervisor coordinating three specialised agents: "
            "Architect (scaffolding), Coder (implementation), and "
            "Reviewer (quality assurance)."
        ),
        model=model,
    )

    logger.info(
        "Created build supervisor with strategy='{}' and 3 sub-agents",
        strategy,
    )

    return supervisor
