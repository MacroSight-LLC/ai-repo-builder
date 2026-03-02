"""Spec-to-Prompt converter — turns a rich structured YAML spec into the
detailed, section-by-section agent prompt that tells the CUGA CodeAct
agent *exactly* what to build.

This replaces the simple ``_spec_to_prompt()`` in main.py with a
version that understands all sections of the enriched spec schema,
including optional GitHub repository creation and code push.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

__all__ = ["load_spec", "spec_to_prompt"]


def load_spec(spec_path: str) -> dict:
    """Read and return a spec YAML file.

    Args:
        spec_path: Path to the YAML spec file.

    Returns:
        Parsed spec dictionary.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        yaml.YAMLError: If the YAML is invalid or not a mapping.
    """
    content = Path(spec_path).read_text(encoding="utf-8")
    result = yaml.safe_load(content)
    if not isinstance(result, dict):
        msg = f"Spec must be a YAML mapping, got {type(result).__name__}"
        raise yaml.YAMLError(msg)
    return result


def _render_dict_section(data: dict, indent: int = 0) -> list[str]:
    """Recursively render a dict into indented bullet lines."""
    prefix = "  " * indent
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}- {key}:")
            lines.extend(_render_dict_section(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{prefix}- {key}:")
            for item in value:
                if isinstance(item, dict):
                    lines.extend(_render_dict_section(item, indent + 1))
                else:
                    lines.append(f"{prefix}  - {item}")
        else:
            lines.append(f"{prefix}- {key}: {value}")
    return lines


def spec_to_prompt(
    spec: dict,
    policy_text: str | None = None,
    workspace_root: str = "/projects/workspace",
) -> str:
    """Convert a full spec dict into a richly-detailed agent prompt.

    Parameters
    ----------
    spec : dict
        The parsed spec YAML.
    policy_text : str | None
        Optional coding policy text to append.
    workspace_root : str
        The filesystem root where files should be written.
        Must match the MCP filesystem server's allowed directory.

    Returns
    -------
    str
        The complete prompt for the CUGA agent.
    """
    name = spec.get("name") or "project"
    desc = spec.get("description") or ""
    stack = spec.get("stack") or {}
    structure = spec.get("structure") or {}
    features = spec.get("features") or []
    data_model = spec.get("data_model") or {}
    api = spec.get("api") or {}
    testing = spec.get("testing") or {}
    pages = spec.get("pages") or []
    components = spec.get("components") or []
    deployment = spec.get("deployment") or {}
    standards = spec.get("standards") or {}
    security = spec.get("security") or {}
    github = spec.get("github") or {}

    parts: list[str] = []

    # ── Header ─────────────────────────────────────────────────
    parts.append(f"# Project: {name}")
    parts.append(f"\n## Description\n{desc}")

    # ── Stack ──────────────────────────────────────────────────
    parts.append("\n## Technology Stack")
    parts.append(f"- Language: {stack.get('language', 'python')}")
    parts.append(f"- Runtime: {stack.get('runtime', 'python3.12')}")

    backend = stack.get("backend", {})
    if backend:
        parts.append(
            f"- Backend: {backend.get('framework', 'fastapi')} ({backend.get('api_style', 'rest')})"
        )

    frontend = stack.get("frontend", {})
    if frontend and frontend.get("framework") not in (None, "none"):
        parts.append(
            f"- Frontend: {frontend.get('framework')} + {frontend.get('styling', 'tailwind')}"
        )
        if frontend.get("state") and frontend["state"] != "none":
            parts.append(f"- State management: {frontend['state']}")

    db = stack.get("database", {})
    if db:
        parts.append(f"- Database: {db.get('primary', 'postgresql')}")
        parts.append(f"- ORM: {db.get('orm', 'sqlalchemy')}")
        if db.get("cache") and db["cache"] != "none":
            parts.append(f"- Cache: {db['cache']}")
        if db.get("search") and db["search"] != "none":
            parts.append(f"- Search: {db['search']}")

    infra = stack.get("infrastructure", {})
    if infra:
        parts.append(f"- Containerization: {infra.get('containerization', 'docker')}")
        parts.append(f"- CI/CD: {infra.get('ci_cd', 'github-actions')}")
        if infra.get("hosting") and infra["hosting"] != "none":
            parts.append(f"- Hosting: {infra['hosting']}")

    # ── File structure ─────────────────────────────────────────
    # Handle legacy flat-list format first
    if isinstance(structure, list):
        parts.append("\n## Files to Create\n")
        for s in structure:
            parts.append(f"- {s}")
        parts.append("")
    else:
        files = structure.get("files", [])
        if files:
            parts.append("\n## Files to Create")
            parts.append(
                "Create EVERY file below **plus any additional files** needed for "
                "a complete application (models, middleware, auth utilities, "
                "components, config, etc.).\n"
                "\nCRITICAL: The `key_contents` listed below are **minimum "
                "starting hints only**. Each file MUST be a **complete, "
                "production-ready implementation** that:\n"
                "- Fully implements ALL features, endpoints, and data models "
                "described in the spec sections below\n"
                "- Includes proper imports, type hints, error handling, and "
                "docstrings\n"
                "- Contains real working logic — never stubs, TODOs, or "
                "placeholder comments\n"
                "- Integrates with other files (e.g., models used by routes, "
                "routes imported by app)\n"
            )
            for f in files:
                if isinstance(f, str):
                    parts.append(f"- `{name}/{f}`")
                    continue
                fpath = f.get("path", "")
                purpose = f.get("purpose", "")
                contents = f.get("key_contents", [])
                parts.append(f"### `{name}/{fpath}`")
                parts.append(f"Purpose: {purpose}")
                if contents:
                    parts.append("Minimum elements (expand into full implementation):")
                    for c in contents:
                        parts.append(f"  - {c}")
                parts.append("")

    # ── Data model ─────────────────────────────────────────────
    entities = data_model.get("entities", [])
    if entities:
        parts.append("\n## Data Model")
        for entity in entities:
            ename = entity.get("name", "")
            fields = entity.get("fields", [])
            rels = entity.get("relationships", [])
            parts.append(f"\n### {ename}")
            if fields:
                parts.append("Fields:")
                for fld in fields:
                    constraints = fld.get("constraints", "")
                    parts.append(
                        f"  - {fld.get('name', '?')}: {fld.get('type', '?')} ({constraints})"
                    )
            if rels:
                parts.append("Relationships:")
                for rel in rels:
                    parts.append(
                        f"  - {rel.get('type', '?')} {rel.get('target', '?')} "
                        f"via {rel.get('foreign_key', '?')}"
                    )

        migration_tool = (data_model.get("migrations") or {}).get("tool")
        if migration_tool and migration_tool != "none":
            parts.append(f"\nMigrations: Use {migration_tool}")
        if (data_model.get("migrations") or {}).get("seed_data"):
            parts.append("Include seed data for development.")

    # ── Pages (frontend routes) ────────────────────────────────
    if pages:
        parts.append("\n## Pages / Routes")
        parts.append(
            "Each page maps to a URL route. Implement them as Next.js/React "
            "pages (or the equivalent in your framework).\n"
        )
        for page in pages:
            ppath = page.get("path", "/")
            pname = page.get("name", "")
            auth = page.get("auth", "public")
            parts.append(f"### `{ppath}` — {pname} (auth: {auth})")
            data_src = page.get("data_source")
            if data_src:
                if isinstance(data_src, list):
                    parts.append(f"Data sources: {', '.join(data_src)}")
                else:
                    parts.append(f"Data source: {data_src}")
            page_components = page.get("components", [])
            if page_components:
                parts.append(f"Components: {', '.join(page_components)}")
            parts.append("")

    # ── UI Components ──────────────────────────────────────────
    if components:
        parts.append("\n## UI Component Hierarchy")
        parts.append(
            "Each component below must be implemented as a standalone module "
            "with typed props. Compose them to build the pages above.\n"
        )
        for comp in components:
            cname = comp.get("name", "")
            ctype = comp.get("type", "widget")
            parts.append(f"### `{cname}` (type: {ctype})")
            props = comp.get("props", [])
            if props:
                parts.append("Props interface:")
                for p in props:
                    req = "required" if p.get("required", True) else "optional"
                    parts.append(f"  - {p.get('name', '?')}: {p.get('type', 'unknown')} ({req})")
            state = comp.get("state", [])
            if state:
                parts.append(f"Local state: {', '.join(state)}")
            children = comp.get("children", [])
            if children:
                parts.append(f"Children: {', '.join(children)}")
            parts.append("")

    # ── Features ───────────────────────────────────────────────
    if features:
        parts.append("\n## Features")
        for feat in features:
            # Handle simple string features (legacy)
            if isinstance(feat, str):
                parts.append(f"- {feat}")
                continue

            fname = feat.get("name", "")
            ftype = feat.get("type", "")
            details = feat.get("details") or {}
            parts.append(f"\n### {fname} (type: {ftype})")

            # Auth-specific details
            if ftype == "auth":
                method = details.get("method", "")
                if method:
                    parts.append(f"Method: {method}")
                for flow in details.get("flows", []):
                    parts.append(f"  - {flow}")
                for sec in details.get("security", []):
                    parts.append(f"  - Security: {sec}")

            # Endpoints
            endpoints = details.get("endpoints", [])
            if endpoints:
                parts.append("Endpoints:")
                for ep in endpoints:
                    parts.append(f"  - {ep}")

            # Business logic
            logic = details.get("business_logic", [])
            if logic:
                parts.append("Business Logic:")
                for item in logic:
                    parts.append(f"  - {item}")

            # Validations
            validations = details.get("validations", [])
            if validations:
                parts.append("Validations:")
                for v in validations:
                    parts.append(f"  - {v}")

    # ── API design ─────────────────────────────────────────────
    if api:
        parts.append("\n## API Design")
        parts.append(f"- Base path: {api.get('base_path', '/api/v1')}")
        if api.get("versioning"):
            parts.append(f"- Versioning: {api['versioning']}")
        parts.append(f"- Auth required: {api.get('auth_required', True)}")

        rate = api.get("rate_limiting")
        if isinstance(rate, dict):
            if rate.get("enabled"):
                parts.append(f"- Rate limiting: {rate.get('default', '100/minute')}")
        elif rate and rate != "none":
            parts.append(f"- Rate limiting: {rate}")

        resp = api.get("response_format", {})
        if resp.get("shape"):
            parts.append(f"- Response envelope:\n```json\n{resp['shape'].strip()}\n```")

        docs = api.get("documentation", {})
        if docs.get("openapi"):
            parts.append(f"- OpenAPI docs at: {docs.get('path', '/docs')}")

    # ── Testing ────────────────────────────────────────────────
    if testing:
        parts.append("\n## Testing Requirements")
        parts.append(f"- Framework: {testing.get('framework', 'pytest')}")

        # Handle nested types (unit/integration/e2e)
        types = testing.get("types", {})
        if types:
            unit = types.get("unit", {})
            if unit:
                cov = unit.get("coverage_target", testing.get("coverage_target"))
                if cov:
                    parts.append(f"- Unit test coverage target: {cov}%")
                for p in unit.get("patterns", []):
                    parts.append(f"  - {p}")

            integration = types.get("integration", {})
            if integration:
                parts.append("- Integration tests:")
                for p in integration.get("patterns", []):
                    parts.append(f"  - {p}")

            e2e = types.get("e2e", {})
            if e2e and e2e.get("tool") not in (None, "none"):
                parts.append(f"- E2E tests: {e2e['tool']}")
                for p in e2e.get("patterns", []):
                    parts.append(f"  - {p}")
        else:
            # Flat testing format
            cov = testing.get("coverage_target")
            if cov:
                parts.append(f"- Coverage target: {cov}%")
            for p in testing.get("patterns", []):
                parts.append(f"  - {p}")

        for fix in testing.get("fixtures", []):
            parts.append(f"  - Fixture: {fix}")

    # ── Security ───────────────────────────────────────────────
    if security:
        parts.append("\n## Security Requirements")
        parts.extend(_render_dict_section(security))

    # ── Deployment ─────────────────────────────────────────────
    if deployment:
        docker = deployment.get("docker", {})
        if docker:
            parts.append("\n## Docker Configuration")
            parts.append(f"- Multi-stage build: {docker.get('multi_stage', True)}")
            parts.append(f"- Base image: {docker.get('base_image', 'python:3.12-slim')}")
            services = docker.get("compose_services", [])
            if services:
                parts.append("- Compose services:")
                for svc in services:
                    img = svc.get("image", "built from Dockerfile")
                    parts.append(f"  - {svc.get('name', '?')}: {img}")
                    for port in svc.get("ports", []):
                        parts.append(f"    port: {port}")

        ci = deployment.get("ci_cd", {})
        if ci:
            parts.append(f"\n## CI/CD Pipeline ({ci.get('provider', 'github-actions')})")
            for step in ci.get("pipeline", []):
                parts.append(f"  - {step}")

        envs = deployment.get("environments", [])
        if envs:
            parts.append("\n## Environments")
            for env in envs:
                parts.append(f"\n### {env.get('name', '?')}")
                for cfg in env.get("config", []):
                    parts.append(f"  - {cfg}")

    # ── Standards ──────────────────────────────────────────────
    if standards:
        parts.append("\n## Code Standards")
        # Handle both nested-dict and flat-string formats for each standard
        fmt = standards.get("formatting", "ruff")
        if isinstance(fmt, dict):
            parts.append(
                f"- Formatter: {fmt.get('tool', 'ruff')} (line length {fmt.get('line_length', 100)})"
            )
        else:
            parts.append(f"- Formatting: {fmt}")

        lint = standards.get("linting", "ruff")
        if isinstance(lint, dict):
            parts.append(
                f"- Linter: {lint.get('tool', 'ruff')} (strict: {lint.get('strict', True)})"
            )
        else:
            parts.append(f"- Linting: {lint}")

        typing_ = standards.get("typing", "mypy")
        if isinstance(typing_, dict):
            parts.append(
                f"- Type checker: {typing_.get('tool', 'mypy')} (strict: {typing_.get('strict', True)})"
            )
        else:
            parts.append(f"- Type checking: {typing_} (strict)")

        doc = standards.get("docstrings")
        if doc is None:
            doc = standards.get("documentation", {})
            if isinstance(doc, dict):
                doc = doc.get("docstrings", "google")
        parts.append(f"- Docstrings: {doc} style")

        git = standards.get("git", {})
        if git:
            parts.append(f"- Commit convention: {git.get('commit_convention', 'conventional')}")
            if git.get("pre_commit_hooks"):
                parts.append("- Pre-commit hooks: enabled")

    # ── GitHub publishing ──────────────────────────────────────
    if github:
        parts.append("\n## GitHub Repository")
        if github.get("create_repo"):
            owner = github.get("owner") or os.environ.get("GITHUB_OWNER", "")
            vis = github.get("visibility", "private")
            parts.append(f"- Create new repo: {owner}/{name}")
            parts.append(f"- Visibility: {vis}")
            if github.get("description"):
                parts.append(f"- Repo description: {github['description']}")
            topics = github.get("topics", [])
            if topics:
                parts.append(f"- Topics: {', '.join(topics)}")
        elif github.get("push_to"):
            parts.append(f"- Push code to existing repo: {github['push_to']}")
        if github.get("branch"):
            parts.append(f"- Branch: {github['branch']}")

    # ── Quality gates (simple specs) ───────────────────────────
    gates = spec.get("quality_gates", [])
    if gates:
        parts.append("\n## Quality Gates")
        for g in gates:
            parts.append(f"  - {g}")

    # ── Build instructions ─────────────────────────────────────
    ws = workspace_root.rstrip("/")
    parts.append(f"""
