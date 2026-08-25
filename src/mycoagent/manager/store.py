from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from mycoagent.models import (
    CatalogQuery,
    GroupInfo,
    GroupPolicyUpdate,
    HeartbeatRequest,
    JoinMode,
    MachineSpec,
    MembershipStatus,
    ModelSpec,
    NodeRecord,
    NodeRegisterRequest,
    NodeStatus,
    SystemSpec,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class GroupNotFound(LookupError):
    pass


class NodeNotFound(LookupError):
    pass


class RegisterForbidden(PermissionError):
    pass


class MembershipError(ValueError):
    pass


@runtime_checkable
class ManagerStoreProtocol(Protocol):
    """Control-plane store. C0: SQLite default, Postgres optional. Not etcd/gossip."""

    def close(self) -> None: ...

    def create_group(
        self,
        name: str,
        *,
        description: str = "",
        join_mode: JoinMode | str = JoinMode.AUTO,
        allow_register: list[str] | None = None,
        allow_parent: list[str] | None = None,
    ) -> GroupInfo: ...

    def update_group(self, name: str, patch: GroupPolicyUpdate) -> GroupInfo: ...

    def delete_group(self, name: str) -> None: ...

    def list_groups(self) -> list[GroupInfo]: ...

    def get_group(self, name: str) -> GroupInfo: ...

    def register_node(self, req: NodeRegisterRequest) -> NodeRecord: ...

    def set_membership(self, group: str, node_id: str, status: MembershipStatus) -> NodeRecord: ...

    def heartbeat(self, node_id: str, req: HeartbeatRequest) -> NodeRecord: ...

    def get_node(self, node_id: str) -> NodeRecord: ...

    def query_catalog(self, query: CatalogQuery) -> list[NodeRecord]: ...


class ManagerStore:
    def __init__(self, path: str, heartbeat_timeout_seconds: int = 15) -> None:
        self.path = path
        self.heartbeat_timeout = timedelta(seconds=heartbeat_timeout_seconds)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS groups (
                name TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                join_mode TEXT NOT NULL DEFAULT 'auto',
                allow_register_json TEXT NOT NULL DEFAULT '[]',
                allow_parent_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                mailbox_url TEXT NOT NULL,
                machine_json TEXT NOT NULL,
                system_json TEXT NOT NULL,
                models_json TEXT NOT NULL,
                skills_json TEXT NOT NULL,
                tools_declared_json TEXT NOT NULL,
                tools_available_json TEXT NOT NULL,
                status TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                created_at TEXT NOT NULL,
                membership_status TEXT NOT NULL DEFAULT 'approved',
                FOREIGN KEY (group_name) REFERENCES groups(name) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_group ON nodes(group_name);
            """
        )
        self._migrate_columns()
        self._conn.commit()

    def _migrate_columns(self) -> None:
        group_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(groups)")}
        node_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(nodes)")}
        alters = []
        if "description" not in group_cols:
            alters.append("ALTER TABLE groups ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        if "join_mode" not in group_cols:
            alters.append("ALTER TABLE groups ADD COLUMN join_mode TEXT NOT NULL DEFAULT 'auto'")
        if "allow_register_json" not in group_cols:
            alters.append("ALTER TABLE groups ADD COLUMN allow_register_json TEXT NOT NULL DEFAULT '[]'")
        if "allow_parent_json" not in group_cols:
            alters.append("ALTER TABLE groups ADD COLUMN allow_parent_json TEXT NOT NULL DEFAULT '[]'")
        if "membership_status" not in node_cols:
            alters.append(
                "ALTER TABLE nodes ADD COLUMN membership_status TEXT NOT NULL DEFAULT 'approved'"
            )
        for statement in alters:
            self._conn.execute(statement)

    def create_group(
        self,
        name: str,
        *,
        description: str = "",
        join_mode: JoinMode | str = JoinMode.AUTO,
        allow_register: list[str] | None = None,
        allow_parent: list[str] | None = None,
    ) -> GroupInfo:
        now = _utcnow().isoformat()
        mode = JoinMode(join_mode)
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO groups(
                        name, created_at, description, join_mode,
                        allow_register_json, allow_parent_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        now,
                        description,
                        mode.value,
                        json.dumps(allow_register or []),
                        json.dumps(allow_parent or []),
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"group already exists: {name}") from exc
        return self.get_group(name)

    def update_group(self, name: str, patch: GroupPolicyUpdate) -> GroupInfo:
        current = self.get_group(name)
        description = current.description if patch.description is None else patch.description
        join_mode = current.join_mode if patch.join_mode is None else patch.join_mode
        allow_register = current.allow_register if patch.allow_register is None else patch.allow_register
        allow_parent = current.allow_parent if patch.allow_parent is None else patch.allow_parent
        with self._lock:
            self._conn.execute(
                """
                UPDATE groups SET
                    description=?, join_mode=?, allow_register_json=?, allow_parent_json=?
                WHERE name=?
                """,
                (
                    description,
                    join_mode.value,
                    json.dumps(allow_register),
                    json.dumps(allow_parent),
                    name,
                ),
            )
            self._conn.commit()
        return self.get_group(name)

    def delete_group(self, name: str) -> None:
        with self._lock:
            cur = self._conn.execute("DELETE FROM groups WHERE name = ?", (name,))
            self._conn.commit()
            if cur.rowcount == 0:
                raise GroupNotFound(name)

    def list_groups(self) -> list[GroupInfo]:
        self._expire_offline()
        with self._lock:
            rows = self._conn.execute(
                "SELECT name FROM groups ORDER BY name"
            ).fetchall()
        return [self.get_group(row["name"]) for row in rows]

    def get_group(self, name: str) -> GroupInfo:
        self._expire_offline()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM groups WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                raise GroupNotFound(name)
            members = self._conn.execute(
                """
                SELECT id FROM nodes
                WHERE group_name = ? AND membership_status = ?
                ORDER BY name
                """,
                (name, MembershipStatus.APPROVED.value),
            ).fetchall()
            pending = self._conn.execute(
                """
                SELECT id FROM nodes
                WHERE group_name = ? AND membership_status = ?
                ORDER BY name
                """,
                (name, MembershipStatus.PENDING.value),
            ).fetchall()
        return GroupInfo(
            name=row["name"],
            created_at=_parse_dt(row["created_at"]),
            description=row["description"] or "",
            join_mode=JoinMode(row["join_mode"]),
            allow_register=json.loads(row["allow_register_json"]),
            allow_parent=json.loads(row["allow_parent_json"]),
            member_ids=[m["id"] for m in members],
            pending_ids=[p["id"] for p in pending],
        )

    def register_node(self, req: NodeRegisterRequest) -> NodeRecord:
        self._expire_offline()
        with self._lock:
            group = self._conn.execute(
                "SELECT * FROM groups WHERE name = ?", (req.group,)
            ).fetchone()
            if group is None:
                raise GroupNotFound(req.group)
            allow_register = json.loads(group["allow_register_json"])
            if allow_register and req.name not in allow_register:
                raise RegisterForbidden("not allowed to register in this group")
            node_id = req.node_id or str(uuid.uuid4())
            now = _utcnow().isoformat()
            existing = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
            join_mode = JoinMode(group["join_mode"])
            if existing is not None and existing["group_name"] == req.group:
                membership = existing["membership_status"]
            elif join_mode == JoinMode.MANUAL:
                membership = MembershipStatus.PENDING.value
            else:
                membership = MembershipStatus.APPROVED.value
            common = (
                req.name,
                req.group,
                req.mailbox_url,
                req.machine.model_dump_json(),
                req.system.model_dump_json(),
                json.dumps([m.model_dump() for m in req.models]),
                json.dumps(req.skills),
                json.dumps(req.tools_declared),
                json.dumps(list(req.tools_available)),
                NodeStatus.IDLE.value,
                now,
                membership,
            )
            if existing:
                self._conn.execute(
                    """
                    UPDATE nodes SET
                        name=?, group_name=?, mailbox_url=?, machine_json=?,
                        system_json=?, models_json=?, skills_json=?,
                        tools_declared_json=?, tools_available_json=?,
                        status=?, last_seen=?, membership_status=?
                    WHERE id=?
                    """,
                    common + (node_id,),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO nodes (
                        id, name, group_name, mailbox_url, machine_json,
                        system_json, models_json, skills_json,
                        tools_declared_json, tools_available_json,
                        status, last_seen, created_at, membership_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (node_id, *common[:11], now, membership),
                )
            self._conn.commit()
        return self.get_node(node_id)

    def set_membership(
        self, group: str, node_id: str, status: MembershipStatus
    ) -> NodeRecord:
        self._expire_offline()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if row is None:
                raise NodeNotFound(node_id)
            if row["group_name"] != group:
                raise MembershipError("node is not in this group")
            self._conn.execute(
                "UPDATE nodes SET membership_status=? WHERE id=?",
                (status.value, node_id),
            )
            self._conn.commit()
        return self.get_node(node_id)

    def heartbeat(self, node_id: str, req: HeartbeatRequest) -> NodeRecord:
        self._expire_offline()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if row is None:
                raise NodeNotFound(node_id)
            tools = (
                json.dumps(req.tools_available)
                if req.tools_available is not None
                else row["tools_available_json"]
            )
            models = (
                json.dumps([m.model_dump() for m in req.models])
                if req.models is not None
                else row["models_json"]
            )
            self._conn.execute(
                """
                UPDATE nodes SET status=?, last_seen=?, tools_available_json=?, models_json=?
                WHERE id=?
                """,
                (
                    req.status.value,
                    _utcnow().isoformat(),
                    tools,
                    models,
                    node_id,
                ),
            )
            self._conn.commit()
        return self.get_node(node_id)

    def get_node(self, node_id: str) -> NodeRecord:
        self._expire_offline()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if row is None:
                raise NodeNotFound(node_id)
        return mapping_to_node(row)

    def query_catalog(self, query: CatalogQuery) -> list[NodeRecord]:
        self._expire_offline()
        with self._lock:
            group = self._conn.execute(
                "SELECT name FROM groups WHERE name = ?", (query.group,)
            ).fetchone()
            if group is None:
                raise GroupNotFound(query.group)
            rows = self._conn.execute(
                """
                SELECT * FROM nodes
                WHERE group_name = ? AND membership_status = ?
                ORDER BY last_seen, id
                """,
                (query.group, MembershipStatus.APPROVED.value),
            ).fetchall()
        return filter_catalog_nodes([mapping_to_node(row) for row in rows], query)

    def _expire_offline(self) -> None:
        cutoff = (_utcnow() - self.heartbeat_timeout).isoformat()
        with self._lock:
            self._conn.execute(
                """
                UPDATE nodes SET status=?
                WHERE last_seen < ? AND status != ?
                """,
                (NodeStatus.OFFLINE.value, cutoff, NodeStatus.OFFLINE.value),
            )
            self._conn.commit()


def catalog_matches(node: NodeRecord, query: CatalogQuery) -> bool:
    """Skills/tools plus optional model name, min context_window, min memory_mb."""
    if query.exclude_node_id and node.id == query.exclude_node_id:
        return False
    if query.idle_only and node.status != NodeStatus.IDLE:
        return False
    required_skills = set(query.skills)
    if required_skills and not required_skills.issubset(set(node.skills)):
        return False
    required_tools = set(query.tools)
    if required_tools and not required_tools.issubset(set(node.tools_available)):
        return False
    if query.min_memory_mb is not None and node.machine.memory_mb < query.min_memory_mb:
        return False
    models = list(node.models)
    if query.model:
        models = [item for item in models if item.name == query.model]
        if not models:
            return False
    if query.min_context_window is not None:
        if not any((item.context_window or 0) >= query.min_context_window for item in models):
            return False
    return True


def filter_catalog_nodes(nodes: list[NodeRecord], query: CatalogQuery) -> list[NodeRecord]:
    return [node for node in nodes if catalog_matches(node, query)]


def mapping_to_node(row: Mapping[str, object]) -> NodeRecord:
    membership = row["membership_status"] if "membership_status" in row.keys() else "approved"
    models_raw = row["models_json"]
    models_data = json.loads(str(models_raw)) if not isinstance(models_raw, list) else models_raw
    return NodeRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        group=str(row["group_name"]),
        mailbox_url=str(row["mailbox_url"]),
        machine=MachineSpec.model_validate_json(str(row["machine_json"])),
        system=SystemSpec.model_validate_json(str(row["system_json"])),
        models=[ModelSpec.model_validate(m) for m in models_data],
        skills=json.loads(str(row["skills_json"])),
        tools_declared=json.loads(str(row["tools_declared_json"])),
        tools_available=json.loads(str(row["tools_available_json"])),
        status=NodeStatus(str(row["status"])),
        last_seen=_parse_dt(str(row["last_seen"])),
        created_at=_parse_dt(str(row["created_at"])),
        membership_status=MembershipStatus(str(membership)),
    )


def open_store(db: str, heartbeat_timeout_seconds: int = 15) -> ManagerStoreProtocol:
    """SQLite path by default; postgres:// or postgresql:// selects Postgres."""
    if db.startswith("postgres://") or db.startswith("postgresql://"):
        from mycoagent.manager.postgres_store import PostgresManagerStore

        return PostgresManagerStore(db, heartbeat_timeout_seconds=heartbeat_timeout_seconds)
    return ManagerStore(db, heartbeat_timeout_seconds=heartbeat_timeout_seconds)
