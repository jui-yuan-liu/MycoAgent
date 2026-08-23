"""Shared helpers for spinning HTTP servers in tests."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import uvicorn

from mycoagent.manager.api import create_app
from mycoagent.manager.store import ManagerStore
from mycoagent.node.api import create_node_app
from mycoagent.node.runtime import NodeRuntime


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
def manager_server(tmp_path, bootstrap_group: str = "default") -> Iterator[str]:
    store = ManagerStore(str(tmp_path / "mgr.db"), heartbeat_timeout_seconds=2)
    app = create_app(store, bootstrap_group=bootstrap_group)
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
        heartbeat_interval=heartbeat_interval,
    )
    app = create_node_app(runtime)
    with run_app(app, port=port) as url:
        yield url, runtime
