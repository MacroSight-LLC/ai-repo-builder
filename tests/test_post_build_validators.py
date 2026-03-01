"""Tests for new post_build validators: run_tests, validate_imports, validate_spec_endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cuga.post_build import (
    run_tests,
    validate_imports,
    validate_project,
    validate_spec_endpoints,
)

# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal project directory."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / ".gitignore").write_text("__pycache__/\n")
    (tmp_path / "README.md").write_text("# Test\n")
    return tmp_path


@pytest.fixture()
def python_project(project_dir: Path) -> Path:
    """Create a Python project with some files."""
    (project_dir / "src" / "__init__.py").write_text("")
    (project_dir / "src" / "main.py").write_text(
        "from __future__ import annotations\n\n"
        "from src.models import User\n\n"
        "def app() -> None:\n    pass\n"
    )
    (project_dir / "src" / "models.py").write_text(
        "from __future__ import annotations\n\n"
        "class User:\n    name: str\n"
    )
    return project_dir


# ── validate_imports tests ─────────────────────────────────────


class TestValidateImports:
    """Tests for validate_imports()."""

    def test_no_broken_imports(self, python_project: Path) -> None:
        """All imports resolve → no broken imports."""
        result = validate_imports(python_project)
        assert result["broken_imports"] == []
        assert result["checked_count"] > 0

    def test_broken_internal_import(self, project_dir: Path) -> None:
        """Import of nonexistent project module is detected."""
        (project_dir / "app.py").write_text(
            "from __future__ import annotations\n\n"
            "from services.auth import login\n"
        )
        # Create a "services" dir so it's treated as internal
        (project_dir / "services").mkdir()
        (project_dir / "services" / "__init__.py").write_text("")
        # But there's no services/auth.py

        result = validate_imports(project_dir)
        broken = result["broken_imports"]
        assert len(broken) >= 1
        assert any("auth" in bi["module"] for bi in broken)

    def test_stdlib_imports_not_flagged(self, project_dir: Path) -> None:
        """Standard library imports should not be flagged as broken."""
        (project_dir / "utils.py").write_text(
            "from __future__ import annotations\n\n"
            "import os\nimport json\nimport pathlib\n"
        )
        result = validate_imports(project_dir)
        assert result["broken_imports"] == []

    def test_empty_project(self, project_dir: Path) -> None:
        """Empty project returns no errors."""
        result = validate_imports(project_dir)
        assert result["broken_imports"] == []
        assert result["checked_count"] == 0

    def test_syntax_error_skipped(self, project_dir: Path) -> None:
        """Files with syntax errors are gracefully skipped."""
        (project_dir / "broken.py").write_text("def foo(\n")
        result = validate_imports(project_dir)
        assert result["broken_imports"] == []


# ── validate_spec_endpoints tests ──────────────────────────────


class TestValidateSpecEndpoints:
    """Tests for validate_spec_endpoints()."""

    def test_all_endpoints_found(self, project_dir: Path) -> None:
        """When code implements all spec endpoints, coverage is 100%."""
        (project_dir / "routes.py").write_text(
            'from __future__ import annotations\n\n'
            'from fastapi import APIRouter\n\n'
            'router = APIRouter()\n\n'
            '@router.get("/api/v1/items")\n'
            'async def list_items(): ...\n\n'
            '@router.post("/api/v1/items")\n'
            'async def create_item(): ...\n'
        )
        spec: dict[str, Any] = {
            "features": [
                {
                    "name": "Items",
                    "type": "crud",
                    "details": {
                        "endpoints": [
                            "GET /api/v1/items - List all items",
                            "POST /api/v1/items - Create item",
                        ],
                    },
                },
            ],
        }
        result = validate_spec_endpoints(project_dir, spec)
        assert result["coverage_pct"] == 100.0
        assert result["missing_endpoints"] == []
        assert len(result["declared_endpoints"]) == 2

    def test_missing_endpoint_detected(self, project_dir: Path) -> None:
        """Missing endpoint is reported."""
        (project_dir / "routes.py").write_text(
            'from __future__ import annotations\n\n'
            'from fastapi import APIRouter\n\n'
            'router = APIRouter()\n\n'
            '@router.get("/api/v1/items")\n'
            'async def list_items(): ...\n'
        )
        spec: dict[str, Any] = {
            "features": [
                {
                    "name": "Items",
                    "type": "crud",
                    "details": {
                        "endpoints": [
                            "GET /api/v1/items - List all items",
                            "DELETE /api/v1/items/{id} - Delete item",
                        ],
                    },
                },
            ],
        }
        result = validate_spec_endpoints(project_dir, spec)
        assert len(result["missing_endpoints"]) == 1
        assert "/api/v1/items/{id}" in result["missing_endpoints"][0]

    def test_no_spec(self, project_dir: Path) -> None:
        """None spec returns 100% coverage (nothing to check)."""
        result = validate_spec_endpoints(project_dir, None)
        assert result["coverage_pct"] == 100

    def test_no_features(self, project_dir: Path) -> None:
        """Spec with no features returns 100% coverage."""
        result = validate_spec_endpoints(project_dir, {"features": []})
        assert result["coverage_pct"] == 100

    def test_string_features_ignored(self, project_dir: Path) -> None:
        """Legacy string features (no endpoints) don't cause errors."""
        spec: dict[str, Any] = {
            "features": ["REST API with CRUD endpoints", "JWT auth"],
        }
        result = validate_spec_endpoints(project_dir, spec)
        assert result["coverage_pct"] == 100