## AVAILABLE TOOLS
You have the following MCP tool servers available:
- **filesystem**: Read, write, list, search files in {ws}
- **execute_command**: Run shell commands (pip, npm, pytest, ruff, mypy, docker, git, curl, etc.)
- **context7**: Look up current documentation for any library or framework
- **brave-search**: Search the web for solutions, examples, or niche library docs
- **postgres**: Execute SQL against the dev PostgreSQL database directly
- **memory**: Store and retrieve key decisions to stay consistent across files
- **sequential-thinking**: Break complex problems into structured reasoning steps
- **puppeteer**: Headless browser — verify frontend renders, take screenshots
- **github**: Create repos, push code, manage branches and issues

## FULL-STACK BUILD WORKFLOW
Follow this workflow to build a production-grade project:

### Phase 1: Plan & Remember
1. Use **sequential-thinking** to plan file creation order (dependencies first).
2. Use **memory** to store key architecture decisions:
   - "ORM style: SQLAlchemy 2.0 async"
   - "Auth strategy: JWT with refresh tokens"
   - "API response shape: {"{"}data, error, meta{"}"}"
3. Use **context7** to look up current docs for the chosen frameworks.
4. Use **brave-search** if context7 doesn't cover a niche library.

### Phase 2: Scaffold & Write
5. Write the project config files first (pyproject.toml, package.json, etc.)
6. Use **execute_command** to install dependencies:
   - `cd {ws}/{name} && pip install -e ".[dev]"` (Python)
   - `cd {ws}/{name} && npm install` (Node/Frontend)
