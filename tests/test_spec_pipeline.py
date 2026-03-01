"""Smoke tests for the two-stage NL → spec → project pipeline.

Run with:
    python -m pytest tests/test_spec_pipeline.py -v
"""

from __future__ import annotations

import pytest
import yaml

from cuga.spec_generator import (
    build_spec_prompt,
    parse_spec_response,
    save_spec,
    validate_spec,
)
from cuga.spec_to_prompt import spec_to_prompt
from cuga.spec_validator_tool import validate_spec_yaml

# ── Fixtures ───────────────────────────────────────────────────

SAMPLE_SPEC: dict = {
    "name": "test-project",
    "description": "A test project for validation",
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
                "purpose": "App entry point",
                "key_contents": ["FastAPI app instance", "CORS middleware"],
            },
            {
                "path": "src/models.py",
                "purpose": "SQLAlchemy models",
                "key_contents": ["User model", "Base class"],
            },
            {
                "path": "tests/test_main.py",
                "purpose": "Unit tests",
                "key_contents": ["Test health endpoint"],
            },
        ],
    },
    "features": [
        {
            "name": "Health Check",
            "type": "crud",
            "details": {
                "endpoints": ["GET /health - Returns 200 OK"],
            },
        },
        {
            "name": "User CRUD",
            "type": "crud",
            "details": {
                "endpoints": [
                    "POST /api/v1/users - Create user",
                    "GET /api/v1/users/{id} - Get user",
                ],
                "business_logic": ["Email must be unique"],
                "validations": ["Email format", "Name length 2-100"],
            },
        },
    ],
    "data_model": {
        "entities": [
            {
                "name": "User",
                "fields": [
                    {"name": "id", "type": "uuid", "constraints": "primary_key"},
                    {
                        "name": "email",
                        "type": "string",
                        "constraints": "unique, not_null",
                    },
                    {"name": "name", "type": "string", "constraints": "not_null"},
                ],
                "relationships": [],
            },
        ],
        "migrations": {"tool": "alembic"},
    },
    "api": {"base_path": "/api/v1", "auth_required": True},
    "testing": {
        "framework": "pytest",
        "types": {"unit": {"coverage_target": 80, "patterns": ["AAA pattern"]}},
        "fixtures": ["test_client", "test_db"],
    },
    "deployment": {
        "docker": {
            "multi_stage": True,
            "base_image": "python:3.11-slim",
            "compose_services": [
                {"name": "app", "ports": ["8000:8000"]},
                {"name": "db", "image": "postgres:16", "ports": ["5432:5432"]},
            ],
        },
        "ci_cd": {
            "provider": "github-actions",
            "pipeline": ["lint", "test", "build", "deploy"],
        },
    },
    "standards": {
        "formatting": {"tool": "ruff", "line_length": 100},
        "linting": {"tool": "ruff", "strict": True},
        "typing": {"tool": "mypy", "strict": True},
        "documentation": {"docstrings": "google"},
        "git": {"commit_convention": "conventional", "pre_commit_hooks": True},
    },
    "security": {
        "authentication": "required",
        "authorization": "rbac",
        "input_validation": "strict",
        "secrets": "environment_variables_only",
    },
}


# ── spec_generator tests ──────────────────────────────────────


class TestBuildSpecPrompt:
    def test_includes_user_input(self):
        prompt = build_spec_prompt("Build me a REST API for invoices")
        assert "invoices" in prompt

    def test_includes_output_rules(self):
        prompt = build_spec_prompt("anything")
        assert "CRITICAL OUTPUT RULES" in prompt
        assert "name:" in prompt

    def test_includes_every_section_requirement(self):
        prompt = build_spec_prompt("anything")
        assert "key_contents" in prompt


