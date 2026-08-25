from __future__ import annotations

import json
import re
from typing import Any

from mycoagent.models import NodeRecord, SubtaskSpec
from mycoagent.node.llm import LLMClient

PLANNER_SYSTEM = """You split a parent agent's job into subtasks for other agents in the same group.
Return JSON only: {"subtasks":[{"description":"...","skills":["..."],"tools":["..."]}]}.
Use catalog skills and tools. Do not assign work to the parent agent_id.
If the job is a single unit of work, return one subtask.
Nested parent agents are forbidden: only this parent will dispatch."""


class PlanningError(RuntimeError):
    pass


class TaskPlanner:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def plan(
        self,
        description: str,
        catalog: list[NodeRecord],
        parent_id: str,
    ) -> list[SubtaskSpec]:
        catalog_lines = []
        for node in catalog:
            catalog_lines.append(
                f"- id={node.id} name={node.name} status={node.status.value} "
                f"skills={node.skills} tools={node.tools_available} "
                f"models={[m.name for m in node.models]}"
            )
        catalog_text = "\n".join(catalog_lines) if catalog_lines else "(no idle peers)"
        user = (
            f"Job: {description}\n"
            f"Parent agent_id: {parent_id}\n"
            f"Same-group catalog:\n{catalog_text}\n"
            "Return JSON only."
        )
        response = await self.llm.chat(
            [
                {"role": "system", "content": PLANNER_SYSTEM},
                {"role": "user", "content": user},
            ]
        )
        if not response.content:
            raise PlanningError("planner returned empty content")
        try:
            payload = _extract_json(response.content)
        except (json.JSONDecodeError, ValueError) as exc:
            raise PlanningError(f"planner returned invalid JSON: {exc}") from exc
        raw_items = payload.get("subtasks")
        if not isinstance(raw_items, list) or not raw_items:
            raise PlanningError("planner returned no subtasks")
        specs: list[SubtaskSpec] = []
        for item in raw_items:
            if not isinstance(item, dict):
                raise PlanningError("planner subtask is not an object")
            description_text = str(item.get("description") or "").strip()
            if not description_text:
                raise PlanningError("planner subtask missing description")
            specs.append(
                SubtaskSpec(
                    description=description_text,
                    skills=_as_str_list(item.get("skills")),
                    tools=_as_str_list(item.get("tools")),
                    payload=dict(item.get("payload") or {}) if isinstance(item.get("payload"), dict) else {},
                )
            )
        return specs


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    else:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload
