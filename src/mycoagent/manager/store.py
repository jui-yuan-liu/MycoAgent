from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone

from mycoagent.models import (
    CatalogQuery,
    GroupInfo,
    HeartbeatRequest,
    MachineSpec,
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
                created_at TEXT NOT NULL
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
                FOREIGN KEY (group_name) REFERENCES groups(name) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_group ON nodes(group_name);
            """
        )
        self._conn.commit()

    def create_group(self, name: str) -> GroupInfo:
        now = _utcnow().isoformat()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO groups(name, created_at) VALUES (?, ?)",
                    (name, now),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"group already exists: {name}") from exc
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
                "SELECT name, created_at FROM groups ORDER BY name"
            ).fetchall()
        return [self.get_group(row["name"]) for row in rows]

    def get_group(self, name: str) -> GroupInfo:
        self._expire_offline()
        with self._lock:
            row = self._conn.execute(
                "SELECT name, created_at FROM groups WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                raise GroupNotFound(name)
            members = self._conn.execute(
                "SELECT id FROM nodes WHERE group_name = ? ORDER BY name",
                (name,),
            ).fetchall()
        return GroupInfo(
            name=row["name"],
            created_at=_parse_dt(row["created_at"]),
            member_ids=[m["id"] for m in members],
        )

    def register_node(self, req: NodeRegisterRequest) -> NodeRecord:
        self._expire_offline()
        with self._lock:
            group = self._conn.execute(
                "SELECT name FROM groups WHERE name = ?", (req.group,)
            ).fetchone()
            if group is None:
                raise GroupNotFound(req.group)
            node_id = req.node_id or str(uuid.uuid4())
            now = _utcnow().isoformat()
            existing = self._conn.execute(
                "SELECT id FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
            payload = (
                node_id,
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
                now,
            )
            if existing:
                self._conn.execute(
                    """
                    UPDATE nodes SET
                        name=?, group_name=?, mailbox_url=?, machine_json=?,
                        system_json=?, models_json=?, skills_json=?,
                        tools_declared_json=?, tools_available_json=?,
                        status=?, last_seen=?
                    WHERE id=?
                    """,
                    payload[1:12] + (node_id,),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO nodes (
                        id, name, group_name, mailbox_url, machine_json,
                        system_json, models_json, skills_json,
                        tools_declared_json, tools_available_json,
                        status, last_seen, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payload,
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
        return self._row_to_node(row)

    def query_catalog(self, query: CatalogQuery) -> list[NodeRecord]:
        self._expire_offline()
        with self._lock:
            group = self._conn.execute(
                "SELECT name FROM groups WHERE name = ?", (query.group,)
            ).fetchone()
            if group is None:
                raise GroupNotFound(query.group)
            rows = self._conn.execute(
                "SELECT * FROM nodes WHERE group_name = ? ORDER BY name",
                (query.group,),
            ).fetchall()
        nodes = [self._row_to_node(row) for row in rows]
        result: list[NodeRecord] = []
        required_skills = set(query.skills)
        required_tools = set(query.tools)
        for node in nodes:
            if query.exclude_node_id and node.id == query.exclude_node_id:
                continue
            if query.idle_only and node.status != NodeStatus.IDLE:
                continue
            if required_skills and not required_skills.issubset(set(node.skills)):
                continue
            if required_tools and not required_tools.issubset(set(node.tools_available)):
                continue
            result.append(node)
        return result

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

    def _row_to_node(self, row: sqlite3.Row) -> NodeRecord:
        return NodeRecord(
            id=row["id"],
            name=row["name"],
            group=row["group_name"],
            mailbox_url=row["mailbox_url"],
            machine=MachineSpec.model_validate_json(row["machine_json"]),
            system=SystemSpec.model_validate_json(row["system_json"]),
            models=[ModelSpec.model_validate(m) for m in json.loads(row["models_json"])],
            skills=json.loads(row["skills_json"]),
            tools_declared=json.loads(row["tools_declared_json"]),
            tools_available=json.loads(row["tools_available_json"]),
            status=NodeStatus(row["status"]),
            last_seen=_parse_dt(row["last_seen"]),
            created_at=_parse_dt(row["created_at"]),
        )
