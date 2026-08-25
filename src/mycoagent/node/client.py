from __future__ import annotations

from typing import Any

import httpx

from mycoagent.models import (
    AssignSubtaskMessage,
    CatalogQuery,
    GroupCreate,
    GroupInfo,
    GroupPolicyUpdate,
    HeartbeatRequest,
    JoinMode,
    NodeRecord,
    NodeRegisterRequest,
    SubtaskResultMessage,
)


class ManagerClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def health(self) -> dict[str, Any]:
        response = await self._http.get("/health")
        response.raise_for_status()
        return response.json()

    async def create_group(
        self,
        name: str,
        *,
        description: str = "",
        join_mode: JoinMode | str = JoinMode.AUTO,
        allow_register: list[str] | None = None,
        allow_parent: list[str] | None = None,
    ) -> GroupInfo:
        body = GroupCreate(
            name=name,
            description=description,
            join_mode=JoinMode(join_mode),
            allow_register=allow_register or [],
            allow_parent=allow_parent or [],
        )
        response = await self._http.post("/groups", json=body.model_dump(mode="json"))
        response.raise_for_status()
        return GroupInfo.model_validate(response.json())

    async def update_group(self, name: str, patch: GroupPolicyUpdate) -> GroupInfo:
        response = await self._http.patch(
            f"/groups/{name}", json=patch.model_dump(mode="json", exclude_none=True)
        )
        response.raise_for_status()
        return GroupInfo.model_validate(response.json())

    async def approve(self, group: str, node_id: str) -> NodeRecord:
        response = await self._http.post(f"/groups/{group}/approve/{node_id}")
        response.raise_for_status()
        return NodeRecord.model_validate(response.json())

    async def deny(self, group: str, node_id: str) -> NodeRecord:
        response = await self._http.post(f"/groups/{group}/deny/{node_id}")
        response.raise_for_status()
        return NodeRecord.model_validate(response.json())

    async def get_node(self, node_id: str) -> NodeRecord:
        response = await self._http.get(f"/nodes/{node_id}")
        response.raise_for_status()
        return NodeRecord.model_validate(response.json())

    async def list_groups(self) -> list[GroupInfo]:
        response = await self._http.get("/groups")
        response.raise_for_status()
        return [GroupInfo.model_validate(item) for item in response.json()]

    async def get_group(self, name: str) -> GroupInfo:
        response = await self._http.get(f"/groups/{name}")
        response.raise_for_status()
        return GroupInfo.model_validate(response.json())

    async def delete_group(self, name: str) -> None:
        response = await self._http.delete(f"/groups/{name}")
        response.raise_for_status()

    async def register(self, req: NodeRegisterRequest) -> NodeRecord:
        response = await self._http.post("/nodes/register", json=req.model_dump(mode="json"))
        response.raise_for_status()
        return NodeRecord.model_validate(response.json())

    async def heartbeat(self, node_id: str, req: HeartbeatRequest) -> NodeRecord:
        response = await self._http.post(
            f"/nodes/{node_id}/heartbeat", json=req.model_dump(mode="json")
        )
        response.raise_for_status()
        return NodeRecord.model_validate(response.json())

    async def catalog(self, query: CatalogQuery) -> list[NodeRecord]:
        params: list[tuple[str, str]] = [
            ("group", query.group),
            ("idle_only", str(query.idle_only).lower()),
        ]
        if query.exclude_node_id:
            params.append(("exclude_node_id", query.exclude_node_id))
        if query.model:
            params.append(("model", query.model))
        if query.min_context_window is not None:
            params.append(("min_context_window", str(query.min_context_window)))
        if query.min_memory_mb is not None:
            params.append(("min_memory_mb", str(query.min_memory_mb)))
        for skill in query.skills:
            params.append(("skills", skill))
        for tool in query.tools:
            params.append(("tools", tool))
        response = await self._http.get("/catalog", params=params)
        response.raise_for_status()
        return [NodeRecord.model_validate(item) for item in response.json()]


class MailboxClient:
    def __init__(self, timeout: float = 10.0) -> None:
        self._http = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def assign(self, mailbox_url: str, message: AssignSubtaskMessage) -> None:
        response = await self._http.post(
            f"{mailbox_url.rstrip('/')}/mailbox",
            json={"type": message.type, "body": message.model_dump(mode="json")},
        )
        response.raise_for_status()

    async def report(self, parent_mailbox_url: str, message: SubtaskResultMessage) -> None:
        response = await self._http.post(
            f"{parent_mailbox_url.rstrip('/')}/mailbox",
            json={"type": message.type, "body": message.model_dump(mode="json")},
        )
        response.raise_for_status()
