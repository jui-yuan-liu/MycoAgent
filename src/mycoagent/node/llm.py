from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

import httpx


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """OpenAI-compatible chat completion."""


class OpenAICompatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._http = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = await self._http.post(
            f"{self.base_url}/chat/completions",
            json=body,
            headers=headers,
        )
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        return _parse_message(message)


class ScriptedLLM:
    """Deterministic stand-in for tests. Each chat() pops the next scripted reply."""

    def __init__(self, script: list[LLMResponse | dict[str, Any]] | None = None) -> None:
        self.script: list[LLMResponse] = [
            item if isinstance(item, LLMResponse) else _from_dict(item) for item in (script or [])
        ]
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        del tools
        self.calls.append(messages)
        if not self.script:
            return LLMResponse(content="done")
        return self.script.pop(0)


def llm_from_env() -> OpenAICompatClient | None:
    base = os.environ.get("MYCOAGENT_LLM_BASE_URL", "").strip()
    if not base:
        return None
    return OpenAICompatClient(
        base_url=base,
        api_key=os.environ.get("MYCOAGENT_LLM_API_KEY", ""),
        model=os.environ.get("MYCOAGENT_LLM_MODEL", "gpt-4o-mini"),
    )


def _from_dict(item: dict[str, Any]) -> LLMResponse:
    calls = []
    for raw in item.get("tool_calls") or []:
        args = raw.get("arguments", {})
        if isinstance(args, str):
            args = json.loads(args or "{}")
        calls.append(
            ToolCall(
                id=raw.get("id") or f"call_{uuid4().hex[:8]}",
                name=raw["name"],
                arguments=args,
            )
        )
    return LLMResponse(content=item.get("content"), tool_calls=calls)


def _parse_message(message: dict[str, Any]) -> LLMResponse:
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        fn = raw.get("function") or {}
        args_raw = fn.get("arguments") or "{}"
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw or "{}")
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
        else:
            args = dict(args_raw)
        calls.append(
            ToolCall(
                id=str(raw.get("id") or f"call_{uuid4().hex[:8]}"),
                name=str(fn.get("name") or ""),
                arguments=args,
            )
        )
    content = message.get("content")
    return LLMResponse(content=content if content else None, tool_calls=calls)
