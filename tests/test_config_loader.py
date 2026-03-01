"""Tests for configuration loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cuga.config_loader import _load_yaml, load_mcp_servers, load_settings


class TestLoadYaml:
    """Tests for the core YAML loading function."""

    def test_loads_valid_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "test.yaml"
        f.write_text("key: value\nnested:\n  a: 1\n")
        result = _load_yaml(f)
        assert result == {"key": "value", "nested": {"a": 1}}

    def test_raises_on_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            _load_yaml("/nonexistent/path.yaml")

    def test_raises_on_invalid_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.yaml"
        f.write_text("{{not valid yaml")
        with pytest.raises(yaml.YAMLError):
            _load_yaml(f)

    def test_raises_on_non_dict(self, tmp_path: Path) -> None:
        f = tmp_path / "list.yaml"
        f.write_text("- item1\n- item2\n")
        with pytest.raises(yaml.YAMLError, match="expected a YAML mapping"):
            _load_yaml(f)

    def test_empty_file_returns_empty_dict(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.yaml"
        f.write_text("")
        result = _load_yaml(f)
        assert result == {}


class TestLoadSettings:
    """Tests for application settings loader."""

    def test_returns_defaults_when_missing(self) -> None:
        result = load_settings("/nonexistent/config.yaml")
        assert result["model_id"] == "ibm/granite-3-8b-instruct"
        assert result["max_steps"] == 150

    def test_overrides_defaults(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("max_steps: 200\ncustom_key: hello\n")
        result = load_settings(str(f))
        assert result["max_steps"] == 200
        assert result["custom_key"] == "hello"
        assert result["model_id"] == "ibm/granite-3-8b-instruct"  # default preserved

    def test_handles_corrupt_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.yaml"
        f.write_text("{{invalid")
        result = load_settings(str(f))
        assert result["model_id"] == "ibm/granite-3-8b-instruct"  # falls back


class TestLoadMcpServers:
    """Tests for MCP server config loader."""

    def test_loads_valid_config(self, tmp_path: Path) -> None:
        f = tmp_path / "mcp.yaml"
        f.write_text(
            yaml.dump(
                {
                    "mcpServers": {
                        "filesystem": {
                            "command": "npx",
                            "args": ["-y", "@anthropic/filesystem"],
                        },
                    },
                }
            )
        )
        result = load_mcp_servers(str(f))
        assert "mcpServers" in result

    def test_raises_on_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_mcp_servers("/nonexistent/mcp.yaml")

    def test_warns_on_empty_servers(self, tmp_path: Path) -> None:
        f = tmp_path / "mcp.yaml"
        f.write_text("other_key: true\n")
        result = load_mcp_servers(str(f))
        assert result == {"other_key": True}

    def test_raises_on_invalid_server_config(self, tmp_path: Path) -> None:
        f = tmp_path / "mcp.yaml"
        f.write_text(
            yaml.dump(
                {
                    "mcpServers": {
                        "bad_server": "not_a_dict",
                    },
                }
            )
        )
        with pytest.raises(yaml.YAMLError, match="must be a mapping"):
            load_mcp_servers(str(f))

    def test_raises_on_invalid_args_type(self, tmp_path: Path) -> None:
        f = tmp_path / "mcp.yaml"
        f.write_text(
            yaml.dump(
                {
                    "mcpServers": {
                        "svr": {
                            "command": "npx",
                            "args": "not-a-list",
                        },
                    },
                }
            )
        )
        with pytest.raises(yaml.YAMLError, match="args must be a list"):
            load_mcp_servers(str(f))

    def test_accepts_valid_args_list(self, tmp_path: Path) -> None:
        f = tmp_path / "mcp.yaml"
        f.write_text(
            yaml.dump(
                {
                    "mcpServers": {
                        "fs": {
                            "command": "npx",
                            "args": ["-y", "@anthropic/filesystem"],
                        },
                    },
                }
            )
        )
        result = load_mcp_servers(str(f))
        assert "mcpServers" in result
