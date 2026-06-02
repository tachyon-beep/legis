from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.protected import ProtectedGate, TrailVerifier
from legis.enforcement.signoff import SignoffGate
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.resolver import IdentityResolver
from legis.store.audit_store import AuditStore

KEY = b"k"
PROTECTED = frozenset({"no-eval"})


class FakeClient:
    def __init__(self, resolve, lineage=None):
        self._resolve = resolve
        self._lineage = lineage or []

    def capability(self):
        return True

    def resolve_locator(self, locator):
        return self._resolve

    def resolve_sei(self, sei):
        return {"sei": sei, "alive": True}

    def lineage(self, sei):
        return self._lineage


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


ALIVE = {"sei": "clarion:eid:abc123", "current_locator": "python:function:m.f",
         "content_hash": "h", "alive": True}


def _app(tmp_path, client):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    eng = EnforcementEngine(store, FixedClock("2026-06-02T12:00:00+00:00"))
    return TestClient(create_app(enforcement=eng, identity=IdentityResolver(client)))


def _complex_app(tmp_path, client, opinion=JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    clock = FixedClock("2026-06-02T12:00:00+00:00")
    pg = ProtectedGate(store, clock, judge=ScriptedJudge(opinion), key=KEY)
    sg = SignoffGate(store, clock)
    return TestClient(create_app(
        protected_gate=pg, signoff_gate=sg, trail_verifier=TrailVerifier(KEY, PROTECTED),
        identity=IdentityResolver(client),
    ))


def test_override_keys_record_on_sei_when_alive(tmp_path):
    c = _app(tmp_path, FakeClient(ALIVE, lineage=[{"event": "born"}]))
    resp = c.post("/overrides", json={
        "policy": "no-eval", "entity": "python:function:m.f",
        "rationale": "reviewed", "agent_id": "agent-1"})
    assert resp.status_code == 201
    trail = c.get("/overrides").json()
    assert trail[0]["entity_key"] == {"value": "clarion:eid:abc123", "identity_stable": True}
    assert trail[0]["identity_stable"] is True


def test_override_degrades_to_locator_when_not_alive(tmp_path):
    c = _app(tmp_path, FakeClient({"alive": False}))
    resp = c.post("/overrides", json={
        "policy": "no-eval", "entity": "python:function:gone",
        "rationale": "reviewed", "agent_id": "agent-1"})
    assert resp.status_code == 201
    trail = c.get("/overrides").json()
    assert trail[0]["entity_key"] == {"value": "python:function:gone", "identity_stable": False}


def test_protected_override_keys_on_sei_and_signature_still_verifies(tmp_path):
    # Broadened scope: the protected (HMAC-signed) tier must also key on SEI —
    # the signed dict binds the entity, so a verdict stays bound to the SEI
    # across a rename. A verified read (200, not 500) proves the signature
    # verifies over the SEI-keyed payload.
    c = _complex_app(tmp_path, FakeClient(ALIVE, lineage=[{"event": "born"}]))
    resp = c.post("/protected/overrides", json={
        "policy": "no-eval", "entity": "python:function:m.f",
        "rationale": "sandboxed", "agent_id": "agent-9",
        "file_fingerprint": "fp", "ast_path": "ap"})
    assert resp.status_code == 201
    read = c.get("/overrides")
    assert read.status_code == 200
    assert read.json()[0]["entity_key"] == {"value": "clarion:eid:abc123", "identity_stable": True}


def test_signoff_request_keys_on_sei_when_alive(tmp_path):
    # Broadened scope: structured sign-off requests also key on SEI.
    c = _complex_app(tmp_path, FakeClient(ALIVE))
    resp = c.post("/signoff/request", json={
        "policy": "prod-deploy", "entity": "python:function:m.f",
        "rationale": "needs human", "agent_id": "agent-1"})
    assert resp.status_code == 202
    trail = c.get("/overrides").json()
    assert trail[0]["entity_key"] == {"value": "clarion:eid:abc123", "identity_stable": True}


def test_record_carries_clarion_two_axis_and_lineage_snapshot(tmp_path):
    from legis.canonical import content_hash
    alive = {"sei": "clarion:eid:abc123", "current_locator": "python:function:m.f",
             "content_hash": "blake3hash", "alive": True}
    lineage = [{"event": "born"}, {"event": "locator_changed"}]
    c = _app(tmp_path, FakeClient(alive, lineage=lineage))
    c.post("/overrides", json={"policy": "no-eval", "entity": "python:function:m.f",
                               "rationale": "reviewed", "agent_id": "agent-1"})
    clarion = c.get("/overrides").json()[0]["extensions"]["clarion"]
    assert clarion["alive"] is True
    assert clarion["content_hash"] == "blake3hash"
    assert clarion["lineage_snapshot"] == {"length": 2, "hash": content_hash(lineage)}
