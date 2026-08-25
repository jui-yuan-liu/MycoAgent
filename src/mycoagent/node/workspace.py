from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class WorkspaceEscape(ValueError):
    pass


class Workspace:
    """Per-assignment directory. Tools must stay inside ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve_path(self, user_path: str) -> Path:
        raw = Path(user_path)
        target = raw.resolve() if raw.is_absolute() else (self.root / raw).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceEscape(f"path escapes workspace: {user_path}") from exc
        return target

    def write_file(self, user_path: str, content: str) -> str:
        path = self.resolve_path(user_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(Path(user_path).as_posix())

    def read_file(self, user_path: str) -> str:
        path = self.resolve_path(user_path)
        return path.read_text(encoding="utf-8")

    def list_dir(self, user_path: str = ".") -> list[str]:
        path = self.resolve_path(user_path)
        if not path.is_dir():
            raise NotADirectoryError(user_path)
        names: list[str] = []
        for child in sorted(path.iterdir()):
            rel = child.relative_to(self.root).as_posix()
            names.append(rel + ("/" if child.is_dir() else ""))
        return names

    def run_shell(self, command: str, timeout: float = 30.0) -> str:
        env = {**os.environ, "HOME": str(self.root), "TMPDIR": str(self.root)}
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"shell timed out after {timeout}s") from exc
        out = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode != 0:
            suffix = f"exit={completed.returncode}"
            out = f"{out.rstrip()}\n{suffix}" if out.strip() else suffix
        return strip_local_paths(out, self.root)

    def iter_files(self) -> Iterator[Path]:
        for path in self.root.rglob("*"):
            if path.is_file():
                yield path


def strip_local_paths(text: str, root: Path) -> str:
    if not text:
        return text
    resolved = root.resolve()
    variants = {str(root), str(resolved), root.as_posix(), resolved.as_posix()}
    private = str(resolved)
    if private.startswith("/private/"):
        variants.add(private.removeprefix("/private"))
    result = text
    for variant in sorted(variants, key=len, reverse=True):
        if variant:
            result = result.replace(variant, "[workspace]")
    return result


@contextmanager
def assignment_workspace(prefix: str = "mycoagent-") -> Iterator[Workspace]:
    root = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield Workspace(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
