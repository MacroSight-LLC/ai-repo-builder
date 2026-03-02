"""
Post-build validation and cleanup for generated projects.

Fixes common LLM output issues (extra indentation) and validates
that the generated project matches the spec.
"""

from __future__ import annotations

import ast
import contextlib
import re
import subprocess
import textwrap
from pathlib import Path

from loguru import logger

__all__ = [
    "check_llm_smells",
    "check_required_files",
    "check_spec_completeness",
    "fix_indentation",
    "post_build_validate",
    "run_ruff_check",
    "run_tests",
    "validate_docker_build",
    "validate_frontend",
    "validate_imports",
    "validate_migrations",
    "validate_project",
    "validate_python_syntax",
    "validate_spec_endpoints",
    "validate_typescript",
]

# ── Shared constants ──────────────────────────────────────────

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        "node_modules",
        ".venv",
        ".git",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)

_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".txt",
        ".md",
        ".yml",
        ".yaml",
        ".toml",
        ".cfg",
        ".ini",
        ".json",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".sh",
        ".bash",
        ".env",
        ".gitignore",
        ".dockerignore",
        "",  # extensionless files (Dockerfile, Makefile, etc.)
    }
)

_SOURCE_EXTENSIONS: frozenset[str] = frozenset({".py", ".js", ".ts", ".jsx", ".tsx"})


def _iter_project_files(
    project_dir: Path,
    extensions: frozenset[str] | None = None,
) -> list[Path]:
    """List project files, skipping cache and virtual-environment directories.

    Args:
        project_dir: Root of the project to scan.
        extensions: If given, only include files whose suffix is in this set.
            Pass ``frozenset({""})`` to match extensionless files.

    Returns:
        Sorted list of matching ``Path`` objects.
    """
    result: list[Path] = []
    for fp in sorted(project_dir.rglob("*")):
        if not fp.is_file():
            continue
        if any(p in _SKIP_DIRS for p in fp.relative_to(project_dir).parts):
            continue
        if extensions is not None and fp.suffix not in extensions:
            continue
        result.append(fp)
    return result


# ── LLM smell patterns ────────────────────────────────────────
# Common broken patterns that LLMs generate. Each tuple is
# (regex_pattern, human description).

LLM_SMELLS: list[tuple[str, str, str]] = [
    # (regex_pattern, severity, human_description)
    (r"(?:#|//)\s*TODO", "warn", "TODO comment — incomplete implementation"),
    (r"(?:#|//)\s*FIXME", "warn", "FIXME comment — known broken code"),
    (r"^\s*pass\s*$", "error", "Empty pass statement — stub function"),
    (r"raise NotImplementedError", "error", "NotImplementedError — stub function"),
    (r"^\s*\.\.\.(?!\s*[)\]])", "warn", "Ellipsis literal — truncated code"),
    (r"(?:#|//)\s*[Ii]mplement\b", "error", "Placeholder comment — not implemented"),
    (
        r"(?:#|//)\s*[Aa]dd\s+.*\s+here",
        "error",
        "Placeholder comment — not implemented",
    ),
    (r"from\s+\S+\s+import\s+\*", "warn", "Wildcard import"),
    (
        r"^\s*except\s*:",
        "warn",
        "Bare except — catches everything including SystemExit",
    ),
    (r'(?:password|passwd)\s*=\s*["\'][^"\'$\{\s]', "error", "Hardcoded password"),
    (
        r'(?:secret|api_key|apikey|token)\s*=\s*["\'][^"\'$\{\s]',
        "error",
        "Hardcoded secret/key",
    ),
]


def _strip_uniform_indent(text: str) -> str:
    """Remove uniform leading whitespace from all lines (textwrap.dedent).

    LLMs frequently write file content inside indented triple-quoted strings,
    producing files where every line has 4+ spaces of unwanted indentation.
    """
    return textwrap.dedent(text)


