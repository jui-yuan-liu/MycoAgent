from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from mycoagent.manager.api import create_app
from mycoagent.manager.store import ManagerStore
from mycoagent.models import (
    HeartbeatRequest,
    MachineSpec,
    NodeRegisterRequest,
    NodeStatus,
    SystemSpec,
)


def _client(tmp_path, timeout: int = 15) -> TestClient:
    store = ManagerStore(str(tmp_path / "m.db"), heartbeat_timeout_seconds=timeout)
    return TestClient(create_app(store, bootstrap_group="default"))


def _register(name: str = "n1", group: str = "default", tools_available: list[str] | None = None) -> NodeRegisterRequest:
    return NodeRegisterRequest(
        name=name,
        group=group,
        mailbox_url="http://127.0.0.1:9000",
        machine=MachineSpec(cpu_cores=4, memory_mb=8192),
        system=SystemSpec(os="darwin", arch="arm64"),
        skills=["coding"],
        tools_declared=["shell", "browser"],
        tools_available=tools_available if tools_available is not None else ["shell"],
    )


def test_bootstrap_and_list_groups(tmp_path):
    client = _client(tmp_path)
    groups = client.get("/groups").json()
    assert any(g["name"] == "default" for g in groups)


def test_create_group_conflict(tmp_path):
    client = _client(tmp_path)
    assert client.post("/groups", json={"name": "dev"}).status_code == 200
    assert client.post("/groups", json={"name": "dev"}).status_code == 409


def test_register_requires_existing_group(tmp_path):
    client = _client(tmp_path)
    body = _register(group="missing")
    assert client.post("/nodes/register", json=body.model_dump(mode="json")).status_code == 404


def test_register_and_catalog_filters_available_tools(tmp_path):
    client = _client(tmp_path)
    created = client.post("/nodes/register", json=_register().model_dump(mode="json"))
    assert created.status_code == 200
    node = created.json()
    assert node["group"] == "default"
    idle = client.get("/catalog", params={"group": "default", "idle_only": True})
    assert len(idle.json()) == 1
    with_browser = client.get(
        "/catalog", params=[("group", "default"), ("tools", "browser")]
    )
    assert with_browser.json() == []
    with_shell = client.get("/catalog", params=[("group", "default"), ("tools", "shell")])
    assert len(with_shell.json()) == 1


def test_heartbeat_refresh_makes_tool_available(tmp_path):
    client = _client(tmp_path)
    node = client.post("/nodes/register", json=_register().model_dump(mode="json")).json()
    client.post(
        f"/nodes/{node['id']}/heartbeat",
        json=HeartbeatRequest(status=NodeStatus.IDLE, tools_available=["shell", "browser"]).model_dump(
            mode="json"
        ),
    )
    found = client.get("/catalog", params=[("group", "default"), ("tools", "browser")])
    assert len(found.json()) == 1


def test_exclude_node_from_catalog(tmp_path):
    client = _client(tmp_path)
    a = client.post("/nodes/register", json=_register(name="a").model_dump(mode="json")).json()
    client.post("/nodes/register", json=_register(name="b").model_dump(mode="json"))
    catalog = client.get(
        "/catalog",
        params={"group": "default", "exclude_node_id": a["id"]},
    ).json()
    assert [n["name"] for n in catalog] == ["b"]


def test_heartbeat_timeout_marks_offline(tmp_path):
    store = ManagerStore(str(tmp_path / "m.db"), heartbeat_timeout_seconds=1)
    client = TestClient(create_app(store, bootstrap_group="default"))
    node = client.post("/nodes/register", json=_register().model_dump(mode="json")).json()
    store._conn.execute(
        "UPDATE nodes SET last_seen=? WHERE id=?",
        ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), node["id"]),
    )
    store._conn.commit()
    listed = client.get("/nodes/" + node["id"]).json()
    assert listed["status"] == "offline"
    idle = client.get("/catalog", params={"group": "default", "idle_only": True}).json()
    assert idle == []
