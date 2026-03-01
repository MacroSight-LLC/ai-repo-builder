"""
Native shell execution tool for the AI Repo Builder agent.

Replaces the broken desktop-commander MCP with a direct Python
implementation that's more reliable and security-scoped.

All commands run inside the project output directory.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

# ── Safety ───────────────────────────────────────────────────────

ALLOWED_COMMANDS = {
    # Python ecosystem
    "python", "python3", "pip", "pip3", "uv", "ruff", "mypy", "pytest",
    "black", "isort", "alembic", "flask", "uvicorn", "gunicorn", "celery",
    # Node ecosystem
    "node", "npm", "npx", "pnpm", "yarn", "tsc", "eslint", "prettier",
    "next", "vite",
    # System utilities
    "cat", "ls", "head", "tail", "wc", "grep", "find", "mkdir", "cp",
    "mv", "touch", "echo", "pwd", "env", "which", "tree", "diff", "sort",
    "uniq", "sed", "awk", "tr", "cut", "xargs", "chmod",
    # Build & container
    "docker", "docker-compose", "make", "cmake",
    # Version control
    "git",
    # Network (read-only)
    "curl", "wget",
}

BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "sudo ",
    "> /dev/",
    "| sh",
    "| bash",
    "eval ",
    "exec ",
    "; rm ",
    "&& rm -rf",
    "mkfs",
    "dd if=",
    ":(){ :|:",
]


def _validate_command(command: str) -> str | None:
    """Return an error message if the command is unsafe, else None."""
    for pattern in BLOCKED_PATTERNS:
        if pattern in command:
            return f"Blocked: command contains dangerous pattern '{pattern}'"

    # Extract the base command (first word)
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()

    if not parts:
        return "Empty command"

    base_cmd = Path(parts[0]).name  # strip path prefix
    if base_cmd not in ALLOWED_COMMANDS:
        return (
            f"Command '{base_cmd}' is not in the allowed list. "
            f"Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"
        )
    return None


# ── Tool implementation ──────────────────────────────────────────

class ShellInput(BaseModel):
    command: str = Field(description="The shell command to execute")
    working_dir: str = Field(
        default="",
        description="Working directory (relative to project root). Leave empty for project root.",
    )


# ── Error line patterns (for smart truncation) ──────────────────

_ERROR_LINE_PATTERN = re.compile(
    r"(error|Error|ERROR|FAIL|FAILED|Traceback|Exception|ModuleNotFoundError"
    r"|ImportError|SyntaxError|TypeError|ValueError|NameError"
    r"|AttributeError|KeyError|AssertionError|FileNotFoundError"
    r"|raise |assert |❌|CRITICAL)",
)


def _smart_truncate(text: str, max_chars: int) -> str:
    """Truncate text while prioritising error-relevant lines.

    Instead of naively keeping head + tail (which drops errors in the middle),
    this function:
    1. Always keeps the first 20 lines (context).
    2. Keeps ALL lines matching error patterns.
    3. Always keeps the last 30 lines (final summary / exit info).
    4. Fills remaining budget with surrounding context.

    Args:
        text: Raw output text to truncate.
        max_chars: Target character budget.

    Returns:
        Truncated text with ``[truncated]`` markers.
    """
    if len(text) <= max_chars:
        return text

    lines = text.splitlines()
    if len(lines) <= 60:
        # Short enough to keep head + tail approach
        return text[:max_chars // 2] + "\n...[truncated]...\n" + text[-(max_chars // 3):]

    head_count = 20
    tail_count = 30
    head = lines[:head_count]
    tail = lines[-tail_count:]

    # Find error lines in the middle section
    middle = lines[head_count:-tail_count]
    error_lines: list[str] = []
    for i, line in enumerate(middle):
        if _ERROR_LINE_PATTERN.search(line):
            # Include 1 line of context before and after
            start = max(0, i - 1)
            end = min(len(middle), i + 2)
            for ctx_line in middle[start:end]:
                if ctx_line not in error_lines:
                    error_lines.append(ctx_line)

    # Assemble
    parts = head
    if error_lines:
        parts.append(f"\n...[{len(middle) - len(error_lines)} lines truncated — showing errors]...\n")
        parts.extend(error_lines)
    else:
        parts.append(f"\n...[{len(middle)} lines truncated]...\n")
    parts.extend(tail)

    result = "\n".join(parts)
    # Final safety trim if still over budget
    if len(result) > max_chars:
        result = result[:max_chars - 50] + "\n...[final truncation]..."
    return result


async def _execute_shell(command: str, working_dir: str = "") -> str:
    """Execute a shell command in the project output directory."""
    # Validate
    error = _validate_command(command)
    if error:
        return f"ERROR: {error}"

    # Resolve working directory
    output_root = os.environ.get("CUGA_OUTPUT_DIR", "./output")
    cwd = str(Path(output_root) / working_dir) if working_dir else output_root

    # Ensure the directory exists
    Path(cwd).mkdir(parents=True, exist_ok=True)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=120,
        )

        result_parts = []
        if stdout:
            out_text = stdout.decode(errors="replace")
            if len(out_text) > 8000:
                out_text = _smart_truncate(out_text, 8000)
            result_parts.append(out_text)
        if stderr:
            err_text = stderr.decode(errors="replace")
            if len(err_text) > 4000:
                err_text = _smart_truncate(err_text, 4000)
            result_parts.append(f"STDERR:\n{err_text}")

        result_parts.append(f"Exit code: {proc.returncode}")
        return "\n".join(result_parts)

    except TimeoutError:
        return "ERROR: Command timed out after 120 seconds"
    except Exception as e:
        return f"ERROR: {e}"


def _sync_execute_shell(command: str, working_dir: str = "") -> str:
    """Sync wrapper for the shell tool."""
    loop = asyncio.get_event_loop()
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, _execute_shell(command, working_dir)).result()
    return loop.run_until_complete(_execute_shell(command, working_dir))


def create_shell_tool() -> StructuredTool:
    """Create a LangChain StructuredTool for shell execution."""
    return StructuredTool(
        name="execute_command",
        description=(
            "Execute a shell command in the project directory. "
            "Use for: installing dependencies (pip install, npm install), "
            "running tests (pytest, npm test), linting (ruff check, eslint), "
            "type checking (mypy), building containers (docker build), "
            "git operations, and verifying file contents (cat, ls, tree). "
            "Commands are security-scoped to a safe allowlist."
        ),
        args_schema=ShellInput,
        func=_sync_execute_shell,
        coroutine=_execute_shell,
    )
