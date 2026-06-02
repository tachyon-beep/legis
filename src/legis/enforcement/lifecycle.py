"""Protected-cell lifecycle gates — decay sweep + override-rate gate.

Both consume the append-only trail read-only. The decay sweep re-judges only
judge-ACCEPTED suppressions (an OVERRIDDEN_BY_OPERATOR entry would re-block
tautologically — the rate gate governs those instead; a BLOCKED entry is not a
suppression at all).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from legis.enforcement.judge import Judge
from legis.enforcement.verdict import Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord


@dataclass(frozen=True)
class DecayFlag:
    seq: int
    policy: str
    entity: str
    fresh_rationale: str


def decay_sweep(records, judge: Judge) -> list[DecayFlag]:
    """Re-judge each kept (ACCEPTED) suppression; flag any that no longer pass."""
    flags: list[DecayFlag] = []
    for rec in records:
        ext = rec.payload.get("extensions", {})
        if ext.get("judge_verdict") != Verdict.ACCEPTED.value:
            continue
        p = rec.payload
        proposed = OverrideRecord(
            policy=p["policy"],
            entity_key=EntityKey.from_dict(p["entity_key"]),
            rationale=p["rationale"],
            agent_id=p["agent_id"],
            recorded_at=p["recorded_at"],
        )
        opinion = judge.evaluate(proposed)
        if opinion.verdict is not Verdict.ACCEPTED:
            flags.append(
                DecayFlag(
                    seq=rec.seq,
                    policy=p["policy"],
                    entity=p["entity_key"]["value"],
                    fresh_rationale=opinion.rationale,
                )
            )
    return flags


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PASS_WITH_NOTICE = "PASS_WITH_NOTICE"


@dataclass(frozen=True)
class GateResult:
    status: GateStatus
    rate: float
    sample_size: int


# Denominator = kept-suppression decisions; BLOCKED is not a kept suppression.
_FINAL = {Verdict.ACCEPTED.value, Verdict.OVERRIDDEN_BY_OPERATOR.value}


def evaluate_override_rate(
    records, *, threshold: float, window: int, min_sample: int
) -> GateResult:
    """Share of kept suppressions forced past the judge by an operator.

    rate = OVERRIDDEN_BY_OPERATOR / (ACCEPTED + OVERRIDDEN_BY_OPERATOR) over the
    most recent ``window`` final-disposition records. Below ``min_sample`` →
    PASS_WITH_NOTICE so small corpora don't trip mechanically.
    """
    finals = [
        r
        for r in records
        if r.payload.get("extensions", {}).get("judge_verdict") in _FINAL
    ]
    finals = finals[-window:]
    n = len(finals)
    overrides = sum(
        1
        for r in finals
        if r.payload["extensions"]["judge_verdict"]
        == Verdict.OVERRIDDEN_BY_OPERATOR.value
    )
    rate = (overrides / n) if n else 0.0
    if n < min_sample:
        status = GateStatus.PASS_WITH_NOTICE
    elif rate > threshold:
        status = GateStatus.FAIL
    else:
        status = GateStatus.PASS
    return GateResult(status=status, rate=rate, sample_size=n)
