"""
Test VariablesManager integration with AgentState.

VariablesManager is per-state (StateVariablesManager backed by the
state's variables_storage dict).  Variables are NOT shared across
independent AgentState instances -- they travel via serialization
(model_dump -> AgentState(**dict)).
"""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.state.agent_state import AgentState


class TestVariablesManagerWithState:
    """Test suite for VariablesManager accessed through AgentState."""

    # -- Per-state persistence --

    def test_per_state_persistence(self) -> None:
        """Variables added to a state are only visible on that state."""
        state1 = AgentState(input="q1", url="http://example1.com")
        state2 = AgentState(input="q2", url="http://example2.com")

        state1.variables_manager.add_variable("value from state1", "var1")

        # state2 is independent -- does NOT see state1's variable
        assert state1.variables_manager.get_variable("var1") == "value from state1"
        assert state2.variables_manager.get_variable("var1") is None
        assert state1.variables_manager.get_variable_count() == 1
        assert state2.variables_manager.get_variable_count() == 0

    def test_variables_in_state_dump(self) -> None:
        """variables_storage IS in the serialized state (that is how they travel)."""
        state = AgentState(input="test query", url="http://example.com")
        state.variables_manager.add_variable("test_value_1", "var1", "First variable")
        state.variables_manager.add_variable([1, 2, 3], "var2", "Second variable")
        state.variables_manager.add_variable({"key": "value"}, "var3", "Third variable")

        state_dict = state.model_dump()

        # variables_storage carries the data between nodes
        assert "variables_storage" in state_dict
        assert "var1" in state_dict["variables_storage"]
        assert state_dict["input"] == "test query"

    def test_state_serialization_and_deserialization(self) -> None:
        """AgentState can be serialized and reconstructed with variables intact."""
        state1 = AgentState(
            input="original query",
            url="http://example.com",
            current_app="test_app",
            final_answer="test answer",
        )
        state1.variables_manager.add_variable("shared_value", "shared_var")

        state2 = AgentState(**state1.model_dump())

        assert state2.input == "original query"
        assert state2.url == "http://example.com"
        assert state2.current_app == "test_app"
        assert state2.final_answer == "test answer"
        assert state2.variables_manager.get_variable("shared_var") == "shared_value"

    def test_variable_isolation_with_reset(self) -> None:
        """reset() clears this state's storage; a new state starts clean."""
        state1 = AgentState(input="session 1", url="http://session1.com")
        state1.variables_manager.add_variable("value1", "var1")
        state1.variables_manager.add_variable("value2", "var2")

        assert state1.variables_manager.get_variable_count() == 2
        assert state1.variables_manager.get_variable_names() == ["var1", "var2"]

        state1.variables_manager.reset()

        assert state1.variables_manager.get_variable_count() == 0
        assert state1.variables_manager.get_variable("var1") is None

        # A brand-new state also starts clean
        state2 = AgentState(input="session 2", url="http://session2.com")
        assert state2.variables_manager.get_variable_count() == 0
        state2.variables_manager.add_variable("new_value", "new_var")
        assert state2.variables_manager.get_variable("new_var") == "new_value"

    def test_multiple_states_isolated(self) -> None:
        """Each AgentState instance has its own variable namespace."""
        states = [AgentState(input=f"query {i}", url=f"http://ex{i}.com") for i in range(5)]

        for i, st in enumerate(states):
            st.variables_manager.add_variable(f"value_{i}", f"var_{i}")

        # Each state only sees its own variable
        for i, st in enumerate(states):
            assert st.variables_manager.get_variable_count() == 1
            assert st.variables_manager.get_variable(f"var_{i}") == f"value_{i}"

    def test_complex_variable_types(self) -> None:
        """Complex data types can be stored and retrieved."""
        state = AgentState(input="test", url="http://example.com")

        test_data = {
            "string_var": "hello world",
            "int_var": 42,
            "float_var": 3.14,
            "list_var": [1, 2, 3, 4, 5],
            "dict_var": {"nested": {"key": "value"}, "list": [1, 2]},
            "bool_var": True,
            "none_var": None,
        }

        for var_name, var_value in test_data.items():
            state.variables_manager.add_variable(var_value, var_name)

        for var_name, expected_value in test_data.items():
            actual = state.variables_manager.get_variable(var_name)
            assert actual == expected_value, f"Mismatch for {var_name}"

    def test_variable_metadata_persistence(self) -> None:
        """Variable metadata persists across a serialization round-trip."""
        state1 = AgentState(input="test1", url="http://example1.com")
        state1.variables_manager.add_variable(
            value="important data",
            name="important_var",
            description="This is important data for the task",
        )

        # Round-trip through serialization
        state2 = AgentState(**state1.model_dump())

        metadata = state2.variables_manager.get_variable_metadata("important_var")
        assert metadata is not None
        assert metadata.value == "important data"
        assert metadata.description == "This is important data for the task"
        assert metadata.type == "str"

    def test_variables_summary_via_roundtrip(self) -> None:
        """Variables summary works after a serialization round-trip."""
        state1 = AgentState(input="test1", url="http://example1.com")
        state1.variables_manager.add_variable([1, 2, 3], "numbers", "List of numbers")
        state1.variables_manager.add_variable("test", "text", "Some text")

        state2 = AgentState(**state1.model_dump())

        summary = state2.variables_manager.get_variables_summary()
        assert "numbers" in summary
        assert "text" in summary
        assert "List of numbers" in summary
        assert "Some text" in summary

    def test_last_n_variables_via_roundtrip(self) -> None:
        """get_last_n_variable_names works after round-trip."""
        state1 = AgentState(input="test1", url="http://example1.com")
        for i in range(10):
            state1.variables_manager.add_variable(f"value_{i}", f"var_{i}")

        state2 = AgentState(**state1.model_dump())

        last_3 = state2.variables_manager.get_last_n_variable_names(3)
        assert last_3 == ["var_7", "var_8", "var_9"]

        summary = state2.variables_manager.get_variables_summary(last_n=3)
        assert "var_7" in summary
        assert "var_8" in summary
        assert "var_9" in summary
        assert "var_0" not in summary

    def test_state_model_dump_exclude_none(self) -> None:
        """model_dump works with various options."""
        state = AgentState(input="test", url="http://example.com")
        state.variables_manager.add_variable("test_value", "test_var")

        dump_normal = state.model_dump()
        dump_exclude_none = state.model_dump(exclude_none=True)
        dump_exclude_unset = state.model_dump(exclude_unset=True)

        assert isinstance(dump_normal, dict)
        assert isinstance(dump_exclude_none, dict)
        assert isinstance(dump_exclude_unset, dict)
        assert state.variables_manager.get_variable("test_var") == "test_value"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
