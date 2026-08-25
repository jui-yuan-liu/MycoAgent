from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from mycoagent.models import ChildWork, SubtaskStatus
from mycoagent.node.executor import _user_prompt
from mycoagent.node.workspace import Workspace, strip_local_paths

OpenCodeRunner = Callable[[list[str], Path, float], tuple[int, str, str]]


class OpenCodeMissing(FileNotFoundError):
    pass


class OpenCodeFailed(RuntimeError):
    pass


def _default_runner(argv: list[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OpenCodeMissing(argv[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"opencode timed out after {timeout}s") from exc
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def extract_opencode_text(stdout: str) -> str:
    """Best-effort parse of `opencode run --format json`, else raw stdout."""
    raw = stdout.strip()
    if not raw:
        return ""
    candidates = [raw]
    if "\n" in raw:
        candidates.append(raw.splitlines()[-1].strip())
    for chunk in candidates:
        try:
            payload = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        text = _json_to_text(payload)
        if text:
            return text
    return raw


def _json_to_text(payload: object) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("text", "content", "message", "result"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, dict):
                nested = _json_to_text(value)
                if nested:
                    return nested
        parts = payload.get("parts")
        if isinstance(parts, list):
            texts = [_json_to_text(part) for part in parts]
            joined = "\n".join(item for item in texts if item)
            if joined:
                return joined
        info = payload.get("info")
        if isinstance(info, dict):
            nested = _json_to_text(info)
            if nested:
                return nested
        part = payload.get("part")
        if isinstance(part, dict):
            return _json_to_text(part)
    if isinstance(payload, list):
        texts = [_json_to_text(item) for item in payload]
        return "\n".join(item for item in texts if item)
    return ""


class OpenCodeExecutor:
    """Run local `opencode run` inside the assignment workspace. Not A2A."""

    def __init__(
        self,
        binary: str | None = None,
        timeout: float = 120.0,
        runner: OpenCodeRunner | None = None,
    ) -> None:
        self.binary = binary or os.environ.get("MYCOAGENT_OPENCODE_BIN", "opencode")
        self.timeout = timeout
        self.runner = runner or _default_runner

    async def run(self, work: ChildWork, workspace: Workspace | None = None) -> ChildWork:
        if workspace is None:
            raise RuntimeError("OpenCodeExecutor requires an assignment workspace")
        resolved = shutil.which(self.binary) if self.runner is _default_runner else self.binary
        if self.runner is _default_runner and not resolved:
            raise OpenCodeMissing(
                f"opencode not found ({self.binary!r}); install OpenCode or set MYCOAGENT_OPENCODE_BIN"
            )
        argv = [
            resolved or self.binary,
            "run",
            "--format",
            "json",
            _user_prompt(work),
        ]
        code, stdout, stderr = self.runner(argv, workspace.root, self.timeout)
        combined = "\n".join(part for part in (stdout, stderr) if part).strip()
        if code != 0:
            detail = strip_local_paths(combined or f"exit={code}", workspace.root)
            raise OpenCodeFailed(detail)
        text = strip_local_paths(extract_opencode_text(stdout) or combined, workspace.root)
        if not text:
            raise OpenCodeFailed("opencode produced no output")
        return work.model_copy(update={"status": SubtaskStatus.COMPLETED, "result": text})
