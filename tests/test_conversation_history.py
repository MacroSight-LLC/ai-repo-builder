"""
Test script for conversation history persistence.

All ConversationHistoryDB methods are async, so tests use
``@pytest.mark.asyncio`` and ``await`` every DB call.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from cuga.backend.server.conversation_history import ConversationHistoryDB
from cuga.backend.cuga_graph.state.agent_state import AgentState


@pytest.fixture()
def _patch_db(tmp_path):
    """Point the storage facade at a throwaway SQLite file."""
    from cuga.backend.storage import facade as storage_facade

    db_path = str(tmp_path / "test.db")
    original = storage_facade._local_db_path
    storage_facade._local_db_path = lambda: db_path
    yield db_path
    storage_facade._local_db_path = original


# -- helpers --

AGENT_ID = "test-agent"
THREAD_ID = "test-thread-123"
USER_ID = "test-user"


def _sample_messages() -> list[dict]:
    return [
        {
            "role": "user",
            "content": "Hello, how are you?",
            "timestamp": "2024-01-01T00:00:00",
            "metadata": {"type": "HumanMessage"},
        },
        {
            "role": "assistant",
            "content": "I'm doing well, thank you!",
            "timestamp": "2024-01-01T00:00:01",
            "metadata": {"type": "AIMessage"},
        },
    ]


# -- main DB test --


@pytest.mark.asyncio
async def test_conversation_history_db(_patch_db: str) -> None:
    """Full lifecycle: save -> retrieve -> update -> version -> delete."""
    db = ConversationHistoryDB()

    messages = _sample_messages()

    # 1. Save
    ok = await db.save_conversation(
        agent_id=AGENT_ID,
        thread_id=THREAD_ID,
        version=1,
        user_id=USER_ID,
        messages=messages,
    )
    assert ok

    # 2. Retrieve
    conv = await db.get_conversation(AGENT_ID, THREAD_ID, 1, USER_ID)
    assert conv is not None
    assert conv.agent_id == AGENT_ID
    assert conv.thread_id == THREAD_ID
    assert conv.version == 1
    assert conv.user_id == USER_ID
    assert len(conv.messages) == 2

    # 3. Update (append a message)
    messages.append(
        {
            "role": "user",
            "content": "What can you help me with?",
            "timestamp": "2024-01-01T00:00:02",
            "metadata": {"type": "HumanMessage"},
        }
    )
    ok = await db.save_conversation(
        agent_id=AGENT_ID,
        thread_id=THREAD_ID,
        version=1,
        user_id=USER_ID,
        messages=messages,
    )
    assert ok
    conv = await db.get_conversation(AGENT_ID, THREAD_ID, 1, USER_ID)
    assert len(conv.messages) == 3

    # 4. Latest version
    latest = await db.get_latest_version(AGENT_ID, THREAD_ID, USER_ID)
    assert latest == 1

    # 5. Save version 2
    v2_msgs = [
        {
            "role": "user",
            "content": "New conversation",
            "timestamp": "2024-01-01T01:00:00",
            "metadata": {"type": "HumanMessage"},
        }
    ]
    ok = await db.save_conversation(AGENT_ID, THREAD_ID, 2, USER_ID, v2_msgs)
    assert ok
    latest = await db.get_latest_version(AGENT_ID, THREAD_ID, USER_ID)
    assert latest == 2

    # 6. Thread history
    history = await db.get_thread_history(THREAD_ID, USER_ID)
    assert len(history) == 2

    # 7. Delete one version
    ok = await db.delete_conversation(AGENT_ID, THREAD_ID, 1, USER_ID)
    assert ok
    conv = await db.get_conversation(AGENT_ID, THREAD_ID, 1, USER_ID)
    assert conv is None

    # 8. Delete thread
    ok = await db.delete_thread(AGENT_ID, THREAD_ID, USER_ID)
    assert ok
    history = await db.get_thread_history(THREAD_ID, USER_ID)
    assert len(history) == 0


# -- AgentState round-trip test --


@pytest.mark.asyncio
async def test_with_agent_state(_patch_db: str) -> None:
    """Save messages derived from an AgentState and retrieve them."""
    db = ConversationHistoryDB()

    # Build a minimal AgentState directly (no runtime config needed)
    state = AgentState(
        input="Test goal",
        url="",
        thread_id="test-thread-456",
        user_id="test-user",
    )

    state.chat_messages = [
        HumanMessage(content="Hello"),
        AIMessage(content="Hi there!"),
        HumanMessage(content="How are you?"),
        AIMessage(content="I'm doing great!"),
    ]

    messages = [
        {
            "role": "user" if isinstance(m, HumanMessage) else "assistant",
            "content": m.content,
            "timestamp": "2024-01-01T00:00:00",
            "metadata": {"type": type(m).__name__},
        }
        for m in state.chat_messages
    ]

    ok = await db.save_conversation(
        agent_id="test-agent",
        thread_id=state.thread_id,
        version=1,
        user_id=state.user_id,
        messages=messages,
    )
    assert ok

    conv = await db.get_conversation("test-agent", state.thread_id, 1, state.user_id)
    assert conv is not None
    assert len(conv.messages) == 4
