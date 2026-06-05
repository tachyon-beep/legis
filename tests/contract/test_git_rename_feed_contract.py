"""Contract lock: GET /git/rename-feed object shape (the deferred Loomweave re-point target).

Unlike ``/git/renames`` (a flat ARRAY consumed today by Loomweave's
``parse_legis_rename_json``), ``/git/rename-feed`` returns an OBJECT
``{status, base, head, committed[], working_tree[]}``. When Loomweave re-points
from ``/git/renames`` to this feed's ``.committed`` leg (ledger item B3), it must
find each committed entry carrying the same ``old_path`` / ``new_path`` fields its
parser reads. This test pins the response shape so a drift breaks here, in legis,
rather than silently at Loomweave after the re-point lands.

Mirrors the discipline of ``test_git_renames_contract.py``.
"""
import subprocess

from fastapi.testclient import TestClient

from legis.api.app import create_app

# The exact field set every ``committed`` / ``working_tree`` entry must carry —
# asdict(RenameEvidence). Loomweave parses old_path/new_path; the rest are the
# superset legis promises and must not silently drop.
RENAME_ENTRY_FIELDS = {
    "commit_sha",
    "old_path",
    "new_path",
    "similarity",
    "old_blob",
    "new_blob",
}

# Core top-level keys Loomweave's re-point depends on. A superset is allowed
# (legis may add additive fields like worktree_checked); these must be present.
REQUIRED_TOP_LEVEL_KEYS = {"status", "base", "head", "committed", "working_tree"}

VALID_STATUSES = {"committed_only", "committed_and_worktree"}


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _parse_like_loomweave(items):
    out = []
    for it in items:
        old, new = it.get("old_path"), it.get("new_path")
        if isinstance(old, str) and isinstance(new, str) and old and new:
            out.append((old, new))
    return out


def _repo_with_committed_rename(tmp_path):
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
    return repo, base


def test_rename_feed_shape_is_contract_locked(tmp_path):
    repo, base = _repo_with_committed_rename(tmp_path)

    c = TestClient(create_app(repo_path=str(repo)))
    resp = c.get("/git/rename-feed", params={"base": base, "head": "HEAD"})
    assert resp.status_code == 200
    body = resp.json()

    # Top-level shape: object with at least the required keys, echoed base/head.
    assert isinstance(body, dict)
    assert REQUIRED_TOP_LEVEL_KEYS <= set(body)
    assert body["status"] in VALID_STATUSES
    assert body["base"] == base
    assert body["head"] == "HEAD"
    assert isinstance(body["committed"], list)
    assert isinstance(body["working_tree"], list)

    # Every committed entry carries exactly the RenameEvidence field set — the
    # superset legis promises Loomweave. A dropped/renamed field breaks here.
    for entry in body["committed"]:
        assert set(entry) == RENAME_ENTRY_FIELDS


def test_rename_feed_committed_leg_matches_loomweave_parser(tmp_path):
    repo, base = _repo_with_committed_rename(tmp_path)

    c = TestClient(create_app(repo_path=str(repo)))
    resp = c.get("/git/rename-feed", params={"base": base, "head": "HEAD"})
    assert resp.status_code == 200

    # The re-point safety property: parsing committed[] the way Loomweave parses
    # /git/renames must surface the same rename.
    pairs = _parse_like_loomweave(resp.json()["committed"])
    assert ("auth.py", "authn.py") in pairs
