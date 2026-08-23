from __future__ import annotations

import os
import platform
import shutil

from mycoagent.models import MachineSpec, ModelSpec, RuntimeKind, SystemSpec
from mycoagent.version import __version__


def detect_machine() -> MachineSpec:
    cpu = float(os.cpu_count() or 1)
    memory_mb = 0
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        memory_mb = int(page * pages / (1024 * 1024))
    except (ValueError, OSError, AttributeError):
        memory_mb = 0
    disk_gb: float | None = None
    try:
        usage = shutil.disk_usage("/")
        disk_gb = round(usage.total / (1024**3), 1)
    except OSError:
        disk_gb = None
    gpu = os.environ.get("MYCOAGENT_GPU")
    return MachineSpec(cpu_cores=cpu, memory_mb=memory_mb, gpu=gpu, disk_gb=disk_gb)


def detect_system() -> SystemSpec:
    runtime = (
        RuntimeKind.CONTAINER
        if os.path.exists("/.dockerenv") or os.environ.get("KUBERNETES_SERVICE_HOST")
        else RuntimeKind.BARE_METAL
    )
    return SystemSpec(
        os=platform.system().lower(),
        arch=platform.machine(),
        runtime=runtime,
        mycoagent_version=__version__,
    )


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_models(value: str | None) -> list[ModelSpec]:
    """Format: name:source[:context],name:source  e.g. llama3:local:8192,gpt-4:api"""
    models: list[ModelSpec] = []
    for item in parse_csv(value):
        parts = item.split(":")
        name = parts[0]
        source = parts[1] if len(parts) > 1 else "local"
        context = int(parts[2]) if len(parts) > 2 else None
        models.append(ModelSpec.model_validate({"name": name, "source": source, "context_window": context}))
    return models
