"""
Quality Gate — Configurable build-pass/fail determination.

Extracts and extends the quality-gate logic from ``build_loop.py`` into
a reusable module that supports:

- Per-stack threshold overrides (e.g. stricter for Python, relaxed for TS)
- Environment-variable configuration (CI-friendly)
- YAML-based project-level overrides (``quality_gate.yaml``)
- Frontend, Docker, and migration gate checks

Usage::

    from cuga.quality_gate import QualityGate, GateConfig

    gate = QualityGate(config=GateConfig.from_env())
    verdict = gate.evaluate(validation_report, stack="python/fastapi")
    if verdict.passed:
        print("Ship it!")
    else:
        for reason in verdict.reasons:
            print(f"  BLOCKED: {reason}")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

__all__ = [
    "GateConfig",
    "GateVerdict",
    "QualityGate",
]


# ── Configuration ──────────────────────────────────────────────


@dataclass(frozen=True)
class StackOverride:
    """Per-stack threshold overrides.

    Attributes:
        stack: Stack key like ``python/fastapi`` or ``typescript/nextjs``.
        max_syntax_errors: Override for max syntax errors (None = use default).
        max_error_smells: Override for max error-severity smells.
        require_lint_pass: Override for lint requirement.
        require_frontend_build: Override for frontend build requirement.
        require_docker_build: Override for docker build requirement.
        require_migrations: Override for migration check.
    """

    stack: str
    max_syntax_errors: int | None = None
    max_error_smells: int | None = None
    require_lint_pass: bool | None = None
    require_frontend_build: bool | None = None
    require_docker_build: bool | None = None
    require_migrations: bool | None = None


@dataclass(frozen=True)
class GateConfig:
    """Configurable quality thresholds.

    Attributes:
        max_syntax_errors: Build passes only if syntax errors ≤ this.
        max_error_smells: Build passes only if error-severity smells ≤ this.
        max_warning_smells: Build passes only if warning-severity smells ≤ this.
            Set to -1 for unlimited.
        require_lint_pass: If True, ruff must exit 0 for a pass.
        require_all_spec_files: If True, every spec file must exist on disk.
        require_required_files: If True, standard required files must exist.
        require_frontend_build: If True, frontend install+build must pass.
        require_docker_build: If True, Docker build must pass.
        require_migrations: If True, ORM projects must have migration files.
        require_typescript_check: If True, TypeScript type-check must pass.
        min_files: Minimum file count to pass (0 = off).
        min_lines: Minimum total line count to pass (0 = off).
        stack_overrides: Per-stack threshold overrides.
    """

    max_syntax_errors: int = 0
    max_error_smells: int = 0
    max_warning_smells: int = -1  # Unlimited by default
    require_lint_pass: bool = False
    require_all_spec_files: bool = True
    require_required_files: bool = True
    require_frontend_build: bool = False
    require_docker_build: bool = False
    require_migrations: bool = False
    require_typescript_check: bool = False
    min_files: int = 0
    min_lines: int = 0
    stack_overrides: tuple[StackOverride, ...] = ()

    @classmethod
    def from_env(cls) -> GateConfig:
        """Load configuration from environment variables.

        Environment variables (all optional, prefix ``CUGA_GATE_``):
            - ``CUGA_GATE_MAX_SYNTAX_ERRORS``: int
            - ``CUGA_GATE_MAX_ERROR_SMELLS``: int
            - ``CUGA_GATE_MAX_WARNING_SMELLS``: int
            - ``CUGA_GATE_REQUIRE_LINT``: ``1``/``true`` to enable
            - ``CUGA_GATE_REQUIRE_FRONTEND``: ``1``/``true`` to enable
            - ``CUGA_GATE_REQUIRE_DOCKER``: ``1``/``true`` to enable
            - ``CUGA_GATE_REQUIRE_MIGRATIONS``: ``1``/``true`` to enable
            - ``CUGA_GATE_REQUIRE_TYPESCRIPT``: ``1``/``true`` to enable
            - ``CUGA_GATE_MIN_FILES``: int
            - ``CUGA_GATE_MIN_LINES``: int

        Returns:
            A ``GateConfig`` with values from the environment.
        """

        def _bool(key: str, default: bool) -> bool:
            val = os.environ.get(key, "").strip().lower()
            if val in ("1", "true", "yes"):
                return True
            if val in ("0", "false", "no"):
                return False
            return default

        def _int(key: str, default: int) -> int:
            val = os.environ.get(key, "").strip()
            if val:
                try:
                    return int(val)
                except ValueError:
                    logger.warning("Invalid int for {}: {!r}", key, val)
            return default

        return cls(
            max_syntax_errors=_int("CUGA_GATE_MAX_SYNTAX_ERRORS", 0),
            max_error_smells=_int("CUGA_GATE_MAX_ERROR_SMELLS", 0),
            max_warning_smells=_int("CUGA_GATE_MAX_WARNING_SMELLS", -1),
            require_lint_pass=_bool("CUGA_GATE_REQUIRE_LINT", False),
            require_all_spec_files=_bool("CUGA_GATE_REQUIRE_SPEC_FILES", True),
            require_required_files=_bool("CUGA_GATE_REQUIRE_REQUIRED_FILES", True),
            require_frontend_build=_bool("CUGA_GATE_REQUIRE_FRONTEND", False),
            require_docker_build=_bool("CUGA_GATE_REQUIRE_DOCKER", False),
            require_migrations=_bool("CUGA_GATE_REQUIRE_MIGRATIONS", False),
            require_typescript_check=_bool("CUGA_GATE_REQUIRE_TYPESCRIPT", False),
            min_files=_int("CUGA_GATE_MIN_FILES", 0),
            min_lines=_int("CUGA_GATE_MIN_LINES", 0),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> GateConfig:
        """Load configuration from a YAML file.

        Args:
            path: Path to a ``quality_gate.yaml`` file.

        Returns:
            A ``GateConfig`` populated from the YAML.

        Raises:
            FileNotFoundError: If the file doesn't exist.
        """
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        overrides: list[StackOverride] = []
        for stack_key, vals in data.get("stack_overrides", {}).items():
            if isinstance(vals, dict):
                overrides.append(
                    StackOverride(
                        stack=stack_key,
                        max_syntax_errors=vals.get("max_syntax_errors"),
                        max_error_smells=vals.get("max_error_smells"),
                        require_lint_pass=vals.get("require_lint_pass"),
                        require_frontend_build=vals.get("require_frontend_build"),
                        require_docker_build=vals.get("require_docker_build"),
                        require_migrations=vals.get("require_migrations"),
                    )
                )

        return cls(
            max_syntax_errors=data.get("max_syntax_errors", 0),
            max_error_smells=data.get("max_error_smells", 0),
            max_warning_smells=data.get("max_warning_smells", -1),
            require_lint_pass=data.get("require_lint_pass", False),
            require_all_spec_files=data.get("require_all_spec_files", True),
            require_required_files=data.get("require_required_files", True),
            require_frontend_build=data.get("require_frontend_build", False),
            require_docker_build=data.get("require_docker_build", False),
            require_migrations=data.get("require_migrations", False),
            require_typescript_check=data.get("require_typescript_check", False),
            min_files=data.get("min_files", 0),
            min_lines=data.get("min_lines", 0),
            stack_overrides=tuple(overrides),
        )

    def for_stack(self, stack: str) -> GateConfig:
        """Return a config with stack overrides applied.

        Args:
            stack: Stack identifier like ``python/fastapi``.

        Returns:
            A new ``GateConfig`` with matching stack overrides merged in.
        """
        override = next((o for o in self.stack_overrides if o.stack == stack), None)
        if override is None:
            return self

        return GateConfig(
            max_syntax_errors=(
                override.max_syntax_errors
                if override.max_syntax_errors is not None
                else self.max_syntax_errors
            ),
            max_error_smells=(
                override.max_error_smells
                if override.max_error_smells is not None
                else self.max_error_smells
            ),
            max_warning_smells=self.max_warning_smells,
            require_lint_pass=(
                override.require_lint_pass
                if override.require_lint_pass is not None
                else self.require_lint_pass
            ),
            require_all_spec_files=self.require_all_spec_files,
            require_required_files=self.require_required_files,
            require_frontend_build=(
                override.require_frontend_build
                if override.require_frontend_build is not None
                else self.require_frontend_build
            ),
            require_docker_build=(
                override.require_docker_build
                if override.require_docker_build is not None
                else self.require_docker_build
            ),
            require_migrations=(
                override.require_migrations
                if override.require_migrations is not None
                else self.require_migrations
            ),
            require_typescript_check=self.require_typescript_check,
            min_files=self.min_files,
            min_lines=self.min_lines,
            stack_overrides=self.stack_overrides,
        )


# ── Verdict ────────────────────────────────────────────────────


@dataclass
class GateVerdict:
    """Result of a quality gate evaluation.

    Attributes:
        passed: Overall pass/fail.
        reasons: Human-readable explanation for each failure.
        checks: Dict of individual check results (True = passed).
    """

    passed: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)


# ── The Gate ───────────────────────────────────────────────────


class QualityGate:
    """Evaluates a validation report against configurable thresholds.

    Args:
        config: Quality gate configuration. Defaults to ``GateConfig()``.
    """

    def __init__(self, config: GateConfig | None = None) -> None:
        self.config = config or GateConfig()

    def evaluate(
        self,
        validation: dict[str, Any],
        stack: str | None = None,
    ) -> GateVerdict:
        """Run all quality checks against a validation report.

        Args:
            validation: Report dict from ``post_build.validate_project``.
            stack: Optional stack identifier for per-stack overrides.

        Returns:
            A ``GateVerdict`` with pass/fail and failure reasons.
        """
        cfg = self.config.for_stack(stack) if stack else self.config
        reasons: list[str] = []
        checks: dict[str, bool] = {}

        # ── Syntax errors ───────────────────────────────────
        syntax_count = len(validation.get("syntax_errors", []))
        ok = syntax_count <= cfg.max_syntax_errors
        checks["syntax_errors"] = ok
        if not ok:
            reasons.append(f"Syntax errors: {syntax_count} (max {cfg.max_syntax_errors})")

        # ── Error-severity smells ───────────────────────────
        smells = validation.get("smells", [])
        error_smells = [s for s in smells if s.get("severity") == "error"]
        ok = len(error_smells) <= cfg.max_error_smells
        checks["error_smells"] = ok
        if not ok:
            reasons.append(f"Error smells: {len(error_smells)} (max {cfg.max_error_smells})")

        # ── Warning-severity smells ─────────────────────────
        if cfg.max_warning_smells >= 0:
            warn_smells = [s for s in smells if s.get("severity") == "warn"]
            ok = len(warn_smells) <= cfg.max_warning_smells
            checks["warning_smells"] = ok
            if not ok:
                reasons.append(f"Warning smells: {len(warn_smells)} (max {cfg.max_warning_smells})")
        else:
            checks["warning_smells"] = True

        # ── Lint pass ───────────────────────────────────────
        if cfg.require_lint_pass:
            ok = validation.get("lint_passed", False)
            checks["lint"] = ok
            if not ok:
                reasons.append("Lint check failed (ruff)")
        else:
            checks["lint"] = True

        # ── Spec files ──────────────────────────────────────
        if cfg.require_all_spec_files:
            missing = validation.get("missing_spec_files", [])
            ok = len(missing) == 0
            checks["spec_files"] = ok
            if not ok:
                reasons.append(f"Missing spec files: {', '.join(missing[:5])}")
        else:
            checks["spec_files"] = True

        # ── Required files ──────────────────────────────────
        if cfg.require_required_files:
            missing = validation.get("missing_required", [])
            ok = len(missing) == 0
            checks["required_files"] = ok
            if not ok:
                reasons.append(f"Missing required files: {', '.join(missing[:5])}")
        else:
            checks["required_files"] = True

        # ── Frontend build ──────────────────────────────────
        frontend = validation.get("frontend", {})
        if cfg.require_frontend_build and frontend.get("has_frontend"):
            install_ok = frontend.get("install_ok", True)
            build_ok = frontend.get("build_ok", True)
            ok = install_ok and build_ok
            checks["frontend_build"] = ok
            if not ok:
                if not install_ok:
                    reasons.append("Frontend npm/pnpm install failed")
                else:
                    reasons.append("Frontend build failed")
        else:
            checks["frontend_build"] = True

        # ── Docker build ────────────────────────────────────
        docker = validation.get("docker", {})
        if cfg.require_docker_build and docker.get("has_dockerfile"):
            ok = docker.get("build_ok", True)
            checks["docker_build"] = ok
            if not ok:
                reasons.append("Docker build failed")
        else:
            checks["docker_build"] = True

        # ── TypeScript check ────────────────────────────────
        ts = validation.get("typescript", {})
        if cfg.require_typescript_check and ts.get("has_tsconfig"):
            ok = ts.get("check_ok", True)
            checks["typescript_check"] = ok
            if not ok:
                reasons.append("TypeScript type-check failed")
        else:
            checks["typescript_check"] = True

        # ── Migrations ──────────────────────────────────────
        migrations = validation.get("migrations", {})
        if cfg.require_migrations and migrations.get("has_orm"):
            ok = migrations.get("has_migrations", False)
            checks["migrations"] = ok
            if not ok:
                orm = migrations.get("orm_type", "unknown")
                reasons.append(f"Missing database migrations ({orm})")
        else:
            checks["migrations"] = True

        # ── Min files ───────────────────────────────────────
        if cfg.min_files > 0:
            actual = validation.get("files_total", 0)
            ok = actual >= cfg.min_files
            checks["min_files"] = ok
            if not ok:
                reasons.append(f"Only {actual} files (min {cfg.min_files})")
        else:
            checks["min_files"] = True

        # ── Min lines ───────────────────────────────────────
        if cfg.min_lines > 0:
            actual = validation.get("lines_total", 0)
            ok = actual >= cfg.min_lines
            checks["min_lines"] = ok
            if not ok:
                reasons.append(f"Only {actual:,} lines (min {cfg.min_lines:,})")
        else:
            checks["min_lines"] = True

        passed = all(checks.values())
        return GateVerdict(passed=passed, reasons=reasons, checks=checks)
