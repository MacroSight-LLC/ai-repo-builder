"""
Spec Generator — Converts plain English project descriptions
into structured, comprehensive YAML project specifications.

Stage 1 of the two-stage pipeline:
    1. spec_generator  (natural language → structured YAML spec)
    2. cuga.main       (structured spec → working project)

Uses the same WatsonX LLM configured in the project to plan the
project architecture before the CUGA agent builds it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import yaml
from loguru import logger

__all__ = [
    "SPEC_SYSTEM_PROMPT",
    "build_spec_prompt",
    "parse_spec_response",
    "save_spec",
    "validate_spec",
]

# ---------------------------------------------------------------------------
# The mega-prompt that teaches the LLM how to produce a complete spec
# ---------------------------------------------------------------------------
SPEC_SYSTEM_PROMPT = r"""You are an expert software architect and project planner.
Your job is to take a plain English project description and produce a comprehensive,
detailed project specification in YAML format.

You must ALWAYS produce a complete spec with ALL of the following sections,
even if the user didn't mention them.  Use your expertise to fill in best
practices for anything the user left unspecified.

Required output sections (every key below MUST appear in your YAML):

name            – kebab-case project name
description     – one-paragraph summary
version         – semver string (default "0.1.0")

stack:
  language      – python | typescript | go | rust | java
  runtime       – e.g. python3.11, node20, go1.22
  frontend:
    framework   – react | nextjs | vue | svelte | angular | none
    styling     – tailwind | css-modules | shadcn | none
    state       – zustand | redux | pinia | none
  backend:
    framework   – fastapi | express | gin | actix | spring | django | nestjs
    api_style   – rest | graphql | grpc | trpc
  database:
    primary     – postgresql | mysql | mongodb | sqlite
    orm         – sqlalchemy | prisma | gorm | typeorm | drizzle
    cache       – redis | none
  infrastructure:
    containerization – docker
    ci_cd       – github-actions | gitlab-ci | none
    hosting     – docker-compose | kubernetes | vercel | none

structure:
  files:        – list of objects, each with:
      path          – relative filepath
      purpose       – what this file does
      key_contents  – list of strings describing what must go inside the file

features:       – list of objects, each with:
    name        – short label
    type        – auth | crud | realtime | integration | background | analytics
    details:
      endpoints      – list of "METHOD /path - description" strings
      business_logic – list of rule descriptions
      validations    – list of validation descriptions

data_model:
  entities:     – list of objects, each with:
      name      – entity name (PascalCase)
      fields:   – list of {name, type, constraints} objects
      relationships: – list of {type, target, foreign_key} objects
  migrations:
    tool        – alembic | prisma-migrate | goose | none

api:
  base_path     – e.g. /api/v1
  auth_required – true | false
  rate_limiting – e.g. "100/minute" or "none"
  response_format:
    envelope    – true | false
    shape       – JSON template string showing the envelope structure

testing:
  framework     – pytest | jest | vitest | go-test
  coverage_target – integer percentage
  fixtures      – list of test fixtures to create
  patterns      – list of testing strategy descriptions

deployment:
  docker:
    multi_stage  – true | false
    base_image   – e.g. python:3.11-slim
    compose_services: – list of {name, image (optional), ports} objects
  ci_cd:
    provider    – github-actions | gitlab-ci | none
    pipeline    – ordered list of step descriptions

standards:
  formatting    – ruff | prettier | gofmt
  linting       – ruff | eslint | golangci-lint
  typing        – mypy | tsc | none
  docstrings    – google | numpy | jsdoc
  git:
    commit_convention – conventional | none

security:
  authentication – required | optional | none
  authorization  – rbac | abac | none
  input_validation – strict | basic
  secrets        – environment_variables_only
  cors_origins   – list of allowed origins
  dependency_scanning – github-dependabot | none

RULES:
1. ALWAYS include every section listed above.  Never skip a section.
2. If the user does not specify a technology, choose the BEST modern option.
3. Every file in the structure section MUST have key_contents describing what
   goes inside.
4. Every CRUD feature MUST have detailed endpoints.
5. Every data-model entity MUST have complete field definitions.
6. The spec must be detailed enough that a developer could build the entire
   project WITHOUT asking any clarifying questions.
7. Default to modern, production-grade choices (not toy/tutorial patterns).
8. Always include auth, testing, Docker, CI/CD, and security — even if
   not explicitly asked for.
9. If it's a full-stack app, include BOTH frontend and backend files.
10. Use the user's exact wording for name and description where possible.
11. EVERY project MUST include these files in structure.files:
    - .gitignore (with Python/Node exclusions as appropriate)
    - .env.example (placeholder values only — NEVER real secrets)
    - README.md (description, quickstart, API docs link, testing)
    - pyproject.toml or package.json (depending on language)
    - Dockerfile (multi-stage, production-ready)
    - docker-compose.yaml (app + database + any other services)
    - .github/workflows/ci.yaml (lint → test → build pipeline)

