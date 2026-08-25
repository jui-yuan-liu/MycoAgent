from __future__ import annotations

import asyncio
import sqlite3
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
    """Parent-only task supervision. Optional SQLite path; Cluster Manager never sees this."""

    def __init__(self, path: str | None = None) -> None:
        self._jobs: dict[str, JobMemory] = {}
        self._lock = asyncio.Lock()
        self.path = path
        self._conn: sqlite3.Connection | None = None
        if path:
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, json TEXT NOT NULL)"
            )
            self._conn.commit()
            self._load()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _load(self) -> None:
        if self._conn is None:
            return
        rows = self._conn.execute("SELECT json FROM jobs").fetchall()
        for (raw,) in rows:
            job = JobMemory.model_validate_json(raw)
            self._jobs[job.job_id] = job

    def _persist(self, job: JobMemory) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            """
            INSERT INTO jobs(id, json) VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET json=excluded.json
            """,
            (job.job_id, job.model_dump_json()),
        )
        self._conn.commit()

    async def create(self, parent_node_id: str, description: str, subtasks: list[SubtaskSpec]) -> JobMemory:
        job = JobMemory(
            job_id=str(uuid4()),
            description=description,
            parent_node_id=parent_node_id,
            created_at=utcnow(),
            subtasks=[_record_from_spec(item) for item in subtasks],
        )
        async with self._lock:
            self._jobs[job.job_id] = job
            self._persist(job)
        return job.model_copy(deep=True)

    async def get(self, job_id: str) -> JobMemory | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    async def list_jobs(self) -> list[JobMemory]:
        async with self._lock:
            return [job.model_copy(deep=True) for job in self._jobs.values()]

    async def inflight_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        async with self._lock:
            for job in self._jobs.values():
                for item in job.subtasks:
                    if item.assignee_node_id and item.status in {
                        SubtaskStatus.ASSIGNED,
                        SubtaskStatus.RUNNING,
                    }:
                        counts[item.assignee_node_id] = counts.get(item.assignee_node_id, 0) + 1
        return counts

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
            self._persist(job)
            return job.model_copy(deep=True)

    async def add_subtask(self, job_id: str, spec: SubtaskSpec) -> tuple[JobMemory, SubtaskRecord]:
        record = _record_from_spec(spec)
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            job.subtasks = [*job.subtasks, record]
            job.status = _roll_up(job)
            self._persist(job)
            return job.model_copy(deep=True), record.model_copy(deep=True)

    async def complete_local(self, job_id: str, result: str) -> JobMemory:
        async with self._lock:
            job = self._jobs[job_id]
            job.local_result = result
            job.status = _roll_up(job)
            self._persist(job)
            return job.model_copy(deep=True)

    async def fail(self, job_id: str, error: str) -> JobMemory:
        async with self._lock:
            job = self._jobs[job_id]
            job.error = error
            job.status = JobStatus.FAILED
            self._persist(job)
            return job.model_copy(deep=True)


def _record_from_spec(item: SubtaskSpec) -> SubtaskRecord:
    return SubtaskRecord(
        id=str(uuid4()),
        description=item.description,
        skills=item.skills,
        tools=item.tools,
        payload=item.payload,
        model=item.model,
        min_context_window=item.min_context_window,
        min_memory_mb=item.min_memory_mb,
    )


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