def fix_indentation(project_dir: Path) -> int:
    """Strip uniform leading whitespace from all text files in a project.

    Returns the number of files fixed.
    """
    fixed = 0
    for fp in _iter_project_files(project_dir, _TEXT_EXTENSIONS):
        try:
            content = fp.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        dedented = _strip_uniform_indent(content)
        if dedented != content:
            fp.write_text(dedented, encoding="utf-8")
            fixed += 1
            logger.debug("Fixed indentation: {}", fp.relative_to(project_dir))

    return fixed


def validate_python_syntax(project_dir: Path) -> list[str]:
    """Check that all .py files are syntactically valid.

    Returns a list of error messages (empty = all OK).
    """
    errors: list[str] = []
    for fp in _iter_project_files(project_dir, frozenset({".py"})):
        try:
            content = fp.read_text(encoding="utf-8")
            ast.parse(content, filename=str(fp))
        except SyntaxError as e:
            rel = fp.relative_to(project_dir)
            errors.append(f"{rel}:{e.lineno}: {e.msg}")
    return errors


def check_spec_completeness(project_dir: Path, spec: dict) -> list[str]:
    """Verify that all files listed in the spec were actually created.

    Returns a list of missing file paths.
    """
    structure = spec.get("structure", {})
    if isinstance(structure, dict):
        raw_files = structure.get("files") or []
        expected_files = [f.get("path", "") if isinstance(f, dict) else str(f) for f in raw_files]
    elif isinstance(structure, list):
        expected_files = structure
    else:
        return []

    missing: list[str] = []
    for fpath in expected_files:
        fpath = fpath.strip().lstrip("/")
        # Strip leading ./ (common in specs)
        if fpath.startswith("./"):
            fpath = fpath[2:]
        # The spec path might include the project name prefix or not
        # Try both with and without project name
        full = project_dir / fpath
        if not full.exists():
            # Try without leading project-name directory
            parts = Path(fpath).parts
            if len(parts) > 1:
                alt = project_dir / Path(*parts[1:])
                if alt.exists():
                    continue
            missing.append(fpath)
    return missing


