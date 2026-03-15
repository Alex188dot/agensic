from fastapi import APIRouter, HTTPException
from agensic.server import deps
from agensic.server.schemas import (
    ProvenanceRunsResponse,
    ProvenanceRegistryAgentsResponse,
    ProvenanceRegistrySummaryResponse,
)

router = APIRouter()


@router.get(
    "/provenance/runs",
    response_model=ProvenanceRunsResponse,
    response_model_exclude_unset=True,
)
def provenance_runs(
    limit: int = 50,
    label: str = "",
    command_contains: str = "",
    since_ts: int = 0,
    before_ts: int = 0,
    before_run_id: str = "",
    tier: str = "",
    agent: str = "",
    agent_name: str = "",
    provider: str = "",
) -> ProvenanceRunsResponse:
    deps.enter_request_or_503()
    try:
        runs = deps.engine.list_command_runs(
            limit=limit,
            label=label,
            command_contains=command_contains,
            since_ts=since_ts,
            before_ts=before_ts,
            before_run_id=before_run_id,
            tier=tier,
            agent=agent,
            agent_name=agent_name,
            provider=provider,
        )
        total_matching = deps.engine.count_command_runs(
            label=label,
            command_contains=command_contains,
            since_ts=since_ts,
            tier=tier,
            agent=agent,
            agent_name=agent_name,
            provider=provider,
        )
        return {
            "status": "ok",
            "runs": runs,
            "total": len(runs),
            "total_matching": int(total_matching or 0),
        }
    finally:
        deps.release_request_slot()


@router.get(
    "/provenance/runs/semantic",
    response_model=ProvenanceRunsResponse,
    response_model_exclude_unset=True,
)
def provenance_runs_semantic(
    query: str,
    limit: int = 50,
    since_ts: int = 0,
    label: str = "",
    tier: str = "",
    agent: str = "",
    agent_name: str = "",
    provider: str = "",
) -> ProvenanceRunsResponse:
    deps.enter_request_or_503()
    try:
        runs = deps.engine.semantic_command_runs(
            query=query,
            limit=limit,
            since_ts=since_ts,
            label=label,
            tier=tier,
            agent=agent,
            agent_name=agent_name,
            provider=provider,
        )
        return {
            "status": "ok",
            "runs": runs,
            "total": len(runs),
            "total_matching": len(runs),
        }
    finally:
        deps.release_request_slot()


@router.get(
    "/provenance/registry",
    response_model=ProvenanceRegistrySummaryResponse,
    response_model_exclude_unset=True,
)
def provenance_registry_summary() -> ProvenanceRegistrySummaryResponse:
    deps.enter_request_or_503()
    try:
        return {
            "status": "ok",
            "summary": deps.engine.get_provenance_registry_summary(),
        }
    finally:
        deps.release_request_slot()


@router.get(
    "/provenance/registry/agents",
    response_model=ProvenanceRegistryAgentsResponse,
    response_model_exclude_unset=True,
)
def provenance_registry_agents(status: str = "") -> ProvenanceRegistryAgentsResponse:
    deps.enter_request_or_503()
    try:
        agents = deps.engine.list_provenance_registry_agents(status_filter=status)
        return {
            "status": "ok",
            "agents": agents,
            "total": len(agents),
        }
    finally:
        deps.release_request_slot()


@router.get(
    "/provenance/registry/agents/{agent_id}",
    response_model=ProvenanceRegistrySummaryResponse,
    response_model_exclude_unset=True,
)
def provenance_registry_agent(agent_id: str) -> ProvenanceRegistrySummaryResponse:
    deps.enter_request_or_503()
    try:
        record = deps.engine.get_provenance_registry_agent(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="agent_not_found")
        return {
            "status": "ok",
            "summary": record,
        }
    finally:
        deps.release_request_slot()


@router.post(
    "/provenance/registry/reload",
    response_model=ProvenanceRegistrySummaryResponse,
    response_model_exclude_unset=True,
)
def provenance_registry_reload() -> ProvenanceRegistrySummaryResponse:
    deps.enter_request_or_503()
    try:
        return {
            "status": "ok",
            "summary": deps.engine.reload_provenance_registry(),
        }
    finally:
        deps.release_request_slot()
