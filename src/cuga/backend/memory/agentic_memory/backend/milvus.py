from __future__ import annotations

import datetime
import json
import uuid
from json import JSONDecodeError

from cuga.backend.memory.agentic_memory.backend.base import BaseMemoryBackend
from cuga.backend.memory.agentic_memory.config import milvus_config
from cuga.backend.memory.agentic_memory.db.sqlite_manager import SQLiteManager
from cuga.backend.memory.agentic_memory.llm.conflict_resolution.conflict_resolution import (
    MemoryEvent,
    resolve_conflicts,
)
from cuga.backend.memory.agentic_memory.llm.fact_extraction.fact_extraction import (
    extract_facts_from_messages,
)
from cuga.backend.memory.agentic_memory.schema import (
    Fact,
    Message,
    Namespace,
    RecordedFact,
    Run,
    fact_schema,
)
from cuga.backend.memory.agentic_memory.utils.exceptions import (
    NamespaceNotFoundException,
    RunNotFoundException,
)
from cuga.backend.memory.agentic_memory.utils.logging import Logging
from cuga.backend.memory.agentic_memory.utils.utils import (
    clean_llm_response,
    get_chat_model,
    get_embedding_model,
    get_milvus_client,
)

logger = Logging.get_logger()


def _safe_filter_value(value: str) -> str:
    """Escape single quotes in a Milvus filter value to prevent injection."""
    return str(value).replace("'", "\\'")


def _build_filter_expr(filters: dict, extra_clauses: list[str] | None = None) -> str:
    """Build a safe Milvus filter expression from a dict of filters."""
    clauses = list(extra_clauses or [])
    clauses.extend(f"{k} == '{_safe_filter_value(v)}'" for k, v in filters.items())
    return " AND ".join(clauses) if clauses else ""


