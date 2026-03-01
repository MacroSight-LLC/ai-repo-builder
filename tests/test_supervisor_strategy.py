"""Tests for the supervisor build strategy module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from cuga.supervisor_strategy import (
    _ARCHITECT_INSTRUCTIONS,
    _CODER_INSTRUCTIONS,
    _REVIEWER_INSTRUCTIONS,
    create_build_supervisor,
    is_supervisor_enabled,
)

# ── Instruction constants tests ────────────────────────────────


class TestInstructions:
    """Tests for the sub-agent instruction strings."""

    def test_architect_instructions_not_empty(self) -> None:
        assert len(_ARCHITECT_INSTRUCTIONS) > 100

    def test_coder_instructions_not_empty(self) -> None:
        assert len(_CODER_INSTRUCTIONS) > 100

    def test_reviewer_instructions_not_empty(self) -> None:
        assert len(_REVIEWER_INSTRUCTIONS) > 100

    def test_architect_mentions_scaffolding(self) -> None:
        assert "scaffold" in _ARCHITECT_INSTRUCTIONS.lower() or "structure" in _ARCHITECT_INSTRUCTIONS.lower()

    def test_coder_mentions_implementation(self) -> None:
        assert "implement" in _CODER_INSTRUCTIONS.lower()

    def test_reviewer_mentions_fix(self) -> None:
        assert "fix" in _REVIEWER_INSTRUCTIONS.lower()

    def test_all_instructions_unique(self) -> None:
        """Each sub-agent has distinct instructions."""
        assert _ARCHITECT_INSTRUCTIONS != _CODER_INSTRUCTIONS
        assert _CODER_INSTRUCTIONS != _REVIEWER_INSTRUCTIONS
        assert _ARCHITECT_INSTRUCTIONS != _REVIEWER_INSTRUCTIONS


# ── is_supervisor_enabled tests ────────────────────────────────


class TestIsSupervisorEnabled:
    """Tests for is_supervisor_enabled().

    Note: ``is_supervisor_enabled`` uses a lazy import of ``settings``
    inside the function body, so we patch ``cuga.config.settings``.
    """

    def test_enabled_true(self) -> None:
        mock_settings = MagicMock()
        mock_settings.supervisor = {"enabled": True}
        with patch("cuga.config.settings", mock_settings):
            assert is_supervisor_enabled() is True

    def test_enabled_false(self) -> None:
        mock_settings = MagicMock()
        mock_settings.supervisor = {"enabled": False}
        with patch("cuga.config.settings", mock_settings):
            assert is_supervisor_enabled() is False

    def test_missing_supervisor_key(self) -> None:
        """When supervisor config is missing, returns False."""
        mock_settings = MagicMock()
        mock_settings.supervisor = {}
        with patch("cuga.config.settings", mock_settings):
            assert is_supervisor_enabled() is False

    def test_import_error_returns_false(self) -> None:
        """If settings import raises, returns False."""
        with patch.dict("sys.modules", {"cuga.config": None}):
            result = is_supervisor_enabled()
            assert result is False

    def test_none_supervisor(self) -> None:
        """When supervisor is None, returns False."""
        mock_settings = MagicMock()
        mock_settings.supervisor = None
        with patch("cuga.config.settings", mock_settings):
            # None has no .get() method → exception → False
            assert is_supervisor_enabled() is False

    def test_enabled_truthy_string(self) -> None:
        """Truthy values other than True are coerced by bool()."""
        mock_settings = MagicMock()
        mock_settings.supervisor = {"enabled": "yes"}
        with patch("cuga.config.settings", mock_settings):
            assert is_supervisor_enabled() is True


# ── create_build_supervisor tests ──────────────────────────────


class TestCreateBuildSupervisor:
    """Tests for create_build_supervisor().

    Note: ``create_build_supervisor`` uses a lazy import of
    ``CugaAgent``/``CugaSupervisor`` from ``cuga.sdk``, so we patch there.
    """

    _AGENT = "cuga.sdk.CugaAgent"
    _SUPERVISOR = "cuga.sdk.CugaSupervisor"

    def test_creates_supervisor_with_3_agents(self) -> None:
        """Factory creates a CugaSupervisor with 3 named sub-agents."""
        mock_tools = [MagicMock(name="tool1"), MagicMock(name="tool2")]

        mock_agent_cls = MagicMock()
        mock_supervisor_cls = MagicMock()

        with (
            patch(self._AGENT, mock_agent_cls),
            patch(self._SUPERVISOR, mock_supervisor_cls),
        ):
            result = create_build_supervisor(tools=mock_tools)

        # CugaAgent called 3 times (architect, coder, reviewer)
        assert mock_agent_cls.call_count == 3

        # CugaSupervisor called once
        mock_supervisor_cls.assert_called_once()
        call_kwargs = mock_supervisor_cls.call_args[1]

        # Check agent names
        agents_dict = call_kwargs["agents"]
        assert set(agents_dict.keys()) == {"architect", "coder", "reviewer"}

        # Each agent got the tools
        for call in mock_agent_cls.call_args_list:
            assert call[1]["tools"] is mock_tools

        # result is the supervisor instance
        assert result is mock_supervisor_cls.return_value

    def test_agent_instructions_are_different(self) -> None:
        """Each sub-agent receives different special_instructions."""
        mock_agent_cls = MagicMock()
        mock_supervisor_cls = MagicMock()

        with (
            patch(self._AGENT, mock_agent_cls),
            patch(self._SUPERVISOR, mock_supervisor_cls),
        ):
            create_build_supervisor(tools=[])

        instructions = [
            call[1]["special_instructions"]
            for call in mock_agent_cls.call_args_list
        ]
        assert len(set(instructions)) == 3  # all unique

    def test_model_override_passed_to_agents(self) -> None:
        """Optional model kwarg is forwarded to all agents and supervisor."""
        fake_model = MagicMock(name="custom_model")
        mock_agent_cls = MagicMock()
        mock_supervisor_cls = MagicMock()

        with (
            patch(self._AGENT, mock_agent_cls),
            patch(self._SUPERVISOR, mock_supervisor_cls),
        ):
            create_build_supervisor(tools=[], model=fake_model)

        # All agents got the model
        for call in mock_agent_cls.call_args_list:
            assert call[1]["model"] is fake_model

        # Supervisor got the model
        assert mock_supervisor_cls.call_args[1]["model"] is fake_model

    def test_default_model_is_none(self) -> None:
        """When no model specified, None is passed."""
        mock_agent_cls = MagicMock()
        mock_supervisor_cls = MagicMock()

        with (
            patch(self._AGENT, mock_agent_cls),
            patch(self._SUPERVISOR, mock_supervisor_cls),
        ):
            create_build_supervisor(tools=[])

        for call in mock_agent_cls.call_args_list:
            assert call[1]["model"] is None

    def test_description_mentions_three_agents(self) -> None:
        """Supervisor description references the three agent roles."""
        mock_supervisor_cls = MagicMock()

        with (
            patch(self._AGENT, MagicMock()),
            patch(self._SUPERVISOR, mock_supervisor_cls),
        ):
            create_build_supervisor(tools=[])

        desc = mock_supervisor_cls.call_args[1]["description"]
        assert "Architect" in desc
        assert "Coder" in desc
        assert "Reviewer" in desc

    def test_strategy_param_accepted(self) -> None:
        """Strategy parameter is accepted (used for logging)."""
        mock_supervisor_cls = MagicMock()

        with (
            patch(self._AGENT, MagicMock()),
            patch(self._SUPERVISOR, mock_supervisor_cls),
        ):
            # Should not raise
            create_build_supervisor(tools=[], strategy="parallel")

    def test_empty_tools_list(self) -> None:
        """Works with empty tools list."""
        mock_agent_cls = MagicMock()
        mock_supervisor_cls = MagicMock()

        with (
            patch(self._AGENT, mock_agent_cls),
            patch(self._SUPERVISOR, mock_supervisor_cls),
        ):
            result = create_build_supervisor(tools=[])

        assert result is not None
        for call in mock_agent_cls.call_args_list:
            assert call[1]["tools"] == []
