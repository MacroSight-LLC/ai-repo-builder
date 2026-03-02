from __future__ import annotations

import os
from typing import Annotated

import uvicorn
from dotenv import load_dotenv
from fastapi import APIRouter, Body, FastAPI, HTTPException, Path, Query
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic.json_schema import SkipJsonSchema

from cuga.backend.memory.agentic_memory.client import MemoryClient
from cuga.backend.memory.agentic_memory.llm.conflict_resolution.conflict_resolution import (
    MemoryEvent,
)
from cuga.backend.memory.agentic_memory.schema import Fact, Message, Namespace, RecordedFact, Run
from cuga.backend.memory.agentic_memory.utils.exceptions import (
    MemoryException,
    NamespaceAlreadyExistsException,
    NamespaceNotFoundException,
    RunNotFoundException,
)
from cuga.backend.memory.agentic_memory.utils.logging import Logging

load_dotenv(override=True)

logger = Logging.get_logger()
app = FastAPI(debug=os.getenv("DEBUG", "false").lower() == "true")
app.openapi_version = "3.0.3"

# Add CORS middleware to handle preflight OPTIONS requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:7860").split(
        ","
    ),  # Override via CORS_ORIGINS env var
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods including OPTIONS
    allow_headers=["*"],  # Allow all headers
)

router_v1 = APIRouter(prefix="/v1")

memory_client = MemoryClient(config=None)
assert memory_client.config.provider != "http", (
    "The memory service cannot be backed by an `http` memory provider."
)


@router_v1.get("/health/live")
@router_v1.get("/health/ready")
async def ready():
    """Attempt to query if the memory service is available"""
    if memory_client.ready():
        return {"status": "ok"}
    raise HTTPException(status_code=503, detail="Memory service is unavailable")


@router_v1.post(
    "/namespaces", status_code=201, response_description="The ID of the created namespace."
)
def create_namespace(
    namespace_id: Annotated[
        str | SkipJsonSchema[None],
        Body(description="The namespace ID to create", pattern=r"^[a-zA-Z0-9_]+$"),
    ] = None,
    user_id: Annotated[
        str | SkipJsonSchema[None],
        Body(description="The user that created the namespace.", pattern=r"^[a-zA-Z0-9_]+$"),
    ] = None,
    agent_id: Annotated[
        str | SkipJsonSchema[None],
        Body(description="The agent associated with the namespace.", pattern=r"^[a-zA-Z0-9_]+$"),
    ] = None,
    app_id: Annotated[
        str | SkipJsonSchema[None],
        Body(
            description="The application associated with the namespace.", pattern=r"^[a-zA-Z0-9_]+$"
        ),
    ] = None,
) -> Namespace:
    """Create a new namespace for facts to exist in."""
    return memory_client.create_namespace(namespace_id, user_id, agent_id, app_id)


@router_v1.get(
    "/namespaces/{namespace_id}", response_description="The ID of the created namespace."
)
def get_namespace_details(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
) -> Namespace:
    """Get the details of a specific namespace."""
    return memory_client.get_namespace_details(namespace_id)


@router_v1.get("/namespaces", response_description="A list of namespaces matching the filters.")
def search_namespaces(
    user_id: Annotated[
        str | SkipJsonSchema[None], Query(description="The user that created the namespace.")
    ] = None,
    agent_id: Annotated[
        str | SkipJsonSchema[None], Query(description="The agent associated with the namespace.")
    ] = None,
    app_id: Annotated[
        str | SkipJsonSchema[None],
        Query(description="The application associated with the namespace."),
    ] = None,
    limit: Annotated[int, Query(description="The number of results to return.")] = 10,
) -> list[Namespace]:
    """Find namespaces matching the filters."""
    return memory_client.search_namespaces(user_id, agent_id, app_id, limit)


@router_v1.delete("/namespaces/{namespace_id}", status_code=204)
def delete_namespace(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
):
    """Delete a namespace that facts exist in."""
    memory_client.delete_namespace(namespace_id=namespace_id)


@router_v1.put(
    "/namespaces/{namespace_id}/facts",
    status_code=201,
    response_description="The ID of the created fact.",
)
def create_and_store_fact(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
    fact: Annotated[
        Fact,
        Body(
            description="A fact about the user, their personal preferences, upcoming plans, "
            "professional details, and other miscellaneous information."
        ),
    ],
) -> list[MemoryEvent]:
    """Based on something the user previously said, create and store in memory a new fact about the user,
    their personal preferences, upcoming plans, professional details, and other miscellaneous information.
    """
    return memory_client.create_and_store_fact(namespace_id=namespace_id, fact=fact)


@router_v1.patch("/namespaces/{namespace_id}/facts")
def update_facts(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
    facts: Annotated[
        list[Fact],
        Body(
            description="A fact about the user, their personal preferences, upcoming plans, "
            "professional details, and other miscellaneous information."
        ),
    ],
    enable_conflict_resolution: Annotated[
        bool, Query(description="If true, old facts will be reconciled with new facts.")
    ] = True,
) -> list[MemoryEvent]:
    """Based on something the user previously said, create and store in memory new facts about the user,
    their personal preferences, upcoming plans, professional details, and other miscellaneous information.
    """
    return memory_client.update_facts(
        namespace_id=namespace_id,
        facts=facts,
        enable_conflict_resolution=enable_conflict_resolution,
    )


