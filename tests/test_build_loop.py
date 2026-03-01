"""Tests for the build loop module."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pytest

from cuga.build_loop import (
    BuildLoop,
    BuildLoopConfig,
    BuildResult,
    IterationRecord,
    _build_feedback_prompt,
    _check_quality_gate,
)

# ── Fixtures ───────────────────────────────────────────────────


@dataclass
class FakeInvokeResult:
    """Minimal stand-in for sdk.InvokeResult."""

    answer: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    thread_id: str = "fake-thread-001"
    error: str | None = None


class FakeAgent:
    """Mock CugaAgent that tracks invocations without calling an LLM.

    Each call to ``invoke`` creates a basic project file so that
    post-build validation has something to find.

    Args:
        project_dir: Where to write stub files when invoked.
        fail_first_n: How many iterations should produce "bad" output
            (syntax error files) before producing clean output.
    """

    def __init__(self, project_dir: Path, fail_first_n: int = 0) -> None:
        self.project_dir = project_dir
        self.fail_first_n = fail_first_n
        self.call_count = 0
        self.prompts: list[str] = []

    async def invoke(
        self,
        message: str,
        thread_id: str | None = None,
        **kwargs: Any,
    ) -> FakeInvokeResult:
        """Simulate agent invocation."""
        self.call_count += 1
        self.prompts.append(message)

        self.project_dir.mkdir(parents=True, exist_ok=True)

        if self.call_count <= self.fail_first_n:
            # Write a file with a syntax error so validation fails
            (self.project_dir / "bad.py").write_text("def broken(\n", encoding="utf-8")
            # Still write .gitignore and README to avoid missing-required failures
            (self.project_dir / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            (self.project_dir / "README.md").write_text("# Test\n", encoding="utf-8")
        else:
            # Clean output — remove any previous bad file
            bad_file = self.project_dir / "bad.py"
            if bad_file.exists():
                bad_file.unlink()
            (self.project_dir / ".gitignore").write_text("__pycache__/\n.env\n", encoding="utf-8")
            (self.project_dir / "README.md").write_text("# My Project\n", encoding="utf-8")
            (self.project_dir / "main.py").write_text(
                'from __future__ import annotations\n\n\ndef main() -> str:\n    return "hello"\n',
                encoding="utf-8",
            )

        return FakeInvokeResult(
            answer="Done",
            thread_id=f"thread-{self.call_count}",
        )


@pytest.fixture()
def spec() -> dict[str, Any]:
    """Minimal valid spec for testing."""
    return {
        "name": "test-project",
        "description": "A test project",
        "stack": {
            "language": "python",
            "backend": {"framework": "fastapi"},
        },
        "features": ["basic endpoint"],
        "structure": {
            "files": [
                {"path": "main.py", "description": "Entry point"},
            ]
        },
    }


# ── _check_quality_gate tests ─────────────────────────────────


class TestCheckQualityGate:
    """Tests for the quality-gate checker."""

    def test_passes_clean_report(self) -> None:
        report: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [],
            "lint_passed": True,
            "missing_spec_files": [],
            "missing_required": [],
        }
        assert _check_quality_gate(report, BuildLoopConfig()) is True

    def test_fails_on_syntax_errors(self) -> None:
        report: dict[str, Any] = {
            "syntax_errors": [{"file": "bad.py", "line": 1, "issue": "invalid syntax"}],
            "smells": [],
            "lint_passed": True,
            "missing_spec_files": [],
            "missing_required": [],
        }
        assert _check_quality_gate(report, BuildLoopConfig()) is False

    def test_fails_on_error_smells(self) -> None:
        report: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [{"severity": "error", "issue": "stub"}],
            "lint_passed": True,
            "missing_spec_files": [],
            "missing_required": [],
        }
        assert _check_quality_gate(report, BuildLoopConfig()) is False

    def test_warns_dont_fail_by_default(self) -> None:
        report: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [{"severity": "warn", "issue": "TODO"}],
            "lint_passed": True,
            "missing_spec_files": [],
            "missing_required": [],
        }
        assert _check_quality_gate(report, BuildLoopConfig()) is True

    def test_fails_on_missing_required_files(self) -> None:
        report: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [],
            "lint_passed": True,
            "missing_spec_files": [],
            "missing_required": [".gitignore"],
        }
        assert _check_quality_gate(report, BuildLoopConfig()) is False

    def test_fails_on_missing_spec_files_when_required(self) -> None:
        report: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [],
            "lint_passed": True,
            "missing_spec_files": ["api/routes.py"],
            "missing_required": [],
        }
        config = BuildLoopConfig(require_all_spec_files=True)
        assert _check_quality_gate(report, config) is False

    def test_passes_missing_spec_files_when_not_required(self) -> None:
        report: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [],
            "lint_passed": True,
            "missing_spec_files": ["api/routes.py"],
            "missing_required": [],
        }
        config = BuildLoopConfig(require_all_spec_files=False)
        assert _check_quality_gate(report, config) is True

    def test_lint_failure_ignored_by_default(self) -> None:
        report: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [],
            "lint_passed": False,
            "lint_output": "E501 line too long",
            "missing_spec_files": [],
            "missing_required": [],
        }
        assert _check_quality_gate(report, BuildLoopConfig()) is True

    def test_lint_failure_when_required(self) -> None:
        report: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [],
            "lint_passed": False,
            "lint_output": "E501 line too long",
            "missing_spec_files": [],
            "missing_required": [],
        }
        config = BuildLoopConfig(require_lint_pass=True)
        assert _check_quality_gate(report, config) is False

    def test_tolerates_syntax_errors_up_to_threshold(self) -> None:
        report: dict[str, Any] = {
            "syntax_errors": [
                {"file": "a.py", "line": 1, "issue": "err1"},
                {"file": "b.py", "line": 1, "issue": "err2"},
            ],
            "smells": [],
            "lint_passed": True,
            "missing_spec_files": [],
            "missing_required": [],
        }
        config = BuildLoopConfig(max_syntax_errors=2)
        assert _check_quality_gate(report, config) is True

        config_strict = BuildLoopConfig(max_syntax_errors=1)
        assert _check_quality_gate(report, config_strict) is False


# ── _build_feedback_prompt tests ───────────────────────────────


class TestBuildFeedbackPrompt:
    """Tests for the error-feedback prompt builder."""

    def test_includes_syntax_errors(self) -> None:
        validation: dict[str, Any] = {
            "syntax_errors": [{"file": "app.py", "line": 10, "issue": "unexpected EOF"}],
            "smells": [],
            "lint_passed": True,
            "missing_spec_files": [],
            "missing_required": [],
        }
        prompt = _build_feedback_prompt(validation, iteration=1, max_errors=20)
        assert "Syntax Errors" in prompt
        assert "app.py" in prompt
        assert "unexpected EOF" in prompt

    def test_includes_lint_issues(self) -> None:
        validation: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [],
            "lint_passed": False,
            "lint_output": "src/main.py:5:1: E302 expected 2 blank lines",
            "missing_spec_files": [],
            "missing_required": [],
        }
        prompt = _build_feedback_prompt(validation, iteration=2, max_errors=20)
        assert "Lint" in prompt
        assert "E302" in prompt

    def test_includes_error_smells(self) -> None:
        validation: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [
                {
                    "severity": "error",
                    "file": "utils.py",
                    "line": 42,
                    "issue": "Hardcoded password",
                    "code": 'password = "admin123"',
                }
            ],
            "lint_passed": True,
            "missing_spec_files": [],
            "missing_required": [],
        }
        prompt = _build_feedback_prompt(validation, iteration=1, max_errors=20)
        assert "Code Quality" in prompt
        assert "Hardcoded password" in prompt

    def test_includes_missing_files(self) -> None:
        validation: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [],
            "lint_passed": True,
            "missing_spec_files": ["api/routes.py", "models/user.py"],
            "missing_required": [".gitignore"],
        }
        prompt = _build_feedback_prompt(validation, iteration=1, max_errors=20)
        assert "Missing Spec Files" in prompt
        assert "api/routes.py" in prompt
        assert ".gitignore" in prompt

    def test_respects_max_errors_cap(self) -> None:
        validation: dict[str, Any] = {
            "syntax_errors": [
                {"file": f"f{i}.py", "line": 1, "issue": f"err{i}"} for i in range(50)
            ],
            "smells": [],
            "lint_passed": True,
            "missing_spec_files": [],
            "missing_required": [],
        }
        prompt = _build_feedback_prompt(validation, iteration=1, max_errors=5)
        # Should only list 5 errors, not all 50
        assert prompt.count("`.py:") <= 5

    def test_empty_validation_still_produces_prompt(self) -> None:
        validation: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [],
            "lint_passed": True,
            "missing_spec_files": [],
            "missing_required": [],
        }
        prompt = _build_feedback_prompt(validation, iteration=1, max_errors=20)
        assert "Iteration 1" in prompt
        assert "Fix ALL" in prompt

    def test_warns_excluded_from_smells(self) -> None:
        validation: dict[str, Any] = {
            "syntax_errors": [],
            "smells": [
                {
                    "severity": "warn",
                    "file": "utils.py",
                    "line": 5,
                    "issue": "TODO comment",
                    "code": "# TODO: implement",
                }
            ],
            "lint_passed": True,
            "missing_spec_files": [],
            "missing_required": [],
        }
        prompt = _build_feedback_prompt(validation, iteration=1, max_errors=20)
        # Warnings should NOT be in the feedback prompt (only errors)
        assert "TODO comment" not in prompt


# ── BuildLoopConfig tests ─────────────────────────────────────


class TestBuildLoopConfig:
    """Tests for configuration defaults and immutability."""

    def test_defaults(self) -> None:
        config = BuildLoopConfig()
        assert config.max_iterations == 5
        assert config.max_syntax_errors == 0
        assert config.max_error_smells == 0
        assert config.require_lint_pass is False
        assert config.require_all_spec_files is True
        assert config.record_to_catalog is True
        assert config.feedback_max_errors == 20

    def test_frozen(self) -> None:
        config = BuildLoopConfig()
        with pytest.raises(AttributeError):
            config.max_iterations = 10  # type: ignore[misc]


# ── IterationRecord / BuildResult tests ────────────────────────


class TestResultTypes:
    """Tests for result dataclass construction."""

    def test_iteration_record(self) -> None:
        rec = IterationRecord(
            iteration=1,
            elapsed_seconds=12.5,
            validation={"passed": True},
            passed=True,
        )
        assert rec.iteration == 1
        assert rec.feedback_prompt is None

    def test_build_result_defaults(self) -> None:
        result = BuildResult(
            passed=True,
            iteration=1,
            total_elapsed=10.0,
        )
        assert result.iterations == []
        assert result.project_dir is None
        assert result.final_validation == {}


# ── BuildLoop integration tests ────────────────────────────────


class TestBuildLoop:
    """Integration tests using FakeAgent."""

    @pytest.mark.asyncio()
    async def test_passes_on_first_try(self, tmp_path: Path, spec: dict[str, Any]) -> None:
        """Agent produces clean output on first iteration — loop stops at 1."""
        project_dir = tmp_path / "test-project"
        agent = FakeAgent(project_dir, fail_first_n=0)

        loop = BuildLoop(
            spec=spec,
            agent=agent,
            project_dir=project_dir,
            config=BuildLoopConfig(
                max_iterations=3,
                record_to_catalog=False,
                require_all_spec_files=False,
            ),
        )
        result = await loop.run()

        assert result.passed is True
        assert result.iteration == 1
        assert agent.call_count == 1
        assert len(result.iterations) == 1

    @pytest.mark.asyncio()
    async def test_retries_on_failure(self, tmp_path: Path, spec: dict[str, Any]) -> None:
        """Agent fails first iteration, succeeds on second."""
        project_dir = tmp_path / "test-project"
        agent = FakeAgent(project_dir, fail_first_n=1)

        loop = BuildLoop(
            spec=spec,
            agent=agent,
            project_dir=project_dir,
            config=BuildLoopConfig(
                max_iterations=3,
                record_to_catalog=False,
                require_all_spec_files=False,
            ),
        )
        result = await loop.run()

        assert result.passed is True
        assert result.iteration == 2
        assert agent.call_count == 2
        assert result.iterations[0].passed is False
        assert result.iterations[1].passed is True

    @pytest.mark.asyncio()
    async def test_feedback_injected_on_retry(self, tmp_path: Path, spec: dict[str, Any]) -> None:
        """On retry, the agent receives an error-feedback prompt, not the original."""
        project_dir = tmp_path / "test-project"
        agent = FakeAgent(project_dir, fail_first_n=1)

        loop = BuildLoop(
            spec=spec,
            agent=agent,
            project_dir=project_dir,
            config=BuildLoopConfig(
                max_iterations=3,
                record_to_catalog=False,
                require_all_spec_files=False,
            ),
        )
        await loop.run()

        # First prompt should be the full build prompt (spec_to_prompt output)
        assert "# Project" in agent.prompts[0] or "Project:" in agent.prompts[0]

        # Second prompt should be the feedback prompt with error details
        assert "Validation FAILED" in agent.prompts[1]
        assert "Fix" in agent.prompts[1]

    @pytest.mark.asyncio()
    async def test_max_iterations_exhausted(self, tmp_path: Path, spec: dict[str, Any]) -> None:
        """When all iterations fail, returns passed=False."""
        project_dir = tmp_path / "test-project"
        agent = FakeAgent(project_dir, fail_first_n=99)

        loop = BuildLoop(
            spec=spec,
            agent=agent,
            project_dir=project_dir,
            config=BuildLoopConfig(
                max_iterations=2,
                record_to_catalog=False,
                require_all_spec_files=False,
            ),
        )
        result = await loop.run()

        assert result.passed is False
        assert result.iteration == 2
        assert agent.call_count == 2
        assert all(not it.passed for it in result.iterations)

    @pytest.mark.asyncio()
    async def test_total_elapsed_tracked(self, tmp_path: Path, spec: dict[str, Any]) -> None:
        """Total elapsed time is reasonable (non-zero, positive)."""
        project_dir = tmp_path / "test-project"
        agent = FakeAgent(project_dir, fail_first_n=0)

        loop = BuildLoop(
            spec=spec,
            agent=agent,
            project_dir=project_dir,
            config=BuildLoopConfig(
                max_iterations=1,
                record_to_catalog=False,
                require_all_spec_files=False,
            ),
        )
        result = await loop.run()

        assert result.total_elapsed >= 0
        assert result.iterations[0].elapsed_seconds >= 0

    @pytest.mark.asyncio()
    async def test_agent_exception_handled(self, tmp_path: Path, spec: dict[str, Any]) -> None:
        """If the agent raises, the iteration is recorded as failed and loop continues."""
        project_dir = tmp_path / "test-project"

        class ExplodingAgent:
            call_count = 0

            async def invoke(self, message: str, **kwargs: Any) -> FakeInvokeResult:
                self.call_count += 1
                if self.call_count == 1:
                    raise RuntimeError("LLM API timeout")
                # Second call succeeds
                project_dir.mkdir(parents=True, exist_ok=True)
                (project_dir / ".gitignore").write_text("__pycache__/\n")
                (project_dir / "README.md").write_text("# Test\n")
                (project_dir / "main.py").write_text(
                    "from __future__ import annotations\n\ndef main() -> str:\n    return 'ok'\n"
                )
                return FakeInvokeResult(answer="Done", thread_id="t2")

        agent = ExplodingAgent()
        loop = BuildLoop(
            spec=spec,
            agent=agent,
            project_dir=project_dir,
            config=BuildLoopConfig(
                max_iterations=3,
                record_to_catalog=False,
                require_all_spec_files=False,
            ),
        )
        result = await loop.run()

        # First iteration failed due to exception, second should pass
        assert result.iterations[0].passed is False
        assert agent.call_count >= 2

    @pytest.mark.asyncio()
    async def test_catalog_recording(self, tmp_path: Path, spec: dict[str, Any]) -> None:
        """When record_to_catalog is True, build_catalog.record_build is called."""
        project_dir = tmp_path / "test-project"
        catalog_dir = tmp_path / "catalog"
        catalog_dir.mkdir()

        agent = FakeAgent(project_dir, fail_first_n=0)

        loop = BuildLoop(
            spec=spec,
            agent=agent,
            project_dir=project_dir,
            config=BuildLoopConfig(
                max_iterations=1,
                record_to_catalog=True,
                require_all_spec_files=False,
            ),
        )

        # The catalog recording is best-effort; we just verify it doesn't crash
        result = await loop.run()
        assert result.passed is True

    @pytest.mark.asyncio()
    async def test_thread_id_preserved_across_iterations(
        self, tmp_path: Path, spec: dict[str, Any]
    ) -> None:
        """Thread ID from first invoke is passed to subsequent invokes."""
        project_dir = tmp_path / "test-project"

        class ThreadTrackingAgent:
            thread_ids_received: ClassVar[list[str | None]] = []
            call_count = 0

            async def invoke(
                self, message: str, thread_id: str | None = None, **kwargs: Any
            ) -> FakeInvokeResult:
                self.call_count += 1
                self.thread_ids_received.append(thread_id)
                project_dir.mkdir(parents=True, exist_ok=True)
                if self.call_count == 1:
                    (project_dir / "bad.py").write_text("def x(\n")
                    (project_dir / ".gitignore").write_text("x\n")
                    (project_dir / "README.md").write_text("# T\n")
                else:
                    bad = project_dir / "bad.py"
                    if bad.exists():
                        bad.unlink()
                    (project_dir / ".gitignore").write_text("__pycache__/\n")
                    (project_dir / "README.md").write_text("# T\n")
                    (project_dir / "main.py").write_text(
                        "from __future__ import annotations\ndef main() -> str:\n    return 'ok'\n"
                    )
                return FakeInvokeResult(answer="Done", thread_id="persistent-thread")

        agent = ThreadTrackingAgent()
        loop = BuildLoop(
            spec=spec,
            agent=agent,
            project_dir=project_dir,
            config=BuildLoopConfig(
                max_iterations=3,
                record_to_catalog=False,
                require_all_spec_files=False,
            ),
        )
        await loop.run()

        # First call: no thread_id (None); second call: should receive the thread_id
        assert agent.thread_ids_received[0] is None
        assert agent.thread_ids_received[1] == "persistent-thread"
