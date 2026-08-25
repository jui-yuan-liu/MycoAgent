from pathlib import Path

import httpx

from cluster_harness import host_server, manager_server, node_server, wait_job
from mycoagent.artifacts import MemoryArtifactStore
from mycoagent.node.executor import ChildAgentExecutor
from mycoagent.node.llm import ScriptedLLM


def test_same_host_agents_can_dispatch_to_each_other(tmp_path):
    with manager_server(tmp_path) as manager:
        with host_server(
            manager,
            "default",
            [{"name": "alpha"}, {"name": "beta"}],
        ) as (url, host):
            by_name = {agent.name: agent for agent in host.agents.values()}
            alpha = by_name["alpha"]
            beta = by_name["beta"]
            assert alpha.node_id != beta.node_id
            assert alpha.mailbox_url.endswith(f"/agents/{alpha.node_id}")
            assert beta.mailbox_url.endswith(f"/agents/{beta.node_id}")
            submitted = httpx.post(
                f"{url}/agents/{alpha.node_id}/jobs",
                json={
                    "description": "same host",
                    "subtasks": [{"description": "child work", "skills": ["coding"], "tools": ["shell"]}],
                },
                timeout=10,
            )
            assert submitted.status_code == 200, submitted.text
            job = wait_job(f"{url}/agents/{alpha.node_id}", submitted.json()["job_id"])
            assert job["status"] == "completed"
            assert job["subtasks"][0]["assignee_node_id"] == beta.node_id
            missing = httpx.get(f"{url}/agents/{beta.node_id}/jobs/{job['job_id']}", timeout=5)
            assert missing.status_code == 404
            refused = httpx.post(
                f"{url}/agents/{alpha.node_id}/jobs/{job['job_id']}/forward",
                json={"description": "to self", "target_node_id": alpha.node_id, "skills": ["coding"]},
                timeout=10,
            )
            assert refused.status_code == 200
            forwarded = wait_job(f"{url}/agents/{alpha.node_id}", job["job_id"])
            assert forwarded["subtasks"][-1]["status"] == "failed"
            assert "self" in (forwarded["subtasks"][-1]["error"] or "")


def test_busy_is_per_agent_not_whole_host(tmp_path):
    with manager_server(tmp_path) as manager:
        with host_server(manager, "default", [{"name": "alpha"}, {"name": "beta"}]) as (url, host):
            health = httpx.get(f"{url}/health", timeout=5).json()
            assert health["role"] == "host"
            assert len(health["agents"]) == 2
            catalog = httpx.get(
                f"{manager}/catalog",
                params={"group": "default", "idle_only": True},
                timeout=5,
            ).json()
            assert {item["name"] for item in catalog} == {"alpha", "beta"}


def test_child_agent_loop_uploads_then_wipes_workspace(tmp_path):
    store = MemoryArtifactStore()
    llm = ScriptedLLM(
        [
            {
                "tool_calls": [
                    {
                        "name": "write_file",
                        "arguments": {"path": "out.txt", "content": "hello-artifact"},
                    }
                ]
            },
            {"content": "created out.txt with hello-artifact"},
        ]
    )
    executor = ChildAgentExecutor(llm, max_steps=6)
    with manager_server(tmp_path) as manager:
        with node_server(manager, "alpha", "default") as (alpha_url, _alpha):
            with node_server(
                manager,
                "beta",
                "default",
                executor=executor,
                artifact_store=store,
            ) as (_beta_url, beta):
                submitted = httpx.post(
                    f"{alpha_url}/jobs",
                    json={
                        "description": "write a file",
                        "subtasks": [{"description": "write out.txt", "skills": ["coding"], "tools": ["shell"]}],
                    },
                    timeout=10,
                )
                assert submitted.status_code == 200, submitted.text
                job = wait_job(alpha_url, submitted.json()["job_id"])
                assert job["status"] == "completed"
                result = job["subtasks"][0]["result"] or ""
                ids = job["subtasks"][0]["artifact_ids"]
                assert ids, job
                assert ids[0].endswith("out.txt")
                assert not ids[0].startswith("/")
                assert "hello-artifact" in store.objects[ids[0]].decode()
                assert beta.last_workspace_path is not None
                assert not Path(beta.last_workspace_path).exists()
                assert beta.last_workspace_path not in result
                assert "/tmp/" not in result
                dumped = httpx.get(f"{alpha_url}/jobs/{job['job_id']}", timeout=5).text
                assert beta.last_workspace_path not in dumped
