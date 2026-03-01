"""Tests for the shell_tool module — command validation and execution."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from cuga.shell_tool import (
    ALLOWED_COMMANDS,
    BLOCKED_PATTERNS,
    _execute_shell,
    _validate_command,
    create_shell_tool,
)

# ── _validate_command tests ────────────────────────────────────


class TestValidateCommand:
    """Tests for the command allowlist and blocklist."""

    # ── Allowed commands ──────────────────────────────────

    @pytest.mark.parametrize(
        "cmd",
        [
            "python --version",
            "python3 -m pytest tests/",
            "pip install flask",
            "ruff check .",
            "ruff format --check .",
            "mypy src/",
            "pytest -v",
            "node --version",
            "npm install",
            "npx prettier --write .",
            "pnpm install",
            "cat README.md",
            "ls -la",
            "head -20 main.py",
            "grep -r 'def ' src/",
            "find . -name '*.py'",
            "mkdir -p src/utils",
            "touch __init__.py",
            "echo hello",
            "git status",
            "git add .",
            "docker build .",
            "make build",
            "curl https://example.com",
            "tree .",
        ],
    )
    def test_allowed_command_passes(self, cmd: str) -> None:
        assert _validate_command(cmd) is None

    # ── Blocked patterns ──────────────────────────────────

    @pytest.mark.parametrize(
        "cmd",
        [
            "rm -rf /",
            "rm -rf ~",
            "sudo apt install",
            "> /dev/null",
            "echo hello | sh",
            "echo hello | bash",
            "eval $(dangerous)",
            "exec bad-thing",
            "ls; rm secret.txt",
            "ls && rm -rf all",
            "mkfs /dev/sda",
            "dd if=/dev/zero of=disk",
            ":(){ :|:",
        ],
    )
    def test_blocked_pattern_rejected(self, cmd: str) -> None:
        result = _validate_command(cmd)
        assert result is not None
        assert "Blocked" in result or "dangerous" in result.lower()

    # ── Disallowed base commands ──────────────────────────

    @pytest.mark.parametrize(
        "cmd",
        [
            "rm -rf project/",
            "shutdown now",
            "reboot",
            "passwd root",
            "chown root:root file",
            "/usr/bin/dangerous_binary",
            "nc -l 8080",
        ],
    )
    def test_disallowed_command_rejected(self, cmd: str) -> None:
        result = _validate_command(cmd)
        assert result is not None
        assert "not in the allowed list" in result

    # ── Edge cases ────────────────────────────────────────

    def test_empty_command(self) -> None:
        assert _validate_command("") == "Empty command"

    def test_path_prefix_stripped(self) -> None:
        """Command with path prefix still resolves to base name."""
        assert _validate_command("/usr/bin/python --version") is None
        assert _validate_command("/usr/local/bin/git status") is None

    def test_command_with_pipes(self) -> None:
        """Pipe to an allowed command is ok (first token is checked)."""
        assert _validate_command("grep -r 'def' . | head -20") is None

    def test_command_with_redirect(self) -> None:
        """Output redirect is allowed when not to /dev/."""
        assert _validate_command("ls > files.txt") is None

    def test_blocked_redirect_to_dev(self) -> None:
        assert _validate_command("cat file > /dev/null") is not None


class TestAllowedCommandsSet:
    """Meta-tests for the ALLOWED_COMMANDS and BLOCKED_PATTERNS constants."""

    def test_allowed_commands_is_set(self) -> None:
        assert isinstance(ALLOWED_COMMANDS, set)

    def test_core_python_commands_present(self) -> None:
        for cmd in ("python", "python3", "pip", "ruff", "pytest", "mypy"):
            assert cmd in ALLOWED_COMMANDS

    def test_core_node_commands_present(self) -> None:
        for cmd in ("node", "npm", "npx", "pnpm", "tsc"):
            assert cmd in ALLOWED_COMMANDS

    def test_core_system_commands_present(self) -> None:
        for cmd in ("cat", "ls", "grep", "find", "mkdir", "git"):
            assert cmd in ALLOWED_COMMANDS

    def test_dangerous_commands_absent(self) -> None:
        """Dangerous commands must NOT be in the allowed list."""
        for cmd in ("rm", "shutdown", "reboot", "passwd", "chown", "nc", "kill"):
            assert cmd not in ALLOWED_COMMANDS

    def test_blocked_patterns_not_empty(self) -> None:
        assert len(BLOCKED_PATTERNS) > 5

    def test_blocked_patterns_cover_critical_dangers(self) -> None:
        assert "rm -rf /" in BLOCKED_PATTERNS
        assert "sudo " in BLOCKED_PATTERNS
        assert "eval " in BLOCKED_PATTERNS


# ── _execute_shell tests ───────────────────────────────────────


class TestExecuteShell:
    """Tests for _execute_shell()."""

    @pytest.mark.asyncio()
    async def test_simple_echo(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"CUGA_OUTPUT_DIR": str(tmp_path)}):
            result = await _execute_shell("echo hello-world")
        assert "hello-world" in result
        assert "Exit code: 0" in result

    @pytest.mark.asyncio()
    async def test_blocked_command_returns_error(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"CUGA_OUTPUT_DIR": str(tmp_path)}):
            result = await _execute_shell("sudo rm -rf /")
        assert "ERROR" in result
        assert "Blocked" in result

    @pytest.mark.asyncio()
    async def test_disallowed_command_returns_error(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"CUGA_OUTPUT_DIR": str(tmp_path)}):
            result = await _execute_shell("rm file.txt")
        assert "ERROR" in result
        assert "not in the allowed list" in result

    @pytest.mark.asyncio()
    async def test_working_dir_relative(self, tmp_path: Path) -> None:
        """Relative working_dir is appended to CUGA_OUTPUT_DIR."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "test.txt").write_text("content", encoding="utf-8")

        with patch.dict(os.environ, {"CUGA_OUTPUT_DIR": str(tmp_path)}):
            result = await _execute_shell("cat test.txt", working_dir="subdir")
        assert "content" in result

    @pytest.mark.asyncio()
    async def test_working_dir_created(self, tmp_path: Path) -> None:
        """Missing working_dir is automatically created."""
        with patch.dict(os.environ, {"CUGA_OUTPUT_DIR": str(tmp_path)}):
            result = await _execute_shell("pwd", working_dir="new_dir")
        assert "new_dir" in result
        assert (tmp_path / "new_dir").exists()

    @pytest.mark.asyncio()
    async def test_nonzero_exit_code(self, tmp_path: Path) -> None:
        """Non-zero exit code is reported."""
        with patch.dict(os.environ, {"CUGA_OUTPUT_DIR": str(tmp_path)}):
            result = await _execute_shell("python3 -c 'raise SystemExit(42)'")
        assert "Exit code: 42" in result

    @pytest.mark.asyncio()
    async def test_stderr_captured(self, tmp_path: Path) -> None:
        """Standard error output is captured."""
        with patch.dict(os.environ, {"CUGA_OUTPUT_DIR": str(tmp_path)}):
            result = await _execute_shell(
                "python3 -c 'import sys; sys.stderr.write(\"err-msg\\n\")'",
            )
        assert "err-msg" in result
        assert "STDERR" in result

    @pytest.mark.asyncio()
    async def test_long_output_truncated(self, tmp_path: Path) -> None:
        """Very long output is truncated to prevent OOM."""
        with patch.dict(os.environ, {"CUGA_OUTPUT_DIR": str(tmp_path)}):
            result = await _execute_shell(
                "python3 -c 'print(\"A\" * 20000)'",
            )
        assert "[truncated]" in result
        # Result should be significantly smaller than 20000 chars
        assert len(result) < 15000

    @pytest.mark.asyncio()
    async def test_empty_command(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"CUGA_OUTPUT_DIR": str(tmp_path)}):
            result = await _execute_shell("")
        assert "ERROR" in result


# ── create_shell_tool tests ────────────────────────────────────


class TestCreateShellTool:
    """Tests for the create_shell_tool() factory."""

    def test_returns_structured_tool(self) -> None:
        tool = create_shell_tool()
        assert tool.name == "execute_command"
        assert "shell" in tool.description.lower() or "command" in tool.description.lower()

    def test_has_sync_and_async(self) -> None:
        tool = create_shell_tool()
        assert tool.func is not None
        assert tool.coroutine is not None

    def test_args_schema_has_command(self) -> None:
        tool = create_shell_tool()
        schema = tool.args_schema
        assert schema is not None
        fields = schema.model_fields
        assert "command" in fields
        assert "working_dir" in fields