def run_ruff_check(project_dir: Path) -> tuple[int, str]:
    """Run ruff check on all Python files. Returns (exit_code, output)."""
    try:
        result = subprocess.run(
            ["ruff", "check", "--select", "E,F", str(project_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except FileNotFoundError:
        return -1, "ruff not found"
    except subprocess.TimeoutExpired:
        return -1, "ruff check timed out"


def check_llm_smells(project_dir: Path) -> list[dict]:
    """Scan generated source files for common LLM code-quality issues.

    Scans Python, JavaScript, and TypeScript files.

    Returns a list of dicts: {file, line, issue, code}.
    """
    issues: list[dict] = []
    for src_file in _iter_project_files(project_dir, _SOURCE_EXTENSIONS):
        try:
            content = src_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        for line_num, line in enumerate(content.splitlines(), 1):
            for pattern, severity, description in LLM_SMELLS:
                if re.search(pattern, line):
                    issues.append(
                        {
                            "file": str(src_file.relative_to(project_dir)),
                            "line": line_num,
                            "severity": severity,
                            "issue": description,
                            "code": line.strip()[:100],
                        }
                    )
    return issues


# ── Frontend / TypeScript validation ──────────────────────────


def validate_frontend(project_dir: Path) -> dict[str, object]:
    """Validate a Node.js / frontend project if package.json exists.

    Runs ``npm install`` (or ``pnpm install``) and ``npm run build``
    if a ``build`` script is defined.

    Returns:
        Dict with keys ``has_frontend``, ``install_ok``, ``build_ok``,
        ``install_output``, ``build_output``.
    """
    pkg_json = project_dir / "package.json"
    if not pkg_json.exists():
        # Check one level down (e.g. frontend/ subdirectory)
        for child in project_dir.iterdir():
            if child.is_dir() and (child / "package.json").exists():
                pkg_json = child / "package.json"
                break
        else:
            return {"has_frontend": False}

    frontend_root = pkg_json.parent
    result: dict[str, object] = {"has_frontend": True, "frontend_root": str(frontend_root)}

    # Determine package manager
    pm = "npm"
    if (frontend_root / "pnpm-lock.yaml").exists():
        pm = "pnpm"
    elif (frontend_root / "yarn.lock").exists():
        pm = "yarn"

    # Install
    try:
        install_proc = subprocess.run(
            [pm, "install", "--ignore-scripts"],
            cwd=str(frontend_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        result["install_ok"] = install_proc.returncode == 0
        result["install_output"] = (install_proc.stdout + install_proc.stderr).strip()[:2000]
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        result["install_ok"] = False
        result["install_output"] = f"{pm} not available or timed out: {exc}"
        return result

    # Build (if script exists)
    import json as _json

    try:
        pkg_data = _json.loads(pkg_json.read_text(encoding="utf-8"))
        scripts = pkg_data.get("scripts", {})
    except (ValueError, OSError):
        scripts = {}

    if "build" in scripts:
        try:
            build_proc = subprocess.run(
                [pm, "run", "build"],
                cwd=str(frontend_root),
                capture_output=True,
                text=True,
                timeout=180,
            )
            result["build_ok"] = build_proc.returncode == 0
            result["build_output"] = (build_proc.stdout + build_proc.stderr).strip()[:2000]
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            result["build_ok"] = False
            result["build_output"] = f"Build failed: {exc}"
    else:
        result["build_ok"] = None  # No build script defined
        result["build_output"] = "No build script in package.json"

    return result


def validate_typescript(project_dir: Path) -> dict[str, object]:
    """Run ``tsc --noEmit`` if a tsconfig.json exists.

    Returns:
        Dict with keys ``has_typescript``, ``tsc_ok``, ``tsc_output``.
    """
    tsconfig = project_dir / "tsconfig.json"
    if not tsconfig.exists():
        # Check one level down
        for child in project_dir.iterdir():
            if child.is_dir() and (child / "tsconfig.json").exists():
                tsconfig = child / "tsconfig.json"
                break
        else:
            return {"has_typescript": False}

    ts_root = tsconfig.parent

    try:
        proc = subprocess.run(
            ["npx", "tsc", "--noEmit"],
            cwd=str(ts_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "has_typescript": True,
            "tsc_ok": proc.returncode == 0,
            "tsc_output": (proc.stdout + proc.stderr).strip()[:2000],
        }
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {
            "has_typescript": True,
            "tsc_ok": False,
            "tsc_output": f"tsc check failed: {exc}",
        }


def validate_docker_build(project_dir: Path) -> dict[str, object]:
    """Verify that ``docker build`` succeeds if a Dockerfile exists.

    Returns:
        Dict with keys ``has_dockerfile``, ``build_ok``, ``build_output``.
    """
    dockerfile = project_dir / "Dockerfile"
    if not dockerfile.exists():
        return {"has_dockerfile": False}

    try:
        proc = subprocess.run(
            ["docker", "build", "--no-cache", "-q", "."],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=300,
        )
        return {
            "has_dockerfile": True,
            "build_ok": proc.returncode == 0,
            "build_output": (proc.stdout + proc.stderr).strip()[:2000],
        }
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {
            "has_dockerfile": True,
            "build_ok": False,
            "build_output": f"Docker build failed: {exc}",
        }


def validate_migrations(project_dir: Path) -> dict[str, object]:
    """Check that database migration files exist if an ORM is detected.

    Looks for SQLAlchemy/Alembic, Django, Prisma, or TypeORM patterns.

    Returns:
        Dict with keys ``has_orm``, ``orm_type``, ``has_migrations``,
        ``migration_dir``.
    """
    result: dict[str, object] = {"has_orm": False}

    # Alembic
    alembic_ini = project_dir / "alembic.ini"
    alembic_dir = project_dir / "alembic"
    if alembic_ini.exists() or alembic_dir.exists():
        result["has_orm"] = True
        result["orm_type"] = "sqlalchemy/alembic"
        versions_dir = alembic_dir / "versions" if alembic_dir.exists() else None
        result["has_migrations"] = bool(versions_dir and any(versions_dir.glob("*.py")))
        result["migration_dir"] = str(alembic_dir) if alembic_dir.exists() else None
        return result

    # Django
    manage_py = project_dir / "manage.py"
    if manage_py.exists():
        # Look for migrations/ dirs inside app directories
        migration_dirs = list(project_dir.rglob("migrations/__init__.py"))
        result["has_orm"] = True
        result["orm_type"] = "django"
        result["has_migrations"] = len(migration_dirs) > 0
        result["migration_dir"] = str(migration_dirs[0].parent) if migration_dirs else None
        return result

    # Prisma
    prisma_schema = project_dir / "prisma" / "schema.prisma"
    if prisma_schema.exists():
        migrations_dir = project_dir / "prisma" / "migrations"
        result["has_orm"] = True
        result["orm_type"] = "prisma"
        result["has_migrations"] = migrations_dir.exists() and any(migrations_dir.iterdir())
        result["migration_dir"] = str(migrations_dir)
        return result

    # Detect SQLAlchemy usage even without Alembic
    for py_file in _iter_project_files(project_dir, frozenset({".py"})):
        try:
            content = py_file.read_text(encoding="utf-8")
            if "from sqlalchemy" in content or "import sqlalchemy" in content:
                result["has_orm"] = True
                result["orm_type"] = "sqlalchemy (no alembic)"
                result["has_migrations"] = False
                result["migration_dir"] = None
                return result
        except (UnicodeDecodeError, OSError):
            continue

    return result


# ── Test execution ─────────────────────────────────────────────


def run_tests(project_dir: Path) -> dict[str, object]:
    """Run the project's test suite if a test framework is detected.

    Detects pytest (Python) or npm/pnpm/yarn test (Node) and executes
    the relevant command.  Returns structured results.

    Args:
        project_dir: Root of the generated project.

    Returns:
        Dict with keys ``has_tests``, ``test_ok``, ``test_output``,
        ``tests_passed``, ``tests_failed``, ``framework``.
    """
    result: dict[str, object] = {"has_tests": False}

    # ── Python tests (pytest) ──────────────────────────────────
    tests_dir = project_dir / "tests"
    has_py_tests = tests_dir.exists() and any(tests_dir.rglob("test_*.py"))
    if not has_py_tests:
        # Also check for tests at project root
        has_py_tests = any(project_dir.glob("test_*.py"))

    if has_py_tests:
        result["has_tests"] = True
        result["framework"] = "pytest"
        try:
            proc = subprocess.run(
                ["python", "-m", "pytest", "tests/", "-v", "--tb=short", "-q"],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=180,
            )
            output = (proc.stdout + proc.stderr).strip()
            result["test_ok"] = proc.returncode == 0
            result["test_output"] = output[-3000:] if len(output) > 3000 else output

            # Parse pass/fail counts from pytest output
            import re as _re

            match = _re.search(r"(\d+) passed", output)
            result["tests_passed"] = int(match.group(1)) if match else 0
            match = _re.search(r"(\d+) failed", output)
            result["tests_failed"] = int(match.group(1)) if match else 0
        except FileNotFoundError:
            result["test_ok"] = False
            result["test_output"] = "pytest not found"
        except subprocess.TimeoutExpired:
            result["test_ok"] = False
            result["test_output"] = "Test suite timed out after 180s"
        return result

    # ── Node tests (npm/pnpm/yarn test) ───────────────────────
    pkg_json = project_dir / "package.json"
    if not pkg_json.exists():
        for child in project_dir.iterdir():
            if child.is_dir() and (child / "package.json").exists():
                pkg_json = child / "package.json"
                break

    if pkg_json.exists():
        import json as _json

        try:
            pkg_data = _json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = pkg_data.get("scripts", {})
        except (ValueError, OSError):
            scripts = {}

        if "test" in scripts:
            frontend_root = pkg_json.parent
            pm = "npm"
            if (frontend_root / "pnpm-lock.yaml").exists():
                pm = "pnpm"
            elif (frontend_root / "yarn.lock").exists():
                pm = "yarn"

            result["has_tests"] = True
            result["framework"] = f"{pm} test"
            try:
                proc = subprocess.run(
                    [pm, "test", "--", "--passWithNoTests"],
                    cwd=str(frontend_root),
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                output = (proc.stdout + proc.stderr).strip()
                result["test_ok"] = proc.returncode == 0
                result["test_output"] = output[-3000:] if len(output) > 3000 else output
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                result["test_ok"] = False
                result["test_output"] = f"Test run failed: {exc}"

    return result


# ── Import resolution validation ───────────────────────────────


def validate_imports(project_dir: Path) -> dict[str, object]:
    """Verify that Python imports in generated files actually resolve.

    For each ``.py`` file, parses the AST to extract top-level imports,
    then checks whether the module is importable in the project context.

    Only checks first-party (project-internal) imports and well-known
    stdlib/third-party — skips the actual import to avoid side-effects.

    Args:
        project_dir: Root of the generated project.

    Returns:
        Dict with ``broken_imports`` (list of {file, module, error})
        and ``checked_count``.
    """
    broken: list[dict[str, str]] = []
    checked = 0

    # Build set of project-internal module names
    project_modules: set[str] = set()
    for py_file in _iter_project_files(project_dir, frozenset({".py"})):
        rel = py_file.relative_to(project_dir)
        # Convert path to dotted module name
        parts = list(rel.with_suffix("").parts)
        for i in range(len(parts)):
            project_modules.add(".".join(parts[: i + 1]))

    for py_file in _iter_project_files(project_dir, frozenset({".py"})):
        try:
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue

        rel = str(py_file.relative_to(project_dir))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    checked += 1
                    root_mod = alias.name.split(".")[0]
                    # Check if it's a project-internal import that doesn't exist
                    if root_mod in project_modules or alias.name in project_modules:
                        # It's internal — verify the file exists
                        mod_path = project_dir / Path(*alias.name.split("."))
                        pkg_init = mod_path / "__init__.py"
                        mod_file = mod_path.with_suffix(".py")
                        if not mod_file.exists() and not pkg_init.exists():
                            broken.append(
                                {
                                    "file": rel,
                                    "module": alias.name,
                                    "error": "Module file not found in project",
                                }
                            )
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                checked += 1
                root_mod = node.module.split(".")[0]
                if root_mod in project_modules or node.module in project_modules:
                    mod_path = project_dir / Path(*node.module.split("."))
                    pkg_init = mod_path / "__init__.py"
                    mod_file = mod_path.with_suffix(".py")
                    if not mod_file.exists() and not pkg_init.exists():
                        broken.append(
                            {
                                "file": rel,
                                "module": node.module,
                                "error": "Module file not found in project",
                            }
                        )

    return {
        "broken_imports": broken,
        "checked_count": checked,
    }


# ── Spec endpoint verification ─────────────────────────────────


def validate_spec_endpoints(project_dir: Path, spec: dict | None) -> dict[str, object]:
    """Check that endpoints listed in the spec exist as actual routes in the code.

    Scans Python and TypeScript files for route decorator patterns
    (``@router.get``, ``@app.post``, ``app.get(``, etc.) and compares
    against endpoints declared in the spec features.

    Args:
        project_dir: Root of the generated project.
        spec: The project spec dict.

    Returns:
        Dict with ``declared_endpoints``, ``found_routes``,
        ``missing_endpoints``, ``coverage_pct``.
    """
    if not spec:
        return {
            "declared_endpoints": [],
            "found_routes": [],
            "missing_endpoints": [],
            "coverage_pct": 100,
        }

    # Extract declared endpoints from spec features
    declared: list[str] = []
    for feat in spec.get("features", []):
        if isinstance(feat, dict):
            endpoints = (feat.get("details") or {}).get("endpoints", [])
            for ep in endpoints:
                # Parse "GET /api/v1/items - List all items" → "/api/v1/items"
                parts = str(ep).split()
                if len(parts) >= 2 and parts[0].upper() in {
                    "GET",
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                    "HEAD",
                    "OPTIONS",
                }:
                    declared.append(parts[1].rstrip("/"))

    if not declared:
        return {
            "declared_endpoints": [],
            "found_routes": [],
            "missing_endpoints": [],
            "coverage_pct": 100,
        }

    # Scan code for route definitions
    found_routes: set[str] = set()
    route_pattern = re.compile(
        r"""(?:@(?:router|app|api)\.(get|post|put|patch|delete|head|options)\s*\(\s*["']([^"']+)["']"""
        r"""|(?:router|app|api)\.(get|post|put|patch|delete)\s*\(\s*["']([^"']+)["'])""",
        re.IGNORECASE,
    )

    for src_file in _iter_project_files(project_dir, frozenset({".py", ".ts", ".js"})):
        try:
            content = src_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in route_pattern.finditer(content):
            path = match.group(2) or match.group(4) or ""
            if path:
                found_routes.add(path.rstrip("/"))

    # Check coverage — normalize paths for comparison
    missing: list[str] = []
    for ep in declared:
        # Check if any found route is a suffix match (handles router prefixes)
        ep_normalized = ep.rstrip("/")
        matched = any(
            ep_normalized == r or ep_normalized.endswith(r) or r.endswith(ep_normalized)
            for r in found_routes
        )
        if not matched:
            missing.append(ep)

    coverage = ((len(declared) - len(missing)) / len(declared) * 100) if declared else 100

    return {
        "declared_endpoints": declared,
        "found_routes": sorted(found_routes),
        "missing_endpoints": missing,
        "coverage_pct": round(coverage, 1),
    }


def post_build_validate(project_dir: Path, spec: dict) -> dict:
    """Run all post-build checks and fixes.

    Returns a summary dict with results.
    """
    summary: dict = {"project_dir": str(project_dir)}

    # 1. Fix indentation
    fixed_count = fix_indentation(project_dir)
    summary["indentation_fixes"] = fixed_count
    if fixed_count:
        logger.info("Fixed indentation in {} files", fixed_count)

    # 2. Check Python syntax
    syntax_errors = validate_python_syntax(project_dir)
    summary["syntax_errors"] = syntax_errors
    if syntax_errors:
        for err in syntax_errors:
            logger.warning("Syntax error: {}", err)
    else:
        logger.info("All Python files are syntactically valid")

    # 3. Check spec completeness
    missing = check_spec_completeness(project_dir, spec)
    summary["missing_files"] = missing
    if missing:
        for m in missing:
            logger.warning("Missing from spec: {}", m)
    else:
        logger.info("All spec files present")

    # 4. Ruff lint check (informational)
    ruff_code, ruff_output = run_ruff_check(project_dir)
    summary["ruff_exit_code"] = ruff_code
    summary["ruff_output"] = ruff_output
    if ruff_code == 0:
        logger.info("Ruff check passed")
    elif ruff_code > 0:
        logger.warning("Ruff found issues:\n{}", ruff_output)

    # 5. LLM smell detection
    smells = check_llm_smells(project_dir)
    summary["llm_smells"] = smells
    if smells:
        logger.warning("{} LLM code smells detected:", len(smells))
        for s in smells[:20]:  # cap log output
            logger.warning("  {}:{} — {} | {}", s["file"], s["line"], s["issue"], s["code"])
        if len(smells) > 20:
            logger.warning("  ... and {} more", len(smells) - 20)
    else:
        logger.info("No LLM code smells detected")

    # 6. File count
    all_files = sorted(f for f in project_dir.rglob("*") if f.is_file())
    summary["file_count"] = len(all_files)
    summary["total_bytes"] = sum(f.stat().st_size for f in all_files)

    return summary


# ── Required files check ──────────────────────────────────────

REQUIRED_FILES = [
    "README.md",
]

RECOMMENDED_FILES = [
    ".gitignore",
    ".env.example",
    "Dockerfile",
    "pyproject.toml",
]


def check_required_files(project_dir: Path) -> tuple[list[str], list[str]]:
    """Check for required and recommended files.

    Returns
    -------
    tuple of (missing_required, missing_recommended)
    """
    missing_required = [f for f in REQUIRED_FILES if not (project_dir / f).exists()]
    missing_recommended = [f for f in RECOMMENDED_FILES if not (project_dir / f).exists()]
    return missing_required, missing_recommended


# ── Rich validation report ─────────────────────────────────────


def validate_project(project_dir: Path, spec: dict | None = None) -> dict:
    """Run all post-build checks and return a structured report.

    This is the recommended validation entry point for the generate
    pipeline. It returns a richer report than ``post_build_validate``.

    Parameters
    ----------
    project_dir : Path
        Root of the generated project.
    spec : dict | None
        The original spec — used to check file completeness.

    Returns
    -------
    dict with keys: passed, files_total, lines_total, syntax_errors,
        lint_passed, lint_output, smells, missing_spec_files,
        missing_required, missing_recommended, summary.
    """
    if not project_dir.exists():
        # Populate missing_spec_files from spec so the gate knows ALL files are missing
        all_spec_files: list[str] = []
        if spec:
            structure = spec.get("structure", {})
            if isinstance(structure, dict):
                raw = structure.get("files") or []
                all_spec_files = [
                    f.get("path", "") if isinstance(f, dict) else str(f)
                    for f in raw
                ]
            elif isinstance(structure, list):
                all_spec_files = list(structure)

        return {
            "passed": False,
            "files_total": 0,
            "lines_total": 0,
            "syntax_errors": [],
            "lint_passed": False,
            "lint_output": "Project directory does not exist",
            "smells": [],
            "missing_spec_files": all_spec_files,
            "missing_required": list(REQUIRED_FILES),
            "missing_recommended": [],
            "summary": f"❌ Project directory not found: {project_dir}",
        }

    # Count files and lines (exclude cache dirs but keep dotfiles like .gitignore)
    all_files = _iter_project_files(project_dir)
    total_lines = 0
    for f in all_files:
        with contextlib.suppress(UnicodeDecodeError, PermissionError):
            total_lines += len(f.read_text(encoding="utf-8").splitlines())

    # Run checks
    syntax_errors_raw = validate_python_syntax(project_dir)
    syntax_errors = []
    for e in syntax_errors_raw:
        # Convert string errors into dicts for consistency
        if isinstance(e, str):
            parts = e.split(":", 2)
            syntax_errors.append(
                {
                    "file": parts[0] if len(parts) > 0 else "?",
                    "line": int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else 0,
                    "issue": parts[2].strip() if len(parts) > 2 else e,
                }
            )
        else:
            syntax_errors.append(e)

    lint_code, lint_output = run_ruff_check(project_dir)
    lint_passed = lint_code == 0
    smells = check_llm_smells(project_dir)
    missing_spec = check_spec_completeness(project_dir, spec) if spec else []
    missing_req, missing_rec = check_required_files(project_dir)

    # Determine pass/fail
    error_smells = [s for s in smells if s.get("severity") == "error"]
    passed = len(syntax_errors) == 0 and len(error_smells) == 0 and len(missing_req) == 0

    # Build summary
    lines: list[str] = []
    lines.append(f"{'✅' if passed else '❌'} Post-Build Validation")
    lines.append(f"   📁 {len(all_files)} files | {total_lines:,} lines")

    if syntax_errors:
        lines.append(f"   ❌ {len(syntax_errors)} syntax errors")
        for err in syntax_errors[:5]:
            lines.append(
                f"      {err.get('file', '?')}:{err.get('line', '?')} — {err.get('issue', '?')}"
            )
    else:
        lines.append("   ✅ Syntax: all files valid")

    if lint_passed:
        lines.append("   ✅ Ruff: clean")
    else:
        error_count = lint_output.count("\n") + 1 if lint_output else 0
        lines.append(f"   ⚠️  Ruff: {error_count} issues")

    if error_smells:
        lines.append(f"   ❌ {len(error_smells)} code smells (errors)")
        for s in error_smells[:5]:
            lines.append(f"      {s['file']}:{s['line']} — {s['issue']}")
    warn_smells = [s for s in smells if s.get("severity") == "warn"]
    if warn_smells:
        lines.append(f"   ⚠️  {len(warn_smells)} warnings")

    if missing_spec:
        lines.append(f"   ❌ {len(missing_spec)} spec files not created")
        for m in missing_spec[:5]:
            lines.append(f"      - {m}")

    if missing_req:
        lines.append(f"   ❌ Missing required: {', '.join(missing_req)}")
    if missing_rec:
        lines.append(f"   ⚠️  Missing recommended: {', '.join(missing_rec)}")

    # ── Full-stack validation (non-blocking) ───────────────────
    frontend_report = validate_frontend(project_dir)
    ts_report = validate_typescript(project_dir)
    docker_report = validate_docker_build(project_dir)
    migration_report = validate_migrations(project_dir)
    test_report = run_tests(project_dir)
    import_report = validate_imports(project_dir)
    endpoint_report = validate_spec_endpoints(project_dir, spec)

    if frontend_report.get("has_frontend"):
        if frontend_report.get("install_ok") is False:
            lines.append("   ❌ Frontend: npm install failed")
        elif frontend_report.get("build_ok") is False:
            lines.append("   ❌ Frontend: build failed")
        elif frontend_report.get("build_ok") is True:
            lines.append("   ✅ Frontend: build passed")
        else:
            lines.append("   ✅ Frontend: install OK (no build script)")

    if ts_report.get("has_typescript"):
        if ts_report.get("tsc_ok"):
            lines.append("   ✅ TypeScript: no type errors")
        else:
            lines.append("   ⚠️  TypeScript: type errors found")

    if docker_report.get("has_dockerfile"):
        if docker_report.get("build_ok"):
            lines.append("   ✅ Docker: build passed")
        else:
            lines.append("   ⚠️  Docker: build failed")

    if migration_report.get("has_orm"):
        orm_type = migration_report.get("orm_type", "unknown")
        if migration_report.get("has_migrations"):
            lines.append(f"   ✅ Migrations: {orm_type} — migrations present")
        else:
            lines.append(f"   ⚠️  Migrations: {orm_type} — no migration files found")

    # ── Test execution ─────────────────────────────────────────
    if test_report.get("has_tests"):
        tp = test_report.get("tests_passed", 0)
        tf = test_report.get("tests_failed", 0)
        if test_report.get("test_ok"):
            lines.append(f"   ✅ Tests: {tp} passed")
        else:
            lines.append(f"   ❌ Tests: {tf} failed, {tp} passed")

    # ── Import validation ──────────────────────────────────────
    broken_imports = import_report.get("broken_imports", [])
    if broken_imports:
        lines.append(f"   ❌ Imports: {len(broken_imports)} broken imports")
        for bi in broken_imports[:3]:
            lines.append(f"      {bi['file']}: cannot import '{bi['module']}'")
    elif import_report.get("checked_count", 0) > 0:
        lines.append(f"   ✅ Imports: {import_report['checked_count']} checked, all resolve")

    # ── Endpoint coverage ──────────────────────────────────────
    missing_eps = endpoint_report.get("missing_endpoints", [])
    if missing_eps:
        lines.append(f"   ⚠️  Endpoints: {len(missing_eps)} spec endpoints missing from code")
        for ep in missing_eps[:3]:
            lines.append(f"      - {ep}")
    elif endpoint_report.get("declared_endpoints"):
        lines.append(
            f"   ✅ Endpoints: {len(endpoint_report['declared_endpoints'])} spec endpoints verified"
        )

    summary = "\n".join(lines)
    logger.info("\n{}", summary)

    return {
        "passed": passed,
        "files_total": len(all_files),
        "lines_total": total_lines,
        "syntax_errors": syntax_errors,
        "lint_passed": lint_passed,
        "lint_output": lint_output,
        "smells": smells,
        "missing_spec_files": missing_spec,
        "missing_required": missing_req,
        "missing_recommended": missing_rec,
        "frontend": frontend_report,
        "typescript": ts_report,
        "docker": docker_report,
        "migrations": migration_report,
        "tests": test_report,
        "imports": import_report,
        "endpoints": endpoint_report,
        "summary": summary,
    }
