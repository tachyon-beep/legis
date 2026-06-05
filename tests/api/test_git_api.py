import pytest
from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.checks.models import CheckOutcome, CheckRun
from legis.checks.surface import CheckSurface
from legis.git.surface import GitError, GitSurface
from legis.pulls.surface import PullSurface

pytestmark = pytest.mark.usefixtures("unsafe_dev_auth")


def client(git_repo):
    return TestClient(create_app(repo_path=git_repo))


def test_git_branches_endpoint(git_repo):
    resp = client(git_repo).get("/git/branches")
    assert resp.status_code == 200
    names = {b["name"]: b for b in resp.json()}
    assert set(names) == {"main", "feature"}
    assert names["main"]["is_current"] is True


def test_git_commit_endpoint(git_repo):
    c = client(git_repo)
    head_sha = {b["name"]: b for b in c.get("/git/branches").json()}["main"]["head_sha"]
    resp = c.get(f"/git/commits/{head_sha}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sha"] == head_sha
    assert body["author_name"] == "Test Author"
    assert "rename" in body["message"]


def test_git_renames_endpoint(git_repo):
    resp = client(git_repo).get("/git/renames", params={"rev_range": "main"})
    assert resp.status_code == 200
    renames = resp.json()
    assert len(renames) == 1
    assert renames[0]["old_path"] == "a.txt"
    assert renames[0]["new_path"] == "renamed.txt"
    assert renames[0]["similarity"] == 100


def test_git_commit_unknown_sha_returns_404(git_repo):
    resp = client(git_repo).get(f"/git/commits/{'0' * 40}")
    assert resp.status_code == 404


def test_git_renames_invalid_range_returns_4xx(git_repo):
    resp = client(git_repo).get("/git/renames", params={"rev_range": "--version"})
    assert resp.status_code in (400, 422)
    assert "invalid" in resp.json()["detail"].lower()


def test_git_branches_errors_are_mapped_to_4xx(git_repo, monkeypatch):
    def fail(self):
        raise GitError("bad repo")

    monkeypatch.setattr(GitSurface, "branches", fail)
    c = TestClient(create_app(repo_path=git_repo), raise_server_exceptions=False)
    resp = c.get("/git/branches")
    assert resp.status_code == 400
    assert "bad repo" in resp.json()["detail"]


def test_git_pulls_recorded_surface_round_trips_and_joins_checks(tmp_path):
    checks = CheckSurface(f"sqlite:///{tmp_path / 'checks.db'}")
    pulls = PullSurface(f"sqlite:///{tmp_path / 'pulls.db'}")
    checks.record(CheckRun(
        check_name="wardline",
        run_id="r1",
        commit_sha="a" * 40,
        outcome=CheckOutcome.FAIL,
        pr=7,
    ))
    checks.record(CheckRun(
        check_name="lint",
        run_id="r2",
        commit_sha="b" * 40,
        outcome=CheckOutcome.PASS,
        pr=99,
    ))
    c = TestClient(create_app(check_surface=checks, pull_surface=pulls))
    post = c.post("/git/pulls", json={
        "number": 7,
        "title": "Add eval guard",
        "base": "main",
        "head": "feature/guard",
        "state": "open",
        "url": "https://forge/pr/7",
    })
    assert post.status_code == 201
    assert post.json()["number"] == 7

    got = c.get("/git/pulls/7")
    assert got.status_code == 200
    body = got.json()
    assert body["title"] == "Add eval guard"
    assert body["state"] == "open"
    assert [ck["check_name"] for ck in body["checks"]] == ["wardline"]

    update = c.post("/git/pulls", json={
        "number": 7,
        "title": "Add eval guard",
        "base": "main",
        "head": "feature/guard",
        "state": "merged",
        "url": "https://forge/pr/7",
    })
    assert update.status_code == 201
    assert c.get("/git/pulls/7").json()["state"] == "merged"


def test_git_pulls_record_server_owned_writer_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_API_TOKEN_ACTORS", "forge-sync:writer=token-a")
    pulls = PullSurface(f"sqlite:///{tmp_path / 'pulls.db'}")
    c = TestClient(create_app(pull_surface=pulls))

    post = c.post(
        "/git/pulls",
        json={
            "number": 7,
            "title": "Add eval guard",
            "base": "main",
            "head": "feature/guard",
            "state": "open",
            "url": "https://forge/pr/7",
            "recorded_by": "spoofed",
        },
        headers={"Authorization": "Bearer token-a"},
    )

    assert post.status_code == 201
    assert post.json()["recorded_by"] == "forge-sync"
    assert c.get("/git/pulls/7").json()["recorded_by"] == "forge-sync"


def test_git_pulls_recorded_pr_is_labeled_unauthenticated_provenance(tmp_path):
    # Q-M4: recorded PR metadata is a writer-supplied claim, not forge-verified.
    # It carries provenance: unauthenticated, server-controlled (a writer cannot
    # forge the label by supplying it in the body).
    pulls = PullSurface(f"sqlite:///{tmp_path / 'pulls.db'}")
    c = TestClient(create_app(pull_surface=pulls))
    post = c.post("/git/pulls", json={
        "number": 7, "title": "t", "base": "main", "head": "f", "state": "open",
        "provenance": "authenticated",
    })
    assert post.status_code == 201
    assert post.json()["provenance"] == "unauthenticated"
    assert c.get("/git/pulls/7").json()["provenance"] == "unauthenticated"


def test_git_pulls_unknown_pr_is_404(tmp_path):
    c = TestClient(create_app(pull_surface=PullSurface(f"sqlite:///{tmp_path / 'pulls.db'}")))
    assert c.get("/git/pulls/999").status_code == 404


def test_git_rename_feed_returns_committed_renames(git_repo):
    resp = client(git_repo).get("/git/rename-feed", params={"base": "HEAD~1", "head": "HEAD"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "committed_only"
    assert body["committed"][0]["new_path"] == "renamed.txt"


def test_git_rename_feed_rejects_bad_ref(git_repo):
    resp = client(git_repo).get("/git/rename-feed", params={"base": "--bad"})

    assert resp.status_code == 400
