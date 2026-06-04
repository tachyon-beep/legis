import pytest
from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.policy.grammar import AllowlistBoundary, PolicyGrammar
from legis.store.audit_store import AuditStore

pytestmark = pytest.mark.usefixtures("unsafe_dev_auth")


def _app(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    eng = EnforcementEngine(store, FixedClock("2026-06-02T12:00:00+00:00"))
    grammar = PolicyGrammar()
    grammar.register(AllowlistBoundary("imports", frozenset({"json"})))
    return TestClient(create_app(enforcement=eng, grammar=grammar))


def test_clear_evaluation_records_no_event(tmp_path):
    c = _app(tmp_path)
    resp = c.post("/policy/evaluate", json={"policy": "imports", "target": {"value": "json"}})
    assert resp.status_code == 200
    assert resp.json()["result"] == "CLEAR"
    assert resp.json()["provenance_gap"] is False
    assert c.get("/overrides").json() == []  # nothing recorded for a clean pass


def test_unknown_policy_is_not_a_pass_and_records_a_provenance_gap(tmp_path):
    c = _app(tmp_path)
    resp = c.post("/policy/evaluate", json={"policy": "unregistered", "target": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "UNKNOWN"  # never CLEAR
    assert body["provenance_gap"] is True
    trail = c.get("/overrides").json()
    assert len(trail) == 1
    assert trail[0]["event"] == "UNKNOWN_POLICY"
    assert trail[0]["policy"] == "unregistered"
    assert trail[0]["provenance_gap"] is True
    assert trail[0]["recorded_at"] == "2026-06-02T12:00:00+00:00"


def test_violation_is_reported(tmp_path):
    c = _app(tmp_path)
    resp = c.post("/policy/evaluate", json={"policy": "imports", "target": {"value": "socket"}})
    assert resp.json()["result"] == "VIOLATION"
