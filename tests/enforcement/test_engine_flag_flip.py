from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


SAME_CALL = dict(
    policy="no-broad-except",
    entity_key=EntityKey.from_locator("src/app.py:handler"),
    rationale="re-raised after logging",
    agent_id="agent-7",
)


def _engine(tmp_path, name, judge):
    store = AuditStore(f"sqlite:///{tmp_path / name}")
    eng = EnforcementEngine(
        store, FixedClock("2026-06-02T12:00:00+00:00"), judge=judge
    )
    return eng, store


def test_flipping_only_the_judge_turns_chill_into_coached(tmp_path):
    # Identical construction and identical submit call; the ONLY difference is
    # whether a judge is injected.
    chill, chill_store = _engine(tmp_path, "chill.db", None)
    coached, coached_store = _engine(
        tmp_path,
        "coached.db",
        ScriptedJudge(JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")),
    )

    chill_result = chill.submit_override(**SAME_CALL)
    coached_result = coached.submit_override(**SAME_CALL)

    # Both accept; both record exactly one event; the engine and call are equal.
    assert chill_result.accepted is True
    assert coached_result.accepted is True

    chill_ext = chill_store.read_all()[0].payload["extensions"]
    coached_ext = coached_store.read_all()[0].payload["extensions"]

    # The flag's entire effect: chill writes no judge fields, coached does.
    assert chill_ext == {}
    assert chill_result.verdict is None
    assert coached_ext["judge_verdict"] == "ACCEPTED"
    assert coached_result.verdict is Verdict.ACCEPTED
