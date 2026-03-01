"""Tests for spec validator — edge cases that previously caused silent build failures."""

from __future__ import annotations

import yaml

from cuga.spec_validator_tool import validate_spec_yaml

VALID_SPEC: dict = {
    "name": "test-project",
    "description": "A test project",
    "version": "0.1.0",
    "stack": {
        "language": "python",
        "runtime": "python3.11",
        "backend": {"framework": "fastapi", "api_style": "rest"},
        "frontend": {"framework": "none"},
        "database": {"primary": "postgresql", "orm": "sqlalchemy"},
        "infrastructure": {"containerization": "docker", "ci_cd": "github-actions"},
    },
    "structure": {
        "files": [
            {
                "path": "src/main.py",
                "purpose": "Entry point",
                "key_contents": ["FastAPI app"],
            },
            {
                "path": "tests/test_main.py",
                "purpose": "Tests",
                "key_contents": ["pytest"],
            },
            {
                "path": "README.md",
                "purpose": "Docs",
                "key_contents": ["Setup"],
            },
            {
                "path": ".gitignore",
                "purpose": "Git exclusions",
                "key_contents": ["__pycache__"],
            },
        ],
    },
    "features": [
        {
            "name": "Health Check",
            "type": "endpoint",
            "details": {"endpoints": ["GET /health"]},
        },
    ],
    "data_model": {
        "entities": [
            {
                "name": "User",
                "fields": [
                    {"name": "id", "type": "uuid", "constraints": "primary_key"},
                    {"name": "email", "type": "string", "constraints": "unique"},
                ],
            },
        ],
    },
    "api": {"base_path": "/api/v1", "auth_required": True},
    "testing": {"framework": "pytest", "coverage_target": 80},
    "deployment": {
        "docker": {"multi_stage": True, "base_image": "python:3.11-slim"},
    },
    "standards": {"formatting": "ruff", "linting": "ruff", "typing": "mypy"},
    "security": {"authentication": "jwt", "secrets": "environment_variables_only"},
}


class TestValidSpec:
    """Test that a correct spec passes cleanly."""

    def test_valid_spec_passes(self) -> None:
        result = validate_spec_yaml(yaml.dump(VALID_SPEC))
        assert result["valid"] is True
        assert result["errors"] == []

    def test_counts_stats(self) -> None:
        result = validate_spec_yaml(yaml.dump(VALID_SPEC))
        assert result["stats"]["files_planned"] == 4
        assert result["stats"]["features"] == 1
        assert result["stats"]["entities"] == 1
        assert result["stats"]["endpoints"] == 1


class TestDuplicateDetection:
    """Tests for duplicate file paths, entities, and fields."""

    def test_duplicate_file_paths_warned(self) -> None:
        spec = {
            **VALID_SPEC,
            "structure": {
                "files": [
                    {
                        "path": "src/main.py",
                        "purpose": "Entry",
                        "key_contents": ["app"],
                    },
                    {"path": "src/main.py", "purpose": "Dup", "key_contents": ["app"]},
                    {"path": "README.md", "purpose": "Docs", "key_contents": ["docs"]},
                ],
            },
        }
        result = validate_spec_yaml(yaml.dump(spec))
        assert any("Duplicate file path" in w for w in result["warnings"])

    def test_duplicate_entity_names_warned(self) -> None:
        spec = {
            **VALID_SPEC,
            "data_model": {
                "entities": [
                    {"name": "User", "fields": [{"name": "id", "type": "uuid"}]},
                    {"name": "User", "fields": [{"name": "id", "type": "uuid"}]},
                ],
            },
        }
        result = validate_spec_yaml(yaml.dump(spec))
        assert any("Duplicate entity" in w for w in result["warnings"])

    def test_duplicate_field_names_warned(self) -> None:
        spec = {
            **VALID_SPEC,
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": [
                            {"name": "id", "type": "uuid"},
                            {"name": "id", "type": "string"},
                        ],
                    },
                ],
            },
        }
        result = validate_spec_yaml(yaml.dump(spec))
        assert any("duplicate field" in w for w in result["warnings"])


class TestEntityValidation:
    """Tests for entity edge cases."""

    def test_empty_entity_name_errors(self) -> None:
        spec = {
            **VALID_SPEC,
            "data_model": {
                "entities": [
                    {"name": "", "fields": [{"name": "id", "type": "uuid"}]},
                ],
            },
        }
        result = validate_spec_yaml(yaml.dump(spec))
        assert any(
            "missing 'name'" in e or "empty" in e.lower() for e in result["errors"]
        )

    def test_field_missing_type_warned(self) -> None:
        spec = {
            **VALID_SPEC,
            "data_model": {
                "entities": [
                    {"name": "User", "fields": [{"name": "id"}]},
                ],
            },
        }
        result = validate_spec_yaml(yaml.dump(spec))
        assert any("missing 'type'" in w for w in result["warnings"])

    def test_entity_with_no_fields_warned(self) -> None:
        spec = {
            **VALID_SPEC,
            "data_model": {"entities": [{"name": "Empty", "fields": []}]},
        }
        result = validate_spec_yaml(yaml.dump(spec))
        assert any("no fields" in w or "0 field" in w for w in result["warnings"])


