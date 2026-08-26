from pathlib import Path

from typer.testing import CliRunner

from mycoagent.cli import _resolve_specs, app
from mycoagent.node.local_config import (
    LocalAgentConfig,
    LocalAgentsFile,
    is_configured,
    load_local_config,
)
from mycoagent.node.opencode import (
    OpenCodeExecutor,
    build_opencode_env,
    opencode_prompt,
)
from mycoagent.node.opencode_discover import discover_opencode_catalog
from mycoagent.models import ChildWork, SubtaskStatus
from mycoagent.node.workspace import assignment_workspace
import json


def test_discover_opencode_catalog_from_home(tmp_path):
    skill = tmp_path / ".config" / "opencode" / "skills" / "git-release"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# release\n", encoding="utf-8")
    other = tmp_path / ".claude" / "skills" / "review"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("# review\n", encoding="utf-8")
    skills, tools = discover_opencode_catalog(home=tmp_path)
    assert "git-release" in skills
    assert "review" in skills
    assert tools == ["opencode"]


def test_discover_extra_dirs(tmp_path):
    extra = tmp_path / "extra"
    (extra / "custom-skill").mkdir(parents=True)
    (extra / "custom-skill" / "SKILL.md").write_text("x", encoding="utf-8")
    skills, tools = discover_opencode_catalog(home=tmp_path / "empty-home", extra_dirs=[extra])
    assert skills == ["custom-skill"]
    assert "opencode" in tools


def test_build_opencode_env_keeps_home_and_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")
    env = build_opencode_env(config="/tmp/oc.json", config_dir="/tmp/ocdir")
    assert env["HOME"] == str(tmp_path)
    assert env["OPENCODE_CONFIG"] == "/tmp/oc.json"
    assert env["OPENCODE_CONFIG_DIR"] == "/tmp/ocdir"
    assert env["PATH"] == "/usr/bin"


def test_opencode_prompt_includes_context():
    work = ChildWork(
        job_id="j1",
        subtask_id="s1",
        parent_node_id="p",
        parent_mailbox_url="http://x",
        description="do work",
        payload={"artifact_ids": ["group/j/s/out.txt"], "note": 1},
        status=SubtaskStatus.RUNNING,
    )
    text = opencode_prompt(work)
    assert "j1" in text and "s1" in text
    assert "do work" in text
    assert "out.txt" in text
    assert "absolute" in text.lower() or "cwd" in text.lower() or "working directory" in text.lower()


async def test_opencode_executor_passes_model_auto_and_env():
    captured: dict[str, object] = {}

    def runner(argv: list[str], cwd: Path, timeout: float, env: dict[str, str]):
        captured["argv"] = argv
        captured["cwd"] = cwd
        captured["env"] = env
        captured["timeout"] = timeout
        return 0, json.dumps({"text": "done"}), ""

    work = ChildWork(
        job_id="j",
        subtask_id="s",
        parent_node_id="p",
        parent_mailbox_url="http://127.0.0.1:1",
        description="task",
        payload={},
        status=SubtaskStatus.RUNNING,
    )
    with assignment_workspace() as workspace:
        finished = await OpenCodeExecutor(
            binary="opencode",
            runner=runner,
            model="ollama/llama3",
            auto=True,
            config="/cfg.json",
        ).run(work, workspace)
        assert finished.status == SubtaskStatus.COMPLETED
        argv = captured["argv"]
        assert isinstance(argv, list)
        assert "--model" in argv and "ollama/llama3" in argv
        assert "--auto" in argv
        env = captured["env"]
        assert isinstance(env, dict)
        assert env.get("OPENCODE_CONFIG") == "/cfg.json"
        assert env.get("HOME") != str(workspace.root)
        assert captured["cwd"] == workspace.root


def test_resolve_specs_opencode_merges_discovered(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mycoagent.node.opencode_discover.discover_opencode_catalog",
        lambda extra_dirs=None: (["from-oc"], ["opencode"]),
    )
    specs = _resolve_specs(
        None,
        name="gamma",
        skills=None,
        tools=None,
        models=None,
        id_file=str(tmp_path / "g.id"),
        executor_name="opencode",
    )
    assert specs[0].skills == ["from-oc"] or "from-oc" in specs[0].skills
    assert "opencode" in specs[0].tools


def test_init_yes_executor_opencode(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mycoagent.node.opencode_discover.discover_opencode_catalog",
        lambda extra_dirs=None, home=None: (["skill-a"], ["opencode"]),
    )
    cfg = tmp_path / "agents.yaml"
    result = CliRunner().invoke(
        app,
        ["init", "--yes", "--executor", "opencode", "--config", str(cfg), "--no-apply"],
    )
    assert result.exit_code == 0, result.output
    loaded = load_local_config(cfg)
    assert loaded is not None
    assert is_configured(loaded)
    assert loaded.agents[0].executor == "opencode"
    assert "skill-a" in loaded.agents[0].skills
    assert "opencode" in loaded.agents[0].tools
    assert loaded.agents[0].llm_url == ""


def test_opencode_agent_config_is_configured():
    from mycoagent.node.local_config import LocalAgentsFile

    file = LocalAgentsFile(agents=[LocalAgentConfig(name="gamma", executor="opencode", llm_url="")])
    assert is_configured(file)
