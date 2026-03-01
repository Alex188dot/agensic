from fastapi import APIRouter, HTTPException
from ghostshell.server import deps
from ghostshell.server.schemas import (
    ProvenanceRunsResponse,
    ProvenanceRegistryAgentsResponse,
    ProvenanceRegistryRefreshResponse,
    ProvenanceRegistrySummaryResponse,
    ProvenanceRegistryVerifyResponse,
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
            tier=tier,
            agent=agent,
            agent_name=agent_name,
            provider=provider,
        )
        return {
            "status": "ok",
            "runs": runs,
            "total": len(runs),
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
    "/provenance/registry/refresh",
    response_model=ProvenanceRegistryRefreshResponse,
    response_model_exclude_unset=True,
)
def provenance_registry_refresh(force: bool = False) -> ProvenanceRegistryRefreshResponse:
    deps.enter_request_or_503()
    try:
        config = deps.load_config()
        result = deps.engine.refresh_provenance_registry(config=config, force=bool(force))
        return {
            "status": "ok",
            "ok": bool(result.get("ok", False)),
            "reason": str(result.get("reason", "") or ""),
            "updated": bool(result.get("updated", False)),
            "version": str(result.get("version", "") or ""),
        }
    finally:
        deps.release_request_slot()


@router.get(
    "/provenance/registry/verify",
    response_model=ProvenanceRegistryVerifyResponse,
    response_model_exclude_unset=True,
)
def provenance_registry_verify() -> ProvenanceRegistryVerifyResponse:
    deps.enter_request_or_503()
    try:
        config = deps.load_config()
        result = deps.engine.verify_provenance_registry_cache(config=config)
        return {
            "status": "ok",
            "ok": bool(result.get("ok", False)),
            "reason": str(result.get("reason", "") or ""),
            "version": str(result.get("version", "") or ""),
            "verified_at": int(result.get("verified_at", 0) or 0),
            "url": str(result.get("url", "") or ""),
        }
    finally:
        deps.release_request_slot()
