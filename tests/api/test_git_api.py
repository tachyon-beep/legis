from fastapi.testclient import TestClient

from legis.api.app import create_app


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
