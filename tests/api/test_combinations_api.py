from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.store.audit_store import AuditStore


def _client(tmp_path, **kw):
    eng = EnforcementEngine(AuditStore(f"sqlite:///{tmp_path / 'g.db'}"),
                            FixedClock("2026-06-02T12:00:00+00:00"))
    return TestClient(create_app(enforcement=eng, **kw))


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


def test_bind_issue_endpoint_attaches_sei(tmp_path):
    class FakeFiligree:
        def __init__(self):
            self.attached = []

        def attach(self, issue_id, entity_id, content_hash, *, actor):
            self.attached.append((issue_id, entity_id, content_hash, actor))
            return {"issue_id": issue_id, "clarion_entity_id": entity_id,
                    "content_hash_at_attach": content_hash, "attached_at": "t",
                    "attached_by": actor}

        def associations_for_entity(self, entity_id):
            return []

    fil = FakeFiligree()
    c = _client(tmp_path, filigree=fil)
    resp = c.post("/signoff/7/bind-issue", json={
        "issue_id": "ISSUE-1", "sei": "clarion:eid:abc", "content_hash": "h"})
    assert resp.status_code == 201
    assert resp.json()["clarion_entity_id"] == "clarion:eid:abc"
    assert fil.attached == [("ISSUE-1", "clarion:eid:abc", "h", "legis")]