7. Write files ONE AT A TIME using **filesystem** in this exact order:

   ⚠️ BEFORE writing any file, create its parent directory:
   ```python
   await filesystem_create_directory(path="{ws}/{name}/backend/routers")
   ```
   The filesystem server rejects writes if the parent directory does not exist.
   Create directories for EVERY subfolder (backend/, backend/routers/, frontend/app/, etc.)
   before writing files into them.

   a. `.gitignore` — ALWAYS first (prevents committing junk).
      Must include: `__pycache__/`, `*.pyc`, `.env`, `.venv/`, `node_modules/`,
      `.ruff_cache/`, `.mypy_cache/`, `.pytest_cache/`, `*.egg-info/`,
      `dist/`, `build/`, `.DS_Store`
   b. `.env.example` — placeholder values, NEVER real secrets
   c. Configuration files (pyproject.toml, package.json, etc.)
   d. Database models / schemas
   e. Service / business logic layer
   f. API routes / controllers
   g. Main application entry point
   h. Tests
   i. Docker / CI/CD files
   j. README.md

   ⚠️ CRITICAL: File content rules:
   - The `content` parameter must be the RAW source code — NOT JSON-encoded.
   - WRONG: content='{{"content": "from fastapi import FastAPI\\n..."}}'
   - WRONG: content=json.dumps({{"content": code}})
   - RIGHT:  content="from fastapi import FastAPI\\n\\napp = FastAPI()\\n..."
   - Do NOT add leading indentation (4 spaces) to file content — write code
     exactly as it should appear in the file (no extra leading whitespace).
   - Use \\n for newlines within the content string, not actual Python indentation.

   Example of a correct filesystem call:
   ```python
   result = await filesystem_write_file(
       path="{ws}/{name}/backend/main.py",
       content="from fastapi import FastAPI\\nfrom fastapi.middleware.cors import CORSMiddleware\\n\\napp = FastAPI(title=\\"{name}\\", version=\\"0.1.0\\")\\n\\n@app.get(\\"/\\")\\nasync def root():\\n    return {{\\"status\\": \\"ok\\"}}\\n"
   )
   ```

