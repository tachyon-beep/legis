import pytest
from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.protected import ProtectedGate, TrailVerifier
from legis.enforcement.signoff import SignoffGate
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.resolver import IdentityResolver
from legis.store.audit_store import AuditStore

pytestmark = pytest.mark.usefixtures("unsafe_dev_auth")

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


class BrokenLineageClient(FakeClient):
    def lineage(self, sei):
        raise RuntimeError("clarion down")


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


def test_identity_gaps_endpoint_surfaces_orphans(tmp_path):
    alive = {"sei": "clarion:eid:abc123", "current_locator": "python:function:m.f",
             "content_hash": "h", "alive": True}

    class OrphanClient(FakeClient):
        def resolve_sei(self, sei):
            return {"sei": sei, "alive": False, "lineage": [{"event": "orphaned"}]}

    c = _app(tmp_path, OrphanClient(alive, lineage=[{"event": "born"}]))
    c.post("/overrides", json={"policy": "no-eval", "entity": "python:function:m.f",
                               "rationale": "reviewed", "agent_id": "agent-1"})
    gaps = c.get("/governance/identity-gaps").json()
    assert gaps == [{"sei": "clarion:eid:abc123", "reason": "orphaned",
                     "lineage": [{"event": "orphaned"}]}]


def test_lineage_integrity_endpoint_reports_clean_when_appended(tmp_path):
    alive = {"sei": "clarion:eid:abc123", "current_locator": "python:function:m.f",
             "content_hash": "h", "alive": True}
    c = _app(tmp_path, FakeClient(alive, lineage=[{"event": "born"}]))
    c.post("/overrides", json={"policy": "no-eval", "entity": "python:function:m.f",
                               "rationale": "reviewed", "agent_id": "agent-1"})
    # FakeClient.lineage still returns the same [born]; snapshot matches → clean.
    assert c.get("/governance/lineage-integrity").json() == {
        "status": "verified",
        "divergences": [],
        "unavailable": [],
    }


def test_lineage_integrity_endpoint_reports_unavailable_not_clean(tmp_path):
    alive = {"sei": "clarion:eid:abc123", "current_locator": "python:function:m.f",
             "content_hash": "h", "alive": True}
    c = _app(tmp_path, BrokenLineageClient(alive, lineage=[{"event": "born"}]))
    c.post("/overrides", json={"policy": "no-eval", "entity": "python:function:m.f",
                               "rationale": "reviewed", "agent_id": "agent-1"})
    body = c.get("/governance/lineage-integrity").json()
    assert body["status"] == "unverified"
    assert body["divergences"] == []
    assert body["unavailable"] == [
        {"sei": "clarion:eid:abc123", "reason": "unavailable"}
    ]


def test_protected_and_signoff_paths_carry_clarion_block(tmp_path):
    from legis.clock import FixedClock
    from legis.enforcement.protected import ProtectedGate, TrailVerifier
    from legis.enforcement.signoff import SignoffGate
    from legis.enforcement.verdict import JudgeOpinion, Verdict
    from legis.store.audit_store import AuditStore

    class _Judge:
        def evaluate(self, record):
            return JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")

    alive = {"sei": "clarion:eid:abc123", "current_locator": "python:function:m.f",
             "content_hash": "blake3hash", "alive": True}
    key = b"k"
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    clock = FixedClock("2026-06-02T12:00:00+00:00")
    pg = ProtectedGate(store, clock, judge=_Judge(), key=key)
    sg = SignoffGate(store, clock)
    app = create_app(
        protected_gate=pg, signoff_gate=sg,
        trail_verifier=TrailVerifier(key, frozenset({"no-eval"})),
        identity=IdentityResolver(FakeClient(alive, lineage=[{"event": "born"}])),
    )
    c = TestClient(app)

    pr = c.post("/protected/overrides", json={
        "policy": "no-eval", "entity": "python:function:m.f", "rationale": "r",
        "agent_id": "agent-1", "file_fingerprint": "fp", "ast_path": "ap"})
    assert pr.status_code == 201
    protected_rec = c.get("/overrides").json()[0]
    assert protected_rec["entity_key"]["value"] == "clarion:eid:abc123"
    assert protected_rec["extensions"]["clarion"]["content_hash"] == "blake3hash"
    # The signed identity binding survived the added extension.
    assert protected_rec["extensions"]["judge_metadata_signature"].startswith("hmac-sha256:")

    # Use a non-protected policy for the sign-off request so the trail verifier
    # (which requires judge_metadata_signature on every protected-policy record)
    # does not reject the unsigned PENDING_SIGNOFF record.
    sr = c.post("/signoff/request", json={
        "policy": "prod-deploy", "entity": "python:function:m.f", "rationale": "r",
        "agent_id": "agent-1"})
    assert sr.status_code == 202
    signoff_rec = c.get("/overrides").json()[1]
    assert signoff_rec["extensions"]["clarion"]["content_hash"] == "blake3hash"