@router_v1.post(
    "/namespaces/{namespace_id}/facts",
    response_description="A list of facts that may be relevant to the user.",
)
def search_for_facts(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
    query: Annotated[
        str | SkipJsonSchema[None],
        Body(
            description="A question written in natural language about the user that needs an answer."
        ),
    ] = None,
    filters: Annotated[
        dict | SkipJsonSchema[None], Body(description="A list of facts relevant to the user.")
    ] = None,
    limit: Annotated[int, Query(description="The maximum number of facts to return.")] = 10,
) -> list[RecordedFact]:
    """Based on a query, find in memory a fact about the user, their personal preferences, upcoming plans,
    professional details, and other miscellaneous information.
    """
    return memory_client.search_for_facts(
        namespace_id=namespace_id, query=query, filters=filters, limit=limit
    )


@router_v1.delete("/namespaces/{namespace_id}/facts/{fact_id}", status_code=204)
def delete_fact_by_id(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
    fact_id: Annotated[str, Path(description="The ID of the fact to delete.")],
):
    """Remove a fact from memory by its ID."""
    memory_client.delete_fact_by_id(namespace_id=namespace_id, fact_id=fact_id)


@router_v1.post(
    "/namespaces/{namespace_id}/messages",
    response_description="The number of messages received for extraction.",
)
async def extract_facts_from_messages(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
    messages: Annotated[
        list[Message], Body(description="A list of messages between a user and a chatbot.")
    ],
    metadata: Annotated[
        dict | SkipJsonSchema[None], Body(description="Metadata to apply to any found facts")
    ] = None,
) -> list[MemoryEvent]:
    """Takes a list of messages between a user and a chatbot, extracting and storing facts about the user,
    their personal preferences, upcoming plans, professional details, and other miscellaneous information.
    """
    return await memory_client.extract_facts_from_messages_async(
        namespace_id=namespace_id, messages=messages, metadata=metadata
    )


@router_v1.post("/namespaces/{namespace_id}/runs", status_code=201)
def create_run(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
    run_id: Annotated[
        str | SkipJsonSchema[None],
        Body(description="Optional ID to create the run with.", embed=True),
    ],
) -> Run:
    """Add a new run into memory. Runs are a series of steps executed in an agentic workflow."""
    return memory_client.create_run(namespace_id=namespace_id, run_id=run_id)


@router_v1.get("/namespaces/{namespace_id}/runs/{run_id}")
def get_run(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
    run_id: Annotated[
        str, Path(description="The run which contains the steps for an agentic workflow.")
    ],
) -> Run:
    """Get a run."""
    return memory_client.get_run(namespace_id=namespace_id, run_id=run_id)


@router_v1.delete("/namespaces/{namespace_id}/runs/{run_id}", status_code=204)
def delete_run(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
    run_id: Annotated[
        str, Path(description="The run which contains the steps for an agentic workflow.")
    ],
):
    """Delete a run from memory."""
    return memory_client.delete_run(namespace_id=namespace_id, run_id=run_id)


@router_v1.post(
    "/namespaces/{namespace_id}/runs/{run_id}/steps", response_description="The number of runs."
)
def add_step(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
    run_id: Annotated[
        str, Path(description="The run which contains the steps for an agentic workflow.")
    ],
    step: Annotated[dict, Body(description="The step, an arbitrary JSON object.")],
    prompt: Annotated[str, Body(description="The prompt used by an LLM to parse a step.")],
) -> MemoryEvent:
    """Add a new step into a run."""
    return memory_client.add_step(namespace_id, run_id, step, prompt)


@router_v1.get("/namespaces/{namespace_id}/runs")
def list_runs(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
    limit: Annotated[int, Query(description="The number of results to return.")] = 10,
) -> list[Run]:
    return memory_client.list_runs(namespace_id=namespace_id, limit=limit)


@router_v1.post("/namespaces/{namespace_id}/runs/search")
def search_runs(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
    query: Annotated[
        str | SkipJsonSchema[None],
        Body(
            description="A question written in natural language about the user that needs an answer."
        ),
    ] = None,
    filters: Annotated[
        dict[str, str] | SkipJsonSchema[None],
        Body(description="A set of filters to apply against the metadata of each step."),
    ] = None,
) -> Run | None:
    return memory_client.search_runs(namespace_id=namespace_id, query=query, filters=filters)


@router_v1.post("/namespaces/{namespace_id}/runs/{run_id}/end")
def end_run(
    namespace_id: Annotated[
        str, Path(description="The namespace which contains facts relevant to the user.")
    ],
    run_id: Annotated[
        str, Path(description="The run which contains the steps for an agentic workflow.")
    ],
) -> None:
    """End a run."""
    memory_client.end_run(namespace_id, run_id)


app.include_router(router_v1)


@app.exception_handler(MemoryException)
async def memory_exception_handler(request, exc):
    if isinstance(exc, (NamespaceNotFoundException, RunNotFoundException)):
        status_code = 404
    elif isinstance(exc, NamespaceAlreadyExistsException):
        status_code = 409
    else:
        status_code = 500
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder({"detail": str(exc)}),
    )


def main():
    """Main entry point for the agentic-memory server."""
    # Set debug logging if FastAPI debug mode is enabled
    log_level = "debug" if app.debug else "info"
    uvicorn.run(app, host="0.0.0.0", port=8888, log_level=log_level)


if __name__ == "__main__":
    main()
