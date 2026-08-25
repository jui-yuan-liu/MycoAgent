from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

AGENT_ID_ENV = "MYCOAGENT_AGENT_ID"
ID_FILE_ENV = "MYCOAGENT_ID_FILE"


def resolve_agent_id(
    *,
    explicit: str | None = None,
    id_file: str | Path | None = None,
    env_id: str | None = None,
) -> str:
    """Reuse a catalog id from explicit value, env, or file; write the file when given."""
    if env_id is None:
        raw_env = os.environ.get(AGENT_ID_ENV)
        env_id = raw_env.strip() if raw_env else None
    if explicit and explicit.strip():
        agent_id = explicit.strip()
    elif env_id:
        agent_id = env_id
    elif id_file and Path(id_file).is_file():
        stored = Path(id_file).read_text(encoding="utf-8").strip()
        agent_id = stored or str(uuid4())
    else:
        agent_id = str(uuid4())
    if id_file:
        path = Path(id_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(agent_id + "\n", encoding="utf-8")
    return agent_id