class TestParseSpecResponse:
    def test_strips_yaml_fences(self):
        raw = "```yaml\nname: test\ndescription: hello\n```"
        result = parse_spec_response(raw)
        assert result["name"] == "test"

    def test_strips_generic_fences(self):
        raw = "```\nname: test\ndescription: hello\n```"
        result = parse_spec_response(raw)
        assert result["name"] == "test"

    def test_strips_leading_prose(self):
        raw = "Here is the spec:\n\nname: test\ndescription: hello\n"
        result = parse_spec_response(raw)
        assert result["name"] == "test"

    def test_handles_multiple_yaml_docs(self):
        raw = "name: first\n---\nname: second\n"
        result = parse_spec_response(raw)
        assert result["name"] == "first"

    def test_rejects_non_dict(self):
        with pytest.raises(yaml.YAMLError, match="Expected a YAML mapping"):
            parse_spec_response("- item1\n- item2\n")

    def test_clean_yaml(self):
        raw = "name: clean\ndescription: works\nversion: '1.0'\n"
        result = parse_spec_response(raw)
        assert result["name"] == "clean"
        assert result["version"] == "1.0"

    def test_strategy2_not_overridden_by_strategy3(self):
        """Strategy 3 must not override Strategy 2 when name: was already found."""
        raw = "Some prose here.\nversion: 0.1\nname: correct\ndescription: hello\n"
        result = parse_spec_response(raw)
        # Strategy 2 should find 'name:' and keep it; Strategy 3 should NOT
        # re-truncate to 'version:' (which would lose 'name:' at the start).
        assert result["name"] == "correct"

    def test_strategy3_fallback_when_no_name(self):
        """Strategy 3 activates when there is no 'name:' line at all."""
        raw = "Some prose.\nversion: 1.0\ndescription: hello\n"
        result = parse_spec_response(raw)
        assert result["version"] == 1.0

    def test_empty_input_raises(self):
        with pytest.raises(yaml.YAMLError):
            parse_spec_response("")

    def test_whitespace_only_raises(self):
        with pytest.raises(yaml.YAMLError):
            parse_spec_response("   \n  \n  ")


