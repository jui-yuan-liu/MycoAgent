import os

import pytest

from mycoagent.manager.store import ManagerStore, ManagerStoreProtocol, open_store
from mycoagent.models import (
    CatalogQuery,
    HeartbeatRequest,
    MachineSpec,
    NodeRegisterRequest,
    NodeStatus,
    SystemSpec,
)


def _register(name: str = "n1") -> NodeRegisterRequest:
    return NodeRegisterRequest(
        name=name,
        group="default",
        mailbox_url="http://127.0.0.1:9000",
        machine=MachineSpec(cpu_cores=2, memory_mb=1024),
        system=SystemSpec(os="darwin", arch="arm64"),
        skills=["coding"],
        tools_declared=["shell"],
        tools_available=["shell"],
    )


def test_sqlite_store_satisfies_protocol(tmp_path):
    store = ManagerStore(str(tmp_path / "m.db"), heartbeat_timeout_seconds=15)
    assert isinstance(store, ManagerStoreProtocol)
    store.create_group("default")
    node = store.register_node(_register())
    store.heartbeat(node.id, HeartbeatRequest(status=NodeStatus.IDLE))
    catalog = store.query_catalog(CatalogQuery(group="default"))
    assert [item.id for item in catalog] == [node.id]
    via_factory = open_store(str(tmp_path / "other.db"))
    assert isinstance(via_factory, ManagerStoreProtocol)
    store.close()
    via_factory.close()


@pytest.mark.skipif(
    not os.environ.get("MYCOAGENT_POSTGRES_DSN"),
    reason="set MYCOAGENT_POSTGRES_DSN to run Postgres store tests",
)
def test_postgres_store_satisfies_protocol():
    store = open_store(os.environ["MYCOAGENT_POSTGRES_DSN"], heartbeat_timeout_seconds=15)
    assert isinstance(store, ManagerStoreProtocol)
    try:
        store.create_group("default")
    except ValueError:
        pass
    node = store.register_node(_register("pg-n1"))
    store.heartbeat(node.id, HeartbeatRequest(status=NodeStatus.IDLE))
    catalog = store.query_catalog(CatalogQuery(group="default", idle_only=True))
    assert any(item.id == node.id for item in catalog)
    store.close()
