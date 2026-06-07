"""CI/check facts (passive data).

Check outcomes and PR associations are forge/CI-reported — not in git — so
legis records them. A ``CheckRun`` is an immutable fact: a named check ran
against a code state and produced an outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from legis.provenance import Provenance


class CheckOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class CheckRun:
    check_name: str
    run_id: str
    commit_sha: str
    outcome: CheckOutcome
    branch: str | None = None
    pr: int | None = None
    ran_against: str | None = None
    rule_set: str | None = None
    policy_version: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    recorded_by: str | None = None
    # Q-M2: a recorded check is a writer-supplied claim, not a forge-verified
    # fact — no signature or forge provenance backs it. Default to
    # "unauthenticated" so a consumer is never misled into treating a
    # writer-asserted "pass" as authoritative. An authenticated path (a signed
    # forge webhook) would set a stronger value; none exists today.
    provenance: str = Provenance.UNAUTHENTICATED
