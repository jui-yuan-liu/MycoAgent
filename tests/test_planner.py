import json

import httpx

from cluster_harness import manager_server, node_server, wait_job
from mycoagent.node.llm import ScriptedLLM
from mycoagent.node.planner import TaskPlanner


def test_parent_llm_splits_description_into_subtasks(tmp_path):
    llm = ScriptedLLM(
        [
            {
                "content": json.dumps(
                    {
                        "subtasks": [
                            {
                                "description": "child work",
                                "skills": ["coding"],
                                "tools": ["shell"],
                            }
                        ]
                    }
                )
            }
        ]
    )
    planner = TaskPlanner(llm)
    with manager_server(tmp_path) as manager:
        with node_server(manager, "alpha", "default", planner=planner) as (alpha_url, alpha):
            with node_server(manager, "beta", "default") as (_beta_url, beta):
                submitted = httpx.post(
                    f"{alpha_url}/jobs",
                    json={"description": "please split this", "subtasks": []},
                    timeout=10,
                )
                assert submitted.status_code == 200, submitted.text
                job = wait_job(alpha_url, submitted.json()["job_id"])
                assert job["status"] == "completed"
                assert len(job["subtasks"]) == 1
                assert job["subtasks"][0]["description"] == "child work"
                assert job["subtasks"][0]["assignee_node_id"] == beta.node_id
                assert job["parent_node_id"] == alpha.node_id
                missing = httpx.get(f"{_beta_url}/jobs/{job['job_id']}", timeout=5)
                assert missing.status_code == 404


def test_explicit_subtasks_skip_planner(tmp_path):
    llm = ScriptedLLM([{"content": "should not be called"}])
    planner = TaskPlanner(llm)
    with manager_server(tmp_path) as manager:
        with node_server(manager, "alpha", "default", planner=planner) as (alpha_url, _alpha):
            with node_server(manager, "beta", "default") as (_beta_url, _beta):
                submitted = httpx.post(
                    f"{alpha_url}/jobs",
                    json={
                        "description": "ctl subtasks",
                        "subtasks": [{"description": "already split", "skills": ["coding"]}],
                    },
                    timeout=10,
                )
                job = wait_job(alpha_url, submitted.json()["job_id"])
                assert job["status"] == "completed"
                assert job["subtasks"][0]["description"] == "already split"
                assert llm.calls == []


def test_planning_failure_marks_job_failed(tmp_path):
    llm = ScriptedLLM([{"content": "not-json"}])
    planner = TaskPlanner(llm)
    with manager_server(tmp_path) as manager:
        with node_server(manager, "alpha", "default", planner=planner) as (alpha_url, _alpha):
            submitted = httpx.post(
                f"{alpha_url}/jobs",
                json={"description": "cannot plan", "subtasks": []},
                timeout=10,
            )
            assert submitted.status_code == 200
            job = submitted.json()
            assert job["status"] == "failed"
            assert "planning failed" in (job["error"] or "")


def test_child_cannot_nested_forward_same_job(tmp_path):
    with manager_server(tmp_path) as manager:
        with node_server(manager, "alpha", "default") as (alpha_url, _alpha):
            with node_server(manager, "beta", "default") as (beta_url, _beta):
                submitted = httpx.post(
                    f"{alpha_url}/jobs",
                    json={
                        "description": "one",
                        "subtasks": [{"description": "x", "skills": ["coding"]}],
                    },
                    timeout=10,
                )
                job = wait_job(alpha_url, submitted.json()["job_id"])
                nested = httpx.post(
                    f"{beta_url}/jobs/{job['job_id']}/forward",
                    json={"description": "nested parent"},
                    timeout=10,
                )
                assert nested.status_code == 404
