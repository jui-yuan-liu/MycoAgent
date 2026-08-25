from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from mycoagent.artifacts import ArtifactStore, MemoryArtifactStore, upload_workspace_files
from mycoagent.models import (
    AssignSubtaskMessage,
    CatalogQuery,
    ChildWork,
    Envelope,
    ForwardRequest,
    HeartbeatRequest,
    JobMemory,
    JobSubmitRequest,
    MembershipStatus,
    ModelSpec,
    NodeRecord,
    NodeRegisterRequest,
    NodeStatus,
    SubtaskRecord,
    SubtaskResultMessage,
    SubtaskSpec,
    SubtaskStatus,
)
from mycoagent.node.client import MailboxClient, ManagerClient
from mycoagent.node.executor import EchoExecutor, Executor
from mycoagent.node.jobs import JobStore
from mycoagent.node.planner import PlanningError, TaskPlanner
from mycoagent.node.specs import detect_machine, detect_system, parse_csv, parse_models
from mycoagent.node.workspace import assignment_workspace, strip_local_paths

log = logging.getLogger("mycoagent.node")


class BusyError(RuntimeError):
    pass


class DispatchError(RuntimeError):
    pass


class ParentForbidden(PermissionError):
    pass


REPORT_ATTEMPTS = 2
REPORT_RETRY_DELAY = 0.25
DEFAULT_MAILBOX_QUEUE = 8


def pick_dispatch_target(
    candidates: list[NodeRecord],
    inflight: dict[str, int],
    round_robin: int,
) -> tuple[NodeRecord, int]:
    """Least in-flight, then round-robin among ties (not ORDER BY name)."""
    if not candidates:
        raise DispatchError("no idle matching node in the same group")
    min_load = min(inflight.get(node.id, 0) for node in candidates)
    pool = [node for node in candidates if inflight.get(node.id, 0) == min_load]
    pool.sort(key=lambda node: node.id)
    chosen = pool[round_robin % len(pool)]
    return chosen, round_robin + 1


def _job_db_path(base: str | None, agent_id: str, *, shared: bool) -> str | None:
    if not base:
        return None
    if shared:
        return base
    path = Path(base)
    suffix = path.suffix or ".db"
    return str(path.with_name(f"{path.stem}-{agent_id}{suffix}"))


@dataclass
class AgentSpec:
    name: str
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    models: list[ModelSpec] = field(default_factory=list)
    agent_id: str | None = None
    root_mailbox: bool = False
    executor: Executor | None = None
    planner: TaskPlanner | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None


def attach_spec_llms(specs: list[AgentSpec], *, max_steps: int = 12) -> None:
    """Give each spec with llm_base_url its own OpenAI-compatible client and executor."""
    from mycoagent.node.executor import ChildAgentExecutor
    from mycoagent.node.llm import OpenAICompatClient

    for spec in specs:
        if not spec.llm_base_url:
            continue
        llm = OpenAICompatClient(
            base_url=spec.llm_base_url,
            api_key=spec.llm_api_key or "",
            model=spec.llm_model or "gpt-4o-mini",
        )
        spec.executor = ChildAgentExecutor(llm, max_steps=max_steps)
        spec.planner = TaskPlanner(llm)


