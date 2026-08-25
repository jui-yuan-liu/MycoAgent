from mycoagent.manager.store import ManagerStore, catalog_matches, filter_catalog_nodes
from mycoagent.models import (
    CatalogQuery,
    MachineSpec,
    ModelSpec,
    NodeRegisterRequest,
    NodeStatus,
    SystemSpec,
)
from mycoagent.node.runtime import pick_dispatch_target


def _register(
    name: str = "n1",
    *,
    memory_mb: int = 1024,
    models: list[ModelSpec] | None = None,
    skills: list[str] | None = None,
) -> NodeRegisterRequest:
    return NodeRegisterRequest(
        name=name,
        group="default",
        mailbox_url="http://127.0.0.1:9000",
        machine=MachineSpec(cpu_cores=2, memory_mb=memory_mb),
        system=SystemSpec(os="darwin", arch="arm64"),
        models=models or [],
        skills=skills or ["coding"],
        tools_declared=["shell"],
        tools_available=["shell"],
    )


def test_catalog_filters_model_context_and_memory(tmp_path):
    store = ManagerStore(str(tmp_path / "m.db"))
    store.create_group("default")
    small = store.register_node(
        _register(
            "small",
            memory_mb=512,
            models=[ModelSpec(name="tiny", source="local", context_window=2048)],
        )
    )
    large = store.register_node(
        _register(
            "large",
            memory_mb=8192,
            models=[ModelSpec(name="llama3", source="local", context_window=8192)],
        )
    )
    by_model = store.query_catalog(CatalogQuery(group="default", model="llama3"))
    assert [item.id for item in by_model] == [large.id]
    by_context = store.query_catalog(CatalogQuery(group="default", min_context_window=4096))
    assert [item.id for item in by_context] == [large.id]
    by_ram = store.query_catalog(CatalogQuery(group="default", min_memory_mb=4096))
    assert [item.id for item in by_ram] == [large.id]
    combo = store.query_catalog(
        CatalogQuery(group="default", model="llama3", min_context_window=8000, min_memory_mb=1024)
    )
    assert [item.id for item in combo] == [large.id]
    none = store.query_catalog(CatalogQuery(group="default", model="llama3", min_context_window=32000))
    assert none == []
    missing = store.query_catalog(CatalogQuery(group="default", model="missing"))
    assert missing == []
    both = store.query_catalog(CatalogQuery(group="default"))
    assert {item.id for item in both} == {small.id, large.id}
    store.close()


def test_catalog_match_helper_used_by_both_stores():
    from datetime import datetime, timezone

    from mycoagent.models import MembershipStatus, NodeRecord

    now = datetime.now(timezone.utc)
    node = NodeRecord(
        id="n1",
        name="gpu",
        group="default",
        mailbox_url="http://x",
        machine=MachineSpec(cpu_cores=2, memory_mb=2048),
        system=SystemSpec(os="linux", arch="x64"),
        models=[ModelSpec(name="gpt-4", source="api", context_window=128000)],
        skills=["coding"],
        tools_declared=["shell"],
        tools_available=["shell"],
        status=NodeStatus.IDLE,
        last_seen=now,
        created_at=now,
        membership_status=MembershipStatus.APPROVED,
    )
    assert catalog_matches(node, CatalogQuery(group="default", model="gpt-4", min_context_window=8000))
    assert not catalog_matches(node, CatalogQuery(group="default", min_memory_mb=4096))
    assert filter_catalog_nodes([node], CatalogQuery(group="default", min_memory_mb=1024)) == [node]


def test_pick_dispatch_target_least_inflight_then_round_robin():
    from datetime import datetime, timezone

    from mycoagent.models import MembershipStatus, NodeRecord

    now = datetime.now(timezone.utc)

    def node(node_id: str, name: str) -> NodeRecord:
        return NodeRecord(
            id=node_id,
            name=name,
            group="default",
            mailbox_url=f"http://{name}",
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

    aaa = node("id-aaa", "aaa")
    zzz = node("id-zzz", "zzz")
    # name-first would always pick aaa; least in-flight prefers the free one
    chosen, cursor = pick_dispatch_target([aaa, zzz], {"id-aaa": 1, "id-zzz": 0}, 0)
    assert chosen.id == "id-zzz"
    first, cursor = pick_dispatch_target([aaa, zzz], {}, 0)
    second, _ = pick_dispatch_target([aaa, zzz], {}, cursor)
    assert {first.id, second.id} == {"id-aaa", "id-zzz"}
    assert first.id != second.id
