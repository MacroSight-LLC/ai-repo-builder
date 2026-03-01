"""Conftest for integration tests — auto-skip unless --run-integration is passed."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add --run-integration CLI flag."""
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that require live services/credentials",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip integration tests unless --run-integration is passed."""
    if config.getoption("--run-integration"):
        return

    skip_marker = pytest.mark.skip(
        reason="Integration test — pass --run-integration to run",
    )
    for item in items:
        # All tests in this directory are integration tests
        item.add_marker(skip_marker)
