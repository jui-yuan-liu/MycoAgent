from __future__ import annotations

import json
from typing import Optional

import httpx
import typer
import uvicorn

from mycoagent.manager.api import create_app
from mycoagent.manager.store import ManagerStore
from mycoagent.models import ForwardRequest, JobSubmitRequest, SubtaskSpec
from mycoagent.node.api import create_node_app
from mycoagent.node.runtime import NodeRuntime

app = typer.Typer(no_args_is_help=True, help="MycoAgent: groups, catalog, nodes, policies")
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
    """Run Cluster Manager (groups + resource catalog)."""
    store = ManagerStore(db, heartbeat_timeout_seconds=heartbeat_timeout)
    uvicorn.run(create_app(store, bootstrap_group=bootstrap_group), host=host, port=port)


@app.command()
def node(
    manager_url: str = typer.Option(..., "--manager", help="Cluster Manager base URL"),
    group: str = typer.Option(..., "--group"),
    name: str = typer.Option(..., "--name"),
    host: str = "0.0.0.0",
    port: int = 9000,
    advertise: Optional[str] = typer.Option(
        None, "--advertise", help="Mailbox URL other nodes should call, default http://127.0.0.1:PORT"
    ),
    skills: Optional[str] = None,
    tools: Optional[str] = None,
    models: Optional[str] = None,
    heartbeat_interval: float = 5.0,
) -> None:
    """Run a node: register, heartbeat, mailbox, parent job memory."""
    mailbox_url = (advertise or f"http://127.0.0.1:{port}").rstrip("/")
    from mycoagent.node.specs import parse_csv, parse_models

    runtime = NodeRuntime(
        manager_url=manager_url,
        name=name,
        group=group,
        mailbox_url=mailbox_url,
        skills=parse_csv(skills),
        tools_declared=parse_csv(tools),
        tools_available=parse_csv(tools),
        models=parse_models(models),
        heartbeat_interval=heartbeat_interval,
    )
    uvicorn.run(create_node_app(runtime), host=host, port=port)


def _csv(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


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
) -> None:
    params: list[tuple[str, str]] = [("group", group), ("idle_only", str(idle_only).lower())]
    if skills:
        for item in skills.split(","):
            params.append(("skills", item.strip()))
    if tools:
        for item in tools.split(","):
            params.append(("tools", item.strip()))
    response = httpx.get(f"{manager_url.rstrip('/')}/catalog", params=params, timeout=10.0)
    _print_response(response)


@ctl.command("submit")
def submit(
    node_url: str = typer.Option(..., "--node"),
    description: str = typer.Option(...),
    subtask: Optional[list[str]] = typer.Option(
        None, "--subtask", help="Repeatable. Optional skills after | e.g. 'write tests|coding'"
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
