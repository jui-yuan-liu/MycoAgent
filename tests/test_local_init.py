from typer.testing import CliRunner

from mycoagent.cli import _resolve_specs, app
from mycoagent.node.executor import ChildAgentExecutor, EchoExecutor
from mycoagent.node.local_config import (
    LocalAgentConfig,
    LocalAgentsFile,
    default_agents,
    is_configured,
    load_local_config,
    save_local_config,
    spec_from_local,
)
from cluster_harness import manager_server, node_server
import httpx


def test_yaml_roundtrip_and_configured_flags(tmp_path):
    path = tmp_path / "agents.yaml"
    assert load_local_config(path) is None
    assert not is_configured(None)

    incomplete = LocalAgentsFile(agents=[LocalAgentConfig(name="alpha", executor="auto", llm_url="")])
    assert not is_configured(incomplete)

    echo = LocalAgentsFile(agents=[LocalAgentConfig(name="alpha", executor="echo", llm_url="")])
    assert is_configured(echo)

    with_llm = default_agents(llm_url="http://host.docker.internal:11434/v1", llm_model="llama3")
    assert is_configured(with_llm)
    save_local_config(path, with_llm)
    loaded = load_local_config(path)
    assert loaded is not None
    assert loaded.agents[0].name == "alpha"
    assert loaded.agents[1].name == "beta"
    assert loaded.agents[0].llm_model == "llama3"
    assert loaded.agents[0].models == ["llama3:local:8192"]
    assert is_configured(loaded)


def test_spec_from_local_echo_drops_llm():
    spec = spec_from_local(
        LocalAgentConfig(
            name="alpha",
            executor="echo",
            llm_url="http://127.0.0.1:11434/v1",
            llm_model="llama3",
        )
    )
    assert spec.llm_base_url is None
    assert spec.name == "alpha"
    assert spec.executor is not None


def test_resolve_specs_without_config_file_stays_echo(tmp_path):
    specs = _resolve_specs(
        None,
        name="alpha",
        skills="coding",
        tools="shell",
        models=None,
        id_file=str(tmp_path / "alpha.id"),
        config_path=str(tmp_path / "missing.yaml"),
    )
    assert len(specs) == 1
    assert specs[0].name == "alpha"
    assert specs[0].skills == ["coding"]
    assert specs[0].llm_base_url is None


def test_resolve_specs_prefers_config_file(tmp_path):
    path = tmp_path / "agents.yaml"
    save_local_config(path, default_agents(llm_url="http://llm:11434/v1", llm_model="llama3"))
    specs = _resolve_specs(
        None,
        name="beta",
        skills="ignored",
        tools=None,
        models=None,
        id_file=str(tmp_path / "beta.id"),
        config_path=str(path),
    )
    assert specs[0].name == "beta"
    assert specs[0].llm_base_url == "http://llm:11434/v1"
    assert specs[0].llm_model == "llama3"


def test_init_yes_writes_without_prompt(tmp_path):
    cfg = tmp_path / "agents.yaml"
    result = CliRunner().invoke(
        app,
        [
            "init",
            "--yes",
            "--config",
            str(cfg),
            "--llm-url",
            "http://127.0.0.1:11434/v1",
            "--llm-model",
            "llama3",
            "--no-apply",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Name" not in result.output
    loaded = load_local_config(cfg)
    assert loaded is not None
    assert is_configured(loaded)
    assert loaded.agents[0].llm_url == "http://127.0.0.1:11434/v1"
    assert loaded.agents[0].llm_model == "llama3"


def test_init_skips_when_already_configured(tmp_path):
    cfg = tmp_path / "agents.yaml"
    save_local_config(cfg, default_agents(llm_url="http://x/v1", llm_model="m"))
    result = CliRunner().invoke(app, ["init", "--config", str(cfg), "--no-apply"])
    assert result.exit_code == 0, result.output
    assert "Already configured" in result.output
    assert "Name" not in result.output


def test_configure_updates_catalog_keeps_agent_id(tmp_path):
    with manager_server(tmp_path) as manager:
        with node_server(manager, "alpha", "default") as (url, runtime):
            original_id = runtime.node_id
            assert isinstance(runtime.executor, EchoExecutor)
            response = httpx.post(
                f"{url}/configure",
                json={
                    "skills": ["review"],
                    "tools": ["shell"],
                    "models": ["llama3:local:8192"],
                    "llm_url": "http://example.invalid/v1",
                    "llm_model": "llama3",
                    "executor": "auto",
                },
                timeout=10,
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["id"] == original_id
            assert body["skills"] == ["review"]
            assert runtime.node_id == original_id
            assert isinstance(runtime.executor, ChildAgentExecutor)
            catalog = httpx.get(
                f"{manager}/catalog",
                params={"group": "default", "idle_only": False},
                timeout=5,
            ).json()
            row = next(item for item in catalog if item["id"] == original_id)
            assert row["skills"] == ["review"]
            assert any(model["name"] == "llama3" for model in row["models"])


def test_init_yes_applies_to_running_host(tmp_path):
    with manager_server(tmp_path) as manager:
        with node_server(manager, "alpha", "default") as (url, runtime):
            original_id = runtime.node_id
            cfg = tmp_path / "agents.yaml"
            result = CliRunner().invoke(
                app,
                [
                    "init",
                    "--yes",
                    "--config",
                    str(cfg),
                    "--llm-url",
                    "http://example.invalid/v1",
                    "--llm-model",
                    "llama3",
                    "--host-url",
                    url,
                ],
            )
            assert result.exit_code == 0, result.output
            assert runtime.node_id == original_id
            catalog = httpx.get(
                f"{manager}/catalog",
                params={"group": "default", "idle_only": False},
                timeout=5,
            ).json()
            row = next(item for item in catalog if item["id"] == original_id)
            assert any(model["name"] == "llama3" for model in row["models"])
