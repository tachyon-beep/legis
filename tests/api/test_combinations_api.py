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


def test_bind_issue_records_to_ledger_and_binding_is_verifiable(tmp_path):
    from legis.governance.binding_ledger import BindingLedger

    clock = FixedClock("2026-06-02T12:00:00+00:00")
    sg = SignoffGate(AuditStore(f"sqlite:///{tmp_path / 'gov.db'}"), clock)
    ledger = BindingLedger(AuditStore(f"sqlite:///{tmp_path / 'bind.db'}"), clock, key=b"k")
    fil = _FakeFiligree()
    c = _client(tmp_path, signoff_gate=sg, filigree=fil, binding_ledger=ledger)

    sg.request(policy="prod-deploy", entity_key=EntityKey.from_sei("clarion:eid:abc"),
               rationale="r", agent_id="a",
               extensions={"clarion": {"content_hash": "blake3", "alive": True,
                                       "lineage_snapshot": None}})
    sg.sign_off(request_seq=1, operator_id="op-1")

    # The body's content_hash is attacker-supplied and must NOT win: the SEI and
    # content_hash come from the CLEARED sign-off record ("blake3"), never the body.
    resp = c.post("/signoff/1/bind-issue",
                  json={"issue_id": "ISSUE-1", "sei": "clarion:eid:abc",
                        "content_hash": "ATTACKER-SUPPLIED"})
    assert resp.status_code == 201
    assert resp.json()["binding_seq"] == 1
    # Full tuple: the cleared "blake3" wins at index [2], NOT "ATTACKER-SUPPLIED".
    assert fil.attached[0] == ("ISSUE-1", "clarion:eid:abc", "blake3", "legis")

    got = c.get("/signoff/1/binding")
    assert got.status_code == 200
    assert got.json()["issue_id"] == "ISSUE-1"
    assert got.json()["entity_key"]["value"] == "clarion:eid:abc"
    # The recorded binding reflects the cleared content_hash, not the body's.
    assert got.json()["content_hash"] == "blake3"


def test_binding_read_404_when_no_ledger(tmp_path):
    c = _client(tmp_path)
    assert c.get("/signoff/1/binding").status_code == 404


def test_binding_read_500_on_forged_record(tmp_path):
    # Fail-closed at read time: a forged binding row whose HMAC does not verify
    # is an honest integrity 500, never silently returned (WP-A3 exit criterion).
    from legis.governance.binding_ledger import BindingLedger

    store = AuditStore(f"sqlite:///{tmp_path / 'bind.db'}")
    ledger = BindingLedger(store, FixedClock("2026-06-02T12:00:00+00:00"), key=b"k")
    store.append({"kind": "issue_binding", "signoff_seq": 1, "issue_id": "I",
                  "entity_key": {"value": "clarion:eid:x", "identity_stable": True},
                  "content_hash": "h", "recorded_at": "t",
                  "binding_signature": "hmac-sha256:v1:deadbeef"})
    c = _client(tmp_path, binding_ledger=ledger)
    assert c.get("/signoff/1/binding").status_code == 500


def test_scan_results_surface_only_records_non_gating(tmp_path):
    c = _client(tmp_path)
    body = {"cell": "surface_only", "agent_id": "agent-1", "scan": {"findings": [
        {"rule_id": "PY-WL-101", "message": "m", "severity": "INFO", "kind": "defect",
         "fingerprint": "fp1", "qualname": "m.f", "properties": {}, "suppressed": "active"}]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 200
    assert resp.json()["routed"][0]["mode"] == "surface_only"
    trail = c.get("/overrides").json()
    assert trail[0]["kind"] == "wardline_surfaced"


def test_scan_results_cell_by_severity_routes_per_finding(tmp_path):
    from legis.clock import FixedClock
    from legis.enforcement.signoff import SignoffGate
    from legis.store.audit_store import AuditStore
    sg = SignoffGate(AuditStore(f"sqlite:///{tmp_path / 's.db'}"),
                     FixedClock("2026-06-02T12:00:00+00:00"))
    c = _client(tmp_path, signoff_gate=sg)
    body = {"agent_id": "a",
            "cell_by_severity": {"CRITICAL": "block_escalate", "INFO": "surface_only"},
            "scan": {"findings": [
                {"rule_id": "R-C", "message": "m", "severity": "CRITICAL", "kind": "defect",
                 "fingerprint": "c", "qualname": "m.f", "properties": {}, "suppressed": "active"},
                {"rule_id": "R-I", "message": "m", "severity": "INFO", "kind": "defect",
                 "fingerprint": "i", "qualname": "m.g", "properties": {}, "suppressed": "active"}]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 200
    modes = {r["fingerprint"]: r["mode"] for r in resp.json()["routed"]}
    assert modes == {"c": "block_escalate", "i": "surface_only"}


def test_scan_results_block_escalate_without_gate_is_409(tmp_path):
    # cell_by_severity needing block_escalate but no signoff_gate wired → pre-loop guard → 409
    c = _client(tmp_path)  # no signoff_gate
    body = {"agent_id": "a", "cell_by_severity": {"CRITICAL": "block_escalate"},
            "scan": {"findings": [
                {"rule_id": "R-C", "message": "m", "severity": "CRITICAL", "kind": "defect",
                 "fingerprint": "c", "qualname": "m.f", "properties": {}, "suppressed": "active"}]}}
    assert c.post("/wardline/scan-results", json=body).status_code == 409


def test_scan_results_rejects_both_or_neither_cell_form(tmp_path):
    c = _client(tmp_path)
    base = {"agent_id": "a", "scan": {"findings": []}}
    assert c.post("/wardline/scan-results", json=base).status_code == 422  # neither
    assert c.post("/wardline/scan-results",
                  json={**base, "cell": "surface_only",
                        "cell_by_severity": {"INFO": "surface_only"}}).status_code == 422  # both


def test_scan_results_block_escalate_only_needs_no_engine(tmp_path):
    # A pure block_escalate scan must route with only a signoff gate wired — no
    # enforcement engine, so engine()'s lazy legis-governance.db is never created.
    sg = SignoffGate(AuditStore(f"sqlite:///{tmp_path / 's.db'}"),
                     FixedClock("2026-06-02T12:00:00+00:00"))
    c = TestClient(create_app(signoff_gate=sg))  # NOT _client: no enforcement injected
    body = {"cell": "block_escalate", "agent_id": "a", "scan": {"findings": [
        {"rule_id": "R-C", "message": "m", "severity": "CRITICAL", "kind": "defect",
         "fingerprint": "c", "qualname": "m.f", "properties": {}, "suppressed": "active"}]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 200
    assert resp.json()["routed"][0]["mode"] == "block_escalate"


def test_scan_results_single_cell_still_works(tmp_path):
    c = _client(tmp_path)
    body = {"cell": "surface_override", "agent_id": "agent-1", "scan": {"findings": [
        {"rule_id": "PY-WL-101", "message": "m", "severity": "ERROR", "kind": "defect",
         "fingerprint": "fp1", "qualname": "m.f", "properties": {}, "suppressed": "active"}]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 200
    assert resp.json()["routed"][0]["mode"] == "surface_override"
