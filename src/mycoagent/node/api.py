from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from mycoagent.auth import enforce_bearer
from mycoagent.models import AgentConfigureRequest, Envelope, ForwardRequest, JobMemory, JobSubmitRequest
from mycoagent.node.runtime import (
    AgentRuntime,
    BusyError,
    DispatchError,
    HostRuntime,
    NodeRuntime,
    ParentForbidden,
)
from mycoagent.version import __version__


def _token_dependency(token: str | None):
    async def _check(request: Request) -> None:
        await enforce_bearer(request, token)

    return Depends(_check)


def _agent_router(
    get_agent: Callable[[], AgentRuntime],
    *,
    write_auth=None,
) -> APIRouter:
    router = APIRouter()
    writes = [write_auth] if write_auth is not None else []

    @router.post("/jobs", response_model=JobMemory, dependencies=writes)
    async def submit_job(body: JobSubmitRequest) -> JobMemory:
        try:
            return await get_agent().submit_job(body)
        except ParentForbidden as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except DispatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/jobs", response_model=list[JobMemory])
    async def list_jobs() -> list[JobMemory]:
        return await get_agent().jobs.list_jobs()

    @router.get("/jobs/{job_id}", response_model=JobMemory)
    async def get_job(job_id: str) -> JobMemory:
        job = await get_agent().jobs.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail="job memory is not on this node (only the parent keeps it)",
            )
        return job

    @router.post("/jobs/{job_id}/forward", response_model=JobMemory, dependencies=writes)
    async def forward_subtask(job_id: str, body: ForwardRequest) -> JobMemory:
        try:
            return await get_agent().forward_subtask(job_id, body)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="job memory is not on this node (only the parent keeps it)",
            ) from exc
        except DispatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/child")
    def child_work() -> dict[str, object]:
        work = get_agent().current_child
        if work is None:
            return {"current": None}
        return {"current": work.model_dump(mode="json")}

    @router.post("/mailbox", dependencies=writes)
    async def mailbox(envelope: Envelope) -> dict[str, object]:
        try:
            return await get_agent().handle_envelope(envelope)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DispatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/configure", dependencies=writes)
    async def configure(body: AgentConfigureRequest) -> dict[str, object]:
        record = await get_agent().apply_config(body)
        return _configure_result(record)

    return router


def _scoped_agent_router(
    fetch: Callable[[str], AgentRuntime],
    *,
    write_auth=None,
) -> APIRouter:
    router = APIRouter()
    writes = [write_auth] if write_auth is not None else []

    @router.post("/jobs", response_model=JobMemory, dependencies=writes)
    async def submit_job(agent_id: str, body: JobSubmitRequest) -> JobMemory:
        try:
            return await fetch(agent_id).submit_job(body)
        except ParentForbidden as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except DispatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/jobs", response_model=list[JobMemory])
    async def list_jobs(agent_id: str) -> list[JobMemory]:
        return await fetch(agent_id).jobs.list_jobs()

    @router.get("/jobs/{job_id}", response_model=JobMemory)
    async def get_job(agent_id: str, job_id: str) -> JobMemory:
        job = await fetch(agent_id).jobs.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail="job memory is not on this node (only the parent keeps it)",
            )
        return job

    @router.post("/jobs/{job_id}/forward", response_model=JobMemory, dependencies=writes)
    async def forward_subtask(agent_id: str, job_id: str, body: ForwardRequest) -> JobMemory:
        try:
            return await fetch(agent_id).forward_subtask(job_id, body)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="job memory is not on this node (only the parent keeps it)",
            ) from exc
        except DispatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/child")
    def child_work(agent_id: str) -> dict[str, object]:
        work = fetch(agent_id).current_child
        if work is None:
            return {"current": None}
        return {"current": work.model_dump(mode="json")}

    @router.post("/mailbox", dependencies=writes)
    async def mailbox(agent_id: str, envelope: Envelope) -> dict[str, object]:
        try:
            return await fetch(agent_id).handle_envelope(envelope)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DispatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/configure", dependencies=writes)
    async def configure(agent_id: str, body: AgentConfigureRequest) -> dict[str, object]:
        record = await fetch(agent_id).apply_config(body)
        return _configure_result(record)

    return router


def _agent_summary(agent: AgentRuntime) -> dict[str, object]:
    return {
        "id": agent.node_id,
        "name": agent.name,
        "status": agent.status.value,
        "mailbox_url": agent.mailbox_url,
    }


def _configure_result(record) -> dict[str, object]:
    return {
        "id": record.id,
        "name": record.name,
        "skills": record.skills,
        "tools_declared": record.tools_declared,
        "models": [item.model_dump(mode="json") for item in record.models],
        "status": record.status.value,
    }


def create_node_app(runtime: NodeRuntime, *, token: str | None = None) -> FastAPI:
    write_auth = _token_dependency(token) if token else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await runtime.start()
        yield
        await runtime.close()

    app = FastAPI(title="MycoAgent Host", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime
    app.state.host = None
    app.state.token = token

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "role": "host",
            "node_id": runtime.node_id,
            "group": runtime.group,
            "busy": runtime.status.value,
            "agents": [_agent_summary(runtime)],
        }

    @app.get("/agents")
    def list_agents() -> list[dict[str, object]]:
        return [_agent_summary(runtime)]

    def fetch(agent_id: str) -> AgentRuntime:
        if runtime.node_id is None or agent_id != runtime.node_id:
            raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
        return runtime

    app.include_router(_agent_router(lambda: runtime, write_auth=write_auth))
    app.include_router(
        _scoped_agent_router(fetch, write_auth=write_auth),
        prefix="/agents/{agent_id}",
    )
    return app


def create_host_app(host: HostRuntime, *, token: str | None = None) -> FastAPI:
    write_auth = _token_dependency(token) if token else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await host.start()
        yield
        await host.close()

    app = FastAPI(title="MycoAgent Host", version=__version__, lifespan=lifespan)
    app.state.host = host
    app.state.runtime = None
    app.state.token = token

    def _agents() -> list[AgentRuntime]:
        return list(host.agents.values()) or list(host._ordered)

    @app.get("/health")
    def health() -> dict[str, object]:
        agents = _agents()
        default = agents[0]
        return {
            "status": "ok",
            "role": "host",
            "node_id": default.node_id,
            "group": host.group,
            "busy": default.status.value,
            "agents": [_agent_summary(agent) for agent in agents],
        }

    @app.get("/agents")
    def list_agents() -> list[dict[str, object]]:
        return [_agent_summary(agent) for agent in _agents()]

    def fetch(agent_id: str) -> AgentRuntime:
        try:
            return host.require(agent_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}") from exc

    app.include_router(
        _scoped_agent_router(fetch, write_auth=write_auth),
        prefix="/agents/{agent_id}",
    )
    if len(host._ordered) == 1:
        app.include_router(_agent_router(lambda: host.default_agent, write_auth=write_auth))
    return app
