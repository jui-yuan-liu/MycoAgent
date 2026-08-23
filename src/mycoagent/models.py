from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from mycoagent.version import __version__


class NodeStatus(StrEnum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"


class SubtaskStatus(StrEnum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ModelSource(StrEnum):
    LOCAL = "local"
    API = "api"


class RuntimeKind(StrEnum):
    BARE_METAL = "bare_metal"
    CONTAINER = "container"


class MachineSpec(BaseModel):
    cpu_cores: float
    memory_mb: int
    gpu: str | None = None
    disk_gb: float | None = None


class SystemSpec(BaseModel):
    os: str
    arch: str
    runtime: RuntimeKind = RuntimeKind.BARE_METAL
    mycoagent_version: str = __version__


class ModelSpec(BaseModel):
    name: str
    source: ModelSource
    context_window: int | None = None


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")


class GroupInfo(BaseModel):
    name: str
    created_at: datetime
    member_ids: list[str] = Field(default_factory=list)


class NodeRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    group: str
    mailbox_url: str
    machine: MachineSpec
    system: SystemSpec
    models: list[ModelSpec] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    tools_declared: list[str] = Field(default_factory=list)
    tools_available: list[str] = Field(default_factory=list)
    node_id: str | None = None


class HeartbeatRequest(BaseModel):
    status: NodeStatus
    tools_available: list[str] | None = None
    models: list[ModelSpec] | None = None


class NodeRecord(BaseModel):
    id: str
    name: str
    group: str
    mailbox_url: str
    machine: MachineSpec
    system: SystemSpec
    models: list[ModelSpec]
    skills: list[str]
    tools_declared: list[str]
    tools_available: list[str]
    status: NodeStatus
    last_seen: datetime
    created_at: datetime


class CatalogQuery(BaseModel):
    group: str
    idle_only: bool = True
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    exclude_node_id: str | None = None


class SubtaskSpec(BaseModel):
    description: str
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class JobSubmitRequest(BaseModel):
    description: str
    subtasks: list[SubtaskSpec] = Field(default_factory=list)


class SubtaskRecord(BaseModel):
    id: str
    description: str
    skills: list[str]
    tools: list[str]
    payload: dict[str, Any]
    assignee_node_id: str | None = None
    assignee_mailbox_url: str | None = None
    status: SubtaskStatus = SubtaskStatus.PENDING
    result: str | None = None
    error: str | None = None


class JobMemory(BaseModel):
    job_id: str
    description: str
    parent_node_id: str
    status: JobStatus = JobStatus.RUNNING
    subtasks: list[SubtaskRecord] = Field(default_factory=list)
    local_result: str | None = None
    error: str | None = None
    created_at: datetime


class AssignSubtaskMessage(BaseModel):
    type: str = "assign_subtask"
    job_id: str
    subtask_id: str
    parent_node_id: str
    parent_mailbox_url: str
    description: str
    skills: list[str]
    tools: list[str]
    payload: dict[str, Any]


class SubtaskResultMessage(BaseModel):
    type: str = "subtask_result"
    job_id: str
    subtask_id: str
    child_node_id: str
    status: SubtaskStatus
    result: str | None = None
    error: str | None = None


class ChildWork(BaseModel):
    job_id: str
    subtask_id: str
    parent_node_id: str
    parent_mailbox_url: str
    description: str
    payload: dict[str, Any]
    status: SubtaskStatus
    result: str | None = None


class Envelope(BaseModel):
    """Inbound mailbox payload. Exactly one of assign/result is set by type."""

    type: str
    body: dict[str, Any]


class ErrorBody(BaseModel):
    detail: str
