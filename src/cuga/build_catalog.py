"""
Build Catalog — Self-improving optimization system for AI Repo Builder.

Records build outcomes, mines recurring patterns, and generates
prompt snippets so the agent avoids repeating past mistakes.

Architecture
------------
- ``catalog/build_history.jsonl`` — append-only log of every build
- ``catalog/optimizations.yaml`` — curated lessons (auto + human)
- This module ties them together.

Usage in the pipeline
---------------------
**After build** (in generate.py / main.py)::

    from cuga.build_catalog import record_build
    record_build(spec, validation_report, elapsed)

**Before build** (in spec_to_prompt.py)::

    from cuga.build_catalog import get_lessons_for_prompt
    lessons = get_lessons_for_prompt(spec)
    # → inject into agent prompt
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml
from loguru import logger

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]  # Windows

__all__ = [
    "get_build_stats",
    "get_lessons_for_prompt",
    "load_history",
    "load_optimizations",
    "mine_lessons",
    "prune_stale_lessons",
    "record_build",
]
# ── Paths ──────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CATALOG_DIR = _PROJECT_ROOT / "catalog"
HISTORY_FILE = CATALOG_DIR / "build_history.jsonl"
OPTIMIZATIONS_FILE = CATALOG_DIR / "optimizations.yaml"


# ── Record a build ────────────────────────────────────────────


def record_build(
    spec: dict,
    validation: dict,
    elapsed_seconds: float,
    catalog_dir: Path | None = None,
) -> Path:
    """Append a structured build record to the history log.

    Parameters
    ----------
    spec : dict
        The project spec that was built.
    validation : dict
        The validation report from ``post_build.validate_project``
        or ``post_build.post_build_validate``.
    elapsed_seconds : float
        Wall-clock build time.
    catalog_dir : Path | None
        Override catalog directory (for testing).

    Returns
    -------
    Path
        The history file that was written to.
    """
    cat_dir = catalog_dir or CATALOG_DIR
    cat_dir.mkdir(parents=True, exist_ok=True)
    history_file = cat_dir / "build_history.jsonl"

    if elapsed_seconds < 0:
        logger.warning("Negative elapsed_seconds ({}) clamped to 0", elapsed_seconds)
        elapsed_seconds = 0.0

    # Extract stack signature
    stack = spec.get("stack") or {}
    language = stack.get("language", "unknown")
    backend_fw = (stack.get("backend") or {}).get("framework", "unknown")
    frontend_fw = (stack.get("frontend") or {}).get("framework", "none")
    database = (stack.get("database") or {}).get("primary", "none")

    # Categorize smells by pattern
    smell_counts: dict[str, int] = {}
    for smell in validation.get("smells") or validation.get("llm_smells") or []:
        issue = smell.get("issue", "")
        issue_lower = issue.lower()
        # Map to canonical pattern names
        # NOTE: Check hardcoded BEFORE stub — "Hardcoded password" contains "pass"
        if "hardcoded" in issue_lower or "secret" in issue_lower:
            smell_counts["hardcoded_secret"] = smell_counts.get("hardcoded_secret", 0) + 1
        elif "stub" in issue_lower or "pass statement" in issue_lower or "NotImplemented" in issue:
            smell_counts["stub_function"] = smell_counts.get("stub_function", 0) + 1
        elif "bare except" in issue_lower:
            smell_counts["bare_except"] = smell_counts.get("bare_except", 0) + 1
        elif "wildcard" in issue_lower:
            smell_counts["wildcard_import"] = smell_counts.get("wildcard_import", 0) + 1
        elif "TODO" in issue or "FIXME" in issue:
            smell_counts["todo_comment"] = smell_counts.get("todo_comment", 0) + 1
        elif "placeholder" in issue_lower or "implement" in issue_lower:
            smell_counts["placeholder"] = smell_counts.get("placeholder", 0) + 1

    record = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "project_name": spec.get("name", "unknown"),
        "stack": f"{language}/{backend_fw}",
        "frontend": frontend_fw,
        "database": database,
        "elapsed_seconds": round(elapsed_seconds, 1),
        "passed": validation.get("passed", False),
        "files_total": validation.get("files_total", validation.get("file_count", 0)),
        "lines_total": validation.get("lines_total", 0),
        "syntax_errors": len(validation.get("syntax_errors", [])),
        "lint_passed": validation.get("lint_passed", validation.get("ruff_exit_code", -1) == 0),
        "smell_counts": smell_counts,
        "total_smells": sum(smell_counts.values()),
        "missing_spec_files": len(
            validation.get("missing_spec_files", validation.get("missing_files", []))
        ),
        "missing_required": validation.get("missing_required", []),
    }

    with open(history_file, "a", encoding="utf-8") as f:
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(record, default=str) + "\n")
        finally:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    logger.info(
        "Build recorded: {} ({}) — {} in {:.1f}s",
        record["project_name"],
        record["stack"],
        "PASS" if record["passed"] else "FAIL",
        record["elapsed_seconds"],
    )

    # Auto-update pattern counters in optimizations.yaml
    _update_pattern_counts(smell_counts, catalog_dir=cat_dir)

    return history_file


# ── Read build history ─────────────────────────────────────────


def load_history(catalog_dir: Path | None = None) -> list[dict]:
    """Load all build records from the history file.

    Returns
    -------
    list[dict]
        Build records in chronological order.
    """
    history_file = (catalog_dir or CATALOG_DIR) / "build_history.jsonl"
    if not history_file.exists():
        return []

    records: list[dict] = []
    for line in history_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


# ── Load optimizations ─────────────────────────────────────────


def load_optimizations(catalog_dir: Path | None = None) -> dict:
    """Load the optimizations catalog.

    Returns
    -------
    dict
        The full optimizations YAML as a dict.
    """
    opt_file = (catalog_dir or CATALOG_DIR) / "optimizations.yaml"
    if not opt_file.exists():
        return {"global": [], "by_stack": {}, "by_pattern": {}}

    try:
        data = yaml.safe_load(opt_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"global": [], "by_stack": {}, "by_pattern": {}}
    except yaml.YAMLError:
        logger.warning("Failed to parse optimizations.yaml — using defaults")
        return {"global": [], "by_stack": {}, "by_pattern": {}}


# ── Get lessons for a specific build ───────────────────────────


def get_lessons_for_prompt(
    spec: dict,
    max_lessons: int = 15,
    catalog_dir: Path | None = None,
) -> str:
    """Build a text block of relevant lessons to inject into the agent prompt.

    Selects lessons based on:
    1. Global lessons (always included)
    2. Stack-specific lessons (matched by language/framework)
    3. Pattern-based lessons (weighted by how often they recur in history)

    Parameters
    ----------
    spec : dict
        The project spec (used to match stack).
    max_lessons : int
        Cap on total lessons returned (to avoid prompt bloat).
    catalog_dir : Path | None
        Override catalog directory (for testing).

    Returns
    -------
    str
        Formatted text block ready for prompt injection.
        Empty string if no lessons found.
    """
    opts = load_optimizations(catalog_dir)
    if not opts.get("global") and not opts.get("by_stack") and not opts.get("by_pattern"):
        return ""

    lessons: list[tuple[str, str, str]] = []  # (severity, lesson, context)

    # 1. Global lessons
    for item in opts.get("global", []):
        lessons.append(
            (
                item.get("severity", "tip"),
                item.get("lesson", ""),
                item.get("context", ""),
            )
        )

    # 2. Stack-specific lessons
    stack = spec.get("stack") or {}
    language = stack.get("language", "").lower()
    backend_fw = (stack.get("backend") or {}).get("framework", "").lower()
    frontend_fw = (stack.get("frontend") or {}).get("framework", "").lower()

    # Try exact match first, then language-only
    stack_keys_to_try = []
    if language and backend_fw:
        stack_keys_to_try.append(f"{language}/{backend_fw}")
    if language and frontend_fw and frontend_fw != "none":
        stack_keys_to_try.append(f"{language}/{frontend_fw}")
    if language:
        stack_keys_to_try.append(language)

    seen_ids: set[str] = set()
    for key in stack_keys_to_try:
        for item in opts.get("by_stack", {}).get(key, []):
            item_id = item.get("id", "")
            if item_id and item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            lessons.append(
                (
                    item.get("severity", "tip"),
                    item.get("lesson", ""),
                    item.get("context", ""),
                )
            )

    # 3. Pattern lessons (only if they've recurred >= 2 times)
    for _pattern_name, pattern_data in opts.get("by_pattern", {}).items():
        if isinstance(pattern_data, dict):
            auto_count = pattern_data.get("auto_count", 0)
            if auto_count >= 2 or pattern_data.get("severity") == "critical":
                lessons.append(
                    (
                        pattern_data.get("severity", "important"),
                        pattern_data.get("lesson", ""),
                        f"Seen {auto_count} times in past builds.",
                    )
                )

    if not lessons:
        return ""

    # Sort: critical > important > tip, then truncate
    severity_order = {"critical": 0, "important": 1, "tip": 2}
    lessons.sort(key=lambda x: severity_order.get(x[0], 3))
    lessons = lessons[:max_lessons]

    # Format
    lines = [
        "## Lessons from Past Builds",
        "These are hard-won lessons from previous builds. Follow them carefully.\n",
    ]
    for severity, lesson, context in lessons:
        icon = {"critical": "🔴", "important": "🟡", "tip": "💡"}.get(severity, "💡")
        lines.append(f"{icon} **{lesson}**")
        if context:
            lines.append(f"   _{context}_")

    return "\n".join(lines)


# ── Build statistics ───────────────────────────────────────────


def get_build_stats(catalog_dir: Path | None = None) -> dict:
    """Compute aggregate statistics from build history.

    Returns
    -------
    dict with keys: total_builds, pass_rate, avg_time, top_stacks,
        most_common_smells, trend (improving/declining/stable).
    """
    records = load_history(catalog_dir)
    if not records:
        return {
            "total_builds": 0,
            "pass_rate": 0.0,
            "avg_time": 0.0,
            "top_stacks": [],
            "most_common_smells": [],
            "trend": "no_data",
        }

    total = len(records)
    passed = sum(1 for r in records if r.get("passed"))
    avg_time = sum(r.get("elapsed_seconds", 0) for r in records) / total

    # Stack frequency
    stack_counts: dict[str, int] = {}
    for r in records:
        s = r.get("stack", "unknown")
        stack_counts[s] = stack_counts.get(s, 0) + 1
    top_stacks = sorted(stack_counts.items(), key=lambda x: -x[1])[:5]

    # Smell frequency
    smell_totals: dict[str, int] = {}
    for r in records:
        for pattern, count in r.get("smell_counts", {}).items():
            smell_totals[pattern] = smell_totals.get(pattern, 0) + count
    top_smells = sorted(smell_totals.items(), key=lambda x: -x[1])[:5]

    # Trend: compare last 5 builds vs previous 5
    trend = "stable"
    if total >= 10:
        recent = records[-5:]
        older = records[-10:-5]
        recent_rate = sum(1 for r in recent if r.get("passed")) / 5
        older_rate = sum(1 for r in older if r.get("passed")) / 5
        if recent_rate > older_rate + 0.1:
            trend = "improving"
        elif recent_rate < older_rate - 0.1:
            trend = "declining"

    return {
        "total_builds": total,
        "pass_rate": round(passed / total * 100, 1),
        "avg_time": round(avg_time, 1),
        "top_stacks": top_stacks,
        "most_common_smells": top_smells,
        "trend": trend,
    }


# ── Internal: update pattern counts in optimizations.yaml ──────


def _update_pattern_counts(
    smell_counts: dict[str, int],
    catalog_dir: Path | None = None,
) -> None:
    """Increment auto_count for patterns seen in optimizations.yaml.

    Args:
        smell_counts: Mapping of pattern names to occurrence counts.
        catalog_dir: Override catalog directory (for testing).
    """
    if not smell_counts:
        return

    opt_file = (catalog_dir or CATALOG_DIR) / "optimizations.yaml"
    if not opt_file.exists():
        return

    try:
        data = yaml.safe_load(opt_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
    except yaml.YAMLError:
        return

    patterns = data.get("by_pattern", {})
    changed = False

    for pattern_name, count in smell_counts.items():
        if pattern_name in patterns and isinstance(patterns[pattern_name], dict):
            patterns[pattern_name]["auto_count"] = (
                patterns[pattern_name].get("auto_count", 0) + count
            )
            changed = True

    if changed:
        # Atomic save: write to temp, then rename
        tmp_file = opt_file.with_suffix(".yaml.tmp")
        tmp_file.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False, width=120),
            encoding="utf-8",
        )
        tmp_file.replace(opt_file)
        logger.debug("Updated pattern counts in optimizations.yaml")


# ── Auto-mining lessons from build history ─────────────────────

# Canonical smell patterns we know how to mine.  Each maps to a
# lesson template that's added to ``by_pattern`` when the pattern
# reaches the frequency threshold.
_MINEABLE_PATTERNS: dict[str, dict[str, str]] = {
    "bare_except": {
        "lesson": "Replace bare except: with except Exception: or a specific exception class.",
        "severity": "important",
    },
    "hardcoded_secret": {
        "lesson": "Move all secrets to .env and load via os.environ or pydantic-settings BaseSettings.",
        "severity": "critical",
    },
    "stub_function": {
        "lesson": "Never write pass or raise NotImplementedError. Implement the full function body.",
        "severity": "critical",
    },
    "wildcard_import": {
        "lesson": "Replace 'from X import *' with explicit imports.",
        "severity": "important",
    },
    "todo_comment": {
        "lesson": "Do not leave TODO/FIXME comments — implement the feature fully.",
        "severity": "important",
    },
    "placeholder": {
        "lesson": "Do not write placeholder or 'Add X here' comments. Write the actual code.",
        "severity": "critical",
    },
    "missing_import": {
        "lesson": "After writing a file, verify all imports exist. Run ruff check immediately.",
        "severity": "important",
    },
}


def mine_lessons(
    min_occurrences: int = 3,
    catalog_dir: Path | None = None,
) -> list[dict[str, str]]:
    """Scan build history and auto-generate new ``by_pattern`` lessons.

    Only adds lessons for patterns seen at least *min_occurrences* times
    across ALL builds (not per-build).  Existing lessons are not duplicated.

    Args:
        min_occurrences: Minimum total smell count to promote to a lesson.
        catalog_dir: Override catalog directory (for testing).

    Returns:
        List of newly added pattern dicts (empty if nothing new).
    """
    cat_dir = catalog_dir or CATALOG_DIR
    records = load_history(cat_dir)
    if not records:
        return []

    # Aggregate smell counts across all builds
    totals: dict[str, int] = {}
    for rec in records:
        for pattern, count in rec.get("smell_counts", {}).items():
            totals[pattern] = totals.get(pattern, 0) + count

    # Load existing optimizations
    opts = load_optimizations(cat_dir)
    existing_patterns = set(opts.get("by_pattern", {}).keys())

    new_lessons: list[dict[str, str]] = []

    for pattern_name, total_count in totals.items():
        if total_count < min_occurrences:
            continue
        if pattern_name in existing_patterns:
            continue  # Already has a lesson
        if pattern_name not in _MINEABLE_PATTERNS:
            continue  # Unknown pattern — can't auto-generate a lesson

        template = _MINEABLE_PATTERNS[pattern_name]
        new_entry = {
            "lesson": template["lesson"],
            "severity": template["severity"],
            "auto_count": total_count,
            "source": "auto",
        }

        # Write to optimizations.yaml
        if "by_pattern" not in opts:
            opts["by_pattern"] = {}
        opts["by_pattern"][pattern_name] = new_entry
        new_lessons.append({"pattern": pattern_name, **new_entry})

    if new_lessons:
        opt_file = cat_dir / "optimizations.yaml"
        tmp_file = opt_file.with_suffix(".yaml.tmp")
        tmp_file.write_text(
            yaml.dump(opts, default_flow_style=False, sort_keys=False, width=120),
            encoding="utf-8",
        )
        tmp_file.replace(opt_file)
        logger.info(
            "Mined {} new lessons from build history: {}",
            len(new_lessons),
            ", ".join(lesson["pattern"] for lesson in new_lessons),
        )

    return new_lessons


def prune_stale_lessons(
    max_age_days: int = 90,
    min_builds: int = 10,
    catalog_dir: Path | None = None,
) -> list[str]:
    """Remove auto-generated lessons whose patterns haven't recurred recently.

    Only removes ``source: auto`` lessons. Human lessons are never pruned.
    A lesson is "stale" if it was auto-generated, the pattern hasn't
    appeared in the last *max_age_days* worth of builds, and there have
    been at least *min_builds* since the pattern was last seen.

    Args:
        max_age_days: Days of inactivity before a lesson is pruned.
        min_builds: Minimum total builds before pruning is considered.
        catalog_dir: Override catalog directory (for testing).

    Returns:
        List of pruned pattern names.
    """
    from datetime import UTC, datetime, timedelta

    cat_dir = catalog_dir or CATALOG_DIR
    records = load_history(cat_dir)
    if len(records) < min_builds:
        return []

    opts = load_optimizations(cat_dir)
    patterns = opts.get("by_pattern", {})
    if not patterns:
        return []

    cutoff = datetime.now(tz=UTC) - timedelta(days=max_age_days)
    pruned: list[str] = []

    # Find the last occurrence of each pattern
    last_seen: dict[str, str] = {}
    for rec in records:
        ts = rec.get("timestamp", "")
        for pattern_name in rec.get("smell_counts", {}):
            if not last_seen.get(pattern_name) or ts > last_seen[pattern_name]:
                last_seen[pattern_name] = ts

    for pattern_name, pattern_data in list(patterns.items()):
        if not isinstance(pattern_data, dict):
            continue
        if pattern_data.get("source") != "auto":
            continue  # Never prune human lessons

        ts_str = last_seen.get(pattern_name, "")
        if not ts_str:
            # Pattern exists in YAML but was never seen in history — prune
            del patterns[pattern_name]
            pruned.append(pattern_name)
            continue

        try:
            last_ts = datetime.fromisoformat(ts_str)
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=UTC)
        except ValueError:
            continue

        if last_ts < cutoff:
            del patterns[pattern_name]
            pruned.append(pattern_name)

    if pruned:
        opt_file = cat_dir / "optimizations.yaml"
        tmp_file = opt_file.with_suffix(".yaml.tmp")
        tmp_file.write_text(
            yaml.dump(opts, default_flow_style=False, sort_keys=False, width=120),
            encoding="utf-8",
        )
        tmp_file.replace(opt_file)
        logger.info("Pruned {} stale lessons: {}", len(pruned), ", ".join(pruned))

    return pruned
