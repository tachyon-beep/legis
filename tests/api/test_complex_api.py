import json
import sqlite3

from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.canonical import canonical_json, content_hash
from legis.clock import FixedClock
from legis.enforcement.protected import ProtectedGate, TrailVerifier
from legis.enforcement.signoff import SignoffGate
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.store.audit_store import GENESIS, AuditStore, _chain


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


KEY = b"k"
PROTECTED = frozenset({"no-eval"})
PBODY = {
    "policy": "no-eval",
    "entity": "src/x.py:f",
    "rationale": "sandboxed",
    "agent_id": "agent-9",
    "file_fingerprint": "fp",
    "ast_path": "ap",
}


def _app(tmp_path, opinion=JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    clock = FixedClock("2026-06-02T12:00:00+00:00")
    pg = ProtectedGate(store, clock, judge=ScriptedJudge(opinion), key=KEY)
    sg = SignoffGate(store, clock)
    app = create_app(
        protected_gate=pg, signoff_gate=sg, trail_verifier=TrailVerifier(KEY, PROTECTED)
    )
    return TestClient(app), store


def test_protected_post_records_and_verified_read_succeeds(tmp_path):
    c, _ = _app(tmp_path)
    assert c.post("/protected/overrides", json=PBODY).status_code == 201
    trail = c.get("/overrides")
    assert trail.status_code == 200
    sig = trail.json()[0]["extensions"]["judge_metadata_signature"]
    assert sig.startswith("hmac-sha256:v1:")


def test_protected_blocked_post_is_409(tmp_path):
    c, _ = _app(tmp_path, JudgeOpinion(Verdict.BLOCKED, "judge@1", "no"))
    assert c.post("/protected/overrides", json=PBODY).status_code == 409


def test_operator_override_post_is_201_and_distinct(tmp_path):
    c, _ = _app(tmp_path, JudgeOpinion(Verdict.BLOCKED, "judge@1", "no"))
    body = {**PBODY, "operator_id": "op-1"}
    del body["agent_id"]
    resp = c.post("/protected/operator-override", json=body)
    assert resp.status_code == 201
    assert resp.json()["verdict"] == "OVERRIDDEN_BY_OPERATOR"


def test_signoff_request_then_sign_clears(tmp_path):
    c, _ = _app(tmp_path)
    req = c.post(
        "/signoff/request",
        json={
            "policy": "prod-deploy",
            "entity": "svc/api",
            "rationale": "hotfix",
            "agent_id": "agent-3",
        },
    )
    assert req.status_code == 202
    seq = req.json()["seq"]
    signed = c.post(f"/signoff/{seq}/sign", json={"operator_id": "op-1", "rationale": "ok"})
    assert signed.status_code == 200
    assert signed.json()["cleared"] is True


def test_tampered_protected_read_is_a_500(tmp_path):
    c, store = _app(tmp_path)
    c.post("/protected/overrides", json=PBODY)
    db = str(tmp_path / "gov.db")
    con = sqlite3.connect(db)
    con.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    seq, payload = con.execute(
        "SELECT seq, payload FROM audit_log ORDER BY seq ASC LIMIT 1"
    ).fetchone()
    p = json.loads(payload)
    p["rationale"] = "FORGED"
    con.execute("UPDATE audit_log SET payload=? WHERE seq=?", (canonical_json(p), seq))
    prev = GENESIS
    for s, pl in con.execute(
        "SELECT seq, payload FROM audit_log ORDER BY seq ASC"
    ).fetchall():
        ch = content_hash(json.loads(pl))
        con.execute(
            "UPDATE audit_log SET content_hash=?, prev_hash=?, chain_hash=? WHERE seq=?",
            (ch, prev, _chain(prev, ch), s),
        )
        prev = _chain(prev, ch)
    con.commit()
    con.close()
    assert store.verify_integrity() is True
    assert c.get("/overrides").status_code == 500


def test_override_rate_endpoint_uses_policy_constants(tmp_path):
    c, _ = _app(tmp_path)
    r = c.get("/governance/override-rate")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "rate", "sample_size"}
    assert body["status"] == "PASS_WITH_NOTICE"  # empty trail < min_sample
