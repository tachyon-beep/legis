"""Protected-cell lifecycle gates — decay sweep + override-rate gate.

Both consume the append-only trail read-only. The decay sweep re-judges only
judge-ACCEPTED suppressions (an OVERRIDDEN_BY_OPERATOR entry would re-block
tautologically — the rate gate governs those instead; a BLOCKED entry is not a
suppression at all).
"""

from __future__ import annotations

from dataclasses import dataclass

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
