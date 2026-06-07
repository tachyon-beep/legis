"""Pull-request facts (forge-reported, recorded by legis)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from legis.provenance import Provenance


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
    recorded_by: str | None = None
    # Q-M4: recorded PR metadata is a writer-supplied claim, not forge-verified.
    # "unauthenticated" so a consumer never treats writer-asserted PR state as
    # authoritative (see CheckRun.provenance).
    provenance: str = Provenance.UNAUTHENTICATED
