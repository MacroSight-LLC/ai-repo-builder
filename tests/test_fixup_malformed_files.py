"""Tests for the _fixup_content helper in build_loop.py."""

from __future__ import annotations

import ast
import contextlib
import json
import sys
import textwrap
import types
from pathlib import Path

# ---------------------------------------------------------------------------
# Direct-load build_loop._fixup_content to avoid heavy cuga.config init chain.
# ---------------------------------------------------------------------------

# Provide a minimal loguru stub (build_loop imports loguru at module level)
if "loguru" not in sys.modules:
    _loguru_mod = types.ModuleType("loguru")

    class _FakeLogger:
        def info(self, *a: object, **kw: object) -> None: ...
        def warning(self, *a: object, **kw: object) -> None: ...
        def debug(self, *a: object, **kw: object) -> None: ...
        def error(self, *a: object, **kw: object) -> None: ...

    _loguru_mod.logger = _FakeLogger()  # type: ignore[attr-defined]
    sys.modules["loguru"] = _loguru_mod

_src = Path(__file__).resolve().parent.parent / "src" / "cuga" / "build_loop.py"
_source = _src.read_text(encoding="utf-8")

# Extract the _fixup_content and _try_strip_spurious_indent function sources
_tree = ast.parse(_source)
_func_sources: dict[str, str | None] = {
    "_fixup_content": None,
    "_try_strip_spurious_indent": None,
}
for node in ast.walk(_tree):
    if isinstance(node, ast.FunctionDef) and node.name in _func_sources:
        _func_sources[node.name] = ast.get_source_segment(_source, node)

assert _func_sources["_fixup_content"] is not None, "_fixup_content not found"
assert _func_sources["_try_strip_spurious_indent"] is not None, (
    "_try_strip_spurious_indent not found"
)

# Execute the function definitions in a clean namespace
_ns: dict = {"json": json, "textwrap": textwrap, "contextlib": contextlib}
for _name, src in _func_sources.items():
    exec(compile(src, "build_loop.py", "exec"), _ns)  # type: ignore[arg-type]
_fixup_content = _ns["_fixup_content"]
_try_strip_spurious_indent = _ns["_try_strip_spurious_indent"]


# ── Tests ──────────────────────────────────────────────────────


class TestJsonUnwrapping:
    """Verify JSON-wrapped file content is properly unwrapped."""

    def test_single_json_wrap(self) -> None:
        raw = '{"content": "from fastapi import FastAPI\\napp = FastAPI()\\n"}'
        result = _fixup_content(raw)
        assert result.startswith("from fastapi import FastAPI")
        assert "app = FastAPI()" in result
        assert '{"content"' not in result

    def test_double_json_wrap(self) -> None:
        inner = '{"content": "from fastapi import FastAPI\\n"}'
        outer = json.dumps({"content": inner})
        result = _fixup_content(outer)
        assert result.startswith("from fastapi import FastAPI")
        assert '{"content"' not in result

    def test_triple_json_wrap(self) -> None:
        core = "print('hello')\n"
        for _ in range(3):
            core = json.dumps({"content": core})
        result = _fixup_content(core)
        assert "print('hello')" in result
        assert '{"content"' not in result

    def test_non_json_passthrough(self) -> None:
        raw = "from pathlib import Path\n\np = Path('.')\n"
        result = _fixup_content(raw)
        assert "from pathlib import Path" in result

    def test_json_without_content_key(self) -> None:
        raw = '{"name": "test", "version": "1.0"}'
        result = _fixup_content(raw)
        # Should NOT unwrap — no "content" key
        assert '"name": "test"' in result


class TestExcessIndentation:
    """Verify excess leading indentation is stripped."""

    def test_uniform_4_space_indent(self) -> None:
        raw = "    from fastapi import FastAPI\n    app = FastAPI()\n"
        result = _fixup_content(raw)
        assert result.startswith("from fastapi import FastAPI")
        assert "\napp = FastAPI()" in result

    def test_mixed_indent_preserved(self) -> None:
        raw = "def hello():\n    return 'world'\n"
        result = _fixup_content(raw)
        assert "def hello():" in result
        assert "    return 'world'" in result

    def test_json_wrap_plus_indent(self) -> None:
        code = "    from fastapi import FastAPI\n    app = FastAPI()\n"
        raw = json.dumps({"content": code})
        result = _fixup_content(raw)
        assert result.startswith("from fastapi import FastAPI")
        assert '{"content"' not in result


