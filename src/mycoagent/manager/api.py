from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from mycoagent.manager.store import (
    GroupNotFound,
    ManagerStore,
    MembershipError,
    NodeNotFound,
    RegisterForbidden,
)
from mycoagent.models import (
    CatalogQuery,
    GroupCreate,
    GroupInfo,
    GroupPolicyUpdate,
    HeartbeatRequest,
    MembershipStatus,
    NodeRecord,
    NodeRegisterRequest,
)
from mycoagent.version import __version__


def create_app(store: ManagerStore, bootstrap_group: str | None = None) -> FastAPI:
    if bootstrap_group:
        try:
            store.create_group(bootstrap_group)
        except ValueError:
            pass
    app = FastAPI(title="MycoAgent Cluster Manager", version=__version__)
    app.state.store = store

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "role": "cluster-manager"}

    @app.post("/groups", response_model=GroupInfo)
    def create_group(body: GroupCreate) -> GroupInfo:
        try:
            return store.create_group(
                body.name,
                description=body.description,
                join_mode=body.join_mode,
                allow_register=body.allow_register,
                allow_parent=body.allow_parent,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/groups", response_model=list[GroupInfo])
    def list_groups() -> list[GroupInfo]:
        return store.list_groups()

    @app.get("/groups/{name}", response_model=GroupInfo)
    def get_group(name: str) -> GroupInfo:
        try:
            return store.get_group(name)
        except GroupNotFound as exc:
            raise HTTPException(status_code=404, detail=f"group not found: {name}") from exc

    @app.patch("/groups/{name}", response_model=GroupInfo)
    def update_group(name: str, body: GroupPolicyUpdate) -> GroupInfo:
        try:
            return store.update_group(name, body)
        except GroupNotFound as exc:
            raise HTTPException(status_code=404, detail=f"group not found: {name}") from exc

    @app.delete("/groups/{name}")
    def delete_group(name: str) -> dict[str, str]:
        try:
            store.delete_group(name)
        except GroupNotFound as exc:
            raise HTTPException(status_code=404, detail=f"group not found: {name}") from exc
        return {"deleted": name}

    @app.post("/groups/{name}/approve/{node_id}", response_model=NodeRecord)
    def approve_member(name: str, node_id: str) -> NodeRecord:
        try:
            return store.set_membership(name, node_id, MembershipStatus.APPROVED)
        except GroupNotFound as exc:
            raise HTTPException(status_code=404, detail=f"group not found: {name}") from exc
        except NodeNotFound as exc:
            raise HTTPException(status_code=404, detail=f"node not found: {node_id}") from exc
        except MembershipError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/groups/{name}/deny/{node_id}", response_model=NodeRecord)
    def deny_member(name: str, node_id: str) -> NodeRecord:
        try:
            return store.set_membership(name, node_id, MembershipStatus.DENIED)
        except GroupNotFound as exc:
            raise HTTPException(status_code=404, detail=f"group not found: {name}") from exc
        except NodeNotFound as exc:
            raise HTTPException(status_code=404, detail=f"node not found: {node_id}") from exc
        except MembershipError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/nodes/register", response_model=NodeRecord)
    def register(body: NodeRegisterRequest) -> NodeRecord:
        try:
            return store.register_node(body)
        except GroupNotFound as exc:
            raise HTTPException(
                status_code=404, detail=f"group not found: {body.group}"
            ) from exc
        except RegisterForbidden as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/nodes/{node_id}/heartbeat", response_model=NodeRecord)
    def heartbeat(node_id: str, body: HeartbeatRequest) -> NodeRecord:
        try:
            return store.heartbeat(node_id, body)
        except NodeNotFound as exc:
            raise HTTPException(status_code=404, detail=f"node not found: {node_id}") from exc

    @app.get("/nodes/{node_id}", response_model=NodeRecord)
    def get_node(node_id: str) -> NodeRecord:
        try:
            return store.get_node(node_id)
        except NodeNotFound as exc:
            raise HTTPException(status_code=404, detail=f"node not found: {node_id}") from exc

    @app.get("/catalog", response_model=list[NodeRecord])
    def catalog(
        group: str,
        idle_only: bool = True,
        skills: list[str] = Query(default=[]),
        tools: list[str] = Query(default=[]),
        exclude_node_id: str | None = None,
    ) -> list[NodeRecord]:
        try:
            return store.query_catalog(
                CatalogQuery(
                    group=group,
                    idle_only=idle_only,
                    skills=skills,
                    tools=tools,
                    exclude_node_id=exclude_node_id,
                )
            )
        except GroupNotFound as exc:
            raise HTTPException(status_code=404, detail=f"group not found: {group}") from exc

    return app
