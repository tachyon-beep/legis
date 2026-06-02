from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.signoff import SignoffGate
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


def _client(tmp_path, **kw):
    eng = EnforcementEngine(AuditStore(f"sqlite:///{tmp_path / 'g.db'}"),
                            FixedClock("2026-06-02T12:00:00+00:00"))
    return TestClient(create_app(enforcement=eng, **kw))


class _FakeFiligree:
    def __init__(self):
        self.attached = []

    def attach(self, issue_id, entity_id, content_hash, *, actor):
        self.attached.append((issue_id, entity_id, content_hash, actor))
        return {"issue_id": issue_id, "clarion_entity_id": entity_id,
                "content_hash_at_attach": content_hash, "attached_at": "t",
                "attached_by": actor}

    def associations_for_entity(self, entity_id):
        return []


def test_scan_results_route_surface_override(tmp_path):
    c = _client(tmp_path)
    body = {"cell": "surface_override", "agent_id": "agent-1", "scan": {"findings": [
        {"rule_id": "PY-WL-101", "message": "untrusted reaches trusted",
         "severity": "ERROR", "kind": "defect", "fingerprint": "fp1",
         "qualname": "m.f", "properties": {}, "suppressed": "active"}]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 200
    assert resp.json()["routed"][0]["mode"] == "surface_override"
    assert c.get("/overrides").json()[0]["policy"] == "PY-WL-101"


def test_bind_issue_endpoint_attaches_sei_from_cleared_record(tmp_path):
    # Binding is governed: the SEI and content_hash come from the recorded,
    # CLEARED sign-off — never from the caller. The caller supplies only issue_id.
    fil = _FakeFiligree()
    gate = SignoffGate(AuditStore(f"sqlite:///{tmp_path / 'sg.db'}"),
                       FixedClock("2026-06-02T12:00:00+00:00"))
    req = gate.request(
        policy="PY-WL-101",
        entity_key=EntityKey.from_sei("clarion:eid:abc"),
        rationale="needs a human",
        agent_id="agent-1",
    )
    gate.sign_off(request_seq=req.seq, operator_id="operator-1")

    c = _client(tmp_path, filigree=fil, signoff_gate=gate)
    resp = c.post(f"/signoff/{req.seq}/bind-issue", json={"issue_id": "ISSUE-1"})

    assert resp.status_code == 201
    assert resp.json()["clarion_entity_id"] == "clarion:eid:abc"
    # SEI sourced from the trail; content_hash is "" because request() records no
    # clarion ext — the honest behaviour of the real record.
    assert fil.attached == [("ISSUE-1", "clarion:eid:abc", "", "legis")]


def test_bind_issue_endpoint_rejects_uncleared_request(tmp_path):
    fil = _FakeFiligree()
    gate = SignoffGate(AuditStore(f"sqlite:///{tmp_path / 'sg.db'}"),
                       FixedClock("2026-06-02T12:00:00+00:00"))
    req = gate.request(
        policy="PY-WL-101",
        entity_key=EntityKey.from_sei("clarion:eid:abc"),
        rationale="needs a human",
        agent_id="agent-1",
    )
    # Not signed off → not cleared.
    c = _client(tmp_path, filigree=fil, signoff_gate=gate)
    resp = c.post(f"/signoff/{req.seq}/bind-issue", json={"issue_id": "ISSUE-1"})

    assert resp.status_code == 409
    assert "not cleared" in resp.json()["detail"]
    assert fil.attached == []


def test_bind_issue_endpoint_404_for_missing_request(tmp_path):
    fil = _FakeFiligree()
    gate = SignoffGate(AuditStore(f"sqlite:///{tmp_path / 'sg.db'}"),
                       FixedClock("2026-06-02T12:00:00+00:00"))
    c = _client(tmp_path, filigree=fil, signoff_gate=gate)
    resp = c.post("/signoff/99/bind-issue", json={"issue_id": "ISSUE-1"})

    assert resp.status_code == 404
    assert fil.attached == []
