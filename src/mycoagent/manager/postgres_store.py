from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from mycoagent.manager.store import (
    GroupNotFound,
    MembershipError,
    NodeNotFound,
    RegisterForbidden,
    filter_catalog_nodes,
    mapping_to_node,
)
from mycoagent.models import (
    CatalogQuery,
    GroupInfo,
    GroupPolicyUpdate,
    HeartbeatRequest,
    JoinMode,
    MembershipStatus,
    NodeRecord,
    NodeRegisterRequest,
    NodeStatus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class PostgresManagerStore:
    """C1-ready Postgres backend. Same protocol as SQLite; no leases or gossip."""

    def __init__(self, dsn: str, heartbeat_timeout_seconds: int = 15) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.dsn = dsn
        self.heartbeat_timeout = timedelta(seconds=heartbeat_timeout_seconds)
        self._lock = threading.RLock()
        self._conn = psycopg.connect(dsn, row_factory=dict_row)
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    name TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    join_mode TEXT NOT NULL DEFAULT 'auto',
                    allow_register_json TEXT NOT NULL DEFAULT '[]',
                    allow_parent_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            self._conn.execute(
                """
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
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nodes_group ON nodes(group_name)"
            )
            self._conn.commit()

    def create_group(
        self,
        name: str,
        *,
        description: str = "",
        join_mode: JoinMode | str = JoinMode.AUTO,
        allow_register: list[str] | None = None,
        allow_parent: list[str] | None = None,
    ) -> GroupInfo:
        import psycopg

        now = _utcnow().isoformat()
        mode = JoinMode(join_mode)
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO groups(
                        name, created_at, description, join_mode,
                        allow_register_json, allow_parent_json
                    ) VALUES (%s, %s, %s, %s, %s, %s)
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
            except psycopg.errors.UniqueViolation as exc:
                self._conn.rollback()
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
                    description=%s, join_mode=%s, allow_register_json=%s, allow_parent_json=%s
                WHERE name=%s
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
            cur = self._conn.execute("DELETE FROM groups WHERE name = %s", (name,))
            self._conn.commit()
            if cur.rowcount == 0:
                raise GroupNotFound(name)

    def list_groups(self) -> list[GroupInfo]:
        self._expire_offline()
        with self._lock:
            rows = self._conn.execute("SELECT name FROM groups ORDER BY name").fetchall()
        return [self.get_group(row["name"]) for row in rows]

    def get_group(self, name: str) -> GroupInfo:
        self._expire_offline()
        with self._lock:
            row = self._conn.execute("SELECT * FROM groups WHERE name = %s", (name,)).fetchone()
            if row is None:
                raise GroupNotFound(name)
            members = self._conn.execute(
                """
                SELECT id FROM nodes
                WHERE group_name = %s AND membership_status = %s
                ORDER BY name
                """,
                (name, MembershipStatus.APPROVED.value),
            ).fetchall()
            pending = self._conn.execute(
                """
                SELECT id FROM nodes
                WHERE group_name = %s AND membership_status = %s
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
                "SELECT * FROM groups WHERE name = %s", (req.group,)
            ).fetchone()
            if group is None:
                raise GroupNotFound(req.group)
            allow_register = json.loads(group["allow_register_json"])
            if allow_register and req.name not in allow_register:
                raise RegisterForbidden("not allowed to register in this group")
            node_id = req.node_id or str(uuid.uuid4())
            now = _utcnow().isoformat()
            existing = self._conn.execute(
                "SELECT * FROM nodes WHERE id = %s", (node_id,)
            ).fetchone()
            join_mode = JoinMode(group["join_mode"])
            if existing is not None and existing["group_name"] == req.group:
                membership = existing["membership_status"]
            elif join_mode == JoinMode.MANUAL:
                membership = MembershipStatus.PENDING.value
            else:
                membership = MembershipStatus.APPROVED.value
            common: tuple[Any, ...] = (
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
                        name=%s, group_name=%s, mailbox_url=%s, machine_json=%s,
                        system_json=%s, models_json=%s, skills_json=%s,
                        tools_declared_json=%s, tools_available_json=%s,
                        status=%s, last_seen=%s, membership_status=%s
                    WHERE id=%s
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
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (node_id, *common[:11], now, membership),
                )
            self._conn.commit()
        return self.get_node(node_id)

    def set_membership(self, group: str, node_id: str, status: MembershipStatus) -> NodeRecord:
        self._expire_offline()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = %s", (node_id,)
            ).fetchone()
            if row is None:
                raise NodeNotFound(node_id)
            if row["group_name"] != group:
                raise MembershipError("node is not in this group")
            self._conn.execute(
                "UPDATE nodes SET membership_status=%s WHERE id=%s",
                (status.value, node_id),
            )
            self._conn.commit()
        return self.get_node(node_id)

    def heartbeat(self, node_id: str, req: HeartbeatRequest) -> NodeRecord:
        self._expire_offline()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = %s", (node_id,)
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
                UPDATE nodes SET status=%s, last_seen=%s, tools_available_json=%s, models_json=%s
                WHERE id=%s
                """,
                (req.status.value, _utcnow().isoformat(), tools, models, node_id),
            )
            self._conn.commit()
        return self.get_node(node_id)

    def get_node(self, node_id: str) -> NodeRecord:
        self._expire_offline()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = %s", (node_id,)
            ).fetchone()
        if row is None:
            raise NodeNotFound(node_id)
        return mapping_to_node(row)

    def query_catalog(self, query: CatalogQuery) -> list[NodeRecord]:
        self._expire_offline()
        with self._lock:
            group = self._conn.execute(
                "SELECT name FROM groups WHERE name = %s", (query.group,)
            ).fetchone()
            if group is None:
                raise GroupNotFound(query.group)
            rows = self._conn.execute(
                """
                SELECT * FROM nodes
                WHERE group_name = %s AND membership_status = %s
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
                UPDATE nodes SET status=%s
                WHERE last_seen < %s AND status != %s
                """,
                (NodeStatus.OFFLINE.value, cutoff, NodeStatus.OFFLINE.value),
            )
            self._conn.commit()
