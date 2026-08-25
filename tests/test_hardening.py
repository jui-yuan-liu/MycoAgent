import asyncio
import os
import threading
import time

import httpx

from cluster_harness import manager_server, node_server, wait_job
from mycoagent.models import (
    AssignSubtaskMessage,
    ChildWork,
    Envelope,
    SubtaskResultMessage,
    SubtaskSpec,
    SubtaskStatus,
)
from mycoagent.node.identity import resolve_agent_id
from mycoagent.node.jobs import JobStore
from mycoagent.node.runtime import AgentRuntime
from mycoagent.node.specs import detect_machine, _physical_memory_mb
from mycoagent.node import specs as specs_mod


class GateExecutor:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    async def run(self, work: ChildWork, workspace=None) -> ChildWork:
        del workspace
        self.entered.set()
        while not self.release.is_set():
            await asyncio.sleep(0.05)
        return work.model_copy(
            update={"status": SubtaskStatus.COMPLETED, "result": f"done:{work.description}"}
        )


class FlakyMail:
    def __init__(self) -> None:
        self.calls = 0
        self.messages: list[SubtaskResultMessage] = []

    async def report(self, parent_mailbox_url: str, message: SubtaskResultMessage) -> None:
        del parent_mailbox_url
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("first report failed")
        self.messages.append(message)

    async def assign(self, mailbox_url: str, message: AssignSubtaskMessage) -> None:
        del mailbox_url, message

    async def aclose(self) -> None:
        return None


def test_resolve_agent_id_persists_across_calls(tmp_path, monkeypatch):
    monkeypatch.delenv("MYCOAGENT_AGENT_ID", raising=False)
    path = tmp_path / "agent.id"
    first = resolve_agent_id(id_file=path)
    second = resolve_agent_id(id_file=path)
    assert first == second
    assert path.read_text(encoding="utf-8").strip() == first
    monkeypatch.setenv("MYCOAGENT_AGENT_ID", "env-fixed-id")
    assert resolve_agent_id() == "env-fixed-id"
    asserted = resolve_agent_id(explicit="explicit-id", id_file=tmp_path / "other.id")
    assert asserted == "explicit-id"


def test_jobstore_sqlite_roundtrip(tmp_path):
    path = str(tmp_path / "jobs.db")

    async def _run() -> str:
        store = JobStore(path=path)
        job = await store.create("parent", "keep me", [SubtaskSpec(description="one")])
        job_id = job.job_id
        store.close()
        reloaded = JobStore(path=path)
        got = await reloaded.get(job_id)
        reloaded.close()
        assert got is not None
        assert got.description == "keep me"
        assert got.subtasks[0].description == "one"
        return job_id

    asyncio.run(_run())


def test_report_retries_once():
    async def _run() -> None:
        mail = FlakyMail()
        runtime = AgentRuntime(
            manager_url="http://127.0.0.1:1",
            name="child",
            group="default",
            mailbox_url="http://127.0.0.1:9",
            mail=mail,
        )
        runtime.node_id = "child-1"
        runtime._current_child = ChildWork(
            job_id="j",
            subtask_id="s",
            parent_node_id="p",
            parent_mailbox_url="http://parent",
            description="work",
            payload={},
            status=SubtaskStatus.RUNNING,
        )
        await runtime._run_child()
        assert mail.calls == 2
        assert mail.messages[0].result and mail.messages[0].result.startswith("done:")
        await runtime.close()

    asyncio.run(_run())


def test_mailbox_queues_second_assign_instead_of_409(tmp_path):
    gate = GateExecutor()
    with manager_server(tmp_path) as manager:
        with node_server(manager, "worker", "default", executor=gate, mailbox_queue_size=4) as (
            url,
            runtime,
        ):
            first = httpx.post(
                f"{url}/mailbox",
                json=_assign_envelope("job-a", "sub-a", "parent"),
                timeout=5,
            )
            assert first.status_code == 200, first.text
            assert gate.entered.wait(timeout=3)
            second = httpx.post(
                f"{url}/mailbox",
                json=_assign_envelope("job-b", "sub-b", "parent"),
                timeout=5,
            )
            assert second.status_code == 200, second.text
            assert runtime.status.value == "busy"
            gate.release.set()
            deadline = time.time() + 5
            while time.time() < deadline:
                child = httpx.get(f"{url}/child", timeout=5).json()
                if child["current"] is None:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("queued work did not drain")
            assert httpx.get(f"{url}/child", timeout=5).json()["current"] is None


def test_mailbox_queue_full_still_409(tmp_path):
    gate = GateExecutor()
    with manager_server(tmp_path) as manager:
        with node_server(manager, "worker", "default", executor=gate, mailbox_queue_size=1) as (url, _):
            assert httpx.post(f"{url}/mailbox", json=_assign_envelope("j1", "s1", "p"), timeout=5).status_code == 200
            assert gate.entered.wait(timeout=3)
            queued = httpx.post(f"{url}/mailbox", json=_assign_envelope("j2", "s2", "p"), timeout=5)
            assert queued.status_code == 200
            full = httpx.post(f"{url}/mailbox", json=_assign_envelope("j3", "s3", "p"), timeout=5)
            assert full.status_code == 409
            gate.release.set()


def test_dispatch_splits_two_subtasks_not_name_order(tmp_path):
    with manager_server(tmp_path) as manager:
        with node_server(manager, "parent", "default") as (parent_url, _parent):
            with node_server(manager, "zzz", "default") as (_z_url, zzz):
                with node_server(manager, "aaa", "default") as (_a_url, aaa):
                    submitted = httpx.post(
                        f"{parent_url}/jobs",
                        json={
                            "description": "two children",
                            "subtasks": [
                                {"description": "one", "skills": ["coding"], "tools": ["shell"]},
                                {"description": "two", "skills": ["coding"], "tools": ["shell"]},
                            ],
                        },
                        timeout=10,
                    )
                    assert submitted.status_code == 200, submitted.text
                    job = wait_job(parent_url, submitted.json()["job_id"], timeout=8)
                    assert job["status"] == "completed"
                    assignees = {item["assignee_node_id"] for item in job["subtasks"]}
                    assert assignees == {zzz.node_id, aaa.node_id}


def test_detect_machine_windows_fallback(monkeypatch):
    def boom(_name: str) -> int:
        raise AttributeError("sysconf missing")

    monkeypatch.setattr(os, "sysconf", boom)
    monkeypatch.setattr(specs_mod, "_windows_memory_mb", lambda: 16384)
    monkeypatch.setattr(specs_mod, "_proc_meminfo_mb", lambda: 0)
    spec = detect_machine()
    assert spec.memory_mb == 16384
    assert _physical_memory_mb() == 16384


def _assign_envelope(job_id: str, subtask_id: str, parent: str) -> dict:
    message = AssignSubtaskMessage(
        job_id=job_id,
        subtask_id=subtask_id,
        parent_node_id=parent,
        parent_mailbox_url="http://127.0.0.1:1",
        description=subtask_id,
        skills=["coding"],
        tools=["shell"],
        payload={},
    )
    return Envelope(type="assign_subtask", body=message.model_dump(mode="json")).model_dump(mode="json")