TECHNOLOGY DEFAULTS (when user does not specify):
  Python backend → FastAPI + SQLAlchemy + Alembic + pytest
  TypeScript backend → NestJS + Prisma + Vitest
  Frontend → Next.js 14 + Tailwind + shadcn/ui
  Database → PostgreSQL 16
  Cache → Redis 7
  Auth → JWT with refresh tokens
  Deploy → Docker + GitHub Actions

Output ONLY the YAML content.  No markdown fences.  No explanatory prose.
"""


def build_spec_prompt(user_input: str) -> str:
    """Build the user-turn that asks the LLM to produce the YAML spec.

    Args:
        user_input: Plain-English project description.

    Returns:
        Formatted prompt string.

    Raises:
        ValueError: If user_input is empty or blank.
    """
    if not user_input or not user_input.strip():
        msg = "Project description must not be empty"
        raise ValueError(msg)
    return (
        "Convert this project description into a complete project specification.\n\n"
        f"PROJECT DESCRIPTION:\n{user_input}\n\n"
        "Generate the full YAML spec following the required format. "
        "Include EVERY section. Fill in best practices for anything not "
        "explicitly mentioned.\n\n"
        "CRITICAL OUTPUT RULES:\n"
        "- Output ONLY raw YAML. No markdown fences (no ```). No explanation text.\n"
        "- The very first character of your response must be the YAML key 'name:'.\n"
        "- Do not include any text before or after the YAML.\n"
        "- Ensure all YAML is properly indented (2 spaces).\n"
        "- Use double quotes for string values that contain special characters.\n"
        "- Every list item under 'files' MUST have 'path', 'purpose', and 'key_contents'.\n"
        "- Every entity MUST have at least 'name' and 'fields' with typed entries.\n"
    )


# ------------------------------------------------------------------
# Response parsing
# ------------------------------------------------------------------


def parse_spec_response(response: str) -> dict:
    """Parse the LLM's text response into a Python dict.

    Handles common LLM quirks: markdown fences, leading/trailing prose,
    multiple YAML documents.
    """
    cleaned = response.strip()

    # Strategy 1: Extract from markdown fences
    if "```" in cleaned:
        match = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()
        else:
            # Fall back: strip all fence lines
            lines = cleaned.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

    # Strategy 2: Find the first line that starts with `name:`
    found_name = False
    if not cleaned.startswith("name:"):
        lines = cleaned.split("\n")
        for i, line in enumerate(lines):
            if re.match(r"^name\s*:", line):
                cleaned = "\n".join(lines[i:])
                found_name = True
                break

    # Strategy 3: Fall back to any YAML key (only if Strategy 2 didn't match)
    if not found_name and not cleaned.startswith("name:"):
        lines = cleaned.split("\n")
        start_idx = 0
        for i, line in enumerate(lines):
            if re.match(r"^[a-z_]+\s*:", line):
                start_idx = i
                break
        cleaned = "\n".join(lines[start_idx:])

    # Handle multiple YAML documents — keep only the first
    if "\n---\n" in cleaned:
        cleaned = cleaned.split("\n---\n")[0]

    spec = yaml.safe_load(cleaned)

    if not isinstance(spec, dict):
        raise yaml.YAMLError(
            f"Expected a YAML mapping (dict), got {type(spec).__name__}: {str(spec)[:200]}"
        )

    return spec


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------


def save_spec(spec: dict, output_dir: str = "specs") -> Path:
    """Write a spec dict to a timestamped YAML file and return the path."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    name = spec.get("name", "untitled-project")
    # Sanitize for filesystem safety
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", name)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    filepath = Path(output_dir) / f"{safe_name}-{stamp}.yaml"
    filepath.write_text(
        yaml.dump(spec, default_flow_style=False, sort_keys=False, width=120),
        encoding="utf-8",
    )
    logger.info("Spec saved → {}", filepath)
    return filepath


# ------------------------------------------------------------------
# Quick validation (lightweight; the full validator is in
# spec_validator_tool.py)
# ------------------------------------------------------------------

REQUIRED_TOP_LEVEL = [
    "name",
    "description",
    "version",
    "stack",
    "structure",
    "features",
    "data_model",
    "api",
    "testing",
    "deployment",
    "standards",
    "security",
]


def validate_spec(spec: dict) -> list[str]:
    """Return a list of human-readable warnings for missing/incomplete parts."""
    warnings: list[str] = []
    missing = [s for s in REQUIRED_TOP_LEVEL if s not in spec]
    if missing:
        warnings.append(f"Missing top-level sections: {', '.join(missing)}")

    for f in (spec.get("structure") or {}).get("files") or []:
        if "key_contents" not in f:
            warnings.append(f"File '{f.get('path', '?')}' has no key_contents")

    for feat in spec.get("features") or []:
        if isinstance(feat, dict) and "details" not in feat:
            warnings.append(f"Feature '{feat.get('name', '?')}' missing details")

    return warnings
