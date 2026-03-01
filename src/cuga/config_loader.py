"""Configuration loader — YAML configs with validation and safe defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

__all__ = ["load_mcp_servers", "load_settings"]


def _load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file with strict validation.

    Args:
        path: Path to the YAML file.

    Returns:
        Parsed dictionary.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        yaml.YAMLError: If the YAML is invalid or not a mapping.
    """
    filepath = Path(path)
    if not filepath.is_file():
        msg = f"Config file not found: {filepath}"
        raise FileNotFoundError(msg)

    text = filepath.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)

    if parsed is None:
        return {}

    if not isinstance(parsed, dict):
        msg = f"{filepath}: expected a YAML mapping, got {type(parsed).__name__}"
        raise yaml.YAMLError(msg)

    return parsed


# ── Defaults ───────────────────────────────────────────────────

_SETTINGS_DEFAULTS: dict[str, Any] = {
    "model_id": "ibm/granite-3-8b-instruct",
    "max_steps": 150,
    "temperature": 0.2,
}


def load_settings(path: str = "cuga_config.yaml") -> dict[str, Any]:
    """Load application settings with sensible defaults.

    If the config file is missing or corrupt, falls back to defaults
    and logs a warning — never crashes.

    Args:
        path: Path to the config YAML.

    Returns:
        Merged settings dictionary (defaults + file overrides).
    """
    defaults = dict(_SETTINGS_DEFAULTS)

    try:
        overrides = _load_yaml(path)
        defaults.update(overrides)
        logger.info("Settings loaded: {} ({} overrides)", path, len(overrides))
    except FileNotFoundError:
        logger.warning("Config not found: {} — using defaults", path)
    except yaml.YAMLError as exc:
        logger.error("Invalid config {}: {} — using defaults", path, exc)

    return defaults


def load_mcp_servers(path: str = "mcp_servers_local.yaml") -> dict[str, Any]:
    """Load MCP server configuration with validation.

    Validates that each server entry is a proper mapping and has a command.

    Args:
        path: Path to the MCP servers YAML.

    Returns:
        MCP server configuration dictionary.

    Raises:
        FileNotFoundError: If the config file is missing.
        yaml.YAMLError: If a server entry is malformed.
    """
    config = _load_yaml(path)

    servers = config.get("mcpServers", config.get("servers", {}))
    if not servers:
        logger.warning("No MCP servers defined in {}", path)
        return config

    for name, server_config in servers.items():
        if not isinstance(server_config, dict):
            msg = f"MCP server '{name}' must be a mapping, got {type(server_config).__name__}"
            raise yaml.YAMLError(msg)

        if "command" not in server_config:
            logger.warning("MCP server '{}' has no 'command' — may not start", name)

        args = server_config.get("args")
        if args is not None and not isinstance(args, list):
            msg = f"MCP server '{name}' args must be a list, got {type(args).__name__}"
            raise yaml.YAMLError(msg)

    logger.info("MCP config loaded: {} ({} servers)", path, len(servers))
    return config
