import json
from pathlib import Path

import pytest

from mycoagent.models import ChildWork, SubtaskStatus
from mycoagent.node.opencode import (
    OpenCodeExecutor,
    OpenCodeFailed,
    OpenCodeMissing,
    extract_opencode_text,
)
from mycoagent.node.workspace import assignment_workspace


def _work() -> ChildWork:
    return ChildWork(
        job_id="j",
        subtask_id="s",
        parent_node_id="p",
        parent_mailbox_url="http://127.0.0.1:1",
        description="write a note",
        payload={},
        status=SubtaskStatus.RUNNING,
    )


def test_extract_opencode_text_from_json_and_raw():
    assert extract_opencode_text('{"text": "hello"}') == "hello"
    assert extract_opencode_text('{"parts": [{"text": "a"}, {"text": "b"}]}') == "a\nb"
    assert extract_opencode_text("not json") == "not json"
    assert extract_opencode_text("") == ""


async def test_opencode_executor_runs_in_workspace_and_strips_paths():
    captured: dict[str, object] = {}

    def runner(argv: list[str], cwd: Path, timeout: float, env: dict[str, str]) -> tuple[int, str, str]:
        captured["argv"] = argv
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        captured["env"] = env
        note = cwd / "note.txt"
        note.write_text("ok", encoding="utf-8")
        return 0, json.dumps({"text": f"wrote {cwd}"}), ""

    work = _work()
    with assignment_workspace() as workspace:
        finished = await OpenCodeExecutor(binary="opencode", runner=runner).run(work, workspace)
        assert finished.status == SubtaskStatus.COMPLETED
        assert "wrote" in (finished.result or "")
        assert str(workspace.root) not in (finished.result or "")
        assert workspace.read_file("note.txt") == "ok"
        assert captured["cwd"] == workspace.root
        assert captured["argv"][0] == "opencode"
        assert captured["argv"][1:4] == ["run", "--format", "json"]
        assert "write a note" in captured["argv"][4]
        assert isinstance(captured["env"], dict)
        assert captured["env"].get("HOME") != str(workspace.root)


async def test_opencode_executor_nonzero_exit_fails():
    def runner(argv: list[str], cwd: Path, timeout: float, env: dict[str, str]) -> tuple[int, str, str]:
        del argv, cwd, timeout, env
        return 2, "", "boom"

    with assignment_workspace() as workspace:
        with pytest.raises(OpenCodeFailed, match="boom"):
            await OpenCodeExecutor(runner=runner).run(_work(), workspace)


async def test_opencode_executor_requires_workspace():
    with pytest.raises(RuntimeError, match="workspace"):
        await OpenCodeExecutor(runner=lambda *a: (0, "x", "")).run(_work(), None)


async def test_opencode_executor_missing_binary():
    with assignment_workspace() as workspace:
        with pytest.raises(OpenCodeMissing):
            await OpenCodeExecutor(binary="/no/such/opencode-mycoagent").run(_work(), workspace)