class AgentRuntime:
    """One catalog identity: mailbox, heartbeat, idle/busy, optional parent JobMemory."""

    def __init__(
        self,
        manager_url: str,
        name: str,
        group: str,
        mailbox_url: str,
        skills: list[str] | None = None,
        tools_declared: list[str] | None = None,
        tools_available: list[str] | None = None,
        models: list | None = None,
        node_id: str | None = None,
        heartbeat_interval: float = 5.0,
        *,
        manager: ManagerClient | None = None,
        mail: MailboxClient | None = None,
        artifact_store: ArtifactStore | None = None,
        executor: Executor | None = None,
        planner: TaskPlanner | None = None,
        jobs: JobStore | None = None,
        job_db: str | None = None,
        mailbox_queue_size: int = DEFAULT_MAILBOX_QUEUE,
        dispatch_timeout: float = 10.0,
    ) -> None:
        self.manager_url = manager_url
        self.name = name
        self.group = group
        self.mailbox_url = mailbox_url.rstrip("/")
        self.skills = skills or []
        self.tools_declared = tools_declared or []
        self.tools_available = tools_available if tools_available is not None else list(self.tools_declared)
        self.models = models or []
        self.node_id = node_id
        self.heartbeat_interval = heartbeat_interval
        self.record: NodeRecord | None = None
        db_path = job_db if job_db is not None else os.environ.get("MYCOAGENT_JOB_DB")
        self.jobs = jobs or JobStore(path=db_path)
        self.executor: Executor = executor or EchoExecutor()
        self.planner = planner
        self._own_manager = manager is None
        self._own_mail = mail is None
        self.manager = manager or ManagerClient(manager_url)
        self.mail = mail or MailboxClient()
        self.artifacts: ArtifactStore = artifact_store or MemoryArtifactStore()
        self._child_lock = asyncio.Lock()
        self._current_child: ChildWork | None = None
        self._mailbox_queue_size = max(mailbox_queue_size, 0)
        self._mailbox_queue: asyncio.Queue[AssignSubtaskMessage] = asyncio.Queue(
            maxsize=self._mailbox_queue_size if self._mailbox_queue_size > 0 else 1
        )
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._closed = False
        self.last_workspace_path: str | None = None
        self._rr_index = 0
        self.dispatch_timeout = dispatch_timeout

    @property
    def agent_id(self) -> str | None:
        return self.node_id

    @property
    def current_child(self):
        return self._current_child

    @property
    def status(self) -> NodeStatus:
        if self._current_child is not None or self._mailbox_queue.qsize() > 0:
            return NodeStatus.BUSY
        return NodeStatus.IDLE

    async def start(self) -> NodeRecord:
        req = NodeRegisterRequest(
            name=self.name,
            group=self.group,
            mailbox_url=self.mailbox_url,
            machine=detect_machine(),
            system=detect_system(),
            models=self.models,
            skills=self.skills,
            tools_declared=self.tools_declared,
            tools_available=self.tools_available,
            node_id=self.node_id,
        )
        self.record = await self.manager.register(req)
        self.node_id = self.record.id
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        log.info("registered agent %s in group %s", self.node_id, self.group)
        return self.record

    async def close(self) -> None:
        self._closed = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        if self._own_manager:
            await self.manager.aclose()
        if self._own_mail:
            await self.mail.aclose()
        self.jobs.close()

    async def submit_job(self, request: JobSubmitRequest) -> JobMemory:
        if not self.node_id:
            raise RuntimeError("agent is not registered")
        await self._assert_can_parent()
        job = await self.jobs.create(self.node_id, request.description, request.subtasks)
        subtasks = list(request.subtasks)
        if not subtasks:
            if self.planner is not None:
                try:
                    catalog = await self.manager.catalog(
                        CatalogQuery(group=self.group, idle_only=True, exclude_node_id=self.node_id)
                    )
                    planned = await self.planner.plan(request.description, catalog, self.node_id)
                except PlanningError as exc:
                    return await self.jobs.fail(job.job_id, f"planning failed: {exc}")
                except Exception as exc:  # noqa: BLE001
                    return await self.jobs.fail(job.job_id, f"planning failed: {exc}")
                for spec in planned:
                    _, record = await self.jobs.add_subtask(job.job_id, spec)
                    try:
                        await self._dispatch(job.job_id, record.id, spec)
                    except DispatchError as err:
                        await self.jobs.update_subtask(
                            job.job_id,
                            record.id,
                            status=SubtaskStatus.FAILED,
                            error=str(err),
                        )
                refreshed = await self.jobs.get(job.job_id)
                assert refreshed is not None
                return refreshed
            return await self._run_local(job.job_id, request.description)
        for subtask in job.subtasks:
            try:
                await self._dispatch(job.job_id, subtask.id, subtask)
            except DispatchError as exc:
                await self.jobs.update_subtask(
                    job.job_id,
                    subtask.id,
                    status=SubtaskStatus.FAILED,
                    error=str(exc),
                )
        refreshed = await self.jobs.get(job.job_id)
        assert refreshed is not None
        return refreshed

    async def _run_local(self, job_id: str, description: str) -> JobMemory:
        with assignment_workspace() as workspace:
            self.last_workspace_path = str(workspace.root)
            local = await self.executor.run(
                ChildWork(
                    job_id=job_id,
                    subtask_id="local",
                    parent_node_id=self.node_id or "",
                    parent_mailbox_url=self.mailbox_url,
                    description=description,
                    payload={},
                    status=SubtaskStatus.RUNNING,
                ),
                workspace=workspace,
            )
            result = strip_local_paths(local.result or "", workspace.root)
        return await self.jobs.complete_local(job_id, result)

    async def forward_subtask(self, job_id: str, request: ForwardRequest) -> JobMemory:
        if not self.node_id:
            raise RuntimeError("agent is not registered")
        if self._current_child is not None and self._current_child.job_id == job_id:
            raise DispatchError("child cannot re-dispatch the same job")
        job = await self.jobs.get(job_id)
        if job is None:
            raise KeyError(f"job memory not on this agent: {job_id}")
        if job.parent_node_id != self.node_id:
            raise DispatchError("only the parent may forward")
        payload = dict(request.payload)
        exclude: set[str] = {self.node_id}
        if request.source_subtask_id:
            source = _find_subtask(job, request.source_subtask_id)
            if source is None:
                raise KeyError(request.source_subtask_id)
            payload["source_subtask_id"] = source.id
            payload["source_result"] = source.result
            payload["source_artifact_ids"] = list(source.artifact_ids)
            if source.assignee_node_id:
                exclude.add(source.assignee_node_id)
        spec = SubtaskSpec(
            description=request.description,
            skills=request.skills,
            tools=request.tools,
            payload=payload,
        )
        _, record = await self.jobs.add_subtask(job_id, spec)
        try:
            await self._dispatch(
                job_id,
                record.id,
                spec,
                exclude_node_ids=exclude,
                target_node_id=request.target_node_id,
            )
        except DispatchError as exc:
            await self.jobs.update_subtask(
                job_id,
                record.id,
                status=SubtaskStatus.FAILED,
                error=str(exc),
            )
        refreshed = await self.jobs.get(job_id)
        assert refreshed is not None
        return refreshed

    async def handle_envelope(self, envelope: Envelope) -> dict[str, Any]:
        if envelope.type == "assign_subtask":
            message = AssignSubtaskMessage.model_validate(envelope.body)
            await self._accept_child(message)
            return {"accepted": True, "role": "child"}
        if envelope.type == "subtask_result":
            message = SubtaskResultMessage.model_validate(envelope.body)
            await self._accept_result(message)
            return {"accepted": True, "role": "parent"}
        raise ValueError(f"unknown mailbox type: {envelope.type}")

    async def _accept_child(self, message: AssignSubtaskMessage) -> None:
        if message.parent_node_id == self.node_id:
            raise DispatchError("cannot accept a subtask from self")
        start_now = False
        async with self._child_lock:
            if self._current_child is None:
                self._current_child = _child_from_assign(message)
                start_now = True
            else:
                if self._mailbox_queue_size <= 0:
                    raise BusyError("agent is busy with another child assignment")
                try:
                    self._mailbox_queue.put_nowait(message)
                except asyncio.QueueFull as exc:
                    raise BusyError("agent is busy and mailbox queue is full") from exc
        if start_now:
            asyncio.create_task(self._run_child())

    async def _run_child(self) -> None:
        work = self._current_child
        if work is None:
            return
        artifact_ids: list[str] = []
        result: SubtaskResultMessage
        try:
            with assignment_workspace() as workspace:
                self.last_workspace_path = str(workspace.root)
                try:
                    finished = await self.executor.run(work, workspace=workspace)
                    summary = strip_local_paths(finished.result or "", workspace.root)
                    artifact_ids = await upload_workspace_files(
                        self.artifacts,
                        workspace,
                        group=self.group,
                        job_id=work.job_id,
                        subtask_id=work.subtask_id,
                    )
                    result = SubtaskResultMessage(
                        job_id=finished.job_id,
                        subtask_id=finished.subtask_id,
                        child_node_id=self.node_id or "",
                        status=SubtaskStatus.COMPLETED,
                        result=summary,
                        artifact_ids=artifact_ids,
                    )
                except Exception as exc:  # noqa: BLE001 — child must always report
                    result = SubtaskResultMessage(
                        job_id=work.job_id,
                        subtask_id=work.subtask_id,
                        child_node_id=self.node_id or "",
                        status=SubtaskStatus.FAILED,
                        error=strip_local_paths(str(exc), workspace.root),
                    )
        except Exception as exc:  # noqa: BLE001
            result = SubtaskResultMessage(
                job_id=work.job_id,
                subtask_id=work.subtask_id,
                child_node_id=self.node_id or "",
                status=SubtaskStatus.FAILED,
                error=str(exc),
            )
        try:
            await self._report_result(work.parent_mailbox_url, result)
        finally:
            nxt: AssignSubtaskMessage | None = None
            async with self._child_lock:
                self._current_child = None
                try:
                    nxt = self._mailbox_queue.get_nowait()
                except asyncio.QueueEmpty:
                    nxt = None
                if nxt is not None:
                    self._current_child = _child_from_assign(nxt)
            if nxt is not None:
                asyncio.create_task(self._run_child())

    async def _report_result(self, parent_mailbox_url: str, result: SubtaskResultMessage) -> None:
        last: Exception | None = None
        for attempt in range(REPORT_ATTEMPTS):
            try:
                await self.mail.report(parent_mailbox_url, result)
                return
            except Exception as exc:  # noqa: BLE001 — retry at least once
                last = exc
                log.warning(
                    "subtask_result report failed (attempt %s/%s): %s",
                    attempt + 1,
                    REPORT_ATTEMPTS,
                    exc,
                )
                if attempt + 1 < REPORT_ATTEMPTS:
                    await asyncio.sleep(REPORT_RETRY_DELAY)
        log.error("subtask_result report exhausted retries: %s", last)

    async def _accept_result(self, message: SubtaskResultMessage) -> None:
        job = await self.jobs.get(message.job_id)
        if job is None:
            raise KeyError(f"job memory not on this agent: {message.job_id}")
        await self.jobs.update_subtask(
            message.job_id,
            message.subtask_id,
            status=message.status,
            result=message.result,
            error=message.error,
            assignee_node_id=message.child_node_id,
            artifact_ids=list(message.artifact_ids),
        )

    async def _assert_can_parent(self) -> None:
        if not self.node_id:
            raise RuntimeError("agent is not registered")
        record = await self.manager.get_node(self.node_id)
        self.record = record
        if record.membership_status != MembershipStatus.APPROVED:
            raise ParentForbidden("node is not an approved group member")
        group = await self.manager.get_group(self.group)
        if group.allow_parent and record.id not in group.allow_parent and record.name not in group.allow_parent:
            raise ParentForbidden("not allowed to submit jobs as parent")

    async def _dispatch(
        self,
        job_id: str,
        subtask_id: str,
        spec: SubtaskSpec | Any,
        *,
        exclude_node_ids: set[str] | None = None,
        target_node_id: str | None = None,
    ) -> None:
        if self._current_child is not None and self._current_child.job_id == job_id:
            raise DispatchError("child cannot re-dispatch the same job")
        blocked = set(exclude_node_ids or ())
        blocked.add(self.node_id or "")
        if target_node_id:
            if target_node_id == self.node_id or target_node_id in blocked:
                raise DispatchError("refusing to dispatch to self or the source sibling")
            target = await self.manager.get_node(target_node_id)
            if target.id in blocked or target.id == self.node_id:
                raise DispatchError("refusing to dispatch to self or the source sibling")
            if target.group != self.group:
                raise DispatchError("refusing to dispatch across groups")
            if target.membership_status != MembershipStatus.APPROVED:
                raise DispatchError("target is not an approved member")
            if target.status != NodeStatus.IDLE:
                raise DispatchError("target is not idle")
        else:
            candidates = await self.manager.catalog(
                CatalogQuery(
                    group=self.group,
                    idle_only=True,
                    skills=list(spec.skills),
                    tools=list(spec.tools),
                    exclude_node_id=self.node_id,
                    model=getattr(spec, "model", None),
                    min_context_window=getattr(spec, "min_context_window", None),
                    min_memory_mb=getattr(spec, "min_memory_mb", None),
                )
            )
            candidates = [node for node in candidates if node.id not in blocked]
            if not candidates:
                raise DispatchError("no idle matching node in the same group")
            inflight = await self.jobs.inflight_counts()
            target, self._rr_index = pick_dispatch_target(candidates, inflight, self._rr_index)
            if target.id == self.node_id:
                raise DispatchError("refusing to dispatch to self")
            if target.group != self.group:
                raise DispatchError("refusing to dispatch across groups")
        await self.jobs.update_subtask(
            job_id,
            subtask_id,
            status=SubtaskStatus.ASSIGNED,
            assignee_node_id=target.id,
            assignee_mailbox_url=target.mailbox_url,
        )
        try:
            await asyncio.wait_for(
                self.mail.assign(
                    target.mailbox_url,
                    AssignSubtaskMessage(
                        job_id=job_id,
                        subtask_id=subtask_id,
                        parent_node_id=self.node_id or "",
                        parent_mailbox_url=self.mailbox_url,
                        description=spec.description,
                        skills=list(spec.skills),
                        tools=list(spec.tools),
                        payload=dict(spec.payload),
                    ),
                ),
                timeout=self.dispatch_timeout,
            )
        except TimeoutError as exc:
            await self.jobs.update_subtask(
                job_id,
                subtask_id,
                status=SubtaskStatus.FAILED,
                error="dispatch timed out",
            )
            raise DispatchError("dispatch timed out") from exc
        await self.jobs.update_subtask(job_id, subtask_id, status=SubtaskStatus.RUNNING)

    async def _heartbeat_loop(self) -> None:
        while not self._closed:
            try:
                if self.node_id:
                    await self.manager.heartbeat(
                        self.node_id,
                        HeartbeatRequest(status=self.status),
                    )
            except Exception:
                log.exception("heartbeat failed")
            await asyncio.sleep(self.heartbeat_interval)


