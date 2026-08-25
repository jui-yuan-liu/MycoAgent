from mycoagent.models import ChildWork, SubtaskStatus
from mycoagent.node.executor import ChildAgentExecutor, EchoExecutor
from mycoagent.node.llm import ScriptedLLM
from mycoagent.node.workspace import assignment_workspace


async def test_echo_executor_ignores_workspace():
    work = ChildWork(
        job_id="j",
        subtask_id="s",
        parent_node_id="p",
        parent_mailbox_url="http://127.0.0.1:1",
        description="echo me",
        payload={},
        status=SubtaskStatus.RUNNING,
    )
    finished = await EchoExecutor().run(work)
    assert finished.result == "done:echo me"


async def test_child_agent_executor_uses_shell_inside_workspace():
    llm = ScriptedLLM(
        [
            {
                "tool_calls": [
                    {"name": "write_file", "arguments": {"path": "n.txt", "content": "ok"}},
                    {"name": "shell", "arguments": {"command": "cat n.txt"}},
                ]
            },
            {"content": "read n.txt"},
        ]
    )
    work = ChildWork(
        job_id="j",
        subtask_id="s",
        parent_node_id="p",
        parent_mailbox_url="http://127.0.0.1:1",
        description="use shell",
        payload={},
        status=SubtaskStatus.RUNNING,
    )
    with assignment_workspace() as workspace:
        finished = await ChildAgentExecutor(llm, max_steps=4).run(work, workspace)
        assert finished.result == "read n.txt"
        assert workspace.read_file("n.txt") == "ok"
        assert str(workspace.root) not in (finished.result or "")
