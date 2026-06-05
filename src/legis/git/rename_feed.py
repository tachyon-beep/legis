"""Structured git rename evidence for Loomweave's identity matcher (additive).

This is a superset of ``GET /git/renames``: it bundles the base/head context and
optionally surfaces uncommitted working-tree renames. The existing committed-only
endpoint is unchanged, so existing consumers are unaffected.

Status semantics: ``status`` reflects *what was found* — it is
``"committed_and_worktree"`` only when at least one working-tree rename was
found, and ``"committed_only"`` otherwise. Because a clean working tree and an
unchecked working tree both yield no renames, ``status`` alone cannot tell them
apart. The separate ``worktree_checked`` flag reflects *what was checked*: it
echoes whether the working tree was inspected at all (``include_worktree``), so a
consumer can distinguish "checked and clean" from "not checked" without inferring
it from the request. (Loomweave, the committed-only consumer, ignores both
``working_tree`` and ``worktree_checked``.)
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
        "worktree_checked": include_worktree,
        "base": base,
        "head": head,
        "committed": committed,
        "working_tree": working_tree,
    }
