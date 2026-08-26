"""Optional shared Bearer token and dispatch assign-failure compensation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from cluster_harness import manager_server, node_server
from mycoagent.auth import bearer_headers
from mycoagent.models import (
    AssignSubtaskMessage,
    CatalogQuery,
    GroupInfo,
    JoinMode,
    MachineSpec,
    MembershipStatus,
    NodeRecord,
    NodeStatus,
    SubtaskSpec,
    SubtaskStatus,
    SystemSpec,
)
from mycoagent.node.client import ManagerClient, MailboxClient
from mycoagent.node.runtime import AgentRuntime, DispatchError


def test_manager_token_required_except_health(tmp_path):
    secret = "shared-secret"
    with manager_server(tmp_path, token=secret) as manager:
        assert httpx.get(f"{manager}/health", timeout=5).status_code == 200
        denied = httpx.get(f"{manager}/groups", timeout=5)
        assert denied.status_code == 401
        wrong = httpx.get(
            f"{manager}/groups",
            headers=bearer_headers("nope"),
            timeout=5,
        )
        assert wrong.status_code == 401
        ok = httpx.get(
            f"{manager}/groups",
            headers=bearer_headers(secret),
            timeout=5,
        )
        assert ok.status_code == 200
        assert any(g["name"] == "default" for g in ok.json())


def test_manager_open_without_token(tmp_path):
    with manager_server(tmp_path) as manager:
        assert httpx.get(f"{manager}/groups", timeout=5).status_code == 200


def test_host_mailbox_and_jobs_require_token(tmp_path):
    secret = "host-secret"
    with manager_server(tmp_path, token=secret) as manager:
        with node_server(manager, "worker", "default", token=secret) as (url, _):
            assert httpx.get(f"{url}/health", timeout=5).status_code == 200
            bare = httpx.post(
                f"{url}/mailbox",
                json={"type": "assign_subtask", "body": {}},
                timeout=5,
            )
            assert bare.status_code == 401
            bare_job = httpx.post(
                f"{url}/jobs",
                json={"description": "x", "subtasks": []},
                timeout=5,
            )
            assert bare_job.status_code == 401
            authed = httpx.post(
                f"{url}/jobs",
                json={"description": "only me", "subtasks": []},
                headers=bearer_headers(secret),
                timeout=10,
            )
            assert authed.status_code == 200, authed.text
            assert authed.json()["status"] == "completed"


def test_clients_send_bearer(tmp_path):
    secret = "client-secret"

    async def _run() -> None:
        with manager_server(tmp_path, token=secret) as manager:
            client = ManagerClient(manager, token=secret)
            try:
                groups = await client.list_groups()
                assert any(g.name == "default" for g in groups)
            finally:
                await client.aclose()
            with node_server(manager, "child", "default", token=secret) as (url, _runtime):
                mail = MailboxClient(token=secret)
                try:
                    await mail.assign(
                        url,
                        AssignSubtaskMessage(
                            job_id="j",
                            subtask_id="s",
                            parent_node_id="parent",
                            parent_mailbox_url="http://127.0.0.1:1",
                            description="work",
                            skills=["coding"],
                            tools=["shell"],
                            payload={},
                        ),
                    )
                finally:
                    await mail.aclose()

    asyncio.run(_run())


def _node(node_id: str, name: str, mailbox: str) -> NodeRecord:
    now = datetime.now(timezone.utc)
    return NodeRecord(
        id=node_id,
        name=name,
        group="default",
        mailbox_url=mailbox,
        machine=MachineSpec(cpu_cores=1, memory_mb=512),
        system=SystemSpec(os="linux", arch="x64"),
        models=[],
        skills=["coding"],
        tools_declared=["shell"],
        tools_available=["shell"],
        status=NodeStatus.IDLE,
        last_seen=now,
        created_at=now,
        membership_status=MembershipStatus.APPROVED,
    )


class _BrokenAssignMail:
    async def assign(self, mailbox_url: str, message: AssignSubtaskMessage) -> None:
        del mailbox_url, message
        raise httpx.ConnectError("child unreachable")

    async def report(self, parent_mailbox_url: str, message) -> None:
        del parent_mailbox_url, message

    async def aclose(self) -> None:
        return None


class _StubManager:
    def __init__(self, parent_id: str, child: NodeRecord) -> None:
        self.parent_id = parent_id
        self.child = child

    async def get_node(self, node_id: str) -> NodeRecord:
        if node_id == self.parent_id:
            return _node(self.parent_id, "parent", "http://127.0.0.1:9")
        return self.child

    async def get_group(self, name: str) -> GroupInfo:
        return GroupInfo(
            name=name,
            created_at=datetime.now(timezone.utc),
            description="",
            join_mode=JoinMode.AUTO,
        )

    async def catalog(self, query: CatalogQuery) -> list[NodeRecord]:
        del query
        return [self.child]

    async def aclose(self) -> None:
        return None


def test_dispatch_assign_failure_marks_failed_not_assigned():
    async def _run() -> None:
        child = _node("child-1", "child", "http://127.0.0.1:59999")
        runtime = AgentRuntime(
            manager_url="http://127.0.0.1:1",
            name="parent",
            group="default",
            mailbox_url="http://127.0.0.1:9",
            manager=_StubManager("parent-1", child),  # type: ignore[arg-type]
            mail=_BrokenAssignMail(),  # type: ignore[arg-type]
        )
        runtime.node_id = "parent-1"
        runtime._own_manager = False
        runtime._own_mail = False
        job = await runtime.jobs.create(
            "parent-1",
            "needs child",
            [SubtaskSpec(description="work", skills=["coding"], tools=["shell"])],
        )
        sub = job.subtasks[0]
        try:
            await runtime._dispatch(job.job_id, sub.id, sub)
            raise AssertionError("expected DispatchError")
        except DispatchError as exc:
            assert "dispatch failed" in str(exc)
        refreshed = await runtime.jobs.get(job.job_id)
        assert refreshed is not None
        assert refreshed.subtasks[0].status == SubtaskStatus.FAILED
        assert refreshed.subtasks[0].status != SubtaskStatus.ASSIGNED
        assert "unreachable" in (refreshed.subtasks[0].error or "")
        runtime.jobs.close()

    asyncio.run(_run())


def test_dispatch_http_error_marks_failed(tmp_path):
    """Integration: assign non-2xx → subtask failed, not stuck assigned."""

    class DyingMail:
        def __init__(self, real: MailboxClient) -> None:
            self._real = real

        async def assign(self, mailbox_url: str, message: AssignSubtaskMessage) -> None:
            del mailbox_url, message
            request = httpx.Request("POST", "http://127.0.0.1/mailbox")
            raise httpx.HTTPStatusError(
                "server error",
                request=request,
                response=httpx.Response(503, request=request),
            )

        async def report(self, parent_mailbox_url: str, message) -> None:
            await self._real.report(parent_mailbox_url, message)

        async def aclose(self) -> None:
            await self._real.aclose()

    with manager_server(tmp_path) as manager:
        with node_server(manager, "parent", "default") as (parent_url, parent):
            with node_server(manager, "child", "default") as (_child_url, _child):
                parent.mail = DyingMail(parent.mail)  # type: ignore[assignment]
                submitted = httpx.post(
                    f"{parent_url}/jobs",
                    json={
                        "description": "boom",
                        "subtasks": [
                            {"description": "x", "skills": ["coding"], "tools": ["shell"]}
                        ],
                    },
                    timeout=10,
                )
                assert submitted.status_code == 200, submitted.text
                job = submitted.json()
                assert job["status"] == "failed"
                assert job["subtasks"][0]["status"] == "failed"
                assert job["subtasks"][0]["status"] != "assigned"
                assert "dispatch failed" in (job["subtasks"][0]["error"] or "")
