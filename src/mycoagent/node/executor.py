from __future__ import annotations

import asyncio

from mycoagent.models import ChildWork, SubtaskStatus


class EchoExecutor:
    """Phase-1 worker: children only execute the assigned item. No job tree."""

    async def run(self, work: ChildWork) -> ChildWork:
        await asyncio.sleep(0.05)
        result = f"done:{work.description}"
        if work.payload:
            result = f"{result} payload={work.payload}"
        return work.model_copy(
            update={"status": SubtaskStatus.COMPLETED, "result": result}
        )
