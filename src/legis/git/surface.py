"""GitSurface — answers "what changed?" over a real repository.

Implemented by shelling out to ``git``: legis *is* the git interface, this adds
no dependency, and rename detection is native (``git -M``). Stateless — the repo
is the source of truth.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from legis.git.models import BranchInfo, CommitInfo, RenameEvidence

US = "\x1f"  # unit separator — field delimiter in git --format strings


class GitError(RuntimeError):
    """A git command failed or a ref/sha could not be resolved."""


class GitSurface:
    _TIMEOUT_SECONDS = 10.0

    def __init__(self, repo_path: str | Path) -> None:
        self._repo = str(repo_path)

    def _run_raw(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", self._repo, *args],
                capture_output=True,
                text=True,
                timeout=self._TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(
                f"git {' '.join(args)} timed out after {self._TIMEOUT_SECONDS:g}s"
            ) from exc

    def _run(self, *args: str) -> str:
        result = self._run_raw(*args)
        if result.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return result.stdout

    def branches(self) -> list[BranchInfo]:
        current = self._run("branch", "--show-current").strip()
        out = self._run(
            "for-each-ref",
            "--format=%(refname:short)%09%(objectname)%09%(upstream:short)",
            "refs/heads",
        )
        branches: list[BranchInfo] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = (line.split("\t") + ["", "", ""])[:3]
            name, sha, upstream = parts[0], parts[1], parts[2]
            up = upstream or None
            ahead = behind = None
            if up:
                # <behind>\t<ahead>  ==  left-right of <upstream>...<branch>
                counts = self._run_raw("rev-list", "--left-right", "--count", f"{up}...{name}")
                if counts.returncode == 0:
                    left, _, right = counts.stdout.strip().partition("\t")
                    if left.isdigit() and right.isdigit():
                        behind, ahead = int(left), int(right)
            branches.append(BranchInfo(
                name=name, head_sha=sha, is_current=(name == current),
                upstream=up, ahead=ahead, behind=behind,
            ))
        return branches

    def commit(self, sha: str) -> CommitInfo:
        import re
        if sha.startswith("-") or not re.match(r"^[a-zA-Z0-9_/.~^-]+$", sha):
            raise GitError(f"invalid commit ref/SHA: {sha}")
        meta_fmt = US.join(["%H", "%an", "%ae", "%cI", "%P", "%B"])
        meta = self._run("show", "-s", f"--format={meta_fmt}", sha)
        # Body (%B) is last and may contain newlines/spaces — split with a cap.
        parts = meta.split(US)
        full_sha, an, ae, cdate, parents_raw = parts[0], parts[1], parts[2], parts[3], parts[4]
        body = US.join(parts[5:]).rstrip("\n")
        parents = parents_raw.split() if parents_raw.strip() else []

        files_changed, insertions, deletions = self._numstat(sha)
        return CommitInfo(
            sha=full_sha,
            author_name=an,
            author_email=ae,
            message=body,
            committed_at=cdate,
            parents=parents,
            files_changed=files_changed,
            insertions=insertions,
            deletions=deletions,
        )

    def _numstat(self, sha: str) -> tuple[int, int, int]:
        out = self._run("show", "--numstat", "--format=", sha)
        files = insertions = deletions = 0
        for line in out.splitlines():
            if not line.strip():
                continue
            ins, _, rest = line.partition("\t")
            dels, _, _path = rest.partition("\t")
            files += 1
            insertions += 0 if ins == "-" else int(ins)
            deletions += 0 if dels == "-" else int(dels)
        return files, insertions, deletions

    def commits(self, ref: str = "HEAD", limit: int = 50) -> list[CommitInfo]:
        import re
        if ref.startswith("-") or not re.match(r"^[a-zA-Z0-9_/.~^-]+$", ref):
            raise GitError(f"invalid commit ref: {ref}")
        out = self._run("rev-list", f"--max-count={limit}", ref)
        return [self.commit(sha) for sha in out.split()]

    def merge_base(self, a: str, b: str) -> str | None:
        import re
        if a.startswith("-") or not re.match(r"^[a-zA-Z0-9_/.~^-]+$", a):
            raise GitError(f"invalid ref: {a}")
        if b.startswith("-") or not re.match(r"^[a-zA-Z0-9_/.~^-]+$", b):
            raise GitError(f"invalid ref: {b}")
        result = self._run_raw("merge-base", a, b)
        if result.returncode != 0:
            return None  # no common ancestor (or a bad ref) → honest None
        sha = result.stdout.strip()
        return sha or None

    def renames(self, rev_range: str) -> list[RenameEvidence]:
        import re
        if rev_range.startswith("-") or not re.match(r"^[a-zA-Z0-9_/.~^-]+(\.\.[a-zA-Z0-9_/.~^-]+)?$", rev_range):
            raise GitError(f"invalid revision range: {rev_range}")
        out = self._run(
            "log",
            "-M",
            "--diff-filter=R",
            "--name-status",
            f"--format=COMMIT{US}%H",
            rev_range,
        )
        evidence: list[RenameEvidence] = []
        current_sha = ""
        for line in out.splitlines():
            if not line.strip():
                continue
            if line.startswith(f"COMMIT{US}"):
                current_sha = line.split(US, 1)[1]
                continue
            # Rename status line: "R<similarity>\t<old>\t<new>"
            status, _, rest = line.partition("\t")
            if not status.startswith("R"):
                continue
            old_path, _, new_path = rest.partition("\t")
            similarity = int(status[1:]) if status[1:].isdigit() else 0
            old_blob = self._blob(f"{current_sha}~1", old_path)
            new_blob = self._blob(current_sha, new_path)
            evidence.append(
                RenameEvidence(
                    commit_sha=current_sha,
                    old_path=old_path,
                    new_path=new_path,
                    similarity=similarity,
                    old_blob=old_blob,
                    new_blob=new_blob,
                )
            )
        return evidence

    def working_tree_renames(self, base: str) -> list[RenameEvidence]:
        import re
        if base.startswith("-") or not re.match(r"^[a-zA-Z0-9_/.~^-]+$", base):
            raise GitError(f"invalid base ref: {base}")
        out = self._run("diff", "-M", "--name-status", base)
        evidence: list[RenameEvidence] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            status, _, rest = line.partition("\t")
            if not status.startswith("R"):
                continue
            old_path, _, new_path = rest.partition("\t")
            similarity = int(status[1:]) if status[1:].isdigit() else 0
            old_blob = self._blob(base, old_path)
            new_blob_result = self._run_raw("hash-object", "--", new_path)
            new_blob = new_blob_result.stdout.strip() if new_blob_result.returncode == 0 else ""
            evidence.append(
                RenameEvidence(
                    commit_sha="WORKTREE",
                    old_path=old_path,
                    new_path=new_path,
                    similarity=similarity,
                    old_blob=old_blob,
                    new_blob=new_blob,
                )
            )
        return evidence

    def _blob(self, rev: str, path: str) -> str:
        """The git object SHA of ``path`` at ``rev`` ("" if it cannot be resolved)."""
        result = self._run_raw("rev-parse", f"{rev}:{path}")
        return result.stdout.strip() if result.returncode == 0 else ""
