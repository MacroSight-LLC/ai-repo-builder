"""Tests for the quality gate module."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from cuga.quality_gate import GateConfig, GateVerdict, QualityGate, StackOverride

# ── Fixtures ───────────────────────────────────────────────────

CLEAN_VALIDATION: dict[str, Any] = {
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
    "summary": "All good",
}

FAILING_VALIDATION: dict[str, Any] = {
    "passed": False,
    "files_total": 3,
    "lines_total": 100,
    "syntax_errors": [
        {"file": "main.py", "line": 10, "issue": "SyntaxError"},
        {"file": "app.py", "line": 5, "issue": "IndentationError"},
    ],
    "lint_passed": False,
    "lint_output": "E001 something",
    "smells": [
        {"file": "a.py", "line": 1, "severity": "error", "issue": "Hardcoded secret"},
        {"file": "b.py", "line": 2, "severity": "error", "issue": "Stub function"},
        {"file": "c.py", "line": 3, "severity": "warn", "issue": "TODO comment"},
        {"file": "d.py", "line": 4, "severity": "warn", "issue": "Bare except"},
    ],
    "missing_spec_files": ["routes.py"],
    "missing_required": [".gitignore"],
    "summary": "Failed",
}


# ── GateConfig ─────────────────────────────────────────────────


class TestGateConfig:
    def test_defaults(self) -> None:
        cfg = GateConfig()
        assert cfg.max_syntax_errors == 0
        assert cfg.max_error_smells == 0
        assert cfg.max_warning_smells == -1
        assert cfg.require_lint_pass is False
        assert cfg.require_all_spec_files is True
        assert cfg.require_frontend_build is False
        assert cfg.min_files == 0

    def test_frozen(self) -> None:
        cfg = GateConfig()
        with pytest.raises(AttributeError):
            cfg.max_syntax_errors = 5  # type: ignore[misc]

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CUGA_GATE_MAX_SYNTAX_ERRORS", "3")
        monkeypatch.setenv("CUGA_GATE_REQUIRE_LINT", "true")
        monkeypatch.setenv("CUGA_GATE_REQUIRE_FRONTEND", "1")
        monkeypatch.setenv("CUGA_GATE_MIN_FILES", "10")
        cfg = GateConfig.from_env()
        assert cfg.max_syntax_errors == 3
        assert cfg.require_lint_pass is True
        assert cfg.require_frontend_build is True
        assert cfg.min_files == 10

    def test_from_env_defaults(self) -> None:
        """Without any env vars, should use sensible defaults."""
        cfg = GateConfig.from_env()
        assert cfg.max_syntax_errors == 0
        assert cfg.require_lint_pass is False

    def test_from_env_invalid_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CUGA_GATE_MAX_SYNTAX_ERRORS", "not_a_number")
        cfg = GateConfig.from_env()
        assert cfg.max_syntax_errors == 0  # Falls back to default

    def test_from_yaml(self, tmp_path: Path) -> None:
        data = {
            "max_syntax_errors": 2,
            "max_error_smells": 1,
            "require_lint_pass": True,
            "require_frontend_build": True,
            "min_files": 5,
            "stack_overrides": {
                "python/fastapi": {
                    "max_syntax_errors": 0,
                    "require_lint_pass": True,
                },
                "typescript/nextjs": {
                    "require_frontend_build": True,
                },
            },
        }
        gate_file = tmp_path / "quality_gate.yaml"
        gate_file.write_text(yaml.dump(data))

        cfg = GateConfig.from_yaml(gate_file)
        assert cfg.max_syntax_errors == 2
        assert cfg.require_lint_pass is True
        assert cfg.require_frontend_build is True
        assert cfg.min_files == 5
        assert len(cfg.stack_overrides) == 2

    def test_from_yaml_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            GateConfig.from_yaml(tmp_path / "nonexistent.yaml")

    def test_for_stack_with_override(self) -> None:
        cfg = GateConfig(
            max_syntax_errors=5,
            require_lint_pass=False,
            stack_overrides=(
                StackOverride(
                    stack="python/fastapi",
                    max_syntax_errors=0,
                    require_lint_pass=True,
                ),
            ),
        )
        resolved = cfg.for_stack("python/fastapi")
        assert resolved.max_syntax_errors == 0
        assert resolved.require_lint_pass is True

    def test_for_stack_without_override(self) -> None:
        cfg = GateConfig(max_syntax_errors=5)
        resolved = cfg.for_stack("unknown/stack")
        assert resolved.max_syntax_errors == 5  # No change

    def test_for_stack_partial_override(self) -> None:
        """Only specified fields in override take effect."""
        cfg = GateConfig(
            max_syntax_errors=5,
            max_error_smells=3,
            require_lint_pass=False,
            stack_overrides=(
                StackOverride(
                    stack="python/fastapi",
                    max_syntax_errors=0,
                    # max_error_smells not set → inherits
                ),
            ),
        )
        resolved = cfg.for_stack("python/fastapi")
        assert resolved.max_syntax_errors == 0
        assert resolved.max_error_smells == 3  # Inherited


# ── QualityGate.evaluate ───────────────────────────────────────


class TestQualityGateEvaluate:
    def test_clean_passes(self) -> None:
        gate = QualityGate()
        verdict = gate.evaluate(CLEAN_VALIDATION)
        assert verdict.passed is True
        assert verdict.reasons == []
        assert all(verdict.checks.values())

    def test_syntax_errors_fail(self) -> None:
        gate = QualityGate(GateConfig(max_syntax_errors=0))
        verdict = gate.evaluate(FAILING_VALIDATION)
        assert verdict.passed is False
        assert verdict.checks["syntax_errors"] is False
        assert any("Syntax errors" in r for r in verdict.reasons)

    def test_syntax_errors_relaxed_threshold(self) -> None:
        gate = QualityGate(GateConfig(
            max_syntax_errors=5,
            max_error_smells=10,
            require_all_spec_files=False,
            require_required_files=False,
        ))
        verdict = gate.evaluate(FAILING_VALIDATION)
        assert verdict.checks["syntax_errors"] is True

    def test_error_smells_fail(self) -> None:
        gate = QualityGate(GateConfig(max_error_smells=0, max_syntax_errors=10))
        validation = {**CLEAN_VALIDATION, "smells": [
            {"severity": "error", "issue": "bad thing"},
        ]}
        verdict = gate.evaluate(validation)
        assert verdict.checks["error_smells"] is False

    def test_warning_smells_unlimited_by_default(self) -> None:
        gate = QualityGate(GateConfig())
        validation = {**CLEAN_VALIDATION, "smells": [
            {"severity": "warn", "issue": f"warn{i}"} for i in range(100)
        ]}
        verdict = gate.evaluate(validation)
        assert verdict.checks["warning_smells"] is True

    def test_warning_smells_threshold(self) -> None:
        gate = QualityGate(GateConfig(max_warning_smells=2))
        validation = {**CLEAN_VALIDATION, "smells": [
            {"severity": "warn", "issue": f"warn{i}"} for i in range(5)
        ]}
        verdict = gate.evaluate(validation)
        assert verdict.checks["warning_smells"] is False
        assert any("Warning smells" in r for r in verdict.reasons)

    def test_lint_check_optional(self) -> None:
        gate = QualityGate(GateConfig(require_lint_pass=False))
        validation = {**CLEAN_VALIDATION, "lint_passed": False}
        verdict = gate.evaluate(validation)
        assert verdict.checks["lint"] is True

    def test_lint_check_required(self) -> None:
        gate = QualityGate(GateConfig(require_lint_pass=True))
        validation = {**CLEAN_VALIDATION, "lint_passed": False}
        verdict = gate.evaluate(validation)
        assert verdict.checks["lint"] is False
        assert any("Lint" in r for r in verdict.reasons)

    def test_missing_spec_files_fail(self) -> None:
        gate = QualityGate(GateConfig(require_all_spec_files=True))
        validation = {**CLEAN_VALIDATION, "missing_spec_files": ["routes.py"]}
        verdict = gate.evaluate(validation)
        assert verdict.checks["spec_files"] is False

    def test_missing_required_files_fail(self) -> None:
        gate = QualityGate(GateConfig(require_required_files=True))
        validation = {**CLEAN_VALIDATION, "missing_required": [".gitignore"]}
        verdict = gate.evaluate(validation)
        assert verdict.checks["required_files"] is False

    def test_frontend_not_required(self) -> None:
        gate = QualityGate(GateConfig(require_frontend_build=False))
        validation = {
            **CLEAN_VALIDATION,
            "frontend": {"has_frontend": True, "install_ok": False},
        }
        verdict = gate.evaluate(validation)
        assert verdict.checks["frontend_build"] is True

    def test_frontend_required_passes(self) -> None:
        gate = QualityGate(GateConfig(require_frontend_build=True))
        validation = {
            **CLEAN_VALIDATION,
            "frontend": {"has_frontend": True, "install_ok": True, "build_ok": True},
        }
        verdict = gate.evaluate(validation)
        assert verdict.checks["frontend_build"] is True

    def test_frontend_required_fails(self) -> None:
        gate = QualityGate(GateConfig(require_frontend_build=True))
        validation = {
            **CLEAN_VALIDATION,
            "frontend": {"has_frontend": True, "install_ok": True, "build_ok": False},
        }
        verdict = gate.evaluate(validation)
        assert verdict.checks["frontend_build"] is False
        assert any("Frontend build" in r for r in verdict.reasons)

    def test_docker_required_fails(self) -> None:
        gate = QualityGate(GateConfig(require_docker_build=True))
        validation = {
            **CLEAN_VALIDATION,
            "docker": {"has_dockerfile": True, "build_ok": False},
        }
        verdict = gate.evaluate(validation)
        assert verdict.checks["docker_build"] is False

    def test_docker_no_dockerfile_ignored(self) -> None:
        gate = QualityGate(GateConfig(require_docker_build=True))
        validation = {
            **CLEAN_VALIDATION,
            "docker": {"has_dockerfile": False},
        }
        verdict = gate.evaluate(validation)
        assert verdict.checks["docker_build"] is True

    def test_typescript_required_fails(self) -> None:
        gate = QualityGate(GateConfig(require_typescript_check=True))
        validation = {
            **CLEAN_VALIDATION,
            "typescript": {"has_tsconfig": True, "check_ok": False},
        }
        verdict = gate.evaluate(validation)
        assert verdict.checks["typescript_check"] is False

    def test_migrations_required_fails(self) -> None:
        gate = QualityGate(GateConfig(require_migrations=True))
        validation = {
            **CLEAN_VALIDATION,
            "migrations": {"has_orm": True, "has_migrations": False, "orm_type": "alembic"},
        }
        verdict = gate.evaluate(validation)
        assert verdict.checks["migrations"] is False
        assert any("alembic" in r for r in verdict.reasons)

    def test_min_files_pass(self) -> None:
        gate = QualityGate(GateConfig(min_files=10))
        verdict = gate.evaluate(CLEAN_VALIDATION)  # files_total=12
        assert verdict.checks["min_files"] is True

    def test_min_files_fail(self) -> None:
        gate = QualityGate(GateConfig(min_files=50))
        verdict = gate.evaluate(CLEAN_VALIDATION)  # files_total=12
        assert verdict.checks["min_files"] is False

    def test_min_lines_fail(self) -> None:
        gate = QualityGate(GateConfig(min_lines=5000))
        verdict = gate.evaluate(CLEAN_VALIDATION)  # lines_total=850
        assert verdict.checks["min_lines"] is False
        assert any("850" in r for r in verdict.reasons)

    def test_stack_override_applied(self) -> None:
        cfg = GateConfig(
            max_syntax_errors=10,
            stack_overrides=(
                StackOverride(stack="python/fastapi", max_syntax_errors=0),
            ),
        )
        gate = QualityGate(cfg)

        # Without stack — relaxed
        v1 = gate.evaluate(FAILING_VALIDATION)
        assert v1.checks["syntax_errors"] is True

        # With stack — strict
        v2 = gate.evaluate(FAILING_VALIDATION, stack="python/fastapi")
        assert v2.checks["syntax_errors"] is False

    def test_multiple_failures(self) -> None:
        gate = QualityGate(GateConfig(
            max_syntax_errors=0,
            max_error_smells=0,
            require_lint_pass=True,
        ))
        verdict = gate.evaluate(FAILING_VALIDATION)
        assert verdict.passed is False
        assert len(verdict.reasons) >= 3


# ── GateVerdict ────────────────────────────────────────────────


class TestGateVerdict:
    def test_construction(self) -> None:
        v = GateVerdict(passed=True)
        assert v.passed is True
        assert v.reasons == []
        assert v.checks == {}

    def test_with_reasons(self) -> None:
        v = GateVerdict(
            passed=False,
            reasons=["too many errors"],
            checks={"syntax_errors": False},
        )
        assert not v.passed
        assert "too many errors" in v.reasons
