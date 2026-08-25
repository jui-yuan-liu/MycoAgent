import pytest

from mycoagent.artifacts import MemoryArtifactStore, artifact_key


@pytest.mark.asyncio
async def test_memory_artifact_store_roundtrip():
    store = MemoryArtifactStore()
    key = await store.put(
        group="default",
        job_id="job1",
        subtask_id="st1",
        name="out.txt",
        data=b"hello",
    )
    assert key == "default/job1/st1/out.txt"
    assert key == artifact_key("default", "job1", "st1", "out.txt")
    assert "/" in key
    assert not key.startswith("/")
    assert await store.get(key) == b"hello"


def test_artifact_key_rejects_parent_segments():
    with pytest.raises(ValueError):
        artifact_key("g", "j", "s", "../secret")
