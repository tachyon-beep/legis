"""Shared test fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_ENV_IDENTITY = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.com",
}


@pytest.fixture
def unsafe_dev_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEGIS_UNSAFE_DEV_AUTH", "1")


@pytest.fixture
def unsafe_wardline_request_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING", "1")


@pytest.fixture
def unsafe_dev_defaults(
    unsafe_dev_auth: None, unsafe_wardline_request_routing: None
) -> None:
    return None


def _git(repo: Path, *args: str) -> str:
    import os

    env = {**os.environ, **_ENV_IDENTITY}
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real repo with a known history:

    - commit 1 (main): add ``a.txt``
    - branch ``feature`` created at commit 1
    - commit 2 (main): add ``b.txt``
    - commit 3 (main): ``git mv a.txt renamed.txt`` (a 100%-similar rename)
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    # Repo-level identity so commits work through any runner (not just the
    # env-carrying fixture helper).
    _git(repo, "config", "user.name", "Test Author")
    _git(repo, "config", "user.email", "author@example.com")

    (repo / "a.txt").write_text("hello\nworld\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "add a.txt")

    _git(repo, "branch", "feature")

    (repo / "b.txt").write_text("second file\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "add b.txt")

    _git(repo, "mv", "a.txt", "renamed.txt")
    _git(repo, "commit", "-m", "rename a.txt -> renamed.txt")

    return repo