NodeRuntime = AgentRuntime


class HostRuntime:
    """One process that registers one or more agents, each with its own mailbox and busy bit."""

    def __init__(
        self,
        manager_url: str,
        group: str,
        advertise: str,
        specs: list[AgentSpec],
        *,
        heartbeat_interval: float = 5.0,
        artifact_store: ArtifactStore | None = None,
        executor: Executor | None = None,
        planner: TaskPlanner | None = None,
        job_db: str | None = None,
        mailbox_queue_size: int = DEFAULT_MAILBOX_QUEUE,
    ) -> None:
        if not specs:
            raise ValueError("host needs at least one agent")
        self.manager_url = manager_url
        self.group = group
        self.advertise = advertise.rstrip("/")
        self.heartbeat_interval = heartbeat_interval
        self.manager = ManagerClient(manager_url)
        self.mail = MailboxClient()
        self.artifacts: ArtifactStore = artifact_store or MemoryArtifactStore()
        self.agents: dict[str, AgentRuntime] = {}
        self._ordered: list[AgentRuntime] = []
        default_executor = executor or EchoExecutor()
        job_base = job_db if job_db is not None else os.environ.get("MYCOAGENT_JOB_DB")
        shared_jobs = len(specs) == 1
        for spec in specs:
            agent_id = spec.agent_id or str(uuid4())
            mailbox = (
                self.advertise
                if spec.root_mailbox
                else f"{self.advertise}/agents/{agent_id}"
            )
            agent = AgentRuntime(
                manager_url=manager_url,
                name=spec.name,
                group=group,
                mailbox_url=mailbox,
                skills=spec.skills,
                tools_declared=spec.tools,
                tools_available=spec.tools,
                models=spec.models,
                node_id=agent_id,
                heartbeat_interval=heartbeat_interval,
                manager=self.manager,
                mail=self.mail,
                artifact_store=self.artifacts,
                executor=spec.executor or default_executor,
                planner=spec.planner or planner,
                job_db=_job_db_path(job_base, agent_id, shared=shared_jobs),
                mailbox_queue_size=mailbox_queue_size,
            )
            self._ordered.append(agent)

    @property
    def default_agent(self) -> AgentRuntime:
        if self.agents:
            return next(iter(self.agents.values()))
        return self._ordered[0]

    @property
    def root_alias(self) -> bool:
        return len(self._ordered) == 1 and self._ordered[0].mailbox_url == self.advertise

    def require(self, agent_id: str) -> AgentRuntime:
        agent = self.agents.get(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        return agent

    async def start(self) -> list[NodeRecord]:
        records: list[NodeRecord] = []
        for agent in self._ordered:
            record = await agent.start()
            self.agents[record.id] = agent
            records.append(record)
        return records

    async def close(self) -> None:
        for agent in self._ordered:
            await agent.close()
        await self.manager.aclose()
        await self.mail.aclose()


def _child_from_assign(message: AssignSubtaskMessage) -> ChildWork:
    return ChildWork(
        job_id=message.job_id,
        subtask_id=message.subtask_id,
        parent_node_id=message.parent_node_id,
        parent_mailbox_url=message.parent_mailbox_url,
        description=message.description,
        payload=message.payload,
        status=SubtaskStatus.RUNNING,
    )


def _find_subtask(job: JobMemory, subtask_id: str) -> SubtaskRecord | None:
    for item in job.subtasks:
        if item.id == subtask_id:
            return item
    return None


def runtime_from_env(
    *,
    manager_url: str,
    name: str,
    group: str,
    mailbox_url: str,
    skills: str | None = None,
    tools: str | None = None,
    models: str | None = None,
) -> NodeRuntime:
    declared = parse_csv(tools)
    return NodeRuntime(
        manager_url=manager_url,
        name=name,
        group=group,
        mailbox_url=mailbox_url,
        skills=parse_csv(skills),
        tools_declared=declared,
        tools_available=declared,
        models=parse_models(models),
    )