class TestMixedIndent:
    """Verify the line-1-at-col-0 + lines-2+-at-col-4 CodeWrapper pattern."""

    def test_import_first_line_no_indent(self) -> None:
        raw = (
            "from fastapi import FastAPI\n"
            "    from cors import A\n"
            "    app = FastAPI()\n"
        )
        result = _fixup_content(raw)
        assert result == "from fastapi import FastAPI\nfrom cors import A\napp = FastAPI()\n"

    def test_mixed_indent_with_nested_def(self) -> None:
        raw = (
            "from fastapi import FastAPI\n"
            "    def foo():\n"
            "        return 1\n"
        )
        result = _fixup_content(raw)
        assert result == "from fastapi import FastAPI\ndef foo():\n    return 1\n"

    def test_block_opener_colon_preserved(self) -> None:
        raw = "def foo():\n    return 1\n"
        result = _fixup_content(raw)
        assert result == "def foo():\n    return 1\n"

    def test_block_opener_brace_preserved(self) -> None:
        raw = "module.exports = {\n    reactStrictMode: true,\n}\n"
        result = _fixup_content(raw)
        assert result == "module.exports = {\n    reactStrictMode: true,\n}\n"

    def test_block_opener_paren_preserved(self) -> None:
        raw = "SECRET_KEY = (\n    'very-long-key'\n)\n"
        result = _fixup_content(raw)
        assert result == "SECRET_KEY = (\n    'very-long-key'\n)\n"

    def test_comment_first_line_stripped(self) -> None:
        raw = "# Main module\n    from fastapi import FastAPI\n    app = FastAPI()\n"
        result = _fixup_content(raw)
        assert result == "# Main module\nfrom fastapi import FastAPI\napp = FastAPI()\n"

    def test_class_block_preserved(self) -> None:
        raw = "class MyApp:\n    x = 1\n    y = 2\n"
        result = _fixup_content(raw)
        assert result == "class MyApp:\n    x = 1\n    y = 2\n"


class TestEscapedNewlines:
    """Verify escaped newlines in JSON content are properly unescaped."""

    def test_literal_escaped_newlines(self) -> None:
        # Content where newlines are literal \n characters (not actual newlines)
        raw = '{"content": "line1\\nline2\\nline3"}'
        result = _fixup_content(raw)
        assert "line1" in result
        assert "line2" in result
        assert "\\n" not in result

    def test_real_newlines_preserved(self) -> None:
        raw = "line1\nline2\nline3\n"
        result = _fixup_content(raw)
        assert "line1\nline2\nline3" in result


class TestEdgeCases:
    """Edge cases for the fixup function."""

    def test_empty_string(self) -> None:
        result = _fixup_content("")
        assert result == ""

    def test_whitespace_only(self) -> None:
        result = _fixup_content("   \n   \n")
        assert result.strip() == ""

    def test_binary_looking_content(self) -> None:
        raw = "#!/bin/bash\nset -e\necho hello\n"
        result = _fixup_content(raw)
        assert "#!/bin/bash" in result


class TestTryStripSpuriousIndent:
    """Verify compile-based indent stripping for Python files."""

    def test_fixes_mixed_indent_python(self) -> None:
        # Real pattern: imports at col 0, code at col 4
        code = (
            "from fastapi import FastAPI\n"
            "from fastapi.middleware.cors import CORSMiddleware\n"
            "\n"
            "    app = FastAPI()\n"
            "\n"
            "    app.add_middleware(CORSMiddleware)\n"
        )
        result = _try_strip_spurious_indent(code, ".py")
        assert "    app" not in result
        assert "app = FastAPI()" in result
        compile(result, "<test>", "exec")

    def test_leaves_valid_python_alone(self) -> None:
        code = "def foo():\n    return 1\n"
        result = _try_strip_spurious_indent(code, ".py")
        assert result == code

    def test_skips_non_python_files(self) -> None:
        code = "    indented line\n    another\n"
        result = _try_strip_spurious_indent(code, ".ts")
        assert result == code

    def test_preserves_nested_indent(self) -> None:
        # After stripping 4 spaces, nested code should still be valid
        code = (
            "from fastapi import FastAPI\n"
            "\n"
            "    def handler():\n"
            "        return 'ok'\n"
        )
        result = _try_strip_spurious_indent(code, ".py")
        assert "def handler():" in result
        assert "    return 'ok'" in result
        compile(result, "<test>", "exec")

    def test_does_not_break_already_broken(self) -> None:
        # If stripping doesn't fix the syntax, return original
        code = "def foo(\n    bar\n    baz)\n"
        result = _try_strip_spurious_indent(code, ".py")
        assert result == code  # unchanged since the fix didn't help