# ── run_tests tests ────────────────────────────────────────────


class TestRunTests:
    """Tests for run_tests()."""

    def test_no_tests_detected(self, project_dir: Path) -> None:
        """When no test files exist, has_tests is False."""
        result = run_tests(project_dir)
        assert result["has_tests"] is False

    def test_pytest_detected(self, project_dir: Path) -> None:
        """When test_*.py files exist, pytest framework is detected."""
        (project_dir / "tests" / "test_example.py").write_text(
            "def test_one():\n    assert True\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type(
                "Result", (), {"returncode": 0, "stdout": "1 passed", "stderr": ""}
            )()
            result = run_tests(project_dir)

        assert result["has_tests"] is True
        assert result["framework"] == "pytest"
        assert result["test_ok"] is True

    def test_npm_test_detected(self, project_dir: Path) -> None:
        """When package.json has a test script, npm test is detected."""
        (project_dir / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest"}})
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type(
                "Result", (), {"returncode": 0, "stdout": "Tests: 5 passed", "stderr": ""}
            )()
            result = run_tests(project_dir)

        assert result["has_tests"] is True
        assert "npm" in str(result.get("framework", ""))
        assert result["test_ok"] is True

    def test_pytest_failure_reported(self, project_dir: Path) -> None:
        """Failed pytest run reports test_ok=False."""
        (project_dir / "tests" / "test_fail.py").write_text(
            "def test_fail():\n    assert False\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type(
                "Result",
                (),
                {"returncode": 1, "stdout": "1 failed, 2 passed", "stderr": ""},
            )()
            result = run_tests(project_dir)

        assert result["test_ok"] is False
        assert result["tests_failed"] == 1
        assert result["tests_passed"] == 2


# ── validate_project integration ───────────────────────────────


class TestValidateProjectWithNewValidators:
    """Verify validate_project includes new validator results."""

    def test_report_includes_tests_key(self, project_dir: Path) -> None:
        """validate_project result has 'tests' key."""
        report = validate_project(project_dir, spec=None)
        assert "tests" in report

    def test_report_includes_imports_key(self, project_dir: Path) -> None:
        """validate_project result has 'imports' key."""
        report = validate_project(project_dir, spec=None)
        assert "imports" in report

    def test_report_includes_endpoints_key(self, project_dir: Path) -> None:
        """validate_project result has 'endpoints' key."""
        report = validate_project(project_dir, spec=None)
        assert "endpoints" in report

    def test_summary_mentions_imports(self, python_project: Path) -> None:
        """Summary text mentions import validation."""
        report = validate_project(python_project, spec=None)
        assert "Imports" in report["summary"]