8. Before writing each file, use **memory** to recall relevant decisions.

IMPORTANT: Every shell command MUST cd into the project directory first:
   cd {ws}/{name} && <command>
Never run commands from the workspace root — always cd into {ws}/{name}.

### Phase 3: Validate & Fix
9. After writing all source files, validate step by step:

   **Step 3a — Syntax check** (for each .py file):
   `cd {ws}/{name} && python -m py_compile <file>`
   If syntax errors:
     1. READ the full error message (file, line number, description)
     2. Use filesystem to read that file around the error line
     3. FIX the specific issue (missing colon, unmatched bracket, bad indent)
     4. Re-run py_compile on JUST that file
     5. Repeat until clean

   **Step 3b — Lint**:
   `cd {ws}/{name} && ruff check . --fix`
   If errors remain after --fix:
     1. READ each error (rule code + message)
     2. Fix manually (unused imports → remove, missing type hints → add)
     3. Re-run: `cd {ws}/{name} && ruff check .`
     4. Max 3 iterations

   **Step 3c — Format**:
   `cd {ws}/{name} && ruff format .`
   If this fails, you have a syntax error — go back to Step 3a.

   **Step 3d — Import check**:
   `cd {ws}/{name} && python -c "import src.main"` (or the main entry point)
   If ImportError:
     1. READ the traceback — it tells you exactly which import failed
     2. Check: is the package in pyproject.toml dependencies?
     3. Check: is the module path correct? (src.models vs models)
     4. Fix and re-run

