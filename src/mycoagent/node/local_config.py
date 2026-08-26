from __future__ import annotations

import os
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel, Field

from mycoagent.node.capabilities import merge_names
from mycoagent.node.runtime import AgentSpec
from mycoagent.node.specs import parse_models

CONFIG_FILENAME = "agents.yaml"
DOCKER_CONFIG_DIR = Path("/config")
HOST_CONFIG_DIR = Path(".mycoagent")
DEFAULT_LLM_URL_DOCKER = "http://host.docker.internal:11434/v1"
DEFAULT_LLM_URL_HOST = "http://127.0.0.1:11434/v1"


class LocalAgentConfig(BaseModel):
    name: str
    skills: list[str] = Field(default_factory=lambda: ["coding"])
    tools: list[str] = Field(default_factory=lambda: ["shell"])
    executor: str = "auto"
    llm_url: str = ""
    llm_model: str = ""
    llm_key: str = ""
    models: list[str] = Field(default_factory=list)


class LocalAgentsFile(BaseModel):
    agents: list[LocalAgentConfig] = Field(default_factory=list)


def running_in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def default_config_path() -> Path:
    if DOCKER_CONFIG_DIR.is_dir():
        return DOCKER_CONFIG_DIR / CONFIG_FILENAME
    return HOST_CONFIG_DIR / CONFIG_FILENAME


def default_host_urls() -> list[str]:
    if running_in_container():
        return ["http://node-a:9001", "http://node-b:9002"]
    return ["http://127.0.0.1:9001", "http://127.0.0.1:9002"]


def default_llm_url() -> str:
    if running_in_container():
        return DEFAULT_LLM_URL_DOCKER
    return DEFAULT_LLM_URL_HOST


def load_local_config(path: str | Path) -> LocalAgentsFile | None:
    file = Path(path)
    if not file.is_file():
        return None
    raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    return LocalAgentsFile.model_validate(raw)


def save_local_config(path: str | Path, config: LocalAgentsFile) -> Path:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    dumped = yaml.safe_dump(
        config.model_dump(mode="python"),
        sort_keys=False,
        allow_unicode=True,
    )
    file.write_text(dumped, encoding="utf-8")
    return file


def agent_is_configured(agent: LocalAgentConfig) -> bool:
    if not agent.name.strip():
        return False
    mode = agent.executor.strip().lower()
    if mode == "opencode":
        return True
    if agent.llm_url.strip():
        return True
    return mode == "echo"


def is_configured(config: LocalAgentsFile | None) -> bool:
    if config is None or not config.agents:
        return False
    return all(agent_is_configured(agent) for agent in config.agents)


def select_agent(config: LocalAgentsFile, name: str | None) -> LocalAgentConfig:
    if name:
        for agent in config.agents:
            if agent.name == name:
                return agent
        raise KeyError(f"agent {name!r} not in {CONFIG_FILENAME}")
    if len(config.agents) == 1:
        return config.agents[0]
    raise KeyError(f"{CONFIG_FILENAME} has multiple agents; pass --name")


def spec_from_local(
    entry: LocalAgentConfig,
    *,
    opencode_bin: str | None = None,
    opencode_timeout: float = 120.0,
    opencode_model: str | None = None,
    opencode_agent: str | None = None,
    opencode_auto: bool = False,
    opencode_attach: str | None = None,
    opencode_config: str | None = None,
    opencode_config_dir: str | None = None,
) -> AgentSpec:
    from mycoagent.node.executor import EchoExecutor
    from mycoagent.node.opencode import OpenCodeExecutor

    models = parse_models(",".join(entry.models)) if entry.models else []
    mode = (entry.executor or "auto").strip().lower()
    llm_url = entry.llm_url.strip() or None
    if mode in {"echo", "opencode"}:
        llm_url = None
    spec = AgentSpec(
        name=entry.name,
        skills=list(entry.skills),
        tools=list(entry.tools),
        models=models,
        llm_base_url=llm_url,
        llm_api_key=entry.llm_key.strip() or None,
        llm_model=entry.llm_model.strip() or None,
    )
    if mode == "echo":
        spec.executor = EchoExecutor()
    elif mode == "opencode":
        spec.executor = OpenCodeExecutor(
            binary=opencode_bin,
            timeout=opencode_timeout,
            model=opencode_model,
            agent=opencode_agent,
            auto=opencode_auto,
            attach=opencode_attach,
            config=opencode_config,
            config_dir=opencode_config_dir,
        )
    return spec


