"""Tests for post-build validation."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cuga.post_build import (
    _SKIP_DIRS,
    _SOURCE_EXTENSIONS,
    _TEXT_EXTENSIONS,
    _iter_project_files,
    check_llm_smells,
    check_required_files,
    check_spec_completeness,
    fix_indentation,
    post_build_validate,
    run_ruff_check,
    validate_project,
    validate_python_syntax,
)


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal valid project."""
    (tmp_path / ".gitignore").write_text("__pycache__/\n.env\n")
    (tmp_path / "README.md").write_text("# Test Project\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "src" / "main.py").write_text(
        'from __future__ import annotations\n\n\ndef main() -> str:\n    """Entry point."""\n    return "hello"\n'
    )
    return tmp_path


# ── Syntax checks ─────────────────────────────────────────────


class TestValidatePythonSyntax:
    def test_valid_files(self, project_dir: Path) -> None:
        errors = validate_python_syntax(project_dir)
        assert errors == []

    def test_catches_syntax_error(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text("def foo(\n")
        errors = validate_python_syntax(tmp_path)
        assert len(errors) == 1
        assert "bad.py" in errors[0]


# ── LLM smell detection ───────────────────────────────────────


class TestCheckLlmSmells:
    def test_clean_code(self, project_dir: Path) -> None:
        smells = check_llm_smells(project_dir)
        assert smells == []

    def test_catches_todo(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1  # TODO: fix this\n")
        smells = check_llm_smells(tmp_path)
        assert any("TODO" in s["issue"] for s in smells)

    def test_catches_stub(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("def foo():\n    pass\n")
        smells = check_llm_smells(tmp_path)
        assert any(
            "stub" in s["issue"].lower() or "pass" in s["issue"].lower() for s in smells
        )

    def test_catches_not_implemented(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("def foo():\n    raise NotImplementedError\n")
        smells = check_llm_smells(tmp_path)
        assert any("NotImplementedError" in s["issue"] for s in smells)

    def test_catches_hardcoded_password(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text('password = "hunter2"\n')
        smells = check_llm_smells(tmp_path)
        assert any(
            "password" in s["issue"].lower() or "secret" in s["issue"].lower()
            for s in smells
        )

    def test_catches_bare_except(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("try:\n    x = 1\nexcept:\n    pass\n")
        smells = check_llm_smells(tmp_path)
        assert any("Bare except" in s["issue"] for s in smells)

    def test_catches_wildcard_import(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("from os import *\n")
        smells = check_llm_smells(tmp_path)
        assert any("Wildcard" in s["issue"] for s in smells)

    def test_scans_js_files(self, tmp_path: Path) -> None:
        """JS files should also be scanned for LLM smells."""
        (tmp_path / "app.js").write_text("// TODO: implement login\n")
        smells = check_llm_smells(tmp_path)
        assert any("TODO" in s["issue"] for s in smells)
        assert smells[0]["file"] == "app.js"

    def test_scans_ts_files(self, tmp_path: Path) -> None:
        """TypeScript files should also be scanned for LLM smells."""
        (tmp_path / "service.ts").write_text("const secret = 'abc123'\n")
        smells = check_llm_smells(tmp_path)
        assert any("secret" in s["issue"].lower() for s in smells)

    def test_scans_tsx_files(self, tmp_path: Path) -> None:
        (tmp_path / "App.tsx").write_text("// FIXME: broken component\n")
        smells = check_llm_smells(tmp_path)
        assert any("FIXME" in s["issue"] for s in smells)

    def test_smell_has_severity(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1  # TODO: fix\n")
        smells = check_llm_smells(tmp_path)
        assert len(smells) > 0
        assert "severity" in smells[0]
        assert smells[0]["severity"] in ("warn", "error")


# ── Spec completeness ─────────────────────────────────────────


class TestCheckSpecCompleteness:
    def test_all_files_present(self, project_dir: Path) -> None:
        spec = {
            "structure": {
                "files": [
                    {"path": "src/main.py"},
                    {"path": "README.md"},
                ]
            }
        }
        missing = check_spec_completeness(project_dir, spec)
        assert missing == []

    def test_detects_missing(self, project_dir: Path) -> None:
        spec = {
            "structure": {
                "files": [
                    {"path": "src/main.py"},
                    {"path": "src/routes.py"},
                ]
            }
        }
        missing = check_spec_completeness(project_dir, spec)
        assert "src/routes.py" in missing

    def test_handles_legacy_list_structure(self, project_dir: Path) -> None:
        spec = {"structure": ["src/main.py", "src/missing.py"]}
        missing = check_spec_completeness(project_dir, spec)
        assert "src/missing.py" in missing

    def test_handles_dotslash_prefix(self, project_dir: Path) -> None:
        """Paths like ./src/main.py should match src/main.py on disk."""
        spec = {
            "structure": {
                "files": [
                    {"path": "./src/main.py"},
                    {"path": "./README.md"},
                ]
            }
        }
        missing = check_spec_completeness(project_dir, spec)
        assert missing == []

    def test_handles_non_dict_file_entries(self, project_dir: Path) -> None:
        """String entries in files list should be handled gracefully."""
        spec = {
            "structure": {
                "files": [
                    "src/main.py",
                    "src/nonexistent.py",
                ]
            }
        }
        missing = check_spec_completeness(project_dir, spec)
        assert "src/nonexistent.py" in missing
        assert "src/main.py" not in missing


# ── Required files ─────────────────────────────────────────────


class TestCheckRequiredFiles:
    def test_all_present(self, project_dir: Path) -> None:
        missing_req, _ = check_required_files(project_dir)
        assert missing_req == []

    def test_missing_gitignore(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Hi\n")
        _, missing_rec = check_required_files(tmp_path)
        assert ".gitignore" in missing_rec

    def test_missing_readme(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("*.pyc\n")
        missing_req, _ = check_required_files(tmp_path)
        assert "README.md" in missing_req

    def test_missing_recommended(self, project_dir: Path) -> None:
        _, missing_rec = check_required_files(project_dir)
        assert ".env.example" in missing_rec
        assert "Dockerfile" in missing_rec


# ── Indentation fix ────────────────────────────────────────────


class TestFixIndentation:
    def test_fixes_indented_file(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("    def foo():\n        return 1\n")
        count = fix_indentation(tmp_path)
        assert count == 1
        assert (tmp_path / "main.py").read_text() == "def foo():\n    return 1\n"

    def test_no_fix_needed(self, project_dir: Path) -> None:
        count = fix_indentation(project_dir)
        assert count == 0


# ── Ruff check ─────────────────────────────────────────────────


class TestRunRuffCheck:
    @pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not installed")
    def test_clean_code_passes(self, project_dir: Path) -> None:
        code, _output = run_ruff_check(project_dir)
        assert code == 0

    def test_bad_code_fails(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text("import os\\nimport sys\\nx=1\\n")
        code, _output = run_ruff_check(tmp_path)
        # ruff should find unused imports
        assert code != 0 or "ruff not found" in _output


# ── validate_project (rich report) ─────────────────────────────


class TestValidateProject:
    def test_valid_project_passes(self, project_dir: Path) -> None:
        report = validate_project(project_dir)
        assert report["passed"] is True
        assert report["files_total"] > 0
        assert report["lines_total"] > 0
        assert "✅" in report["summary"]

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        report = validate_project(tmp_path / "nonexistent")
        assert report["passed"] is False
        assert report["files_total"] == 0

    def test_with_spec(self, project_dir: Path) -> None:
        spec = {
            "structure": {
                "files": [
                    {"path": "src/main.py"},
                    {"path": "src/missing.py"},
                ]
            }
        }
        report = validate_project(project_dir, spec)
        assert "src/missing.py" in report["missing_spec_files"]

    def test_catches_syntax_error(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("")
        (tmp_path / "README.md").write_text("")
        (tmp_path / "bad.py").write_text("def foo(\n")
        report = validate_project(tmp_path)
        assert report["passed"] is False
        assert len(report["syntax_errors"]) > 0

    def test_catches_error_smells(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("")
        (tmp_path / "README.md").write_text("")
        (tmp_path / "stub.py").write_text("def foo():\n    raise NotImplementedError\n")
        report = validate_project(tmp_path)
        assert report["passed"] is False
        assert any(s["severity"] == "error" for s in report["smells"])

    def test_counts_dotfiles(self, tmp_path: Path) -> None:
        """Dotfiles like .gitignore must be counted in files_total."""
        (tmp_path / ".gitignore").write_text("__pycache__/\n")
        (tmp_path / ".env.example").write_text("KEY=\n")
        (tmp_path / "README.md").write_text("# Hi\n")
        report = validate_project(tmp_path)
        assert report["files_total"] >= 3  # .gitignore, .env.example, README.md

    def test_excludes_git_dir(self, tmp_path: Path) -> None:
        """Files inside .git/ should NOT be counted."""
        (tmp_path / ".gitignore").write_text("")
        (tmp_path / "README.md").write_text("")
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        report = validate_project(tmp_path)
        # .git/HEAD should not appear in files_total
        assert report["files_total"] == 2


# ── post_build_validate backward compat ────────────────────────


class TestPostBuildValidate:
    def test_returns_summary_dict(self, project_dir: Path) -> None:
        spec = {"structure": {"files": [{"path": "src/main.py"}]}}
        result = post_build_validate(project_dir, spec)
        assert "syntax_errors" in result
        assert "missing_files" in result
        assert "file_count" in result
        assert "llm_smells" in result


# ── _iter_project_files helper ─────────────────────────────────


class TestIterProjectFiles:
    def test_returns_all_files(self, project_dir: Path) -> None:
        files = _iter_project_files(project_dir)
        assert len(files) > 0

    def test_filters_by_extension(self, project_dir: Path) -> None:
        files = _iter_project_files(project_dir, frozenset({".py"}))
        assert all(f.suffix == ".py" for f in files)
        assert len(files) >= 1  # at least main.py and __init__.py

    def test_skips_pycache(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "app.cpython-312.pyc").write_text("")
        files = _iter_project_files(tmp_path)
        names = [f.name for f in files]
        assert "app.py" in names
        assert "app.cpython-312.pyc" not in names

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        (tmp_path / "index.js").write_text("console.log('hi')\n")
        nm = tmp_path / "node_modules" / "lodash"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}\n")
        files = _iter_project_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "index.js"

    def test_skips_dot_git(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Hi\n")
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        files = _iter_project_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "README.md"

    def test_returns_sorted(self, tmp_path: Path) -> None:
        (tmp_path / "b.py").write_text("")
        (tmp_path / "a.py").write_text("")
        files = _iter_project_files(tmp_path)
        assert files == sorted(files)

    def test_none_extensions_returns_all(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.txt").write_text("")
        (tmp_path / "Dockerfile").write_text("")
        files = _iter_project_files(tmp_path, extensions=None)
        assert len(files) == 3


class TestSmellsSkipDirs:
    def test_skips_node_modules(self, tmp_path: Path) -> None:
        """Files inside node_modules should not trigger smell detection."""
        nm = tmp_path / "node_modules" / "dep"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("// TODO: implement\n")
        (tmp_path / "app.py").write_text("x = 1\n")
        smells = check_llm_smells(tmp_path)
        assert smells == []

    def test_skips_venv(self, tmp_path: Path) -> None:
        """Files inside .venv should not trigger smell detection."""
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "bad.py").write_text("# TODO: fix\n")
        (tmp_path / "app.py").write_text("x = 1\n")
        smells = check_llm_smells(tmp_path)
        assert smells == []


class TestSharedConstants:
    def test_skip_dirs_is_frozenset(self) -> None:
        assert isinstance(_SKIP_DIRS, frozenset)
        assert "__pycache__" in _SKIP_DIRS
        assert "node_modules" in _SKIP_DIRS

    def test_text_extensions_includes_python(self) -> None:
        assert ".py" in _TEXT_EXTENSIONS
        assert ".js" in _TEXT_EXTENSIONS
        assert "" in _TEXT_EXTENSIONS  # extensionless

    def test_source_extensions_subset_of_text(self) -> None:
        assert _SOURCE_EXTENSIONS <= _TEXT_EXTENSIONS
