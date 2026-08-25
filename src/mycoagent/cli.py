from __future__ import annotations

import json
from typing import Optional

import httpx
import typer
import uvicorn

from mycoagent.artifacts import artifact_store_from_env
from mycoagent.manager.api import create_app
from mycoagent.manager.store import open_store
from mycoagent.models import ForwardRequest, JobSubmitRequest, SubtaskSpec
from mycoagent.node.api import create_host_app, create_node_app
from mycoagent.node.executor import ChildAgentExecutor, EchoExecutor
from mycoagent.node.llm import llm_from_env
from mycoagent.node.opencode import OpenCodeExecutor
from mycoagent.node.identity import ID_FILE_ENV, resolve_agent_id
from mycoagent.node.planner import TaskPlanner
from mycoagent.node.runtime import (
    DEFAULT_MAILBOX_QUEUE,
    AgentSpec,
    HostRuntime,
    NodeRuntime,
    attach_spec_llms,
)
from mycoagent.node.capabilities import load_capabilities, load_names, merge_names
from mycoagent.node.specs import parse_csv, parse_models

app = typer.Typer(no_args_is_help=True, help="MycoAgent: groups, catalog, host agents, policies")
ctl = typer.Typer(no_args_is_help=True, help="Admin commands against Cluster Manager")
app.add_typer(ctl, name="ctl")


@app.command()
def manager(
    host: str = "0.0.0.0",
    port: int = 8080,
    db: str = "mycoagent.db",
    bootstrap_group: Optional[str] = "default",
    heartbeat_timeout: int = 15,
) -> None:
    """Run Cluster Manager (groups + resource catalog). --db is a SQLite path or postgres:// DSN."""
    store = open_store(db, heartbeat_timeout_seconds=heartbeat_timeout)
    uvicorn.run(create_app(store, bootstrap_group=bootstrap_group), host=host, port=port)