class MilvusMemoryBackend(BaseMemoryBackend):
    _milvus = None
    _embedding_model = None

    @property
    def milvus(self):
        if self._milvus is None:
            self._milvus = get_milvus_client()
        return self._milvus

    @property
    def embedding_model(self):
        if self._embedding_model is None:
            self._embedding_model = get_embedding_model("sentence-transformers/all-MiniLM-L6-v2")
        return self._embedding_model

    def ready(self):
        _ = self.milvus.list_collections()
        return {"status": "ok"}

    def validate_namespace(self, namespace_id: str):
        if not self.milvus.has_collection(namespace_id):
            raise NamespaceNotFoundException(f"Namespace {namespace_id}' not found")

    def create_namespace(
        self,
        namespace_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
    ) -> Namespace:
        """Create a new namespace for facts to exist in."""
        namespace_id = namespace_id or "ns_" + str(uuid.uuid4()).replace("-", "_")

        if not self.milvus.has_collection(namespace_id):
            self.milvus.create_collection(
                collection_name=namespace_id, dimension=768, auto_id=False, schema=fact_schema
            )

        with SQLiteManager() as db_manager:
            return db_manager.create_namespace(namespace_id, user_id, agent_id, app_id)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        self.validate_namespace(namespace_id)

        with SQLiteManager() as db_manager:
            namespace = db_manager.get_namespace(namespace_id)
            namespace.num_entities = self.milvus.get_collection_stats(namespace_id)["row_count"]
            return namespace

    def search_namespaces(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
        limit: int = 10,
    ) -> list[Namespace]:
        with SQLiteManager() as db_manager:
            namespaces = []
            for namespace in db_manager.search_namespaces(user_id, agent_id, app_id, limit):
                namespace.num_entities = self.milvus.get_collection_stats(namespace.id)["row_count"]
                namespaces.append(namespace)
            return namespaces

    def delete_namespace(self, namespace_id: str):
        """Delete a namespace that facts exist in."""
        self.milvus.drop_collection(collection_name=namespace_id)

        with SQLiteManager() as db_manager:
            db_manager.delete_namespace(namespace_id)

    def update_facts(
        self, namespace_id: str, facts: list[Fact], enable_conflict_resolution: bool = True
    ) -> list[MemoryEvent]:
        self.validate_namespace(namespace_id)
        now = datetime.datetime.now(datetime.UTC)
        # Use fact's metadata if provided, otherwise default to empty dict for Milvus compatibility
        facts_with_temporary_ids = []
        for i, fact in enumerate(facts):
            fact_data = fact.model_dump()
            if fact_data.get("metadata") is None:
                fact_data["metadata"] = {}
            facts_with_temporary_ids.append(
                RecordedFact(
                    **fact_data,
                    created_at=datetime.datetime.now(datetime.UTC),
                    id=f"Unprocessed_Fact_{i}",
                )
            )

        if enable_conflict_resolution:
            old_facts = []
            for fact in facts:
                old_facts.extend(
                    self.search_for_facts(namespace_id=namespace_id, query=fact.content)
                )

            updates = resolve_conflicts(old_facts, facts_with_temporary_ids)
            for update in updates:
                match update.event:
                    case "ADD":
                        fact_id = str(
                            self.milvus.insert(
                                collection_name=namespace_id,
                                data={
                                    "content": update.content,
                                    "created_at": int(now.timestamp()),
                                    "embedding": self.embedding_model.encode(update.content),
                                    "metadata": update.metadata,
                                    "run_id": "",
                                },
                            )["ids"][0]
                        )
                        update.id = fact_id
                    case "UPDATE":
                        self.milvus.upsert(
                            collection_name=namespace_id,
                            data={
                                "id": update.id,
                                "content": update.content,
                                "created_at": int(now.timestamp()),
                                "embedding": self.embedding_model.encode(update.content),
                                "metadata": update.metadata,
                            },
                            kwargs={"partial_update": True},
                        )
                    case "DELETE":
                        self.delete_fact_by_id(namespace_id=namespace_id, fact_id=update.id)
                    case "NONE":
                        pass
        else:
            updates = []
            for fact in facts:
                fact_id = str(
                    self.milvus.insert(
                        collection_name=namespace_id,
                        data={
                            "content": fact.content,
                            "created_at": int(now.timestamp()),
                            "embedding": self.embedding_model.encode(fact.content),
                            "metadata": fact.metadata,
                            "run_id": "",
                        },
                    )["ids"][0]
                )
                updates.append(
                    MemoryEvent(
                        id=fact_id, content=fact.content, event="ADD", metadata=fact.metadata
                    )
                )
        return updates

    def create_and_store_fact(
        self, namespace_id: str, fact: Fact, enable_conflict_resolution: bool = True
    ) -> list[MemoryEvent]:
        return self.update_facts(
            namespace_id=namespace_id,
            facts=[fact],
            enable_conflict_resolution=enable_conflict_resolution,
        )

    def search_for_facts(
        self,
        namespace_id: str,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[RecordedFact]:
        self.validate_namespace(namespace_id)
        filters = filters or {}

        if query is None:
            results = self.milvus.query(
                collection_name=namespace_id,
                filter=_build_filter_expr(filters, ["id > 0"]),
            )
        else:
            filter_expr = _build_filter_expr(filters)
            query_kwargs = {
                "collection_name": namespace_id,
                "anns_field": "embedding",
                "data": [self.embedding_model.encode(query)],
                "limit": limit,
                "search_params": {"metric_type": "IP"},
            }
            if filter_expr:
                query_kwargs["filter"] = filter_expr
            results = self.milvus.query(**query_kwargs)
        return [parse_milvus_fact(i) for i in results]

    def delete_fact_by_id(self, namespace_id: str, fact_id: str):
        fact_id = int(fact_id)
        self.validate_namespace(namespace_id)
        self.milvus.delete(collection_name=namespace_id, ids=[fact_id])

    async def extract_facts_from_messages_async(
        self, namespace_id: str, messages: list[Message], metadata: dict | None = None
    ) -> list[MemoryEvent]:
        """Takes a list of messages between a user and a chatbot, extracting and storing facts about the user,
        their personal preferences, upcoming plans, professional details, and other miscellaneous information.
        """
        self.validate_namespace(namespace_id)
        extracted_facts = await extract_facts_from_messages(messages)
        return self.update_facts(
            namespace_id=namespace_id,
            facts=[Fact(content=fact, metadata=metadata) for fact in extracted_facts],
        )

    def create_run(self, namespace_id: str, run_id: str) -> Run:
        """Create a new agentic workflow run."""
        run_id = run_id or "run_" + str(uuid.uuid4()).replace("-", "_")
        with SQLiteManager() as db_manager:
            return db_manager.create_run(namespace_id, run_id)

    def delete_run(self, namespace_id: str, run_id: str):
        self.validate_namespace(namespace_id)
        self.milvus.delete(collection_name=namespace_id, filter=f"run_id == '{_safe_filter_value(run_id)}'")
        with SQLiteManager() as db_manager:
            db_manager.delete_run(namespace_id=namespace_id, run_id=run_id)

    def add_step(self, namespace_id: str, run_id: str, step: dict, prompt: str) -> MemoryEvent:
        self.validate_namespace(namespace_id)
        llm = get_chat_model(milvus_config.step_processing)
        messages = [
            {
                "role": "system",
                "content": prompt
                + "\n\nHere is the actual step you are working on:\n"
                + json.dumps(step, indent=4),
            }
        ]

        decode_error = None
        for attempt in range(3):
            extraction = llm.invoke(messages).content
            try:
                parsed_extraction = json.loads(clean_llm_response(extraction))
            except JSONDecodeError as e:
                decode_error = e
                continue
            else:
                break
        else:
            raise decode_error

        metadata = {**parsed_extraction, "run_id": run_id, "step": step}
        added_step = self.milvus.insert(
            collection_name=namespace_id,
            data={
                "content": parsed_extraction["summary"],
                "created_at": int(datetime.datetime.now(datetime.UTC).timestamp()),
                "run_id": run_id,
                "embedding": self.embedding_model.encode(parsed_extraction["summary"]),
                "metadata": {**parsed_extraction, "step": step},
            },
        )

        return MemoryEvent(
            id=str(added_step["ids"][0]),
            content=parsed_extraction["summary"],
            event="ADD",
            metadata=metadata,
        )

    def get_run(self, namespace_id: str, run_id: str) -> Run:
        self.validate_namespace(namespace_id)
        steps = [
            parse_milvus_fact(step)
            for step in self.milvus.query(
                collection_name=namespace_id,
                filter=f"run_id == '{_safe_filter_value(run_id)}'",
            )
        ]
        sorted_steps = sorted(steps, key=lambda step: step.created_at)

        with SQLiteManager() as db_manager:
            run = db_manager.get_run(namespace_id=namespace_id, run_id=run_id)
        if run is None:
            raise RunNotFoundException(f"Run `{run_id}` not found.")
        run.steps = sorted_steps
        return run

    def search_runs(self, namespace_id: str, query: str, filters: dict[str, str]) -> Run | None:
        self.validate_namespace(namespace_id)
        filters = filters or {}

        results = [
            parse_milvus_fact(i)
            for i in self.milvus.query(
                collection_name=namespace_id,
                anns_field="embedding",
                data=[self.embedding_model.encode(query)],
                filter=_build_filter_expr(
                    filters, ['run_id != ""']
                ),
                limit=5,
                search_params={"metric_type": "IP"},
            )
        ]

        if len(results) > 0:
            run_id = results[0].run_id
            return self.get_run(namespace_id, run_id)
        else:
            return None


def parse_milvus_fact(fact: dict) -> RecordedFact:
    return RecordedFact.model_validate(
        {
            **fact,
            "id": str(fact["id"]),
            "created_at": datetime.datetime.fromtimestamp(fact["created_at"], datetime.UTC),
        }
    )
