"""
Spec Validator — deep-validates a generated YAML spec against the
required schema so we can catch problems *before* the CUGA agent
tries to build the project.

Used by the generate.py pipeline:
    1. LLM produces YAML text
    2. validate_spec_yaml() checks it
    3. If invalid, errors are fed back to the LLM for self-correction
    4. After ≤3 retries we either have a good spec or bail out
"""

from __future__ import annotations

from typing import Any

import yaml

from cuga.spec_generator import validate_spec  # lightweight check

__all__ = ["validate_spec_yaml"]

# ------------------------------------------------------------------
# Required schema definition
# ------------------------------------------------------------------
REQUIRED_SPEC_SCHEMA: dict[str, Any] = {
    "name": {"type": "string", "required": True},
    "description": {"type": "string", "required": True},
    "stack": {
        "type": "dict",
        "required": True,
        "children": {
            "language": {"type": "string", "required": True},
            "backend": {
                "type": "dict",
                "required": True,
                "children": {
                    "framework": {"type": "string", "required": True},
                    "api_style": {"type": "string", "required": True},
                },
            },
            "frontend": {
                "type": "dict",
                "required": False,
                "children": {
                    "framework": {"type": "string", "required": True},
                },
            },
            "database": {
                "type": "dict",
                "required": True,
                "children": {
                    "primary": {"type": "string", "required": True},
                    "orm": {"type": "string", "required": True},
                },
            },
            "infrastructure": {
                "type": "dict",
                "required": True,
                "children": {
                    "containerization": {"type": "string", "required": True},
                    "ci_cd": {"type": "string", "required": True},
                },
            },
        },
    },
    "structure": {
        "type": "dict",
        "required": True,
        "children": {
            "files": {"type": "list", "required": True},
        },
    },
    "features": {"type": "list", "required": True},
    "data_model": {
        "type": "dict",
        "required": True,
        "children": {
            "entities": {"type": "list", "required": True},
        },
    },
    "api": {"type": "dict", "required": True},
    "testing": {"type": "dict", "required": True},
    "deployment": {"type": "dict", "required": True},
    "standards": {"type": "dict", "required": True},
    "security": {"type": "dict", "required": True},
}

_TYPE_MAP = {
    "string": str,
    "dict": dict,
    "list": list,
    "int": int,
    "bool": bool,
}


# ------------------------------------------------------------------
# Recursive schema checker
# ------------------------------------------------------------------
def _validate_against_schema(
    data: dict,
    schema: dict,
    path: str = "",
) -> list[str]:
    """Recursively validate data against a schema definition.

    Args:
        data: The data dictionary to validate.
        schema: Schema definition with type/required/children entries.
        path: Dot-separated path for error messages.

    Returns:
        List of error message strings.
    """
    if not isinstance(data, dict):
        return [f"{path or 'root'}: expected dict, got {type(data).__name__}"]

    errors: list[str] = []
    for key, rules in schema.items():
        full_path = f"{path}.{key}" if path else key
        is_required = rules.get("required", False)
        expected = rules.get("type", "string")

        if key not in data:
            if is_required:
                errors.append(f"Missing required field: {full_path}")
            continue

        value = data[key]
        py_type = _TYPE_MAP.get(expected)
        if py_type and not isinstance(value, py_type):
            errors.append(
                f"{full_path}: expected {expected}, got {type(value).__name__}"
            )

        if expected == "dict" and isinstance(value, dict) and "children" in rules:
            errors.extend(_validate_against_schema(value, rules["children"], full_path))

    return errors


# ------------------------------------------------------------------
# Content-level quality checks
# ------------------------------------------------------------------
def _check_file_quality(spec: dict) -> list[str]:
    """Check file structure for duplicates, missing paths, and essential files."""
    warnings: list[str] = []
    files = (spec.get("structure") or {}).get("files") or []
    seen_paths: set[str] = set()

    for f in files:
        if not f.get("path"):
            warnings.append(f"File entry missing 'path': {f}")
            continue

        path = f["path"]
        # Duplicate file paths
        if path in seen_paths:
            warnings.append(f"Duplicate file path: {path}")
        seen_paths.add(path)

        if not f.get("key_contents"):
            warnings.append(
                f"File '{path}' has no key_contents — "
                "agent won't know what to put in it"
            )

    # Check for essential files
    for essential in (".gitignore", "README.md"):
        if essential not in seen_paths:
            warnings.append(f"Missing recommended file: {essential}")

    return warnings


