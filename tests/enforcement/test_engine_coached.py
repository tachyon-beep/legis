from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


class ScriptedJudge:
    def __init__(self, opinion: JudgeOpinion) -> None:
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


def engine(tmp_path, opinion):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    eng = EnforcementEngine(
        store,
        FixedClock("2026-06-02T12:00:00+00:00"),
        judge=ScriptedJudge(opinion),
    )
    return eng, store


def submit(eng):
    return eng.submit_override(
        policy="no-broad-except",
        entity_key=EntityKey.from_locator("src/app.py:handler"),
        rationale="re-raised after logging",
        agent_id="agent-7",
    )


def test_coached_accepted_records_with_judge_fields(tmp_path):
    eng, store = engine(
        tmp_path, JudgeOpinion(Verdict.ACCEPTED, "judge@1", "specific and correct")
    )
    result = submit(eng)
    assert result.accepted is True
    assert result.verdict is Verdict.ACCEPTED
    assert result.judge_model == "judge@1"
    ext = store.read_all()[0].payload["extensions"]
    assert ext["judge_verdict"] == "ACCEPTED"
    assert ext["judge_model"] == "judge@1"
    assert ext["judge_rationale"] == "specific and correct"


def test_coached_blocked_does_not_persist_as_accepted_but_is_recorded(tmp_path):
    eng, store = engine(
        tmp_path, JudgeOpinion(Verdict.BLOCKED, "judge@1", "rationale is boilerplate")
    )
    result = submit(eng)
    assert result.accepted is False
    assert result.verdict is Verdict.BLOCKED
    assert result.judge_rationale == "rationale is boilerplate"
    # The blocked attempt IS recorded — judge_verdict distinguishes it; the
    # async human sees the full trail. It is not recorded as accepted.
    trail = store.read_all()
    assert len(trail) == 1
    ext = trail[0].payload["extensions"]
    assert ext["judge_verdict"] == "BLOCKED"
    assert ext["judge_model"] == "judge@1"   # model recorded on every verdict
    assert store.verify_integrity() is True
