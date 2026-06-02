"""Judge verdict value types — shared by the judge and the engine.

A ``str`` enum so verdicts serialize to plain JSON in the audit trail and on the
HTTP surface (same discipline as ``CheckOutcome``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"
    OVERRIDDEN_BY_OPERATOR = "OVERRIDDEN_BY_OPERATOR"


class SignoffState(str, Enum):
    PENDING = "PENDING_SIGNOFF"
    SIGNED_OFF = "SIGNED_OFF"


@dataclass(frozen=True)
class JudgeOpinion:
    verdict: Verdict
    model: str
    rationale: str