class TestBuildSpecPromptValidation:
    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="empty"):
            build_spec_prompt("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="empty"):
            build_spec_prompt("   \n  ")

    def test_valid_input_succeeds(self):
        prompt = build_spec_prompt("Build an API")
        assert "Build an API" in prompt


class TestValidateSpecVersion:
    def test_missing_version_warned(self):
        spec = {k: v for k, v in SAMPLE_SPEC.items() if k != "version"}
        warnings = validate_spec(spec)
        assert any("version" in w for w in warnings)


class TestSaveSpec:
    def test_creates_file(self, tmp_path):
        path = save_spec(SAMPLE_SPEC, output_dir=str(tmp_path))
        assert path.exists()
        loaded = yaml.safe_load(path.read_text())
        assert loaded["name"] == "test-project"

    def test_filename_contains_project_name(self, tmp_path):
        path = save_spec(SAMPLE_SPEC, output_dir=str(tmp_path))
        assert "test-project" in path.name


class TestValidateSpec:
    def test_catches_missing_sections(self):
        incomplete = {"name": "test", "description": "hello"}
        warnings = validate_spec(incomplete)
        assert any("Missing top-level" in w for w in warnings)

    def test_catches_missing_key_contents(self):
        spec = {**SAMPLE_SPEC, "structure": {"files": [{"path": "foo.py"}]}}
        warnings = validate_spec(spec)
        assert any("key_contents" in w for w in warnings)

    def test_catches_missing_feature_details(self):
        spec = {**SAMPLE_SPEC, "features": [{"name": "feat1"}]}
        warnings = validate_spec(spec)
        assert any("details" in w for w in warnings)

    def test_passes_complete_spec(self):
        warnings = validate_spec(SAMPLE_SPEC)
        assert not any("Missing top-level" in w for w in warnings)


# ── spec_validator_tool tests ─────────────────────────────────


class TestValidateSpecYaml:
    def test_passes_valid_spec(self):
        yaml_str = yaml.dump(SAMPLE_SPEC, default_flow_style=False)
        result = validate_spec_yaml(yaml_str)
        assert result["valid"] is True
        assert result["stats"]["files_planned"] == 3
        assert result["stats"]["features"] == 2
        assert result["stats"]["entities"] == 1
        assert result["stats"]["endpoints"] == 3

    def test_catches_missing_required_sections(self):
        incomplete = {"name": "test", "description": "hello"}
        yaml_str = yaml.dump(incomplete, default_flow_style=False)
        result = validate_spec_yaml(yaml_str)
        assert result["valid"] is False
        assert any("stack" in e for e in result["errors"])

    def test_catches_invalid_yaml(self):
        result = validate_spec_yaml("{{bad yaml")
        assert result["valid"] is False
        assert any("Invalid YAML" in e or "YAML" in e for e in result["errors"])

    def test_catches_non_dict_yaml(self):
        result = validate_spec_yaml("- just\n- a\n- list\n")
        assert result["valid"] is False

    def test_warns_on_entity_without_pk(self):
        spec = {**SAMPLE_SPEC}
        spec["data_model"] = {
            "entities": [
                {
                    "name": "Foo",
                    "fields": [
                        {"name": "bar", "type": "string", "constraints": "not_null"},
                        {"name": "baz", "type": "int", "constraints": ""},
                    ],
                },
            ]
        }
        yaml_str = yaml.dump(spec, default_flow_style=False)
        result = validate_spec_yaml(yaml_str)
        assert any("primary key" in w for w in result["warnings"])

    def test_warns_on_entity_with_too_few_fields(self):
        spec = {**SAMPLE_SPEC}
        spec["data_model"] = {
            "entities": [
                {
                    "name": "Tiny",
                    "fields": [
                        {"name": "id", "type": "int", "constraints": "primary_key"},
                    ],
                },
            ]
        }
        yaml_str = yaml.dump(spec, default_flow_style=False)
        result = validate_spec_yaml(yaml_str)
        assert any("only 1 field" in w for w in result["warnings"])


# ── spec_to_prompt tests ──────────────────────────────────────


class TestSpecToPrompt:
    def test_includes_project_name(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "test-project" in prompt

    def test_includes_all_files(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "src/main.py" in prompt
        assert "src/models.py" in prompt
        assert "tests/test_main.py" in prompt

    def test_includes_features(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "Health Check" in prompt
        assert "User CRUD" in prompt
        assert "Email must be unique" in prompt

    def test_includes_data_model(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "User" in prompt
        assert "uuid" in prompt
        assert "email" in prompt

    def test_includes_critical_instructions(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "CRITICAL RULES" in prompt
        assert "filesystem_write_file" in prompt
        assert "asyncio.to_thread" in prompt
        # New full-stack tools should be mentioned
        assert "execute_command" in prompt
        assert "memory" in prompt
        assert "sequential-thinking" in prompt

    def test_includes_policy_when_provided(self):
        prompt = spec_to_prompt(SAMPLE_SPEC, policy_text="Never use print statements.")
        assert "Never use print statements" in prompt
        assert "Coding Policy" in prompt

    def test_no_policy_section_when_none(self):
        prompt = spec_to_prompt(SAMPLE_SPEC, policy_text=None)
        assert "Coding Policy" not in prompt

    def test_includes_stack_details(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "python" in prompt.lower()
        assert "fastapi" in prompt.lower()
        assert "postgresql" in prompt.lower()

    def test_includes_docker(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "Multi-stage" in prompt or "multi_stage" in prompt
        assert "python:3.11-slim" in prompt

    def test_includes_testing(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "pytest" in prompt
        assert "80" in prompt  # coverage target

    def test_includes_security(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "authentication" in prompt.lower()
        assert "rbac" in prompt.lower()

    def test_handles_simple_string_features(self):
        simple = {
            **SAMPLE_SPEC,
            "features": ["auth", "crud", "search"],
        }
        prompt = spec_to_prompt(simple)
        assert "auth" in prompt
        assert "search" in prompt

    def test_handles_nested_standards(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "ruff" in prompt.lower()
        assert "mypy" in prompt.lower()

    def test_handles_flat_standards(self):
        flat = {
            **SAMPLE_SPEC,
            "standards": {
                "formatting": "ruff",
                "linting": "eslint",
                "typing": "tsc",
            },
        }
        prompt = spec_to_prompt(flat)
        assert "ruff" in prompt
        assert "eslint" in prompt

    def test_handles_legacy_structure_list(self):
        legacy = {
            **SAMPLE_SPEC,
            "structure": ["src/main.py", "Dockerfile", "README.md"],
        }
        prompt = spec_to_prompt(legacy)
        assert "Dockerfile" in prompt

    def test_includes_workspace_root(self):
        prompt = spec_to_prompt(SAMPLE_SPEC, workspace_root="/custom/path")
        assert "/custom/path" in prompt

    def test_includes_api_base_path(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "/api/v1" in prompt

    def test_includes_ci_cd_pipeline(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "github-actions" in prompt
        assert "lint" in prompt
        assert "deploy" in prompt


# ── GitHub publishing tests ───────────────────────────────────

GITHUB_SPEC: dict = {
    **SAMPLE_SPEC,
    "github": {
        "create_repo": True,
        "owner": "test-org",
        "visibility": "private",
        "branch": "main",
        "description": "A generated project",
        "topics": ["python", "fastapi"],
    },
}


class TestGitHubPrompt:
    def test_github_section_rendered(self):
        prompt = spec_to_prompt(GITHUB_SPEC)
        assert "GitHub Repository" in prompt
        assert "test-org/test-project" in prompt

    def test_github_visibility(self):
        prompt = spec_to_prompt(GITHUB_SPEC)
        assert "private" in prompt.lower()

    def test_github_phase5_present(self):
        prompt = spec_to_prompt(GITHUB_SPEC)
        assert "Phase 5" in prompt
        assert "Publish to GitHub" in prompt

    def test_github_creates_repo(self):
        prompt = spec_to_prompt(GITHUB_SPEC)
        assert "create_repository" in prompt

    def test_github_git_init(self):
        prompt = spec_to_prompt(GITHUB_SPEC)
        assert "git init" in prompt
        assert "git add -A" in prompt
        assert "git commit" in prompt

    def test_github_push_commands(self):
        prompt = spec_to_prompt(GITHUB_SPEC)
        assert "git push" in prompt or "git remote add origin" in prompt

    def test_no_github_section_without_config(self):
        prompt = spec_to_prompt(SAMPLE_SPEC)
        assert "Phase 6" not in prompt
        assert "Publish to GitHub" not in prompt

    def test_github_push_to_existing(self):
        push_spec = {
            **SAMPLE_SPEC,
            "github": {
                "push_to": "https://github.com/myorg/existing-repo.git",
                "branch": "develop",
            },
        }
        prompt = spec_to_prompt(push_spec)
        assert "Phase 5" in prompt
        assert "existing-repo" in prompt
        assert "develop" in prompt

    def test_github_public_visibility(self):
        pub_spec = {
            **SAMPLE_SPEC,
            "github": {
                "create_repo": True,
                "owner": "pub-org",
                "visibility": "public",
            },
        }
        prompt = spec_to_prompt(pub_spec)
        assert "public" in prompt.lower()

    def test_github_topics_includes_push(self):
        """When topics are present, the prompt must still include git push."""
        topics_spec = {
            **SAMPLE_SPEC,
            "github": {
                "create_repo": True,
                "owner": "my-org",
                "visibility": "private",
                "branch": "main",
                "topics": ["python", "fastapi"],
            },
        }
        prompt = spec_to_prompt(topics_spec)
        assert "git push" in prompt


class TestSpecToPromptNullSafety:
    """Spec sections set to None (YAML null) must not crash."""

    def test_null_features(self):
        spec = {**SAMPLE_SPEC, "features": None}
        prompt = spec_to_prompt(spec)
        assert "test-project" in prompt

    def test_null_stack(self):
        spec = {**SAMPLE_SPEC, "stack": None}
        prompt = spec_to_prompt(spec)
        assert "test-project" in prompt

    def test_null_structure(self):
        spec = {**SAMPLE_SPEC, "structure": None}
        prompt = spec_to_prompt(spec)
        assert "CRITICAL RULES" in prompt

    def test_null_data_model(self):
        spec = {**SAMPLE_SPEC, "data_model": None}
        prompt = spec_to_prompt(spec)
        assert "CRITICAL RULES" in prompt


# ── generate.py GitHub flag tests ─────────────────────────────


class TestInjectGitHubConfig:
    def test_no_flag_no_github(self):
        from cuga.generate import _inject_github_config

        class FakeArgs:
            github = False
            github_owner = None
            public = False

        spec = {**SAMPLE_SPEC}
        result = _inject_github_config(spec, FakeArgs())
        assert "github" not in result

    def test_flag_adds_github_section(self):
        from cuga.generate import _inject_github_config

        class FakeArgs:
            github = True
            github_owner = "my-org"
            public = False

        spec = {**SAMPLE_SPEC}
        result = _inject_github_config(spec, FakeArgs())
        assert result["github"]["create_repo"] is True
        assert result["github"]["owner"] == "my-org"
        assert result["github"]["visibility"] == "private"

    def test_public_flag_overrides(self):
        from cuga.generate import _inject_github_config

        class FakeArgs:
            github = True
            github_owner = "my-org"
            public = True

        spec = {**SAMPLE_SPEC}
        result = _inject_github_config(spec, FakeArgs())
        assert result["github"]["visibility"] == "public"


# ── generate.py _run_async test ───────────────────────────────


class TestRunAsync:
    def test_runs_simple_coroutine(self):
        from cuga.generate import _run_async

        async def add(a: int, b: int) -> int:
            return a + b

        assert _run_async(add(3, 4)) == 7

    def test_returns_value(self):
        from cuga.generate import _run_async

        async def identity(x: str) -> str:
            return x

        assert _run_async(identity("hello")) == "hello"


# ── generate.py _parse_args tests ─────────────────────────────


class TestParseArgs:
    def test_description_positional(self):
        from cuga.generate import _parse_args

        args = _parse_args(["Build a REST API"])
        assert args.description == "Build a REST API"

    def test_spec_only_flag(self):
        from cuga.generate import _parse_args

        args = _parse_args(["--spec-only", "Build an API"])
        assert args.spec_only is True

    def test_dry_run_flag(self):
        from cuga.generate import _parse_args

        args = _parse_args(["--dry-run", "Build an API"])
        assert args.dry_run is True

    def test_github_flags(self):
        from cuga.generate import _parse_args

        args = _parse_args(["--github", "--github-owner", "myorg", "--public", "API"])
        assert args.github is True
        assert args.github_owner == "myorg"
        assert args.public is True

    def test_from_file_flag(self):
        from cuga.generate import _parse_args

        args = _parse_args(["--from-file", "brief.txt"])
        assert args.from_file == "brief.txt"

    def test_default_values(self):
        from cuga.generate import _parse_args

        args = _parse_args([])
        assert args.description is None
        assert args.max_retries == 3
        assert args.output == "output"
        assert args.private is True


# ── generate.py _print_build_success tests ─────────────────────


class TestPrintBuildSuccess:
    def test_prints_github_url(self, capsys):
        from cuga.generate import _print_build_success

        spec = {"name": "my-app", "github": {"create_repo": True, "owner": "org"}}
        _print_build_success(spec, "output")
        captured = capsys.readouterr()
        assert "https://github.com/org/my-app" in captured.out

    def test_prints_local_path(self, capsys):
        from cuga.generate import _print_build_success

        spec = {"name": "my-app"}
        _print_build_success(spec, "output")
        captured = capsys.readouterr()
        assert "output/my-app/" in captured.out


# ── main._spec_to_prompt backward compat ──────────────────────


class TestMainSpecToPrompt:
    def test_rich_spec_delegates(self):
        from cuga.main import _spec_to_prompt

        prompt = _spec_to_prompt(SAMPLE_SPEC, None)
        assert "CRITICAL RULES" in prompt
        assert "test-project" in prompt

    def test_simple_spec_uses_legacy(self):
        from cuga.main import _spec_to_prompt

        simple = {
            "name": "simple-app",
            "description": "Simple app",
            "stack": {"language": "python"},
            "features": ["auth", "crud"],
            "structure": ["src/main.py", "Dockerfile"],
            "quality_gates": ["tests pass"],
        }
        prompt = _spec_to_prompt(simple, None)
        assert "simple-app" in prompt
        assert "auth" in prompt

    def test_rich_spec_with_github(self):
        from cuga.main import _spec_to_prompt

        prompt = _spec_to_prompt(GITHUB_SPEC, None)
        assert "Phase 5" in prompt
        assert "test-org" in prompt


# ── spec_to_prompt load_spec tests ────────────────────────────


class TestLoadSpec:
    def test_loads_valid_spec(self, tmp_path):
        from cuga.spec_to_prompt import load_spec

        f = tmp_path / "spec.yaml"
        f.write_text(yaml.dump(SAMPLE_SPEC))
        result = load_spec(str(f))
        assert result["name"] == "test-project"

    def test_raises_on_missing_file(self):
        from cuga.spec_to_prompt import load_spec

        with pytest.raises(FileNotFoundError):
            load_spec("/nonexistent/spec.yaml")

    def test_raises_on_non_dict(self, tmp_path):
        from cuga.spec_to_prompt import load_spec

        f = tmp_path / "bad.yaml"
        f.write_text("- item1\n- item2\n")
        with pytest.raises(yaml.YAMLError, match="mapping"):
            load_spec(str(f))


class TestRenderDictSection:
    def test_flat_dict(self):
        from cuga.spec_to_prompt import _render_dict_section

        result = _render_dict_section({"a": 1, "b": "hello"})
        assert "- a: 1" in result
        assert "- b: hello" in result

    def test_nested_dict(self):
        from cuga.spec_to_prompt import _render_dict_section

        result = _render_dict_section({"outer": {"inner": "val"}})
        assert "- outer:" in result
        assert "  - inner: val" in result

    def test_list_values(self):
        from cuga.spec_to_prompt import _render_dict_section

        result = _render_dict_section({"items": ["x", "y"]})
        assert "- items:" in result
        assert "  - x" in result
        assert "  - y" in result
