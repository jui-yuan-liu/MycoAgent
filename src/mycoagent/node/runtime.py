from __future__ import annotations

import asyncio
import logging
from typing import Any

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
    NodeRecord,
    NodeRegisterRequest,
    NodeStatus,
    SubtaskRecord,
    SubtaskResultMessage,
    SubtaskSpec,
    SubtaskStatus,
)
from mycoagent.node.client import MailboxClient, ManagerClient
from mycoagent.node.executor import EchoExecutor
from mycoagent.node.jobs import JobStore
from mycoagent.node.specs import detect_machine, detect_system, parse_csv, parse_models

log = logging.getLogger("mycoagent.node")


class BusyError(RuntimeError):
    pass


class DispatchError(RuntimeError):
    pass


class ParentForbidden(PermissionError):
    pass


class NodeRuntime:
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
        self.jobs = JobStore()
        self.executor = EchoExecutor()
        self.manager = ManagerClient(manager_url)
        self.mail = MailboxClient()
        self._child_lock = asyncio.Lock()
        self._current_child: ChildWork | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def current_child(self):
        return self._current_child

    @property
    def status(self) -> NodeStatus:
        return NodeStatus.BUSY if self._current_child is not None else NodeStatus.IDLE

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
        log.info("registered node %s in group %s", self.node_id, self.group)
        return self.record

    async def close(self) -> None:
        self._closed = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await self.manager.aclose()
        await self.mail.aclose()

    async def submit_job(self, request: JobSubmitRequest) -> JobMemory:
        if not self.node_id:
            raise RuntimeError("node is not registered")
        await self._assert_can_parent()
        job = await self.jobs.create(self.node_id, request.description, request.subtasks)
        if not request.subtasks:
            local = await self.executor.run(
                ChildWork(
                    job_id=job.job_id,
                    subtask_id="local",
                    parent_node_id=self.node_id,
                    parent_mailbox_url=self.mailbox_url,
                    description=request.description,
                    payload={},
                    status=SubtaskStatus.RUNNING,
                )
            )
            return await self.jobs.complete_local(job.job_id, local.result or "")
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

    async def forward_subtask(self, job_id: str, request: ForwardRequest) -> JobMemory:
        if not self.node_id:
            raise RuntimeError("node is not registered")
        if self._current_child is not None and self._current_child.job_id == job_id:
            raise DispatchError("child cannot re-dispatch the same job")
        job = await self.jobs.get(job_id)
        if job is None:
            raise KeyError(f"job memory not on this node: {job_id}")
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
        async with self._child_lock:
            if self._current_child is not None:
                raise BusyError("node is busy with another child assignment")
            self._current_child = ChildWork(
                job_id=message.job_id,
                subtask_id=message.subtask_id,
                parent_node_id=message.parent_node_id,
                parent_mailbox_url=message.parent_mailbox_url,
                description=message.description,
                payload=message.payload,
                status=SubtaskStatus.RUNNING,
            )
        asyncio.create_task(self._run_child())

    async def _run_child(self) -> None:
        work = self._current_child
        if work is None:
            return
        try:
            finished = await self.executor.run(work)
            result = SubtaskResultMessage(
                job_id=finished.job_id,
                subtask_id=finished.subtask_id,
                child_node_id=self.node_id or "",
                status=SubtaskStatus.COMPLETED,
                result=finished.result,
            )
        except Exception as exc:  # noqa: BLE001 — child must always report
            result = SubtaskResultMessage(
                job_id=work.job_id,
                subtask_id=work.subtask_id,
                child_node_id=self.node_id or "",
                status=SubtaskStatus.FAILED,
                error=str(exc),
            )
        try:
            await self.mail.report(work.parent_mailbox_url, result)
        finally:
            async with self._child_lock:
                self._current_child = None

    async def _accept_result(self, message: SubtaskResultMessage) -> None:
        job = await self.jobs.get(message.job_id)
        if job is None:
            raise KeyError(f"job memory not on this node: {message.job_id}")
        await self.jobs.update_subtask(
            message.job_id,
            message.subtask_id,
            status=message.status,
            result=message.result,
            error=message.error,
            assignee_node_id=message.child_node_id,
        )

    async def _assert_can_parent(self) -> None:
        if not self.node_id:
            raise RuntimeError("node is not registered")
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
                )
            )
            candidates = [node for node in candidates if node.id not in blocked]
            if not candidates:
                raise DispatchError("no idle matching node in the same group")
            target = candidates[0]
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
        await self.mail.assign(
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
        )
        await self.jobs.update_subtask(job_id, subtask_id, status=SubtaskStatus.RUNNING)

    async def _heartbeat_loop(self) -> None:
        while not self._closed:
            try:
                if self.node_id:
                    await self.manager.heartbeat(
                        self.node_id,
                        HeartbeatRequest(
                            status=self.status,
                            tools_available=self.tools_available,
                            models=self.models,
                        ),
                    )
            except Exception:
                log.exception("heartbeat failed")
            await asyncio.sleep(self.heartbeat_interval)


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