def _check_feature_quality(spec: dict) -> list[str]:
    """Check features for missing names and endpoint gaps."""
    warnings: list[str] = []
    for i, feat in enumerate(spec.get("features") or []):
        # String features are valid but limited
        if isinstance(feat, str):
            continue
        if not isinstance(feat, dict):
            warnings.append(f"features[{i}] should be a dict or string")
            continue

        if "name" not in feat:
            warnings.append(f"features[{i}] missing 'name'")

        details = feat.get("details", {})
        if isinstance(details, dict):
            endpoints = details.get("endpoints", [])
            if not endpoints and feat.get("type") in ("crud", "auth", "integration"):
                warnings.append(
                    f"Feature '{feat.get('name', '?')}' has no endpoints defined"
                )
    return warnings


def _check_data_model_quality(spec: dict) -> tuple[list[str], list[str]]:
    """Check data model entities for duplicate names, missing fields, and types.

    Returns:
        A tuple of (errors, warnings) lists.
    """
    warnings: list[str] = []
    errors: list[str] = []
    entities = (spec.get("data_model") or {}).get("entities") or []
    seen_entities: set[str] = set()

    for i, entity in enumerate(entities):
        if not isinstance(entity, dict):
            errors.append(f"data_model.entities[{i}] must be a mapping")
            continue

        ename = entity.get("name", "")
        if not ename or not isinstance(ename, str):
            errors.append(f"data_model.entities[{i}] missing 'name'")
            continue

        if ename in seen_entities:
            warnings.append(f"Duplicate entity name: {ename}")
        seen_entities.add(ename)

        fields = entity.get("fields", [])
        if not isinstance(fields, list) or len(fields) == 0:
            warnings.append(f"Entity '{ename}' has no fields — seems incomplete")
        else:
            if len(fields) < 2:
                warnings.append(
                    f"Entity '{ename}' has only "
                    f"{len(fields)} field(s) — seems incomplete"
                )

            field_names: set[str] = set()
            for j, field in enumerate(fields):
                if not isinstance(field, dict):
                    warnings.append(f"{ename}.fields[{j}] must be a mapping")
                    continue
                fname = field.get("name", "")
                if not fname:
                    warnings.append(f"{ename}.fields[{j}] missing 'name'")
                if fname in field_names:
                    warnings.append(f"{ename}: duplicate field '{fname}'")
                field_names.add(fname)
                if "type" not in field:
                    warnings.append(f"{ename}.{fname or f'fields[{j}]'} missing 'type'")

            has_pk = any(
                "primary" in str(f.get("constraints", ""))
                for f in fields
                if isinstance(f, dict)
            )
            if not has_pk:
                warnings.append(f"Entity '{ename}' has no primary key field")

    return errors, warnings


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------
def validate_spec_yaml(yaml_content: str) -> dict:
    """Validate a YAML string and return structured feedback.

    Returns::

        {
            "valid":    bool,
            "errors":   [...],   # hard failures
            "warnings": [...],   # quality issues (non-blocking)
            "stats": {
                "files_planned": int,
                "features":      int,
                "entities":      int,
                "endpoints":     int,
            },
        }
    """
    result: dict[str, Any] = {
        "valid": False,
        "errors": [],
        "warnings": [],
        "stats": {},
    }

    # ── YAML parse ─────────────────────────────────────────────
    try:
        spec = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        result["errors"].append(f"Invalid YAML: {exc}")
        return result

    if not isinstance(spec, dict):
        result["errors"].append(
            "Spec must be a YAML mapping (dict), not a scalar or list"
        )
        return result

    # ── Schema validation ──────────────────────────────────────
    result["errors"].extend(_validate_against_schema(spec, REQUIRED_SPEC_SCHEMA))

    # ── Name quality ───────────────────────────────────────────
    name = spec.get("name", "")
    if isinstance(name, str) and " " in name:
        result["warnings"].append(f"Project name '{name}' has spaces — use kebab-case")

    # ── Content checks (lightweight from spec_generator) ───────
    result["warnings"].extend(validate_spec(spec))

    # ── Deeper quality checks ──────────────────────────────────
    result["warnings"].extend(_check_file_quality(spec))
    result["warnings"].extend(_check_feature_quality(spec))
    dm_errors, dm_warnings = _check_data_model_quality(spec)
    result["errors"].extend(dm_errors)
    result["warnings"].extend(dm_warnings)

    # ── Stats ──────────────────────────────────────────────────
    files = (spec.get("structure") or {}).get("files") or []
    features = spec.get("features") or []
    entities = (spec.get("data_model") or {}).get("entities") or []
    total_endpoints = sum(
        len((f.get("details") or {}).get("endpoints") or [])
        for f in features
        if isinstance(f, dict)
    )
    result["stats"] = {
        "files_planned": len(files),
        "features": len(features),
        "entities": len(entities),
        "endpoints": total_endpoints,
    }

    result["valid"] = len(result["errors"]) == 0
    return result
