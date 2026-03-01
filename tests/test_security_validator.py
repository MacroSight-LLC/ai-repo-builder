"""Tests for SecurityValidator — verifies string-literal stripping
prevents false positives while still blocking real threats."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

# ── Stub the entire import chain so we can load security.py in isolation ──
# The SecurityValidator module only truly depends on ast, re, loguru, and
# a single function ``is_benchmark_mode`` from benchmark_mode.  We stub
# everything else so the test doesn't pull in the full cuga application.

# Provide a minimal benchmark_mode module
_bm = ModuleType("cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.benchmark_mode")
_bm.is_benchmark_mode = lambda: False  # type: ignore[attr-defined]
sys.modules["cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.benchmark_mode"] = _bm

# Load the security module directly from file, bypassing the cuga package
# init which pulls in heavy dependencies.
_sec_path = (
    pathlib.Path(__file__).resolve().parent.parent
    / "src"
    / "cuga"
    / "backend"
    / "cuga_graph"
    / "nodes"
    / "cuga_lite"
    / "executors"
    / "common"
    / "security.py"
)
_spec = importlib.util.spec_from_file_location(
    "cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.security",
    _sec_path,
)
assert _spec is not None and _spec.loader is not None
_security_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _security_mod
_spec.loader.exec_module(_security_mod)

SecurityValidator = _security_mod.SecurityValidator  # type: ignore[attr-defined]


class TestStringLiteralStripping:
    """Verify _strip_string_literals removes string content."""

    def test_double_quoted(self) -> None:
        result = SecurityValidator._strip_string_literals('x = "os.environ[KEY]"')
        assert "os.environ" not in result

    def test_single_quoted(self) -> None:
        result = SecurityValidator._strip_string_literals("x = 'subprocess.run()'")
        assert "subprocess.run" not in result

    def test_triple_quoted(self) -> None:
        result = SecurityValidator._strip_string_literals('x = """import os\nos.system("rm -rf /")"""')
        assert "os.system" not in result

    def test_executable_code_preserved(self) -> None:
        result = SecurityValidator._strip_string_literals("os.environ['KEY']")
        # The dict key is stripped but os.environ is outside strings
        assert "os.environ" in result


class TestFalsePositivesPrevented:
    """Agent writing file content should NOT trigger security violations."""

    def test_env_example_content(self) -> None:
        """Writing .env.example should not be blocked."""
        code = (
            'content = "NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1"\n'
            'await filesystem_write_file(path="/output/.env.example", content=content)\n'
        )
        # Should not raise
        SecurityValidator.validate_wrapped_code(code)

    def test_gitignore_with_dot_env(self) -> None:
        """Gitignore referencing .env should not be blocked."""
        code = (
            'gitignore = "node_modules/\\n__pycache__/\\n.env\\n.env.local"\n'
            'await filesystem_write_file(path="/output/.gitignore", content=gitignore)\n'
        )
        SecurityValidator.validate_wrapped_code(code)

    def test_readme_with_os_environ_mention(self) -> None:
        """README mentioning os.environ should not be blocked."""
        code = (
            'readme = "To configure, set os.environ[API_KEY] in your shell."\n'
            'await filesystem_write_file(path="/output/README.md", content=readme)\n'
        )
        SecurityValidator.validate_wrapped_code(code)

    def test_python_source_with_imports_as_content(self) -> None:
        """Writing a Python file whose content has import os should not be blocked."""
        code = (
            'main_py = "from __future__ import annotations\\nimport os\\n"\n'
            'await filesystem_write_file(path="/output/main.py", content=main_py)\n'
        )
        SecurityValidator.validate_wrapped_code(code)

    def test_docker_compose_with_socket_mention(self) -> None:
        """Docker compose content should not be blocked."""
        code = (
            'compose = "services:\\n  api:\\n    image: python-requests:latest"\n'
            'await filesystem_write_file(path="/output/docker-compose.yml", content=compose)\n'
        )
        SecurityValidator.validate_wrapped_code(code)

    def test_requirements_txt_content(self) -> None:
        """requirements.txt with package names should not be blocked."""
        code = (
            'reqs = "fastapi==0.104.1\\nrequests==2.31.0\\nuvicorn==0.23.2"\n'
            'await filesystem_write_file(path="/output/requirements.txt", content=reqs)\n'
        )
        SecurityValidator.validate_wrapped_code(code)


class TestRealThreatsBlocked:
    """Actual dangerous code must still be caught."""

    def test_os_import_blocked(self) -> None:
        code = "import os\nkey = os.environ['SECRET']\n"
        with pytest.raises((PermissionError, ImportError)):
            SecurityValidator.validate_wrapped_code(code)

    def test_subprocess_import_blocked(self) -> None:
        code = "import subprocess\nsubprocess.run(['ls'])\n"
        with pytest.raises((PermissionError, ImportError)):
            SecurityValidator.validate_wrapped_code(code)

    def test_eval_call_blocked(self) -> None:
        code = "result = eval('1+1')\n"
        with pytest.raises(PermissionError):
            SecurityValidator.validate_wrapped_code(code)

    def test_exec_call_blocked(self) -> None:
        code = "exec('print(1)')\n"
        with pytest.raises(PermissionError):
            SecurityValidator.validate_wrapped_code(code)

    def test_builtins_access_blocked(self) -> None:
        code = "x = __builtins__['open']\n"
        with pytest.raises(PermissionError):
            SecurityValidator.validate_wrapped_code(code)

    def test_sys_import_blocked(self) -> None:
        code = "import sys\nsys.exit(0)\n"
        with pytest.raises((PermissionError, ImportError)):
            SecurityValidator.validate_wrapped_code(code)

    def test_pathlib_call_blocked(self) -> None:
        code = "pathlib.Path('/etc/passwd').read_text()\n"
        with pytest.raises(PermissionError):
            SecurityValidator.validate_wrapped_code(code)

    def test_pickle_blocked(self) -> None:
        code = "pickle.loads(data)\n"
        with pytest.raises(PermissionError):
            SecurityValidator.validate_wrapped_code(code)
