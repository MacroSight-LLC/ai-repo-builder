"""Tests for the build catalog system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cuga.build_catalog import (
    get_build_stats,
    get_lessons_for_prompt,
    load_history,
    load_optimizations,
    record_build,
)

# ── Fixtures ───────────────────────────────────────────────────

SAMPLE_SPEC: dict = {
    "name": "test-api",
    "description": "A test API",
    "stack": {
        "language": "python",
        "backend": {"framework": "fastapi"},
        "frontend": {"framework": "none"},
        "database": {"primary": "postgresql"},
    },
    "features": [{"name": "CRUD", "type": "crud"}],
}

SAMPLE_VALIDATION_PASS: dict = {
    "passed": True,
    "files_total": 12,
    "lines_total": 850,
    "syntax_errors": [],
    "lint_passed": True,
    "lint_output": "",
    "smells": [],
    "missing_spec_files": [],
    "missing_required": [],
    "missing_recommended": [],
    "summary": "✅ All good",
}

SAMPLE_VALIDATION_FAIL: dict = {
    "passed": False,
    "files_total": 8,
    "lines_total": 400,
    "syntax_errors": [{"file": "main.py", "line": 10, "issue": "SyntaxError"}],
    "lint_passed": False,
    "lint_output": "E001 something",
    "smells": [
        {
            "file": "main.py",
            "line": 5,
            "severity": "error",
            "issue": "Hardcoded secret/key",
            "code": "api_key = 'abc'",
        },
        {
            "file": "app.py",
            "line": 20,
            "severity": "error",
            "issue": "Empty pass statement — stub function",
            "code": "pass",
        },
        {
            "file": "app.py",
            "line": 30,
            "severity": "warn",
            "issue": "Bare except — catches everything",
            "code": "except:",
        },
        {
            "file": "util.py",
            "line": 1,
            "severity": "warn",
            "issue": "TODO comment — incomplete",
            "code": "# TODO fix",
        },
    ],
    "missing_spec_files": ["src/routes.py"],
    "missing_required": [".gitignore"],
    "missing_recommended": ["Dockerfile"],
    "summary": "❌ Failed",
}


@pytest.fixture()
def catalog_dir(tmp_path: Path) -> Path:
    """Create a temporary catalog directory."""
    cat = tmp_path / "catalog"
    cat.mkdir()
    return cat


@pytest.fixture()
def catalog_with_opts(catalog_dir: Path) -> Path:
    """Create a catalog with sample optimizations."""
    opts = {
        "global": [
            {
                "id": "g-001",
                "severity": "critical",
                "lesson": "Write .gitignore first",
                "context": "Prevents junk",
                "source": "human",
            },
            {
                "id": "g-002",
                "severity": "tip",
                "lesson": "Use memory tool",
                "context": "Consistency",
                "source": "human",
            },
        ],
        "by_stack": {
            "python/fastapi": [
                {
                    "id": "pf-001",
                    "severity": "critical",
                    "lesson": "Use future annotations",
                    "context": "PEP 604",
                    "source": "human",
                },
            ],
            "typescript/nextjs": [
                {
                    "id": "tn-001",
                    "severity": "important",
                    "lesson": "Use App Router",
                    "context": "Pages deprecated",
                    "source": "human",
                },
            ],
        },
        "by_pattern": {
            "stub_function": {
                "lesson": "Never write pass",
                "severity": "critical",
                "auto_count": 0,
                "source": "auto",
            },
            "hardcoded_secret": {
                "lesson": "Use env vars",
                "severity": "critical",
                "auto_count": 0,
                "source": "auto",
            },
            "bare_except": {
                "lesson": "Catch specific exceptions",
                "severity": "important",
                "auto_count": 3,
                "source": "auto",
            },
        },
    }
    (catalog_dir / "optimizations.yaml").write_text(
        yaml.dump(opts, default_flow_style=False)
    )
    return catalog_dir


# ── record_build ───────────────────────────────────────────────


class TestRecordBuild:
    def test_creates_history_file(self, catalog_dir: Path) -> None:
        path = record_build(
            SAMPLE_SPEC, SAMPLE_VALIDATION_PASS, 25.0, catalog_dir=catalog_dir
        )
        assert path.exists()
        assert path.name == "build_history.jsonl"

    def test_appends_valid_json(self, catalog_dir: Path) -> None:
        record_build(SAMPLE_SPEC, SAMPLE_VALIDATION_PASS, 25.0, catalog_dir=catalog_dir)
        record_build(SAMPLE_SPEC, SAMPLE_VALIDATION_FAIL, 45.0, catalog_dir=catalog_dir)

        history = (catalog_dir / "build_history.jsonl").read_text()
        lines = [ln for ln in history.strip().splitlines() if ln.strip()]
        assert len(lines) == 2

        rec1 = json.loads(lines[0])
        assert rec1["project_name"] == "test-api"
        assert rec1["stack"] == "python/fastapi"
        assert rec1["passed"] is True
        assert rec1["elapsed_seconds"] == 25.0

        rec2 = json.loads(lines[1])
        assert rec2["passed"] is False
        assert rec2["total_smells"] >= 4

    def test_records_smell_counts(self, catalog_dir: Path) -> None:
        record_build(SAMPLE_SPEC, SAMPLE_VALIDATION_FAIL, 30.0, catalog_dir=catalog_dir)
        history = load_history(catalog_dir)
        assert len(history) == 1
        smells = history[0]["smell_counts"]
        assert smells.get("hardcoded_secret", 0) >= 1
        assert smells.get("stub_function", 0) >= 1
        assert smells.get("bare_except", 0) >= 1

    def test_updates_pattern_counts(self, catalog_with_opts: Path) -> None:
        record_build(
            SAMPLE_SPEC, SAMPLE_VALIDATION_FAIL, 30.0, catalog_dir=catalog_with_opts
        )

        opts = yaml.safe_load((catalog_with_opts / "optimizations.yaml").read_text())
        assert opts["by_pattern"]["stub_function"]["auto_count"] >= 1
        assert opts["by_pattern"]["hardcoded_secret"]["auto_count"] >= 1
        # bare_except started at 3, should be incremented
        assert opts["by_pattern"]["bare_except"]["auto_count"] >= 4


# ── load_history ───────────────────────────────────────────────


class TestLoadHistory:
    def test_empty_on_missing_file(self, catalog_dir: Path) -> None:
        assert load_history(catalog_dir) == []

    def test_loads_records(self, catalog_dir: Path) -> None:
        record_build(SAMPLE_SPEC, SAMPLE_VALIDATION_PASS, 10.0, catalog_dir=catalog_dir)
        records = load_history(catalog_dir)
        assert len(records) == 1
        assert records[0]["project_name"] == "test-api"

    def test_handles_corrupt_lines(self, catalog_dir: Path) -> None:
        history_file = catalog_dir / "build_history.jsonl"
        history_file.write_text('{"valid": true}\nnot json\n{"also_valid": true}\n')
        records = load_history(catalog_dir)
        assert len(records) == 2


# ── load_optimizations ─────────────────────────────────────────


class TestLoadOptimizations:
    def test_empty_on_missing_file(self, catalog_dir: Path) -> None:
        opts = load_optimizations(catalog_dir)
        assert opts["global"] == []
        assert opts["by_stack"] == {}
        assert opts["by_pattern"] == {}

    def test_loads_file(self, catalog_with_opts: Path) -> None:
        opts = load_optimizations(catalog_with_opts)
        assert len(opts["global"]) == 2
        assert "python/fastapi" in opts["by_stack"]
        assert "stub_function" in opts["by_pattern"]


# ── get_lessons_for_prompt ─────────────────────────────────────


class TestGetLessonsForPrompt:
    def test_empty_without_catalog(self, catalog_dir: Path) -> None:
        assert get_lessons_for_prompt(SAMPLE_SPEC, catalog_dir=catalog_dir) == ""

    def test_includes_global_lessons(self, catalog_with_opts: Path) -> None:
        lessons = get_lessons_for_prompt(SAMPLE_SPEC, catalog_dir=catalog_with_opts)
        assert ".gitignore" in lessons
        assert "memory tool" in lessons

    def test_includes_stack_lessons(self, catalog_with_opts: Path) -> None:
        lessons = get_lessons_for_prompt(SAMPLE_SPEC, catalog_dir=catalog_with_opts)
        assert "future annotations" in lessons

    def test_excludes_wrong_stack(self, catalog_with_opts: Path) -> None:
        lessons = get_lessons_for_prompt(SAMPLE_SPEC, catalog_dir=catalog_with_opts)
        assert "App Router" not in lessons  # typescript/nextjs, not python/fastapi

    def test_includes_recurring_patterns(self, catalog_with_opts: Path) -> None:
        # bare_except has auto_count=3, should be included
        lessons = get_lessons_for_prompt(SAMPLE_SPEC, catalog_dir=catalog_with_opts)
        assert "specific exceptions" in lessons.lower() or "Catch" in lessons

    def test_includes_critical_patterns_even_if_zero_count(
        self, catalog_with_opts: Path
    ) -> None:
        # stub_function is critical severity, should appear even with auto_count=0
        lessons = get_lessons_for_prompt(SAMPLE_SPEC, catalog_dir=catalog_with_opts)
        assert "Never write pass" in lessons

    def test_respects_max_lessons(self, catalog_with_opts: Path) -> None:
        lessons = get_lessons_for_prompt(
            SAMPLE_SPEC, max_lessons=2, catalog_dir=catalog_with_opts
        )
        # Should have at most 2 lesson entries (plus header lines)
        lesson_lines = [
            ln for ln in lessons.splitlines() if ln.startswith(("🔴", "🟡", "💡"))
        ]
        assert len(lesson_lines) <= 2

    def test_has_header(self, catalog_with_opts: Path) -> None:
        lessons = get_lessons_for_prompt(SAMPLE_SPEC, catalog_dir=catalog_with_opts)
        assert "Lessons from Past Builds" in lessons

    def test_frontend_stack_matching(self, catalog_with_opts: Path) -> None:
        ts_spec = {
            **SAMPLE_SPEC,
            "stack": {
                "language": "typescript",
                "backend": {"framework": "express"},
                "frontend": {"framework": "nextjs"},
                "database": {"primary": "postgresql"},
            },
        }
        lessons = get_lessons_for_prompt(ts_spec, catalog_dir=catalog_with_opts)
        assert "App Router" in lessons


# ── get_build_stats ────────────────────────────────────────────


class TestGetBuildStats:
    def test_empty_history(self, catalog_dir: Path) -> None:
        stats = get_build_stats(catalog_dir)
        assert stats["total_builds"] == 0
        assert stats["trend"] == "no_data"

    def test_basic_stats(self, catalog_dir: Path) -> None:
        record_build(SAMPLE_SPEC, SAMPLE_VALIDATION_PASS, 20.0, catalog_dir=catalog_dir)
        record_build(SAMPLE_SPEC, SAMPLE_VALIDATION_FAIL, 40.0, catalog_dir=catalog_dir)
        record_build(SAMPLE_SPEC, SAMPLE_VALIDATION_PASS, 15.0, catalog_dir=catalog_dir)

        stats = get_build_stats(catalog_dir)
        assert stats["total_builds"] == 3
        assert stats["pass_rate"] == pytest.approx(66.7, abs=0.1)
        assert stats["avg_time"] == pytest.approx(25.0, abs=0.1)
        assert ("python/fastapi", 3) in stats["top_stacks"]

    def test_smell_aggregation(self, catalog_dir: Path) -> None:
        record_build(SAMPLE_SPEC, SAMPLE_VALIDATION_FAIL, 30.0, catalog_dir=catalog_dir)
        record_build(SAMPLE_SPEC, SAMPLE_VALIDATION_FAIL, 35.0, catalog_dir=catalog_dir)

        stats = get_build_stats(catalog_dir)
        smell_names = [s[0] for s in stats["most_common_smells"]]
        assert any("stub" in n or "secret" in n or "bare" in n for n in smell_names)


# ── Smell classification ───────────────────────────────────────


class TestSmellClassification:
    """Tests that smell categorization maps to the correct canonical patterns."""

    def test_hardcoded_password_classified_as_secret(self, catalog_dir: Path) -> None:
        """'Hardcoded password' must map to hardcoded_secret, not stub_function."""
        validation = {
            **SAMPLE_VALIDATION_PASS,
            "passed": False,
            "smells": [
                {
                    "file": "db.py",
                    "line": 5,
                    "severity": "error",
                    "issue": "Hardcoded password",
                    "code": 'password = "hunter2"',
                },
            ],
        }
        record_build(SAMPLE_SPEC, validation, 20.0, catalog_dir=catalog_dir)
        history = load_history(catalog_dir)
        smells = history[0]["smell_counts"]
        assert smells.get("hardcoded_secret", 0) >= 1
        assert smells.get("stub_function", 0) == 0

    def test_pass_statement_classified_as_stub(self, catalog_dir: Path) -> None:
        """'Empty pass statement' must map to stub_function."""
        validation = {
            **SAMPLE_VALIDATION_PASS,
            "passed": False,
            "smells": [
                {
                    "file": "app.py",
                    "line": 10,
                    "severity": "error",
                    "issue": "Empty pass statement — stub function",
                    "code": "pass",
                },
            ],
        }
        record_build(SAMPLE_SPEC, validation, 20.0, catalog_dir=catalog_dir)
        history = load_history(catalog_dir)
        smells = history[0]["smell_counts"]
        assert smells.get("stub_function", 0) >= 1

    def test_empty_spec_records_safely(self, catalog_dir: Path) -> None:
        """An empty spec should record without crashing."""
        path = record_build({}, SAMPLE_VALIDATION_PASS, 5.0, catalog_dir=catalog_dir)
        assert path.exists()
        history = load_history(catalog_dir)
        assert len(history) == 1
        assert history[0]["project_name"] == "unknown"

    def test_negative_elapsed_clamped_to_zero(self, catalog_dir: Path) -> None:
        """Negative elapsed_seconds should be clamped to 0."""
        record_build(SAMPLE_SPEC, SAMPLE_VALIDATION_PASS, -5.0, catalog_dir=catalog_dir)
        history = load_history(catalog_dir)
        assert history[0]["elapsed_seconds"] == 0.0