class TestFileQuality:
    """Tests for file structure quality checks."""

    def test_missing_gitignore_warned(self) -> None:
        spec = {
            **VALID_SPEC,
            "structure": {
                "files": [
                    {
                        "path": "src/main.py",
                        "purpose": "Entry",
                        "key_contents": ["app"],
                    },
                    {
                        "path": "tests/test.py",
                        "purpose": "Tests",
                        "key_contents": ["pytest"],
                    },
                    {
                        "path": "pyproject.toml",
                        "purpose": "Config",
                        "key_contents": ["deps"],
                    },
                ],
            },
        }
        result = validate_spec_yaml(yaml.dump(spec))
        assert any(".gitignore" in w for w in result["warnings"])

    def test_missing_readme_warned(self) -> None:
        spec = {
            **VALID_SPEC,
            "structure": {
                "files": [
                    {
                        "path": "src/main.py",
                        "purpose": "Entry",
                        "key_contents": ["app"],
                    },
                    {"path": ".gitignore", "purpose": "Git", "key_contents": ["cache"]},
                    {
                        "path": "pyproject.toml",
                        "purpose": "Config",
                        "key_contents": ["deps"],
                    },
                ],
            },
        }
        result = validate_spec_yaml(yaml.dump(spec))
        assert any("README" in w for w in result["warnings"])


class TestStackValidation:
    """Tests for stack configuration issues."""

    def test_missing_backend_framework_errors(self) -> None:
        spec = {
            **VALID_SPEC,
            "stack": {
                **VALID_SPEC["stack"],
                "backend": {"api_style": "rest"},
            },
        }
        result = validate_spec_yaml(yaml.dump(spec))
        assert any("framework" in e for e in result["errors"])

    def test_name_with_spaces_warned(self) -> None:
        spec = {**VALID_SPEC, "name": "my cool project"}
        result = validate_spec_yaml(yaml.dump(spec))
        assert any("spaces" in w or "kebab" in w for w in result["warnings"])


class TestFeatureValidation:
    """Tests for feature quality checks."""

    def test_feature_missing_name_warned(self) -> None:
        spec = {
            **VALID_SPEC,
            "features": [{"type": "crud", "details": {}}],
        }
        result = validate_spec_yaml(yaml.dump(spec))
        assert any(
            "missing" in w.lower() and "name" in w.lower() for w in result["warnings"]
        )

    def test_string_features_accepted(self) -> None:
        spec = {**VALID_SPEC, "features": ["User CRUD", "Auth"]}
        result = validate_spec_yaml(yaml.dump(spec))
        # Should not error out on string features
        assert result["stats"]["features"] == 2

    def test_endpoint_counting(self) -> None:
        spec = {
            **VALID_SPEC,
            "features": [
                {
                    "name": "Users",
                    "type": "crud",
                    "details": {
                        "endpoints": [
                            "GET /users",
                            "POST /users",
                            "GET /users/{id}",
                            "PUT /users/{id}",
                            "DELETE /users/{id}",
                        ],
                    },
                },
            ],
        }
        result = validate_spec_yaml(yaml.dump(spec))
        assert result["stats"]["endpoints"] == 5


class TestEdgeCases:
    """Tests for malformed input."""

    def test_non_yaml_string(self) -> None:
        result = validate_spec_yaml("this is not yaml: {{{")
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_yaml_list_instead_of_dict(self) -> None:
        result = validate_spec_yaml("- item1\n- item2\n")
        assert result["valid"] is False
        assert any("dict" in e or "mapping" in e for e in result["errors"])

    def test_empty_string(self) -> None:
        result = validate_spec_yaml("")
        assert result["valid"] is False

    def test_wrong_type_for_name(self) -> None:
        """Numeric name should be flagged as wrong type."""
        spec = {**VALID_SPEC, "name": 123}
        result = validate_spec_yaml(yaml.dump(spec))
        assert any("expected string" in e for e in result["errors"])

    def test_non_dict_entity_item(self) -> None:
        """Non-dict entity items should produce an error."""
        spec = {
            **VALID_SPEC,
            "data_model": {"entities": ["not a dict"]},
        }
        result = validate_spec_yaml(yaml.dump(spec))
        assert any("must be a mapping" in e for e in result["errors"])

    def test_non_dict_feature_item(self) -> None:
        """Non-dict, non-string feature items should produce a warning."""
        spec = {**VALID_SPEC, "features": [42]}
        result = validate_spec_yaml(yaml.dump(spec))
        assert any("dict or string" in w for w in result["warnings"])


class TestNullYamlValues:
    """Tests for specs where YAML keys exist with null values."""

    def test_null_features_does_not_crash(self) -> None:
        spec = {**VALID_SPEC, "features": None}
        result = validate_spec_yaml(yaml.dump(spec))
        assert result["stats"]["features"] == 0

    def test_null_structure_does_not_crash(self) -> None:
        spec = {**VALID_SPEC, "structure": None}
        result = validate_spec_yaml(yaml.dump(spec))
        assert result["stats"]["files_planned"] == 0

    def test_null_data_model_does_not_crash(self) -> None:
        spec = {**VALID_SPEC, "data_model": None}
        result = validate_spec_yaml(yaml.dump(spec))
        assert result["stats"]["entities"] == 0

    def test_null_stack_does_not_crash(self) -> None:
        spec = {**VALID_SPEC, "stack": None}
        result = validate_spec_yaml(yaml.dump(spec))
        # Should have errors for missing required fields under stack
        assert any("stack" in e.lower() for e in result["errors"])
