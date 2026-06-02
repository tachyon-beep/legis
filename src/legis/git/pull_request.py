"""Pull-request context — an injectable forge seam (WP-A9).

A PR's title/base/head/state are a forge concept (GitHub/GitLab), not local git,
so legis does not fetch them: it defines the shape and consumes an injected
``PullRequestSource`` (the same injection posture as the identity/filigree
clients). A deployment wires a provider backed by ``gh``/the GitHub API; tests
run offline against a fake. legis bakes in no forge HTTP and no GitHub assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PullRequestContext:
    number: int
    title: str
    base: str
    head: str
    state: str


@runtime_checkable
class PullRequestSource(Protocol):
    def get(self, number: int) -> "PullRequestContext | None": ...
