"""Structured git rename evidence for Clarion's identity matcher (additive).

This is a superset of ``GET /git/renames``: it bundles the base/head context and
optionally surfaces uncommitted working-tree renames. The existing committed-only
endpoint is unchanged, so existing consumers are unaffected.

Status semantics: ``status`` is ``"committed_and_worktree"`` only when at least
one working-tree rename was found. When ``include_worktree=True`` but the working
tree has no renames, ``status`` stays ``"committed_only"`` — i.e. the status
field conflates "working tree checked and clean" with "working tree not checked".
Callers that need to distinguish those cases must look at the request's
``include_worktree`` flag, not infer it from ``status``. (No current consumer
needs the distinction; the committed-only consumer, Clarion, ignores
``working_tree`` entirely.)
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from legis.git.surface import GitSurface


def build_rename_feed(
    repo_path: str | Path,
    *,
    base: str,
    head: str = "HEAD",
    include_worktree: bool = False,
) -> dict:
    surface = GitSurface(repo_path)
    committed = [asdict(item) for item in surface.renames(f"{base}..{head}")]
    working_tree = (
        [asdict(item) for item in surface.working_tree_renames(head)]
        if include_worktree
        else []
    )
    status = "committed_and_worktree" if working_tree else "committed_only"
    return {
        "status": status,
        "base": base,
        "head": head,
        "committed": committed,
        "working_tree": working_tree,
    }
