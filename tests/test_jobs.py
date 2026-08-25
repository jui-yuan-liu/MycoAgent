import json
import time

import httpx

from cluster_harness import manager_server, node_server


def _wait_job(node_url: str, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    current: dict | None = None
    while time.time() < deadline:
        current = httpx.get(f"{node_url}/jobs/{job_id}", timeout=5).json()
        if current["status"] in {"completed", "failed"}:
            return current
        time.sleep(0.1)
    assert current is not None
    return current


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
                current = _wait_job(alpha_url, job["job_id"])
                assert current["status"] == "completed"
                assert current["subtasks"][0]["result"].startswith("done:child work")
                assert current["subtasks"][0]["artifact_ids"] == []
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


def test_unapproved_node_cannot_submit_as_parent(tmp_path):
    with manager_server(tmp_path) as manager:
        created = httpx.post(
            f"{manager}/groups",
            json={"name": "locked", "join_mode": "manual"},
            timeout=5,
        )
        assert created.status_code == 200
        with node_server(manager, "waiter", "locked") as (url, runtime):
            assert runtime.record is not None
            assert runtime.record.membership_status == "pending"
            catalog = httpx.get(f"{manager}/catalog", params={"group": "locked"}, timeout=5).json()
            assert catalog == []
            submitted = httpx.post(
                f"{url}/jobs",
                json={"description": "should fail", "subtasks": []},
                timeout=10,
            )
            assert submitted.status_code == 403
            assert "approved" in submitted.json()["detail"]
            approved = httpx.post(
                f"{manager}/groups/locked/approve/{runtime.node_id}", timeout=5
            )
            assert approved.status_code == 200
            allowed = httpx.post(
                f"{url}/jobs",
                json={"description": "only me", "subtasks": []},
                timeout=10,
            )
            assert allowed.status_code == 200
            assert allowed.json()["status"] == "completed"


def test_unauthorized_member_cannot_submit_as_parent(tmp_path):
    with manager_server(tmp_path) as manager:
        created = httpx.post(
            f"{manager}/groups",
            json={"name": "work", "allow_parent": ["boss"]},
            timeout=5,
        )
        assert created.status_code == 200
        with node_server(manager, "boss", "work") as (boss_url, _boss):
            with node_server(manager, "worker", "work") as (worker_url, _worker):
                denied = httpx.post(
                    f"{worker_url}/jobs",
                    json={"description": "nope", "subtasks": []},
                    timeout=10,
                )
                assert denied.status_code == 403
                assert "not allowed to submit jobs as parent" in denied.json()["detail"]
                allowed = httpx.post(
                    f"{boss_url}/jobs",
                    json={
                        "description": "ok",
                        "subtasks": [{"description": "help", "skills": ["coding"]}],
                    },
                    timeout=10,
                )
                assert allowed.status_code == 200
                job = _wait_job(boss_url, allowed.json()["job_id"])
                assert job["status"] == "completed"


def test_parent_forwards_sibling_result_without_peer_network(tmp_path):
    with manager_server(tmp_path) as manager:
        with node_server(manager, "alpha", "default") as (alpha_url, alpha):
            with node_server(manager, "beta", "default") as (beta_url, beta):
                with node_server(manager, "gamma", "default") as (gamma_url, gamma):
                    submitted = httpx.post(
                        f"{alpha_url}/jobs",
                        json={
                            "description": "two step",
                            "subtasks": [
                                {
                                    "description": "step-a",
                                    "skills": ["coding"],
                                    "tools": ["shell"],
                                }
                            ],
                        },
                        timeout=10,
                    )
                    assert submitted.status_code == 200, submitted.text
                    first_job = _wait_job(alpha_url, submitted.json()["job_id"])
                    assert first_job["status"] == "completed"
                    first = first_job["subtasks"][0]
                    first_mailbox = first["assignee_mailbox_url"]
                    other = gamma if first["assignee_node_id"] == beta.node_id else beta
                    other_url = gamma_url if other is gamma else beta_url
                    forwarded = httpx.post(
                        f"{alpha_url}/jobs/{first_job['job_id']}/forward",
                        json={
                            "description": "step-b",
                            "skills": ["coding"],
                            "tools": ["shell"],
                            "source_subtask_id": first["id"],
                            "target_node_id": other.node_id,
                        },
                        timeout=10,
                    )
                    assert forwarded.status_code == 200, forwarded.text
                    final = _wait_job(alpha_url, first_job["job_id"])
                    assert final["status"] == "completed"
                    assert len(final["subtasks"]) == 2
                    second = final["subtasks"][1]
                    assert second["assignee_node_id"] == other.node_id
                    assert second["payload"]["source_result"] == first["result"]
                    assert first["id"] == second["payload"]["source_subtask_id"]
                    dumped = json.dumps(second["payload"])
                    assert first_mailbox not in dumped
                    assert "mailbox_url" not in dumped
                    missing = httpx.get(f"{other_url}/jobs/{first_job['job_id']}", timeout=5)
                    assert missing.status_code == 404
                    child_forward = httpx.post(
                        f"{other_url}/jobs/{first_job['job_id']}/forward",
                        json={"description": "nested"},
                        timeout=10,
                    )
                    assert child_forward.status_code == 404