@app.command()
def node(
    manager_url: Optional[str] = typer.Option(
        None,
        "--manager",
        envvar="MYCOAGENT_MANAGER",
        help="Cluster Manager base URL (or MYCOAGENT_MANAGER)",
    ),
    group: str = typer.Option(..., "--group"),
    name: Optional[str] = typer.Option(None, "--name", help="Single-agent shorthand"),
    host: str = "0.0.0.0",
    port: int = 9000,
    advertise: Optional[str] = typer.Option(
        None, "--advertise", help="Host base URL others should call, default http://127.0.0.1:PORT"
    ),
    skills: Optional[str] = None,
    tools: Optional[str] = None,
    models: Optional[str] = None,
    skills_file: Optional[str] = typer.Option(
        None,
        "--skills-file",
        help="File or directory of local skill names (JSON list, one-per-line, or SKILL.md folders)",
    ),
    tools_file: Optional[str] = typer.Option(
        None,
        "--tools-file",
        help="File or directory of local tool names (same formats as --skills-file)",
    ),
    capabilities_file: Optional[str] = typer.Option(
        None,
        "--capabilities-file",
        help="JSON {skills,tools} or a directory containing both",
    ),
    agent: Optional[list[str]] = typer.Option(
        None,
        "--agent",
        help="Repeatable. name=alpha,skills=coding,tools=shell,id_file=/path,llm_url=http://127.0.0.1:11434,llm_model=llama3",
    ),
    executor_name: str = typer.Option(
        "auto",
        "--executor",
        help="auto (echo without LLM, built-in agent loop with LLM), echo, agent, or opencode",
    ),
    max_steps: int = typer.Option(12, "--max-steps"),
    opencode_bin: Optional[str] = typer.Option(
        None,
        "--opencode-bin",
        envvar="MYCOAGENT_OPENCODE_BIN",
        help="opencode binary when --executor opencode (default: opencode)",
    ),
    opencode_timeout: float = typer.Option(120.0, "--opencode-timeout"),
    heartbeat_interval: float = 5.0,
    id_file: Optional[str] = typer.Option(
        None,
        "--id-file",
        envvar=ID_FILE_ENV,
        help="Read/write this agent's catalog id (or MYCOAGENT_ID_FILE). MYCOAGENT_AGENT_ID also works.",
    ),
    job_db: Optional[str] = typer.Option(
        None,
        "--job-db",
        envvar="MYCOAGENT_JOB_DB",
        help="Optional SQLite file for parent JobMemory (MYCOAGENT_JOB_DB).",
    ),
    mailbox_queue: int = typer.Option(
        DEFAULT_MAILBOX_QUEUE,
        "--mailbox-queue",
        help="In-process assign_subtask queue slots while busy; 0 = reject with 409 immediately.",
    ),
) -> None:
    """Run a Host: one process, one or more agents, each with mailbox and heartbeat."""
    if not manager_url:
        raise typer.BadParameter("provide --manager or set MYCOAGENT_MANAGER")
    mailbox_base = (advertise or f"http://127.0.0.1:{port}").rstrip("/")
    worker, planner = _executor_and_planner(
        executor_name,
        max_steps,
        opencode_bin=opencode_bin,
        opencode_timeout=opencode_timeout,
    )
    artifacts = artifact_store_from_env()
    specs = _agent_specs(
        agent,
        name=name,
        skills=skills,
        tools=tools,
        models=models,
        skills_file=skills_file,
        tools_file=tools_file,
        capabilities_file=capabilities_file,
        id_file=id_file,
    )
    attach_spec_llms(specs, max_steps=max_steps)
    if len(specs) == 1 and specs[0].root_mailbox:
        spec = specs[0]
        runtime = NodeRuntime(
            manager_url=manager_url,
            name=spec.name,
            group=group,
            mailbox_url=mailbox_base,
            skills=spec.skills,
            tools_declared=spec.tools,
            tools_available=spec.tools,
            models=spec.models,
            node_id=spec.agent_id,
            heartbeat_interval=heartbeat_interval,
            artifact_store=artifacts,
            executor=worker,
            planner=planner,
            job_db=job_db,
            mailbox_queue_size=mailbox_queue,
        )
        uvicorn.run(create_node_app(runtime), host=host, port=port)
        return
    host_runtime = HostRuntime(
        manager_url=manager_url,
        group=group,
        advertise=mailbox_base,
        specs=specs,
        heartbeat_interval=heartbeat_interval,
        artifact_store=artifacts,
        executor=worker,
        planner=planner,
        job_db=job_db,
        mailbox_queue_size=mailbox_queue,
    )
    uvicorn.run(create_host_app(host_runtime), host=host, port=port)


