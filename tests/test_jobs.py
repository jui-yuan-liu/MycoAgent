import time

import httpx

from cluster_harness import manager_server, node_server


def test_parent_dispatches_only_to_same_group_other_node(tmp_path):
    with manager_server(tmp_path) as manager:
        with node_server(manager, "alpha", "default") as (alpha_url, alpha):
            with node_server(manager, "beta", "default") as (beta_url, beta):
                submitted = httpx.post(
                    f"{alpha_url}/jobs",
                    json={
                        "description": "parent job",
                        "subtasks": [
                            {"description": "child work", "skills": ["coding"], "tools": ["shell"]}
                        ],
                    },
                    timeout=10.0,
                )
                assert submitted.status_code == 200, submitted.text
                job = submitted.json()
                assert job["parent_node_id"] == alpha.node_id
                assert job["subtasks"][0]["assignee_node_id"] == beta.node_id
                deadline = time.time() + 5
                current = job
                while time.time() < deadline:
                    current = httpx.get(f"{alpha_url}/jobs/{job['job_id']}", timeout=5).json()
                    if current["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.1)
                assert current["status"] == "completed"
                assert current["subtasks"][0]["result"].startswith("done:child work")
                missing = httpx.get(f"{beta_url}/jobs/{job['job_id']}", timeout=5)
                assert missing.status_code == 404
                child = httpx.get(f"{beta_url}/child", timeout=5).json()
                assert child["current"] is None


def test_cannot_dispatch_when_alone_in_group(tmp_path):
    with manager_server(tmp_path) as manager:
        with node_server(manager, "solo", "default") as (url, _runtime):
            submitted = httpx.post(
                f"{url}/jobs",
                json={
                    "description": "needs a child",
                    "subtasks": [{"description": "x", "skills": ["coding"]}],
                },
                timeout=10.0,
            )
            assert submitted.status_code == 200
            job = submitted.json()
            assert job["status"] == "failed"
            assert "no idle matching node" in (job["subtasks"][0]["error"] or "")


def test_local_job_without_subtasks_stays_on_parent(tmp_path):
    with manager_server(tmp_path) as manager:
        with node_server(manager, "solo", "default") as (url, runtime):
            submitted = httpx.post(
                f"{url}/jobs",
                json={"description": "only me", "subtasks": []},
                timeout=10.0,
            )
            assert submitted.status_code == 200
            job = submitted.json()
            assert job["status"] == "completed"
            assert job["local_result"].startswith("done:only me")
            assert job["parent_node_id"] == runtime.node_id


def test_cross_group_nodes_are_not_catalog_matches(tmp_path):
    with manager_server(tmp_path) as manager:
        httpx.post(f"{manager}/groups", json={"name": "other"}, timeout=5)
        with node_server(manager, "a", "default") as (a_url, _a):
            with node_server(manager, "b", "other") as (_b_url, b):
                catalog = httpx.get(
                    f"{manager}/catalog",
                    params={"group": "default", "exclude_node_id": "nope"},
                    timeout=5,
                ).json()
                ids = {item["id"] for item in catalog}
                assert b.node_id not in ids
                submitted = httpx.post(
                    f"{a_url}/jobs",
                    json={"description": "need help", "subtasks": [{"description": "x", "skills": ["coding"]}]},
                    timeout=10,
                )
                job = submitted.json()
                assert job["status"] == "failed"
