"""Contract lock: /git/renames must match Clarion's LegisGitRenameSource parser.

Clarion's `parse_legis_rename_json` (clarion-cli/src/sei_git.rs) reads a JSON
ARRAY and takes `old_path` and `new_path` (string, non-empty) from each item;
all other fields are ignored. This test fabricates a rename in a real repo and
asserts the endpoint emits exactly that shape. Mirrors Clarion's parser logic.
"""
import subprocess

from fastapi.testclient import TestClient

from legis.api.app import create_app


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _parse_like_clarion(items):
    # Re-implements parse_legis_rename_json: array → (old,new) pairs, skip empties.
    out = []
    for it in items:
        old, new = it.get("old_path"), it.get("new_path")
        if isinstance(old, str) and isinstance(new, str) and old and new:
            out.append((old, new))
    return out


def test_git_renames_endpoint_matches_clarion_parser(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "auth.py").write_text("def login():\n    return 1\n" * 5)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    (repo / "authn.py").write_text((repo / "auth.py").read_text())
    (repo / "auth.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "rename auth -> authn")

    c = TestClient(create_app(repo_path=str(repo)))
    resp = c.get("/git/renames", params={"rev_range": f"{base}..HEAD"})
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)                       # Clarion requires an array
    pairs = _parse_like_clarion(items)
    assert ("auth.py", "authn.py") in pairs              # the rename survives the contract


def _parse_git_diff_lines(stdout: str) -> list[tuple[str, str]]:
    out = []
    for line in stdout.splitlines():
        cols = line.split("\t")
        if len(cols) >= 3 and cols[0].startswith("R"):
            old, new = cols[1], cols[2]
            if old and new:
                out.append((old, new))
    return out


def test_git_renames_union_integration(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")

    # Initial files in base commit
    (repo / "auth.py").write_text("def login():\n    return 1\n" * 5)
    (repo / "extra.py").write_text("def helper():\n    return 2\n" * 5)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()

    # 1. Committed rename: auth.py -> authn.py
    (repo / "authn.py").write_text((repo / "auth.py").read_text())
    (repo / "auth.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "rename auth -> authn")

    # 2. Uncommitted working-tree rename: extra.py -> extras.py
    _git(repo, "mv", "extra.py", "extras.py")

    # Query Legis for the committed window
    c = TestClient(create_app(repo_path=str(repo)))
    resp = c.get("/git/renames", params={"rev_range": f"{base}..HEAD"})
    assert resp.status_code == 200
    committed_renames = _parse_like_clarion(resp.json())

    # Query local git for the uncommitted working-tree window
    git_diff_out = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-status", "-M", "HEAD"],
        capture_output=True, text=True, check=True
    ).stdout
    working_tree_renames = _parse_git_diff_lines(git_diff_out)

    # Perform the union (exactly mimicking Clarion's gather_git_renames)
    union_renames = []
    for rename in committed_renames + working_tree_renames:
        if rename not in union_renames:
            union_renames.append(rename)

    # Assertions
    assert len(union_renames) == 2
    assert ("auth.py", "authn.py") in union_renames
    assert ("extra.py", "extras.py") in union_renames

