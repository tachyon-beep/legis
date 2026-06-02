from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.store.audit_store import AuditStore


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


def chill_client(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    eng = EnforcementEngine(store, FixedClock("2026-06-02T12:00:00+00:00"))
    return TestClient(create_app(enforcement=eng))


def coached_client(tmp_path, opinion):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    eng = EnforcementEngine(
        store, FixedClock("2026-06-02T12:00:00+00:00"), judge=ScriptedJudge(opinion)
    )
    return TestClient(create_app(enforcement=eng))


BODY = {
    "policy": "no-broad-except",
    "entity": "src/app.py:handler",
    "rationale": "re-raised after logging",
    "agent_id": "agent-7",
}


def test_chill_post_override_returns_201_and_records(tmp_path):
    c = chill_client(tmp_path)
    resp = c.post("/overrides", json=BODY)
    assert resp.status_code == 201
    body = resp.json()
    assert body["accepted"] is True
    assert body["verdict"] is None

    trail = c.get("/overrides").json()
    assert len(trail) == 1
    assert trail[0]["policy"] == "no-broad-except"
    assert trail[0]["identity_stable"] is False


def test_coached_blocked_post_returns_409_with_judge_reasoning(tmp_path):
    c = coached_client(
        tmp_path, JudgeOpinion(Verdict.BLOCKED, "judge@1", "rationale is boilerplate")
    )
    resp = c.post("/overrides", json=BODY)
    assert resp.status_code == 409
    body = resp.json()
    assert body["accepted"] is False
    assert body["verdict"] == "BLOCKED"
    assert body["judge_rationale"] == "rationale is boilerplate"
    # Even blocked, the attempt is in the trail for async review.
    assert len(c.get("/overrides").json()) == 1


def test_coached_accepted_post_returns_201(tmp_path):
    c = coached_client(
        tmp_path, JudgeOpinion(Verdict.ACCEPTED, "judge@1", "specific and correct")
    )
    resp = c.post("/overrides", json=BODY)
    assert resp.status_code == 201
    body = resp.json()
    assert body["accepted"] is True
    assert body["verdict"] == "ACCEPTED"
    assert body["judge_model"] == "judge@1"
