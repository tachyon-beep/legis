from legis.enforcement.lifecycle import decay_sweep
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.audit_store import AuditStore


class PolicyJudge:
    """Blocks any rationale containing 'stale'; accepts the rest."""

    def evaluate(self, record):
        v = Verdict.BLOCKED if "stale" in record.rationale else Verdict.ACCEPTED
        return JudgeOpinion(v, "judge@2", f"re-judged: {record.rationale}")


def _accepted(policy, entity, rationale):
    rec = OverrideRecord(
        policy=policy,
        entity_key=EntityKey.from_locator(entity),
        rationale=rationale,
        agent_id="a",
        recorded_at="t",
        extensions={"judge_verdict": "ACCEPTED", "judge_model": "judge@1"},
    )
    return rec.to_payload()


def test_decay_flags_kept_suppressions_that_fail_a_fresh_pass(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    store.append(_accepted("p", "e1", "still valid reason"))
    store.append(_accepted("p", "e2", "stale reason no longer holds"))
    # a BLOCKED and an OVERRIDDEN record must be ignored by the sweep
    store.append({**_accepted("p", "e3", "stale"), "extensions": {"judge_verdict": "BLOCKED"}})
    store.append(
        {**_accepted("p", "e4", "stale"), "extensions": {"judge_verdict": "OVERRIDDEN_BY_OPERATOR"}}
    )

    flags = decay_sweep(store.read_all(), PolicyJudge())
    assert {f.entity for f in flags} == {"e2"}  # only the ACCEPTED-but-now-stale one
    assert flags[0].seq == 2
    assert "stale" in flags[0].fresh_rationale
