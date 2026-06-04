"""Pull-request facts (forge-reported, recorded by legis)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PullRequestState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    base: str
    head: str
    state: PullRequestState
    url: str | None = None
