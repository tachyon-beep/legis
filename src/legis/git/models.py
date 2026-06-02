"""Structured git/change facts (passive data)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BranchInfo:
    name: str
    head_sha: str
    is_current: bool
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    author_name: str
    author_email: str
    message: str
    committed_at: str
    parents: list[str]
    files_changed: int
    insertions: int
    deletions: int


@dataclass(frozen=True)
class RenameEvidence:
    """Git-layer (path) rename evidence: what ``git -M`` detects.

    Symbol-level identity resolution is Clarion's (it combines this signal with
    body hashes, SEI spec §3); WP-6.3 re-exposes this surface to Clarion's
    matcher. This does not claim symbol-level rename detection.
    """

    commit_sha: str
    old_path: str
    new_path: str
    similarity: int
