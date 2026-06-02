from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.checks.surface import CheckSurface


def client(tmp_path):
    surface = CheckSurface(f"sqlite:///{tmp_path / 'checks.db'}")
    return TestClient(create_app(check_surface=surface))


def a_run(**over):
    body = {
        "check_name": "wardline",
        "run_id": "run-1",
        "commit_sha": "a" * 40,
        "outcome": "pass",
        "branch": "main",
        "pr": None,
        "ran_against": "tree:deadbeef",
        "rule_set": "wardline@1",
        "policy_version": "p1",
        "started_at": "2026-06-01T00:00:00+00:00",
        "finished_at": "2026-06-01T00:01:00+00:00",
    }
    body.update(over)
    return body


def test_post_check_then_get_by_commit(tmp_path):
    c = client(tmp_path)
    post = c.post("/checks", json=a_run())
    assert post.status_code == 201
    resp = c.get(f"/checks/commit/{'a' * 40}")
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 1
    assert runs[0]["check_name"] == "wardline"
    assert runs[0]["outcome"] == "pass"


def test_get_by_branch_and_pr(tmp_path):
    c = client(tmp_path)
    c.post("/checks", json=a_run(run_id="r1", branch="main", pr=None))
    c.post("/checks", json=a_run(run_id="r2", branch="feature", pr=7,
                                 commit_sha="b" * 40))
    assert {r["run_id"] for r in c.get("/checks/branch/feature").json()} == {"r2"}
    assert {r["run_id"] for r in c.get("/checks/pr/7").json()} == {"r2"}


def test_post_rejects_invalid_outcome(tmp_path):
    c = client(tmp_path)
    resp = c.post("/checks", json=a_run(outcome="exploded"))
    assert resp.status_code == 422


def test_check_api_round_trips_rule_set_and_policy_version(tmp_path):
    c = client(tmp_path)
    assert c.post("/checks", json=a_run(rule_set="wardline@3", policy_version="pv-9")).status_code == 201
    got = c.get(f"/checks/commit/{'a' * 40}").json()[0]
    assert got["rule_set"] == "wardline@3"
    assert got["policy_version"] == "pv-9"
