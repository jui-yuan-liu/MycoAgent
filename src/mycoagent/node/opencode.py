from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from mycoagent.models import ChildWork, SubtaskStatus
from mycoagent.node.workspace import Workspace, strip_local_paths

OpenCodeRunner = Callable[[list[str], Path, float, dict[str, str]], tuple[int, str, str]]


class OpenCodeMissing(FileNotFoundError):
    pass


class OpenCodeFailed(RuntimeError):
    pass


def _default_runner(
    argv: list[str],
    cwd: Path,
    timeout: float,
    env: dict[str, str],
) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
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


def opencode_prompt(work: ChildWork) -> str:
    """Prompt for opencode run: assignment context, write into cwd only."""
    payload = work.payload or {}
    artifact_ids = payload.get("artifact_ids") if isinstance(payload, dict) else None
    names: list[str] = []
    if isinstance(artifact_ids, list):
        for item in artifact_ids:
            text = str(item).strip()
            if text:
                names.append(Path(text).name or text)
    payload_json = json.dumps(payload, ensure_ascii=False, default=str) if payload else "{}"
    lines = [
        f"Job {work.job_id} subtask {work.subtask_id}",
        f"Description: {work.description}",
        f"Payload JSON: {payload_json}",
    ]
    if names:
        lines.append("Referenced artifact file names: " + ", ".join(names))
    lines.append(
        "Work only inside the current working directory. "
        "Write outputs as files there. "
        "Do not mention absolute local filesystem paths in the final answer; "
        "summarize and name artifact files instead."
    )
    return "\n".join(lines)


def build_opencode_env(
    *,
    base: dict[str, str] | None = None,
    config: str | None = None,
    config_dir: str | None = None,
) -> dict[str, str]:
    """Inherit process env; keep real HOME so global OpenCode skills/auth/MCP load."""
    env = dict(base if base is not None else os.environ)
    home = env.get("HOME") or str(Path.home())
    env["HOME"] = home
    cfg = (config if config is not None else os.environ.get("OPENCODE_CONFIG", "")).strip()
    cfg_dir = (
        config_dir if config_dir is not None else os.environ.get("OPENCODE_CONFIG_DIR", "")
    ).strip()
    if cfg:
        env["OPENCODE_CONFIG"] = cfg
    if cfg_dir:
        env["OPENCODE_CONFIG_DIR"] = cfg_dir
    return env


class OpenCodeExecutor:
    """Run local `opencode run` inside the assignment workspace. Not A2A."""

    def __init__(
        self,
        binary: str | None = None,
        timeout: float = 120.0,
        runner: OpenCodeRunner | None = None,
        *,
        model: str | None = None,
        agent: str | None = None,
        auto: bool = False,
        attach: str | None = None,
        config: str | None = None,
        config_dir: str | None = None,
    ) -> None:
        self.binary = binary or os.environ.get("MYCOAGENT_OPENCODE_BIN", "opencode")
        self.timeout = timeout
        self.runner = runner or _default_runner
        self.model = (model or os.environ.get("MYCOAGENT_OPENCODE_MODEL", "")).strip() or None
        self.agent = (agent or os.environ.get("MYCOAGENT_OPENCODE_AGENT", "")).strip() or None
        self.auto = auto or os.environ.get("MYCOAGENT_OPENCODE_AUTO", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        self.attach = (
            attach if attach is not None else os.environ.get("MYCOAGENT_OPENCODE_ATTACH", "")
        ).strip() or None
        self.config = config
        self.config_dir = config_dir

    def _argv(self, work: ChildWork) -> list[str]:
        resolved = shutil.which(self.binary) if self.runner is _default_runner else self.binary
        if self.runner is _default_runner and not resolved:
            raise OpenCodeMissing(
                f"opencode not found ({self.binary!r}); install OpenCode or set MYCOAGENT_OPENCODE_BIN"
            )
        argv = [resolved or self.binary, "run", "--format", "json"]
        if self.attach:
            argv.extend(["--attach", self.attach])
        if self.model:
            argv.extend(["--model", self.model])
        if self.agent:
            argv.extend(["--agent", self.agent])
        if self.auto:
            argv.append("--auto")
        argv.append(opencode_prompt(work))
        return argv

    async def run(self, work: ChildWork, workspace: Workspace | None = None) -> ChildWork:
        if workspace is None:
            raise RuntimeError("OpenCodeExecutor requires an assignment workspace")
        argv = self._argv(work)
        env = build_opencode_env(config=self.config, config_dir=self.config_dir)
        code, stdout, stderr = self.runner(argv, workspace.root, self.timeout, env)
        combined = "\n".join(part for part in (stdout, stderr) if part).strip()
        if code != 0:
            detail = strip_local_paths(combined or f"exit={code}", workspace.root)
            raise OpenCodeFailed(detail)
        text = strip_local_paths(extract_opencode_text(stdout) or combined, workspace.root)
        if not text:
            raise OpenCodeFailed("opencode produced no output")
        return work.model_copy(update={"status": SubtaskStatus.COMPLETED, "result": text})