10. Use **postgres** to verify database schema if applicable.
11. If frontend exists, use **puppeteer** to verify it renders.

### Phase 3.5: Frontend Validation (if frontend exists)
   **Step 3e — Install frontend dependencies**:
   `cd {ws}/{name} && npm install` (or pnpm/yarn)
   If install fails, check package.json for missing or invalid packages.

   **Step 3f — TypeScript type check** (if using TypeScript):
   `cd {ws}/{name} && npx tsc --noEmit`
   If type errors:
     1. READ each error — file:line "Type 'X' is not assignable to type 'Y'"
     2. Fix the type mismatch in the source file
     3. Re-run tsc

   **Step 3g — Frontend build**:
   `cd {ws}/{name} && npm run build`
   If build fails:
     1. Most common: missing imports, incorrect component props, `"use client"` directive missing
     2. For Next.js App Router: pages in `app/` are Server Components by default
        — add `"use client"` at the top only if using hooks (useState, useEffect, onClick, etc.)
     3. Fix and re-run build

   **Step 3h — API client consistency**:
   Verify that every frontend fetch/API call matches a backend endpoint:
   - URL paths must match the backend router prefixes + route paths
   - Request/response types must match the backend schemas
   - Auth headers must be included for protected endpoints

### Phase 4: Test
12. Run: `cd {ws}/{name} && python -m pytest tests/ -v --tb=short`

   If tests fail:
     1. READ the full traceback — focus on the LAST frame (actual error)
     2. Common causes and fixes:
        - ImportError → wrong module path or missing dependency
        - AttributeError → typo in method/attribute name, check source
        - TypeError → wrong number of args, check function signature
        - AssertionError → expected value wrong, check logic
        - ConnectionError → service not available, mock it
     3. Fix the SOURCE code (not the test) unless the test is wrong
     4. Re-run JUST the failing test: `pytest tests/test_foo.py::test_bar -v`
     5. When that passes, re-run ALL tests: `pytest tests/ -v`
     6. Max 3 fix-test cycles. Target: ALL tests pass.

