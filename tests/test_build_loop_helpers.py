"""Tests for build_loop helpers: _extract_failing_files, _detect_regressions,
_build_escalation_hint, _error_signature."""

from __future__ import annotations

from typing import Any

from cuga.build_loop import (
    _build_escalation_hint,
    _detect_regressions,
    _error_signature,
    _extract_failing_files,
)

# ── _extract_failing_files tests ───────────────────────────────


class TestExtractFailingFiles:
    """Tests for _extract_failing_files()."""

    def test_empty_validation(self) -> None:
        """Empty validation returns empty set."""
        assert _extract_failing_files({}) == set()

    def test_syntax_errors_dict(self) -> None:
        """Dict-style syntax errors extract file paths."""
        validation: dict[str, Any] = {
            "syntax_errors": [
                {"file": "src/main.py", "issue": "unexpected indent"},
                {"file": "src/routes.py", "issue": "missing colon"},
            ],
        }
        result = _extract_failing_files(validation)
        assert result == {"src/main.py", "src/routes.py"}

    def test_syntax_errors_string(self) -> None:
        """String-style syntax errors extract file from 'file:line' format."""
        validation: dict[str, Any] = {
            "syntax_errors": ["app.py:10: SyntaxError"],
        }
        result = _extract_failing_files(validation)
        assert "app.py" in result

    def test_error_smells_only(self) -> None:
        """Only severity='error' smells are extracted."""
        validation: dict[str, Any] = {
            "smells": [
                {"file": "bad.py", "severity": "error", "issue": "todo stub"},
                {"file": "ok.py", "severity": "warning", "issue": "long func"},
            ],
        }
        result = _extract_failing_files(validation)
        assert result == {"bad.py"}

    def test_broken_imports(self) -> None:
        """Broken imports are extracted."""
        validation: dict[str, Any] = {
            "imports": {
                "broken_imports": [
                    {"file": "app.py", "module": "services.auth"},
                ],
            },
        }
        result = _extract_failing_files(validation)
        assert result == {"app.py"}

    def test_empty_strings_discarded(self) -> None:
        """Empty file strings are removed."""
        validation: dict[str, Any] = {
            "syntax_errors": [{"file": "", "issue": "x"}],
        }
        assert _extract_failing_files(validation) == set()

    def test_imports_none(self) -> None:
        """None imports dict is handled gracefully."""
        validation: dict[str, Any] = {
            "imports": None,
        }
        assert _extract_failing_files(validation) == set()


# ── _detect_regressions tests ──────────────────────────────────


class TestDetectRegressions:
    """Tests for _detect_regressions()."""

    def test_no_regression(self) -> None:
        """When current failing files is subset of previous, no regressions."""
        prev: dict[str, Any] = {
            "syntax_errors": [{"file": "a.py", "issue": "x"}],
        }
        curr: dict[str, Any] = {
            "syntax_errors": [{"file": "a.py", "issue": "x"}],
        }
        assert _detect_regressions(prev, curr) == []

    def test_regression_detected(self) -> None:
        """New failing file that wasn't in prev is flagged."""
        prev: dict[str, Any] = {
            "syntax_errors": [{"file": "a.py", "issue": "x"}],
        }
        curr: dict[str, Any] = {
            "syntax_errors": [
                {"file": "a.py", "issue": "x"},
                {"file": "b.py", "issue": "y"},
            ],
        }
        result = _detect_regressions(prev, curr)
        assert result == ["b.py"]

    def test_both_empty(self) -> None:
        """Both empty → no regressions."""
        assert _detect_regressions({}, {}) == []

    def test_prev_had_errors_now_clean(self) -> None:
        """Fixed errors are not regressions."""
        prev: dict[str, Any] = {
            "syntax_errors": [{"file": "a.py", "issue": "x"}],
        }
        assert _detect_regressions(prev, {}) == []


# ── _build_escalation_hint tests ───────────────────────────────


class TestBuildEscalationHint:
    """Tests for _build_escalation_hint()."""

    def test_no_stuck_errors(self) -> None:
        """No persistent errors → None."""
        result = _build_escalation_hint({"syntax:a.py:indent": 1}, iteration=2)
        assert result is None

    def test_stuck_errors_produce_hint(self) -> None:
        """Errors seen ≥2 times get escalation hint."""
        history: dict[str, int] = {
            "syntax:a.py:indent": 3,
            "import:b.py:utils": 2,
        }
        result = _build_escalation_hint(history, iteration=3)
        assert result is not None
        assert "Escalation" in result
        assert "context7" in result
        assert "syntax:a.py:indent" in result

    def test_max_5_errors_shown(self) -> None:
        """At most 5 stuck errors are shown."""
        history = {f"err:{i}": 5 for i in range(10)}
        result = _build_escalation_hint(history, iteration=5)
        assert result is not None
        assert result.count("`err:") <= 5

    def test_empty_history(self) -> None:
        """Empty history → None."""
        assert _build_escalation_hint({}, iteration=1) is None


# ── _error_signature tests ─────────────────────────────────────


class TestErrorSignature:
    """Tests for _error_signature()."""

    def test_empty_validation(self) -> None:
        """Empty report → empty set."""
        assert _error_signature({}) == set()

    def test_syntax_error_sig(self) -> None:
        """Syntax error gets a signature."""
        validation: dict[str, Any] = {
            "syntax_errors": [{"file": "a.py", "issue": "unexpected indent"}],
        }
        sigs = _error_signature(validation)
        assert len(sigs) == 1
        sig = next(iter(sigs))
        assert sig.startswith("syntax:")
        assert "a.py" in sig

    def test_smell_error_sig(self) -> None:
        """Error-severity smell gets a signature."""
        validation: dict[str, Any] = {
            "smells": [{"file": "b.py", "issue": "todo stub", "severity": "error"}],
        }
        sigs = _error_signature(validation)
        assert any(s.startswith("smell:") for s in sigs)

    def test_warning_smell_no_sig(self) -> None:
        """Warning-severity smell does NOT get a signature."""
        validation: dict[str, Any] = {
            "smells": [{"file": "b.py", "issue": "long function", "severity": "warning"}],
        }
        sigs = _error_signature(validation)
        assert len(sigs) == 0

    def test_import_sig(self) -> None:
        """Broken import gets a signature."""
        validation: dict[str, Any] = {
            "imports": {
                "broken_imports": [{"file": "app.py", "module": "services.auth"}],
            },
        }
        sigs = _error_signature(validation)
        assert any("import:" in s for s in sigs)

    def test_test_failure_sig(self) -> None:
        """Test failures get a signature."""
        validation: dict[str, Any] = {
            "tests": {"has_tests": True, "test_ok": False, "tests_failed": 3},
        }
        sigs = _error_signature(validation)
        assert any("tests_failed" in s for s in sigs)

    def test_passing_tests_no_sig(self) -> None:
        """Passing tests do NOT produce a signature."""
        validation: dict[str, Any] = {
            "tests": {"has_tests": True, "test_ok": True, "tests_failed": 0},
        }
        sigs = _error_signature(validation)
        assert not any("test" in s for s in sigs)

    def test_long_issue_truncated(self) -> None:
        """Issue text longer than 50 chars is truncated in signature."""
        long_issue = "x" * 100
        validation: dict[str, Any] = {
            "syntax_errors": [{"file": "a.py", "issue": long_issue}],
        }
        sigs = _error_signature(validation)
        sig = next(iter(sigs))
        # Signature should not contain the full 100-char issue
        assert len(sig) < 100
