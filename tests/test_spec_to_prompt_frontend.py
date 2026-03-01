"""Tests for spec_to_prompt pages / components rendering."""

from __future__ import annotations

from typing import Any

import pytest

from cuga.spec_to_prompt import spec_to_prompt


@pytest.fixture()
def base_spec() -> dict[str, Any]:
    """Minimal spec that spec_to_prompt can render."""
    return {
        "name": "test-app",
        "description": "A test application",
        "stack": {"language": "typescript", "framework": "next"},
        "structure": {"root": "test-app/", "files": ["src/app/page.tsx"]},
    }


class TestPagesRendering:
    """Verify pages/routes section is rendered properly."""

    def test_pages_section_rendered(self, base_spec: dict[str, Any]) -> None:
        """When pages are present, 'Pages / Routes' heading appears."""
        base_spec["pages"] = [
            {"path": "/", "name": "Home", "auth": "public"},
            {"path": "/dashboard", "name": "Dashboard", "auth": "user"},
        ]
        result = spec_to_prompt(base_spec)
        assert "## Pages / Routes" in result
        assert "`/`" in result
        assert "`/dashboard`" in result

    def test_page_auth_displayed(self, base_spec: dict[str, Any]) -> None:
        """Auth level is shown for each page."""
        base_spec["pages"] = [
            {"path": "/admin", "name": "Admin Panel", "auth": "admin"},
        ]
        result = spec_to_prompt(base_spec)
        assert "auth: admin" in result

    def test_page_data_source_list(self, base_spec: dict[str, Any]) -> None:
        """Data sources rendered when provided as list."""
        base_spec["pages"] = [
            {
                "path": "/tasks",
                "name": "Tasks",
                "data_source": ["GET /api/tasks", "GET /api/categories"],
            },
        ]
        result = spec_to_prompt(base_spec)
        assert "GET /api/tasks" in result

    def test_page_data_source_string(self, base_spec: dict[str, Any]) -> None:
        """Data source rendered when provided as string."""
        base_spec["pages"] = [
            {"path": "/profile", "name": "Profile", "data_source": "GET /api/me"},
        ]
        result = spec_to_prompt(base_spec)
        assert "GET /api/me" in result

    def test_page_components_listed(self, base_spec: dict[str, Any]) -> None:
        """Page components are listed."""
        base_spec["pages"] = [
            {
                "path": "/",
                "name": "Home",
                "components": ["Header", "TaskList", "Footer"],
            },
        ]
        result = spec_to_prompt(base_spec)
        assert "Header" in result
        assert "TaskList" in result

    def test_no_pages_no_section(self, base_spec: dict[str, Any]) -> None:
        """When no pages, the section heading is absent."""
        result = spec_to_prompt(base_spec)
        assert "## Pages / Routes" not in result

    def test_empty_pages_no_section(self, base_spec: dict[str, Any]) -> None:
        """Explicitly empty pages list → no section."""
        base_spec["pages"] = []
        result = spec_to_prompt(base_spec)
        assert "## Pages / Routes" not in result


class TestComponentsRendering:
    """Verify UI Component Hierarchy rendering."""

    def test_components_section_rendered(self, base_spec: dict[str, Any]) -> None:
        """When components are present, heading appears."""
        base_spec["components"] = [
            {"name": "TaskCard", "type": "widget"},
        ]
        result = spec_to_prompt(base_spec)
        assert "## UI Component Hierarchy" in result
        assert "`TaskCard`" in result

    def test_component_props_rendered(self, base_spec: dict[str, Any]) -> None:
        """Typed props are shown."""
        base_spec["components"] = [
            {
                "name": "TaskCard",
                "type": "widget",
                "props": [
                    {"name": "title", "type": "string", "required": True},
                    {"name": "done", "type": "boolean", "required": False},
                ],
            },
        ]
        result = spec_to_prompt(base_spec)
        assert "title: string (required)" in result
        assert "done: boolean (optional)" in result

    def test_component_state(self, base_spec: dict[str, Any]) -> None:
        """Local state items are shown."""
        base_spec["components"] = [
            {
                "name": "LoginForm",
                "type": "form",
                "state": ["email", "password", "error"],
            },
        ]
        result = spec_to_prompt(base_spec)
        assert "email" in result
        assert "password" in result

    def test_component_children(self, base_spec: dict[str, Any]) -> None:
        """Children components are listed."""
        base_spec["components"] = [
            {
                "name": "Sidebar",
                "type": "layout",
                "children": ["NavItem", "UserAvatar"],
            },
        ]
        result = spec_to_prompt(base_spec)
        assert "NavItem" in result
        assert "UserAvatar" in result

    def test_no_components_no_section(self, base_spec: dict[str, Any]) -> None:
        """When no components, the section heading is absent."""
        result = spec_to_prompt(base_spec)
        assert "## UI Component Hierarchy" not in result

    def test_empty_components_no_section(self, base_spec: dict[str, Any]) -> None:
        """Explicitly empty components list → no section."""
        base_spec["components"] = []
        result = spec_to_prompt(base_spec)
        assert "## UI Component Hierarchy" not in result


class TestFrontendValidationPhase:
    """Verify Phase 3.5 frontend validation is in the prompt."""

    def test_frontend_phase_present(self, base_spec: dict[str, Any]) -> None:
        """Phase 3.5 frontend validation appears when pages are specified."""
        base_spec["pages"] = [{"path": "/", "name": "Home"}]
        result = spec_to_prompt(base_spec)
        # The phase should be in the build workflow
        assert "Frontend Validation" in result or "frontend" in result.lower()

    def test_full_spec_renders_without_error(self) -> None:
        """A comprehensive spec with all sections renders cleanly."""
        spec: dict[str, Any] = {
            "name": "fullstack-app",
            "description": "Test full stack app",
            "stack": {
                "language": "typescript",
                "framework": "next",
                "backend": {"framework": "fastapi", "api_style": "rest"},
                "database": {"primary": "postgresql"},
            },
            "structure": {"root": "app/", "files": ["src/app/page.tsx"]},
            "pages": [
                {
                    "path": "/",
                    "name": "Home",
                    "auth": "public",
                    "components": ["Header", "Hero"],
                },
                {
                    "path": "/dashboard",
                    "name": "Dashboard",
                    "auth": "user",
                    "data_source": ["GET /api/tasks"],
                    "components": ["TaskList", "TaskForm"],
                },
            ],
            "components": [
                {
                    "name": "TaskList",
                    "type": "widget",
                    "props": [{"name": "tasks", "type": "Task[]", "required": True}],
                    "state": ["filter"],
                    "children": ["TaskCard"],
                },
                {"name": "TaskCard", "type": "widget"},
            ],
            "features": [
                {
                    "name": "Tasks",
                    "type": "crud",
                    "details": {
                        "endpoints": ["GET /api/tasks", "POST /api/tasks"],
                    },
                },
            ],
            "data_model": {
                "entities": [
                    {
                        "name": "Task",
                        "fields": [
                            {"name": "id", "type": "uuid"},
                            {"name": "title", "type": "string"},
                        ],
                    },
                ],
            },
        }
        result = spec_to_prompt(spec)
        assert "## Pages / Routes" in result
        assert "## UI Component Hierarchy" in result
        assert "TaskList" in result
        assert len(result) > 500
