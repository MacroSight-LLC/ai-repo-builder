"""Tests for shell_tool smart truncation: _smart_truncate."""

from __future__ import annotations

from cuga.shell_tool import _smart_truncate


class TestSmartTruncate:
    """Tests for _smart_truncate()."""

    def test_short_text_unchanged(self) -> None:
        """Text under max_chars is returned unchanged."""
        text = "hello\nworld\n"
        assert _smart_truncate(text, 1000) == text

    def test_exact_budget(self) -> None:
        """Text exactly at max_chars is returned unchanged."""
        text = "x" * 500
        assert _smart_truncate(text, 500) == text

    def test_long_text_truncated(self) -> None:
        """Very long text is truncated."""
        lines = [f"line {i}: some output content" for i in range(200)]
        text = "\n".join(lines)
        result = _smart_truncate(text, 2000)
        assert len(result) <= 2000

    def test_error_lines_preserved(self) -> None:
        """Error lines in the middle are kept even when truncated."""
        lines = [f"ok line {i}" for i in range(40)]
        lines.insert(25, "ERROR: something went wrong")
        lines.insert(26, "Traceback (most recent call last):")
        lines.extend([f"tail line {i}" for i in range(40)])
        text = "\n".join(lines)
        result = _smart_truncate(text, 2000)
        assert "ERROR: something went wrong" in result
        assert "Traceback" in result

    def test_head_preserved(self) -> None:
        """First 20 lines are always kept."""
        lines = [f"header line {i}" for i in range(100)]
        text = "\n".join(lines)
        result = _smart_truncate(text, 3000)
        # First 20 lines should be present
        for i in range(20):
            assert f"header line {i}" in result

    def test_tail_preserved(self) -> None:
        """Last 30 lines are always kept."""
        lines = [f"line {i}" for i in range(100)]
        text = "\n".join(lines)
        result = _smart_truncate(text, 3000)
        # Last 30 lines should be present
        for i in range(70, 100):
            assert f"line {i}" in result

    def test_truncation_marker_present(self) -> None:
        """Truncated output contains a marker."""
        lines = [f"line {i}: {'x' * 80}" for i in range(100)]
        text = "\n".join(lines)
        result = _smart_truncate(text, 2000)
        assert "truncated" in result.lower()

    def test_few_lines_uses_fallback(self) -> None:
        """Text with ≤60 lines uses simpler truncation."""
        lines = [f"line {i}: {'x' * 200}" for i in range(50)]
        text = "\n".join(lines)
        # Ensure it's long enough to need truncation
        result = _smart_truncate(text, 2000)
        assert len(result) <= 2000 + 100  # small buffer for final truncation

    def test_multiple_error_blocks(self) -> None:
        """Multiple error lines scattered in middle are all captured."""
        lines = [f"ok {i}" for i in range(25)]
        lines.append("ImportError: No module named 'foo'")
        lines.extend([f"ok {i}" for i in range(25, 50)])
        lines.append("FAILED test_example.py::test_one")
        lines.extend([f"ok {i}" for i in range(50, 80)])
        text = "\n".join(lines)
        result = _smart_truncate(text, 3000)
        assert "ImportError" in result
        assert "FAILED" in result

    def test_no_error_lines_still_truncates(self) -> None:
        """When no errors, middle is still truncated with marker."""
        lines = [f"ok line {i}: {'y' * 80}" for i in range(100)]
        text = "\n".join(lines)
        result = _smart_truncate(text, 2000)
        assert "truncated" in result.lower()

    def test_context_around_errors(self) -> None:
        """Lines adjacent to errors are included as context."""
        lines = [f"context {i}" for i in range(80)]
        lines[40] = "SyntaxError: unexpected token"
        text = "\n".join(lines)
        result = _smart_truncate(text, 3000)
        # The error's context line (39 or 41) should also be present
        assert "context 39" in result or "context 41" in result