### Phase 5: Package & Verify
13. Use **execute_command** to build Docker image:
    - `cd {ws}/{name} && docker build -t {name} .`
14. Verify at least 3 key files with **filesystem** read_file.
15. List every file created with its path.
""")

    # ── Phase 5: GitHub (conditional) ──────────────────────────
    _gh_owner = github.get("owner") or os.environ.get("GITHUB_OWNER", "") if github else ""
    _gh_vis = github.get("visibility", "private") if github else "private"
    _gh_create = github.get("create_repo", False) if github else False
    _gh_push = github.get("push_to", "") if github else ""
    _gh_branch = github.get("branch", "main") if github else "main"
    _gh_desc = github.get("description", desc) if github else desc
    _gh_topics = github.get("topics", []) if github else []

    if _gh_create or _gh_push:
        parts.append(f"""
### Phase 6: Publish to GitHub
16. Initialize a git repo and commit all files:
    ```
    result = await execute_command(command="cd {ws}/{name} && git init && git add -A && git commit -m 'Initial commit — generated by AI Repo Builder'")
    ```""")

        if _gh_create:
            parts.append(f"""\
17. Create a new GitHub repository:
    ```
    result = await create_repository(name="{name}", description="{_gh_desc}", private={str(_gh_vis == "private").lower()}, auto_init=False)
    ```""")
            if _gh_topics:
                parts.append(f"""\
    Then add remote and push:
    ```
    result = await execute_command(command="cd {ws}/{name} && git remote add origin https://github.com/{_gh_owner}/{name}.git && git branch -M {_gh_branch} && git push -u origin {_gh_branch}")
    ```""")
            else:
                parts.append(f"""\
