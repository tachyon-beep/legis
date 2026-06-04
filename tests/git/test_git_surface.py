import subprocess

import pytest

from legis.git.surface import GitError, GitSurface


def _g(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def test_branches_lists_all_with_current(git_repo):
    s = GitSurface(git_repo)
    by_name = {b.name: b for b in s.branches()}
    assert set(by_name) == {"main", "feature"}
    assert by_name["main"].is_current is True
    assert by_name["feature"].is_current is False
    assert all(len(b.head_sha) == 40 for b in by_name.values())


def test_commit_head_has_metadata(git_repo):
    s = GitSurface(git_repo)
    head = s.commits(limit=1)[0]
    assert head.author_name == "Test Author"
    assert head.author_email == "author@example.com"
    assert "rename" in head.message
    assert "T" in head.committed_at  # ISO 8601
    assert len(head.parents) == 1


def test_commit_stats_count_insertions_and_files(git_repo):
    s = GitSurface(git_repo)
    add_b = next(c for c in s.commits() if c.message.startswith("add b.txt"))
    assert add_b.files_changed == 1
    assert add_b.insertions == 1  # b.txt is a single line
    assert add_b.deletions == 0


def test_commits_are_ordered_newest_first_and_limited(git_repo):
    s = GitSurface(git_repo)
    commits = s.commits(limit=2)
    assert len(commits) == 2
    assert commits[0].message.startswith("rename")
    assert commits[1].message.startswith("add b.txt")


def test_unknown_sha_raises(git_repo):
    s = GitSurface(git_repo)
    with pytest.raises(GitError):
        s.commit("0" * 40)


def test_merge_base_of_main_and_feature(git_repo):
    s = GitSurface(git_repo)
    # feature branched at the first commit, so the merge base is that commit.
    base = s.merge_base("main", "feature")
    first = s.commits(ref="feature")[-1]  # feature's only/oldest commit
    assert base == first.sha


def test_merge_base_returns_none_for_unrelated(git_repo):
    s = GitSurface(git_repo)
    s._run("checkout", "--orphan", "island")
    s._run("commit", "--allow-empty", "-m", "orphan root")
    assert s.merge_base("main", "island") is None


def test_renames_detects_path_rename_with_similarity(git_repo):
    s = GitSurface(git_repo)
    renames = s.renames("main")
    assert len(renames) == 1
    r = renames[0]
    assert r.old_path == "a.txt"
    assert r.new_path == "renamed.txt"
    assert r.similarity == 100
    assert len(r.commit_sha) == 40


def test_branch_reports_upstream_and_ahead_behind(git_repo):
    _g(git_repo, "branch", "--set-upstream-to=main", "feature")  # local upstream
    by = {b.name: b for b in GitSurface(str(git_repo)).branches()}
    assert by["feature"].upstream == "main"
    assert by["feature"].behind == 2   # main has 2 commits feature lacks (b.txt, rename)
    assert by["feature"].ahead == 0
    # An untracked branch degrades honestly — never a guessed 0/0.
    assert by["main"].upstream is None
    assert by["main"].ahead is None and by["main"].behind is None


def test_renames_carry_pre_and_post_blob_shas(git_repo):
    [ev] = GitSurface(str(git_repo)).renames("HEAD~1..HEAD")  # commit 3: a.txt -> renamed.txt
    assert (ev.old_path, ev.new_path) == ("a.txt", "renamed.txt")
    assert len(ev.old_blob) == 40 and len(ev.new_blob) == 40
    assert ev.old_blob == ev.new_blob   # pure git mv → identical blob


def test_git_surface_command_injection_mitigation(git_repo):
    s = GitSurface(git_repo)

    # Option injections starting with '-'
    with pytest.raises(GitError):
        s.commit("-o")
    with pytest.raises(GitError):
        s.commits("-o")
    with pytest.raises(GitError):
        s.merge_base("-o", "main")
    with pytest.raises(GitError):
        s.merge_base("main", "-o")
    with pytest.raises(GitError):
        s.renames("-o")

    # Invalid patterns
    with pytest.raises(GitError):
        s.commit("main; rm -rf /")
    with pytest.raises(GitError):
        s.commits("HEAD&echo")
    with pytest.raises(GitError):
        s.merge_base("main|sh", "feature")
    with pytest.raises(GitError):
        s.renames("HEAD~1..HEAD; echo")


def test_working_tree_renames_detects_uncommitted_rename(git_repo):
    s = GitSurface(git_repo)
    # git_repo HEAD has renamed.txt; move it in the working tree without committing.
    s._run("mv", "renamed.txt", "moved.txt")

    evidence = s.working_tree_renames("HEAD")

    assert len(evidence) == 1
    assert evidence[0].commit_sha == "WORKTREE"
    assert evidence[0].old_path == "renamed.txt"
    assert evidence[0].new_path == "moved.txt"


def test_working_tree_renames_rejects_unsafe_base_ref(git_repo):
    import pytest

    from legis.git.surface import GitError

    s = GitSurface(git_repo)
    # Mirror the sibling test_git_surface_command_injection_mitigation coverage:
    # an option-like or metacharacter ref must raise before any git invocation.
    with pytest.raises(GitError):
        s.working_tree_renames("-o")
    with pytest.raises(GitError):
        s.working_tree_renames("HEAD; echo pwned")


def test_git_surface_times_out_slow_git_commands(git_repo, monkeypatch):
    def slow_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", slow_run)
    with pytest.raises(GitError, match="timed out"):
        GitSurface(git_repo).branches()
