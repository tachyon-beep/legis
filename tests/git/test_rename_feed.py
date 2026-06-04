from legis.git.rename_feed import build_rename_feed
from legis.git.surface import GitSurface


def test_build_rename_feed_reports_committed_renames(git_repo):
    feed = build_rename_feed(git_repo, base="HEAD~1", head="HEAD")

    assert feed["status"] == "committed_only"
    assert feed["base"] == "HEAD~1"
    assert feed["head"] == "HEAD"
    assert feed["committed"][0]["old_path"] == "a.txt"
    assert feed["committed"][0]["new_path"] == "renamed.txt"
    assert feed["working_tree"] == []


def test_build_rename_feed_can_include_worktree_renames(git_repo):
    GitSurface(git_repo)._run("mv", "renamed.txt", "moved.txt")

    feed = build_rename_feed(git_repo, base="HEAD", include_worktree=True)

    assert feed["status"] == "committed_and_worktree"
    assert feed["working_tree"][0]["old_path"] == "renamed.txt"
    assert feed["working_tree"][0]["new_path"] == "moved.txt"


def test_include_worktree_with_no_worktree_renames_stays_committed_only(git_repo):
    # The documented conflation: include_worktree=True but a clean working tree
    # leaves status "committed_only" (checked-and-clean is indistinguishable from
    # not-checked by status alone).
    feed = build_rename_feed(git_repo, base="HEAD~1", head="HEAD", include_worktree=True)

    assert feed["status"] == "committed_only"
    assert feed["working_tree"] == []
    assert feed["committed"][0]["new_path"] == "renamed.txt"