def default_agents(
    *,
    llm_url: str = "",
    llm_model: str = "",
    llm_key: str = "",
    executor: str | None = None,
) -> LocalAgentsFile:
    if executor and executor.strip():
        mode = executor.strip().lower()
    else:
        mode = "auto" if llm_url.strip() else "echo"
    models = [f"{llm_model}:local:8192"] if llm_model.strip() else []
    skills = ["coding"]
    tools = ["shell"]
    if mode == "opencode":
        from mycoagent.node.opencode_discover import discover_opencode_catalog

        found_skills, found_tools = discover_opencode_catalog()
        skills = merge_names(skills, found_skills) if found_skills else skills
        tools = merge_names(tools, found_tools)
        llm_url = ""
        llm_model = ""
        models = []
    return LocalAgentsFile(
        agents=[
            _seed_agent(
                "alpha",
                llm_url=llm_url,
                llm_model=llm_model,
                llm_key=llm_key,
                executor=mode,
                models=models,
                skills=skills,
                tools=tools,
            ),
            _seed_agent(
                "beta",
                llm_url=llm_url,
                llm_model=llm_model,
                llm_key=llm_key,
                executor=mode,
                models=models,
                skills=skills,
                tools=tools,
            ),
        ]
    )


def apply_flags(
    config: LocalAgentsFile,
    *,
    llm_url: str | None = None,
    llm_model: str | None = None,
    llm_key: str | None = None,
    executor: str | None = None,
) -> LocalAgentsFile:
    for agent in config.agents:
        if executor is not None:
            agent.executor = executor.strip() or agent.executor
            if agent.executor == "opencode":
                agent.llm_url = ""
                from mycoagent.node.opencode_discover import discover_opencode_catalog

                found_skills, found_tools = discover_opencode_catalog()
                agent.skills = merge_names(agent.skills, found_skills)
                agent.tools = merge_names(agent.tools, found_tools)
        if llm_url is not None and agent.executor != "opencode":
            agent.llm_url = llm_url
            agent.executor = "auto" if llm_url.strip() else "echo"
        if llm_model is not None and agent.executor != "opencode":
            agent.llm_model = llm_model
        if llm_key is not None and agent.executor != "opencode":
            agent.llm_key = llm_key
        if agent.llm_model.strip() and not agent.models and agent.executor != "opencode":
            agent.models = [f"{agent.llm_model}:local:8192"]
    return config


def summarize(config: LocalAgentsFile) -> str:
    lines: list[str] = []
    for agent in config.agents:
        key = "(set)" if agent.llm_key.strip() else "(empty)"
        url = agent.llm_url.strip() or "(echo)"
        lines.append(
            f"- {agent.name}: executor={agent.executor} skills={agent.skills} tools={agent.tools} "
            f"llm_url={url} llm_model={agent.llm_model or '-'} llm_key={key} models={agent.models}"
        )
    return "\n".join(lines) if lines else "(no agents)"


def probe_llm(base_url: str, api_key: str = "", timeout: float = 3.0) -> str | None:
    from mycoagent.node.providers import normalize_openai_base_url

    url = normalize_openai_base_url(base_url)
    if not url:
        return None
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(timeout=timeout) as client:
            models = client.get(f"{url}/models", headers=headers)
            if models.is_success:
                return None
            chat = client.post(
                f"{url}/chat/completions",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "model": "probe",
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
            if chat.is_success:
                return None
            return (
                f"LLM probe failed: HTTP {chat.status_code} "
                f"(also /models HTTP {models.status_code})"
            )
    except Exception as exc:  # noqa: BLE001 — probe must not block init
        return f"LLM probe failed: {exc}"


def apply_config_to_hosts(
    config: LocalAgentsFile,
    host_urls: list[str],
    timeout: float = 10.0,
) -> list[str]:
    from mycoagent.auth import bearer_headers, resolve_token

    headers = bearer_headers(resolve_token())
    warnings: list[str] = []
    for agent, host in zip(config.agents, host_urls):
        body = {
            "name": agent.name,
            "skills": agent.skills,
            "tools": agent.tools,
            "models": agent.models,
            "llm_url": agent.llm_url,
            "llm_model": agent.llm_model,
            "llm_key": agent.llm_key,
            "executor": agent.executor,
        }
        try:
            response = httpx.post(
                f"{host.rstrip('/')}/configure",
                json=body,
                headers=headers,
                timeout=timeout,
            )
            if response.is_error:
                warnings.append(f"{host}: HTTP {response.status_code} {response.text[:200]}")
        except Exception as exc:  # noqa: BLE001 — still keep the yaml
            warnings.append(f"{host}: {exc}")
    extra = len(config.agents) - len(host_urls)
    if extra > 0:
        warnings.append(f"{extra} agent(s) in yaml have no host URL to POST /configure")
    return warnings


def _seed_agent(
    name: str,
    *,
    llm_url: str,
    llm_model: str,
    llm_key: str,
    executor: str,
    models: list[str],
    skills: list[str] | None = None,
    tools: list[str] | None = None,
) -> LocalAgentConfig:
    return LocalAgentConfig(
        name=name,
        skills=list(skills) if skills is not None else ["coding"],
        tools=list(tools) if tools is not None else ["shell"],
        executor=executor,
        llm_url=llm_url,
        llm_model=llm_model,
        llm_key=llm_key,
        models=list(models),
    )
