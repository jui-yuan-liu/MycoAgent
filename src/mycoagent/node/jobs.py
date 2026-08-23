from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from mycoagent.models import (
    JobMemory,
    JobStatus,
    SubtaskRecord,
    SubtaskSpec,
    SubtaskStatus,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStore:
    """Parent-only in-memory task supervision. Cluster Manager never sees this."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobMemory] = {}
        self._lock = asyncio.Lock()

    async def create(self, parent_node_id: str, description: str, subtasks: list[SubtaskSpec]) -> JobMemory:
        job = JobMemory(
            job_id=str(uuid4()),
            description=description,
            parent_node_id=parent_node_id,
            created_at=utcnow(),
            subtasks=[
                SubtaskRecord(
                    id=str(uuid4()),
                    description=item.description,
                    skills=item.skills,
                    tools=item.tools,
                    payload=item.payload,
                )
                for item in subtasks
            ],
        )
        async with self._lock:
            self._jobs[job.job_id] = job
        return job.model_copy(deep=True)

    async def get(self, job_id: str) -> JobMemory | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    async def list_jobs(self) -> list[JobMemory]:
        async with self._lock:
            return [job.model_copy(deep=True) for job in self._jobs.values()]

    async def update_subtask(self, job_id: str, subtask_id: str, **changes: object) -> JobMemory:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            updated: list[SubtaskRecord] = []
            found = False
            for item in job.subtasks:
                if item.id == subtask_id:
                    found = True
                    updated.append(item.model_copy(update=changes))
                else:
                    updated.append(item)
            if not found:
                raise KeyError(subtask_id)
            job.subtasks = updated
            job.status = _roll_up(job)
            return job.model_copy(deep=True)

    async def complete_local(self, job_id: str, result: str) -> JobMemory:
        async with self._lock:
            job = self._jobs[job_id]
            job.local_result = result
            job.status = _roll_up(job)
            return job.model_copy(deep=True)

    async def fail(self, job_id: str, error: str) -> JobMemory:
        async with self._lock:
            job = self._jobs[job_id]
            job.error = error
            job.status = JobStatus.FAILED
            return job.model_copy(deep=True)


def _roll_up(job: JobMemory) -> JobStatus:
    if job.error:
        return JobStatus.FAILED
    if not job.subtasks:
        return JobStatus.COMPLETED if job.local_result is not None else JobStatus.RUNNING
    if any(item.status == SubtaskStatus.FAILED for item in job.subtasks):
        return JobStatus.FAILED
    if all(item.status == SubtaskStatus.COMPLETED for item in job.subtasks):
        return JobStatus.COMPLETED
    return JobStatus.RUNNING