def _csv(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _executor_and_planner(
    executor_name: str,
    max_steps: int,
    *,
    opencode_bin: str | None = None,
    opencode_timeout: float = 120.0,
):
    llm = llm_from_env()
    mode = executor_name.strip().lower()
    if mode not in {"auto", "echo", "agent", "opencode"}:
        raise typer.BadParameter("--executor must be auto, echo, agent, or opencode")
    planner = TaskPlanner(llm) if llm is not None else None
    if mode == "opencode":
        return OpenCodeExecutor(binary=opencode_bin, timeout=opencode_timeout), planner
    if mode == "echo" or (mode == "auto" and llm is None):
        return EchoExecutor(), planner
    if llm is None:
        raise typer.BadParameter("agent executor requires MYCOAGENT_LLM_BASE_URL")
    return ChildAgentExecutor(llm, max_steps=max_steps), planner


def _parse_agent_option(raw: str) -> AgentSpec:
    fields: dict[str, str] = {}
    for item in raw.split(","):
        piece = item.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise typer.BadParameter(f"agent field must be key=value: {piece}")
        key, value = piece.split("=", 1)
        fields[key.strip()] = value.strip()
    name = fields.get("name")
    if not name:
        raise typer.BadParameter("--agent needs name=...")
    try:
        skills, tools = _skills_and_tools(
            skills_csv=fields.get("skills"),
            tools_csv=fields.get("tools"),
            skills_file=fields.get("skills_file"),
            tools_file=fields.get("tools_file"),
            capabilities_file=fields.get("capabilities_file"),
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    return AgentSpec(
        name=name,
        skills=skills,
        tools=tools,
        models=parse_models(fields.get("models")),
        agent_id=resolve_agent_id(
            explicit=fields.get("id"),
            id_file=fields.get("id_file"),
            env_id=None,
        ),
        root_mailbox=False,
        llm_base_url=fields.get("llm_url") or None,
        llm_api_key=fields.get("llm_key") or None,
        llm_model=fields.get("llm_model") or None,
    )


def _skills_and_tools(
    *,
    skills_csv: str | None,
    tools_csv: str | None,
    skills_file: str | None,
    tools_file: str | None,
    capabilities_file: str | None,
) -> tuple[list[str], list[str]]:
    from_cap: tuple[list[str], list[str]] = ([], [])
    if capabilities_file:
        from_cap = load_capabilities(capabilities_file)
    skills_from_file = load_names(skills_file, kind="skills") if skills_file else []
    tools_from_file = load_names(tools_file, kind="tools") if tools_file else []
    return (
        merge_names(parse_csv(skills_csv), from_cap[0], skills_from_file),
        merge_names(parse_csv(tools_csv), from_cap[1], tools_from_file),
    )


def _agent_specs(
    agents: list[str] | None,
    *,
    name: str | None,
    skills: str | None,
    tools: str | None,
    models: str | None,
    skills_file: str | None = None,
    tools_file: str | None = None,
    capabilities_file: str | None = None,
    id_file: str | None = None,
) -> list[AgentSpec]:
    if agents:
        specs = [_parse_agent_option(item) for item in agents]
        if len(specs) == 1:
            specs[0].root_mailbox = True
        return specs
    if not name:
        raise typer.BadParameter("provide --name or at least one --agent")
    try:
        skill_names, tool_names = _skills_and_tools(
            skills_csv=skills,
            tools_csv=tools,
            skills_file=skills_file,
            tools_file=tools_file,
            capabilities_file=capabilities_file,
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    return [
        AgentSpec(
            name=name,
            skills=skill_names,
            tools=tool_names,
            models=parse_models(models),
            agent_id=resolve_agent_id(id_file=id_file),
            root_mailbox=True,
        )
    ]


@ctl.command("groups-create")
def groups_create(
    name: str,
    manager_url: str = typer.Option("http://127.0.0.1:8080", "--manager"),
    description: str = typer.Option("", "--description"),
    join_mode: str = typer.Option("auto", "--join-mode"),
    allow_register: Optional[str] = typer.Option(None, help="Comma-separated node names; empty = anyone"),
    allow_parent: Optional[str] = typer.Option(None, help="Comma-separated node names or ids; empty = any member"),
) -> None:
    response = httpx.post(
        f"{manager_url.rstrip('/')}/groups",
        json={
            "name": name,
            "description": description,
            "join_mode": join_mode,
            "allow_register": _csv(allow_register),
            "allow_parent": _csv(allow_parent),
        },
        timeout=10.0,
    )
    _print_response(response)


@ctl.command("groups-update")
def groups_update(
    name: str,
    manager_url: str = typer.Option("http://127.0.0.1:8080", "--manager"),
    description: Optional[str] = typer.Option(None, "--description"),
    join_mode: Optional[str] = typer.Option(None, "--join-mode"),
    allow_register: Optional[str] = typer.Option(None, help="Comma-separated names; pass empty string to clear"),
    allow_parent: Optional[str] = typer.Option(None, help="Comma-separated names or ids; pass empty string to clear"),
) -> None:
    body: dict[str, object] = {}
    if description is not None:
        body["description"] = description
    if join_mode is not None:
        body["join_mode"] = join_mode
    if allow_register is not None:
        body["allow_register"] = _csv(allow_register)
    if allow_parent is not None:
        body["allow_parent"] = _csv(allow_parent)
    response = httpx.patch(f"{manager_url.rstrip('/')}/groups/{name}", json=body, timeout=10.0)
    _print_response(response)


@ctl.command("group")
def group_get(name: str, manager_url: str = typer.Option("http://127.0.0.1:8080", "--manager")) -> None:
    response = httpx.get(f"{manager_url.rstrip('/')}/groups/{name}", timeout=10.0)
    _print_response(response)


@ctl.command("approve")
def approve(
    group: str,
    node_id: str,
    manager_url: str = typer.Option("http://127.0.0.1:8080", "--manager"),
) -> None:
    response = httpx.post(
        f"{manager_url.rstrip('/')}/groups/{group}/approve/{node_id}", timeout=10.0
    )
    _print_response(response)


@ctl.command("deny")
def deny(
    group: str,
    node_id: str,
    manager_url: str = typer.Option("http://127.0.0.1:8080", "--manager"),
) -> None:
    response = httpx.post(
        f"{manager_url.rstrip('/')}/groups/{group}/deny/{node_id}", timeout=10.0
    )
    _print_response(response)


@ctl.command("groups")
def groups_list(manager_url: str = typer.Option("http://127.0.0.1:8080", "--manager")) -> None:
    response = httpx.get(f"{manager_url.rstrip('/')}/groups", timeout=10.0)
    _print_response(response)


@ctl.command("catalog")
def catalog(
    group: str,
    manager_url: str = typer.Option("http://127.0.0.1:8080", "--manager"),
    idle_only: bool = True,
    skills: Optional[str] = None,
    tools: Optional[str] = None,
    model: Optional[str] = None,
    min_context_window: Optional[int] = typer.Option(None, "--min-context-window"),
    min_memory_mb: Optional[int] = typer.Option(None, "--min-memory-mb"),
) -> None:
    params: list[tuple[str, str]] = [("group", group), ("idle_only", str(idle_only).lower())]
    if skills:
        for item in skills.split(","):
            params.append(("skills", item.strip()))
    if tools:
        for item in tools.split(","):
            params.append(("tools", item.strip()))
    if model:
        params.append(("model", model))
    if min_context_window is not None:
        params.append(("min_context_window", str(min_context_window)))
    if min_memory_mb is not None:
        params.append(("min_memory_mb", str(min_memory_mb)))
    response = httpx.get(f"{manager_url.rstrip('/')}/catalog", params=params, timeout=10.0)
    _print_response(response)


@ctl.command("submit")
def submit(
    node_url: str = typer.Option(..., "--node"),
    description: str = typer.Option(...),
    subtask: Optional[list[str]] = typer.Option(
        None,
        "--subtask",
        help="Repeatable. Optional skills after | e.g. 'write tests|coding'. Omit to let the parent LLM split using the catalog.",
    ),
) -> None:
    specs: list[SubtaskSpec] = []
    for raw in subtask or []:
        if "|" in raw:
            text, skill = raw.split("|", 1)
            specs.append(SubtaskSpec(description=text.strip(), skills=[skill.strip()]))
        else:
            specs.append(SubtaskSpec(description=raw))
    body = JobSubmitRequest(description=description, subtasks=specs)
    response = httpx.post(
        f"{node_url.rstrip('/')}/jobs",
        json=body.model_dump(mode="json"),
        timeout=30.0,
    )
    _print_response(response)


@ctl.command("job")
def job_get(job_id: str, node_url: str = typer.Option(..., "--node")) -> None:
    response = httpx.get(f"{node_url.rstrip('/')}/jobs/{job_id}", timeout=10.0)
    _print_response(response)


@ctl.command("forward")
def forward(
    job_id: str,
    node_url: str = typer.Option(..., "--node"),
    description: str = typer.Option(...),
    source_subtask: Optional[str] = typer.Option(None, "--from-subtask"),
    target: Optional[str] = typer.Option(None, "--target", help="Child node id to receive the follow-up"),
    skills: Optional[str] = None,
    tools: Optional[str] = None,
) -> None:
    body = ForwardRequest(
        description=description,
        skills=_csv(skills),
        tools=_csv(tools),
        source_subtask_id=source_subtask,
        target_node_id=target,
    )
    response = httpx.post(
        f"{node_url.rstrip('/')}/jobs/{job_id}/forward",
        json=body.model_dump(mode="json"),
        timeout=30.0,
    )
    _print_response(response)


def _print_response(response: httpx.Response) -> None:
    try:
        payload = response.json()
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    except Exception:
        typer.echo(response.text)
    if response.is_error:
        raise typer.Exit(code=1)
