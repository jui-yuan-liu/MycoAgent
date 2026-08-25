import json

import pytest

from mycoagent.cli import _agent_specs, _parse_agent_option
from mycoagent.node.capabilities import load_capabilities, load_names, merge_names


def test_load_names_from_json_list_and_text(tmp_path):
    listed = tmp_path / "skills.json"
    listed.write_text(json.dumps(["coding", "review"]), encoding="utf-8")
    assert load_names(listed, kind="skills") == ["coding", "review"]
    lines = tmp_path / "tools.txt"
    lines.write_text("# comment\nshell\nbrowser,search\n", encoding="utf-8")
    assert load_names(lines, kind="tools") == ["shell", "browser", "search"]


def test_load_names_from_skill_directory(tmp_path):
    coding = tmp_path / "coding"
    coding.mkdir()
    (coding / "SKILL.md").write_text("# coding\n", encoding="utf-8")
    review = tmp_path / "review"
    review.mkdir()
    (review / "SKILL.md").write_text("# review\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("x", encoding="utf-8")
    assert load_names(tmp_path, kind="skills") == ["coding", "notes", "review"]


def test_load_capabilities_json_and_merge_cli(tmp_path):
    cap = tmp_path / "capabilities.json"
    cap.write_text(json.dumps({"skills": ["coding"], "tools": ["shell"]}), encoding="utf-8")
    skills, tools = load_capabilities(cap)
    assert skills == ["coding"]
    assert tools == ["shell"]
    specs = _agent_specs(
        None,
        name="gamma",
        skills="extra",
        tools=None,
        models=None,
        capabilities_file=str(cap),
    )
    assert specs[0].skills == ["extra", "coding"]
    assert specs[0].tools == ["shell"]


def test_parse_agent_option_skills_file(tmp_path):
    path = tmp_path / "skills.json"
    path.write_text(json.dumps(["coding"]), encoding="utf-8")
    spec = _parse_agent_option(f"name=gamma,skills_file={path},tools=shell")
    assert spec.skills == ["coding"]
    assert spec.tools == ["shell"]


def test_load_names_missing_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_names(tmp_path / "nope", kind="skills")


def test_merge_names_dedupes():
    assert merge_names(["a", "b"], ["b", "c"]) == ["a", "b", "c"]
