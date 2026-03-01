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
    "validate_project",
    "validate_python_syntax",
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
        expected_files = [
            f.get("path", "") if isinstance(f, dict) else str(f) for f in raw_files
        ]
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
            logger.warning(
                "  {}:{} — {} | {}", s["file"], s["line"], s["issue"], s["code"]
            )
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
    ".gitignore",
    "README.md",
]

RECOMMENDED_FILES = [
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
    missing_recommended = [
        f for f in RECOMMENDED_FILES if not (project_dir / f).exists()
    ]
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
        return {
            "passed": False,
            "files_total": 0,
            "lines_total": 0,
            "syntax_errors": [],
            "lint_passed": False,
            "lint_output": "Project directory does not exist",
            "smells": [],
            "missing_spec_files": [],
            "missing_required": [],
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
                    "line": int(parts[1])
                    if len(parts) > 1 and parts[1].strip().isdigit()
                    else 0,
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
    passed = (
        len(syntax_errors) == 0 and len(error_smells) == 0 and len(missing_req) == 0
    )

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
        "summary": summary,
    }
