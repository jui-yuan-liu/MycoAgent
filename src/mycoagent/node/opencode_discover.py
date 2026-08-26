from __future__ import annotations

import os
from pathlib import Path

from mycoagent.node.capabilities import load_names, merge_names

OPENCODE_TOOL_TAG = "opencode"


def default_opencode_skill_dirs(*, home: Path | None = None) -> list[Path]:
    """Standard OpenCode / Claude / agents skill roots (may not exist)."""
    root = home if home is not None else Path.home()
    dirs = [
        root / ".config" / "opencode" / "skills",
        root / ".claude" / "skills",
        root / ".agents" / "skills",
    ]
    config_dir = os.environ.get("OPENCODE_CONFIG_DIR", "").strip()
    if config_dir:
        dirs.append(Path(config_dir).expanduser() / "skills")
    return dirs


def discover_opencode_catalog(
    *,
    extra_dirs: list[str | Path] | None = None,
    home: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Return (skills, tools) discovered from local OpenCode skill directories.

    Tools always include the ``opencode`` tag so catalog matching can select
    OpenCode Hosts. Skills are folder / file stems under known roots.
    """
    skills: list[str] = []
    roots = list(default_opencode_skill_dirs(home=home))
    for raw in extra_dirs or []:
        roots.append(Path(raw).expanduser())
    for path in roots:
        if not path.is_dir():
            continue
        try:
            skills = merge_names(skills, load_names(path, kind="skills"))
        except (OSError, ValueError):
            continue
    return skills, [OPENCODE_TOOL_TAG]
