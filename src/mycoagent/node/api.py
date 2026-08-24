from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from mycoagent.models import Envelope, ForwardRequest, JobMemory, JobSubmitRequest
from mycoagent.node.runtime import BusyError, DispatchError, NodeRuntime, ParentForbidden
from mycoagent.version import __version__


def create_node_app(runtime: NodeRuntime) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await runtime.start()
        yield
        await runtime.close()

    app = FastAPI(title="MycoAgent Node", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime

    @app.get("/health")
    def health() -> dict[str, str | None]:
        return {
            "status": "ok",
            "role": "node",
            "node_id": runtime.node_id,
            "group": runtime.group,
            "busy": str(runtime.status.value),
        }

    @app.post("/jobs", response_model=JobMemory)
    async def submit_job(body: JobSubmitRequest) -> JobMemory:
        try:
            return await runtime.submit_job(body)
        except ParentForbidden as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except DispatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/jobs", response_model=list[JobMemory])
    async def list_jobs() -> list[JobMemory]:
        return await runtime.jobs.list_jobs()

    @app.get("/jobs/{job_id}", response_model=JobMemory)
    async def get_job(job_id: str) -> JobMemory:
        job = await runtime.jobs.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail="job memory is not on this node (only the parent keeps it)",
            )
        return job

    @app.post("/jobs/{job_id}/forward", response_model=JobMemory)
    async def forward_subtask(job_id: str, body: ForwardRequest) -> JobMemory:
        try:
            return await runtime.forward_subtask(job_id, body)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="job memory is not on this node (only the parent keeps it)",
            ) from exc
        except DispatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/child")
    def child_work() -> dict[str, object]:
        work = runtime.current_child
        if work is None:
            return {"current": None}
        return {"current": work.model_dump(mode="json")}

    @app.post("/mailbox")
    async def mailbox(envelope: Envelope) -> dict[str, object]:
        try:
            return await runtime.handle_envelope(envelope)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DispatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
