"""Shared helpers for spinning HTTP servers in tests."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import httpx
import uvicorn

from mycoagent.artifacts import ArtifactStore
from mycoagent.manager.api import create_app
from mycoagent.manager.store import ManagerStore
from mycoagent.node.api import create_host_app, create_node_app
from mycoagent.node.executor import Executor
from mycoagent.node.planner import TaskPlanner
from mycoagent.node.runtime import AgentSpec, HostRuntime, NodeRuntime


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def run_app(app, host: str = "127.0.0.1", port: int | None = None) -> Iterator[str]:
    port = port or free_port()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://{host}:{port}"
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            httpx.get(f"{base}/health", timeout=0.2)
            break
        except Exception:
            time.sleep(0.05)
    else:
        raise RuntimeError("server failed to start")
    try:
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=3)


@contextmanager
def manager_server(
    tmp_path,
    bootstrap_group: str = "default",
    *,
    token: str | None = None,
) -> Iterator[str]:
    store = ManagerStore(str(tmp_path / "mgr.db"), heartbeat_timeout_seconds=2)
    app = create_app(store, bootstrap_group=bootstrap_group, token=token)
    with run_app(app) as url:
        yield url


@contextmanager
def node_server(
    manager_url: str,
    name: str,
    group: str,
    skills: list[str] | None = None,
    tools: list[str] | None = None,
    heartbeat_interval: float = 0.4,
    *,
    executor: Executor | None = None,
    artifact_store: ArtifactStore | None = None,
    planner: TaskPlanner | None = None,
    node_id: str | None = None,
    models: list | None = None,
    mailbox_queue_size: int = 8,
    job_db: str | None = None,
    token: str | None = None,
) -> Iterator[tuple[str, NodeRuntime]]:
    port = free_port()
    mailbox = f"http://127.0.0.1:{port}"
    runtime = NodeRuntime(
        manager_url=manager_url,
        name=name,
        group=group,
        mailbox_url=mailbox,
        skills=skills or ["coding"],
        tools_declared=tools or ["shell"],
        tools_available=tools or ["shell"],
        models=models or [],
        node_id=node_id,
        heartbeat_interval=heartbeat_interval,
        executor=executor,
        artifact_store=artifact_store,
        planner=planner,
        mailbox_queue_size=mailbox_queue_size,
        job_db=job_db,
        token=token,
    )
    app = create_node_app(runtime, token=token)
    with run_app(app, port=port) as url:
        yield url, runtime


@contextmanager
def host_server(
    manager_url: str,
    group: str,
    agents: Sequence[dict[str, object]],
    heartbeat_interval: float = 0.4,
    *,
    executor: Executor | None = None,
    artifact_store: ArtifactStore | None = None,
    planner: TaskPlanner | None = None,
    token: str | None = None,
) -> Iterator[tuple[str, HostRuntime]]:
    port = free_port()
    advertise = f"http://127.0.0.1:{port}"
    specs = [
        AgentSpec(
            name=str(item["name"]),
            skills=list(item.get("skills") or ["coding"]),  # type: ignore[arg-type]
            tools=list(item.get("tools") or ["shell"]),  # type: ignore[arg-type]
            agent_id=str(item["agent_id"]) if item.get("agent_id") else None,
            root_mailbox=len(agents) == 1,
        )
        for item in agents
    ]
    host = HostRuntime(
        manager_url=manager_url,
        group=group,
        advertise=advertise,
        specs=specs,
        heartbeat_interval=heartbeat_interval,
        artifact_store=artifact_store,
        executor=executor,
        planner=planner,
        token=token,
    )
    app = create_host_app(host, token=token)
    with run_app(app, port=port) as url:
        yield url, host


def wait_job(node_url: str, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    current: dict | None = None
    while time.time() < deadline:
        current = httpx.get(f"{node_url}/jobs/{job_id}", timeout=5).json()
        if current["status"] in {"completed", "failed"}:
            return current
        time.sleep(0.1)
    assert current is not None
    return current
