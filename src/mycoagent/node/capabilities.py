from __future__ import annotations

import json
from pathlib import Path


def merge_names(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for item in group:
            name = item.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(name)
    return out


def load_names(path: str | Path, *, kind: str) -> list[str]:
    """Load skill or tool names from a file or directory.

    File: JSON list, JSON object with ``kind`` key, or one name per line.
    Directory: subdirectory names (Cursor/OpenCode ``SKILL.md`` folders) and
    ``*.md``/``*.json``/``*.txt`` stems. A nested ``skills/`` or ``tools/``
    folder is used when present.
    """
    root = Path(path).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"{kind} path not found: {root}")
    if root.is_dir():
        return _from_dir(root, kind=kind)
    return _from_file(root, kind=kind)


def load_capabilities(path: str | Path) -> tuple[list[str], list[str]]:
    """Load both skills and tools from a JSON file or a directory."""
    root = Path(path).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"capabilities path not found: {root}")
    if root.is_file():
        data = json.loads(root.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{root} must be a JSON object with skills and/or tools")
        return _as_str_list(data.get("skills")), _as_str_list(data.get("tools"))
    cap = root / "capabilities.json"
    if cap.is_file():
        return load_capabilities(cap)
    return load_names(root, kind="skills"), load_names(root, kind="tools")


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError("skills/tools must be a list or comma-separated string")


def _from_file(path: Path, *, kind: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
        if isinstance(data, dict):
            if kind in data:
                return _as_str_list(data[kind])
            if "name" in data and isinstance(data["name"], str) and data["name"].strip():
                return [data["name"].strip()]
            raise ValueError(f"{path} JSON must be a list or an object with {kind!r}")
        raise ValueError(f"{path} JSON must be a list or object")
    names: list[str] = []
    for line in text.splitlines():
        piece = line.strip()
        if not piece or piece.startswith("#"):
            continue
        names.extend(item.strip() for item in piece.split(",") if item.strip())
    return merge_names(names)


def _from_dir(root: Path, *, kind: str) -> list[str]:
    nested = root / kind
    scan = nested if nested.is_dir() else root
    cap = scan / "capabilities.json"
    if cap.is_file() and nested.is_dir():
        skills, tools = load_capabilities(cap)
        return skills if kind == "skills" else tools
    names: list[str] = []
    for child in sorted(scan.iterdir(), key=lambda p: p.name.lower()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            names.append(child.name)
            continue
        if child.name.lower() in {"readme.md", "capabilities.json"}:
            continue
        if child.suffix.lower() in {".md", ".json", ".txt", ".toml"}:
            names.append(child.stem)
    return merge_names(names)
