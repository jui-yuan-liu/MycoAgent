from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from mycoagent.models import ChildWork, SubtaskStatus
from mycoagent.node.llm import LLMClient, LLMResponse
from mycoagent.node.workspace import Workspace, strip_local_paths

CHILD_SYSTEM = (
    "You are a child agent. Complete the assigned work using tools. "
    "The shell and file tools operate only inside this assignment workspace. "
    "Do not mention local filesystem paths in the final answer; summarize and name artifacts instead."
)

SHELL_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Run a shell command with cwd set to the assignment workspace.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a UTF-8 text file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files in a workspace directory (relative path, default '.').",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
]


class Executor(Protocol):
    async def run(self, work: ChildWork, workspace: Workspace | None = None) -> ChildWork:
        """Execute assigned child work. Echo is for tests; agents use the tool loop."""


class EchoExecutor:
    """Deterministic worker for tests and runs without an LLM."""

    async def run(self, work: ChildWork, workspace: Workspace | None = None) -> ChildWork:
        del workspace
        await asyncio.sleep(0.05)
        result = f"done:{work.description}"
        if work.payload:
            result = f"{result} payload={work.payload}"
        return work.model_copy(update={"status": SubtaskStatus.COMPLETED, "result": result})


class ChildAgentExecutor:
    """OpenAI-compatible tool-calling loop. Shell is a workspace-scoped tool."""

    def __init__(self, llm: LLMClient, max_steps: int = 12, shell_timeout: float = 30.0) -> None:
        self.llm = llm
        self.max_steps = max_steps
        self.shell_timeout = shell_timeout

    async def run(self, work: ChildWork, workspace: Workspace | None = None) -> ChildWork:
        if workspace is None:
            raise RuntimeError("ChildAgentExecutor requires an assignment workspace")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": CHILD_SYSTEM},
            {
                "role": "user",
                "content": _user_prompt(work),
            },
        ]
        last_text = ""
        for _ in range(self.max_steps):
            response = await self.llm.chat(messages, tools=SHELL_TOOLS)
            if response.tool_calls:
                messages.append(_assistant_tool_message(response))
                for call in response.tool_calls:
                    output = self._dispatch_tool(workspace, call.name, call.arguments)
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": output}
                    )
                continue
            last_text = strip_local_paths(response.content or "", workspace.root)
            if last_text:
                return work.model_copy(
                    update={"status": SubtaskStatus.COMPLETED, "result": last_text}
                )
        raise RuntimeError(f"agent loop exceeded max_steps={self.max_steps}")

    def _dispatch_tool(self, workspace: Workspace, name: str, arguments: dict[str, Any]) -> str:
        try:
            if name == "shell":
                return workspace.run_shell(str(arguments.get("command") or ""), timeout=self.shell_timeout)
            if name == "write_file":
                path = workspace.write_file(str(arguments.get("path") or ""), str(arguments.get("content") or ""))
                return f"wrote {path}"
            if name == "read_file":
                return workspace.read_file(str(arguments.get("path") or ""))
            if name == "list_dir":
                entries = workspace.list_dir(str(arguments.get("path") or "."))
                return "\n".join(entries) if entries else "(empty)"
            return f"unknown tool: {name}"
        except Exception as exc:  # noqa: BLE001 — tool errors go back to the model
            return strip_local_paths(f"tool error: {exc}", workspace.root)


def _user_prompt(work: ChildWork) -> str:
    payload = json.dumps(work.payload, ensure_ascii=False, default=str) if work.payload else "{}"
    return (
        f"Job {work.job_id} subtask {work.subtask_id}\n"
        f"Description: {work.description}\n"
        f"Payload JSON: {payload}\n"
        "Finish the work, then reply with a short summary and any artifact file names."
    )


def _assistant_tool_message(response: LLMResponse) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": response.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in response.tool_calls
        ],
    }