18. Add remote and push:
    ```
    result = await execute_command(command="cd {ws}/{name} && git remote add origin https://github.com/{_gh_owner}/{name}.git && git branch -M {_gh_branch} && git push -u origin {_gh_branch}")
    ```""")
        elif _gh_push:
            parts.append(f"""\
17. Push to existing repo:
    ```
    result = await execute_command(command="cd {ws}/{name} && git remote add origin {_gh_push} && git branch -M {_gh_branch} && git push -u origin {_gh_branch}")
    ```""")

        parts.append(f"""
19. Verify the push succeeded:
    ```
    result = await execute_command(command="cd {ws}/{name} && git log --oneline -3")
    ```
""")

    parts.append(f"""
## CRITICAL RULES
1. Create EVERY file listed in the structure section — do not skip any.
   Also create additional files needed for a complete app (models, middleware,
   auth utilities, services, components, tests) even if not explicitly listed.
2. Every file must be **complete, production-ready code** — NEVER stubs, TODOs,
   one-liners, or placeholder comments. The `key_contents` in the spec are
   MINIMUM STARTING HINTS — you must expand each file into a full implementation
   with all imports, types, logic, error handling, and docstrings.
   For example, if key_contents says `from fastapi import FastAPI; app = FastAPI()`,
   the file MUST include all routes, middleware, CORS config, exception handlers,
   lifespan hooks, etc. — not just those two lines.
3. The filesystem workspace root is {ws} — write ALL files under:
       {ws}/{name}/<filepath>
4. CRITICAL: Call tools DIRECTLY with await — NEVER use asyncio.to_thread,
   asyncio.run, or lambda wrappers (they silently fail in the CodeAct sandbox).
5. Include all imports, type hints, docstrings, and error handling.
6. All code must be consistent with the chosen framework's conventions.
7. Use memory to persist decisions — so file 28 is consistent with file 1.
8. Always run linting and tests after writing code — fix errors before moving on.
9. When done, provide a summary of all files created and test results.
10. MINIMUM file sizes: config files ≥ 10 lines, source files ≥ 30 lines,
    main entry points ≥ 50 lines. If a file is shorter, you are writing stubs.
""")

    # ── Coding policy ─────────────────────────────────────────
    if policy_text:
        parts.append(f"## Coding Policy\n```yaml\n{policy_text}\n```\n")

    # ── Lessons from past builds ───────────────────────────────
    try:
        from cuga.build_catalog import get_lessons_for_prompt

        lessons = get_lessons_for_prompt(spec)
        if lessons:
            parts.append(lessons)
    except ImportError:
        pass  # build_catalog not available

    return "\n".join(parts)
