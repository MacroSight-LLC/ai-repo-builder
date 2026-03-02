"""
Test VariablesManager with LangGraph-like state updates.

VariablesManager is NOT a singleton -- it is a StateVariablesManager
backed by each AgentState's variables_storage dict.  Variables move
between nodes by serializing (model_dump) then deserializing
(AgentState(**dict)).
"""

from __future__ import annotations

import json

import pytest

from cuga.backend.cuga_graph.state.agent_state import AgentState


class TestVariablesManagerLangGraphIntegration:
    """Test VariablesManager behavior with LangGraph-style state management."""

    # -- Core: variables survive serialization round-trips --

    def test_state_updates_preserve_variable_access(self) -> None:
        """Variables survive a model_dump -> AgentState(**dict) round-trip."""
        state1 = AgentState(input="initial query", url="http://example.com", final_answer="")
        state1.variables_manager.add_variable("important_data", "data_var")

        state1_dict = state1.model_dump()
        state1_dict["final_answer"] = "Updated answer"
        state1_dict["next_step"] = "NextNode"

        state2 = AgentState(**state1_dict)

        assert state2.variables_manager.get_variable("data_var") == "important_data"
        assert state2.final_answer == "Updated answer"
        assert state2.next_step == "NextNode"

    def test_multi_node_state_flow(self) -> None:
        """Simulate multiple node executions with state updates."""
        state = AgentState(input="fetch user data", url="http://api.com")
        state.variables_manager.add_variable({"user_id": 123}, "user_input")

        state_dict = state.model_dump()
        state_dict["next_step"] = "CodeAgent"
        state_dict["current_app"] = "api_service"
        state = AgentState(**state_dict)

        user_data = state.variables_manager.get_variable("user_input")
        assert user_data == {"user_id": 123}

        state.variables_manager.add_variable(
            {"user_id": 123, "name": "John", "email": "john@example.com"},
            "user_data",
        )

        state_dict = state.model_dump()
        state_dict["next_step"] = "FinalAnswer"
        state = AgentState(**state_dict)

        final_data = state.variables_manager.get_variable("user_data")
        assert final_data["name"] == "John"
        assert state.variables_manager.get_variable_count() == 2

    def test_state_json_serialization(self) -> None:
        """State can be JSON-serialized (checkpointing) and recovered."""
        state = AgentState(input="test query", url="http://example.com", final_answer="test answer")
        state.variables_manager.add_variable([1, 2, 3], "numbers")

        state_json = json.dumps(state.model_dump())
        recovered = AgentState(**json.loads(state_json))

        assert recovered.input == "test query"
        assert recovered.final_answer == "test answer"
        assert recovered.variables_manager.get_variable("numbers") == [1, 2, 3]

    # -- Parallel branches --

    def test_parallel_branch_execution(self) -> None:
        """Branches from the same base state share context via serialized storage."""
        base = AgentState(input="parallel task", url="http://example.com")
        base.variables_manager.add_variable("shared_context", "context_var")

        branch1 = AgentState(**{**base.model_dump(), "next_step": "Branch1Node"})
        branch2 = AgentState(**{**base.model_dump(), "next_step": "Branch2Node"})

        assert branch1.variables_manager.get_variable("context_var") == "shared_context"
        assert branch2.variables_manager.get_variable("context_var") == "shared_context"

        # Branches are isolated -- a write in branch1 does NOT leak to branch2
        branch1.variables_manager.add_variable("branch1_result", "result1")
        assert branch1.variables_manager.get_variable("result1") == "branch1_result"
        assert branch2.variables_manager.get_variable("result1") is None

    # -- Session reset --

    def test_state_reset_between_sessions(self) -> None:
        """A fresh AgentState with default storage has zero variables."""
        s1 = AgentState(input="session 1 query", url="http://s1.com")
        s1.variables_manager.add_variable("session1_data", "s1_var")
        assert s1.variables_manager.get_variable("s1_var") == "session1_data"

        s2 = AgentState(input="session 2 query", url="http://s2.com")
        assert s2.variables_manager.get_variable("s1_var") is None
        assert s2.variables_manager.get_variable_count() == 0

        s2.variables_manager.add_variable("session2_data", "s2_var")
        assert s2.variables_manager.get_variable_count() == 1

    # -- Optional fields --

    def test_state_with_optional_fields(self) -> None:
        """Optional fields get their defaults after a round-trip."""
        state1 = AgentState(input="test", url="http://test.com")
        state1.variables_manager.add_variable("data", "var1")

        state2 = AgentState(**state1.model_dump())

        assert state2.current_app is None
        assert state2.final_answer == ""
        assert state2.next_step == ""
        assert state2.variables_manager.get_variable("var1") == "data"

    # -- Partial updates --

    def test_state_partial_updates(self) -> None:
        """Only changed fields update; variables persist through the round-trip."""
        state = AgentState(
            input="original query",
            url="http://original.com",
            current_app="app1",
            final_answer="",
            next_step="Node1",
        )
        state.variables_manager.add_variable("original_data", "data_var")

        d = state.model_dump()
        d["next_step"] = "Node2"
        d["current_app"] = "app2"
        new_state = AgentState(**d)

        assert new_state.next_step == "Node2"
        assert new_state.current_app == "app2"
        assert new_state.input == "original query"
        assert new_state.url == "http://original.com"
        assert new_state.variables_manager.get_variable("data_var") == "original_data"

    # -- Chained recreation --

    def test_variables_survive_state_recreation(self) -> None:
        """Variables persist even when state is recreated many times."""
        current = AgentState(input="test", url="http://test.com")
        current.variables_manager.add_variable("persistent_value", "persistent_var")

        for i in range(10):
            d = current.model_dump()
            d["next_step"] = f"Node{i}"
            current = AgentState(**d)

            assert current.variables_manager.get_variable("persistent_var") == "persistent_value"
            current.variables_manager.add_variable(f"value_{i}", f"var_{i}")

        assert current.variables_manager.get_variable_count() == 11


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
