import json
import sqlite3

from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.canonical import canonical_json, content_hash
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.protected import TrailVerifier
from legis.enforcement.signoff import SignoffGate
from legis.enforcement.signing import sign
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import GENESIS, AuditStore, _chain
from legis.wardline.ingest import wardline_artifact_fields


def _client(tmp_path, **kw):
    eng = EnforcementEngine(AuditStore(f"sqlite:///{tmp_path / 'g.db'}"),
                            FixedClock("2026-06-02T12:00:00+00:00"))
    return TestClient(create_app(enforcement=eng, **kw))


class _FakeFiligree:
    def __init__(self):
        self.attached = []

    def attach(self, issue_id, entity_id, content_hash, *, actor,
               signoff_seq=None, signature=None):
        self.attached.append(
            (issue_id, entity_id, content_hash, actor, signoff_seq, signature)
        )
        return {"issue_id": issue_id, "clarion_entity_id": entity_id,
                "content_hash_at_attach": content_hash, "attached_at": "t",
                "attached_by": actor}

    def associations_for_entity(self, entity_id):
        return []


def _tamper_first_record(db, mutate):
    con = sqlite3.connect(db)
    con.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    seq, payload = con.execute(
        "SELECT seq, payload FROM audit_log ORDER BY seq ASC LIMIT 1"
    ).fetchone()
    p = json.loads(payload)
    mutate(p)
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
    assert fil.attached == [("ISSUE-1", "clarion:eid:abc", "", "legis", req.seq, None)]


def test_bind_issue_endpoint_transmits_hmac_binding_signature(tmp_path):
    from legis.enforcement.signing import verify

    key = b"k" * 32
    fil = _FakeFiligree()
    gate = SignoffGate(AuditStore(f"sqlite:///{tmp_path / 'sg.db'}"),
                       FixedClock("2026-06-02T12:00:00+00:00"))
    req = gate.request(
        policy="PY-WL-101",
        entity_key=EntityKey.from_sei("clarion:eid:abc"),
        rationale="needs a human",
        agent_id="agent-1",
        extensions={"clarion": {"content_hash": "blake3"}},
    )
    gate.sign_off(request_seq=req.seq, operator_id="operator-1")

    c = _client(tmp_path, filigree=fil, signoff_gate=gate, binding_key=key)
    resp = c.post(f"/signoff/{req.seq}/bind-issue", json={"issue_id": "ISSUE-1"})

    assert resp.status_code == 201
    sig = resp.json()["binding_signature"]
    assert verify(
        {
            "issue_id": "ISSUE-1",
            "entity_id": "clarion:eid:abc",
            "content_hash": "blake3",
            "signoff_seq": req.seq,
        },
        sig,
        key,
    )
    assert fil.attached == [
        ("ISSUE-1", "clarion:eid:abc", "blake3", "legis", req.seq, sig)
    ]


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
    assert fil.attached[0] == ("ISSUE-1", "clarion:eid:abc", "blake3", "legis", 1, None)

    got = c.get("/signoff/1/binding")
    assert got.status_code == 200
    assert got.json()["issue_id"] == "ISSUE-1"
    assert got.json()["entity_key"]["value"] == "clarion:eid:abc"
    # The recorded binding reflects the cleared content_hash, not the body's.
    assert got.json()["content_hash"] == "blake3"


def test_bind_issue_fails_closed_on_tampered_signed_signoff_request(tmp_path):
    fil = _FakeFiligree()
    db = tmp_path / "sg.db"
    gate = SignoffGate(
        AuditStore(f"sqlite:///{db}"),
        FixedClock("2026-06-02T12:00:00+00:00"),
        signer=True,
        key=b"k",
    )
    req = gate.request(
        policy="prod-deploy",
        entity_key=EntityKey.from_sei("clarion:eid:abc"),
        rationale="r",
        agent_id="a",
        extensions={"clarion": {"content_hash": "blake3", "alive": True,
                                "lineage_snapshot": {"length": 1, "hash": "lh"}}},
    )
    gate.sign_off(request_seq=req.seq, operator_id="op-1")
    _tamper_first_record(
        db,
        lambda p: p["extensions"]["clarion"].update({"content_hash": "forged"}),
    )
    c = _client(
        tmp_path,
        signoff_gate=gate,
        filigree=fil,
        trail_verifier=TrailVerifier(b"k", frozenset()),
    )

    resp = c.post(f"/signoff/{req.seq}/bind-issue", json={"issue_id": "ISSUE-1"})

    assert resp.status_code == 500
    assert fil.attached == []


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
    assert trail[0]["extensions"]["wardline"]["finding_count"] == 1
    assert trail[0]["extensions"]["wardline"]["active_count"] == 1
    assert trail[0]["extensions"]["wardline"]["scan_digest"].startswith("sha256:")


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


def test_scan_results_fail_on_routes_threshold_per_finding(tmp_path):
    from legis.clock import FixedClock
    from legis.enforcement.signoff import SignoffGate
    from legis.store.audit_store import AuditStore

    sg = SignoffGate(AuditStore(f"sqlite:///{tmp_path / 's.db'}"),
                     FixedClock("2026-06-02T12:00:00+00:00"))
    c = _client(tmp_path, signoff_gate=sg)
    body = {"agent_id": "a", "cell": "block_escalate", "fail_on": "ERROR",
            "scan": {"findings": [
                {"rule_id": "R-E", "message": "m", "severity": "ERROR", "kind": "defect",
                 "fingerprint": "e", "qualname": "m.f", "properties": {}, "suppressed": "active"},
                {"rule_id": "R-W", "message": "m", "severity": "WARN", "kind": "defect",
                 "fingerprint": "w", "qualname": "m.g", "properties": {}, "suppressed": "active"}]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 200
    modes = {r["fingerprint"]: r["mode"] for r in resp.json()["routed"]}
    assert modes == {"e": "block_escalate", "w": "surface_only"}


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


def test_scan_results_empty_cell_by_severity_is_422(tmp_path):
    c = _client(tmp_path)
    body = {"agent_id": "a", "cell_by_severity": {}, "scan": {"findings": []}}
    assert c.post("/wardline/scan-results", json=body).status_code == 422


def test_scan_results_rejects_malformed_findings_without_writing(tmp_path):
    c = _client(tmp_path)
    for scan in (
        {"findings": "not-a-list"},
        {"findings": [{"message": "missing rule", "severity": "ERROR", "kind": "defect",
                       "fingerprint": "fp", "qualname": "m.f"}]},
        {"findings": [{"rule_id": "R", "message": "bad severity", "severity": "BOGUS",
                       "kind": "defect", "fingerprint": "fp", "qualname": "m.f"}]},
    ):
        resp = c.post("/wardline/scan-results",
                      json={"cell": "surface_override", "agent_id": "agent-1", "scan": scan})
        assert resp.status_code == 422
    assert c.get("/overrides").json() == []


def test_scan_results_rejects_suppressed_defect_without_proof(tmp_path):
    c = _client(tmp_path)
    scan = {"findings": [
        {"rule_id": "R-C", "message": "m", "severity": "CRITICAL", "kind": "defect",
         "fingerprint": "c", "qualname": "m.f", "properties": {}, "suppressed": "waived"}
    ]}
    resp = c.post("/wardline/scan-results",
                  json={"cell": "surface_only", "agent_id": "a", "scan": scan})
    assert resp.status_code == 422
    assert c.get("/overrides").json() == []


def test_scan_results_rejects_invalid_trust_tier_without_writing(tmp_path):
    c = _client(tmp_path)
    scan = {"findings": [
        {"rule_id": "R-C", "message": "m", "severity": "CRITICAL", "kind": "defect",
         "fingerprint": "c", "qualname": "m.f",
         "properties": {"actual_return": "ROOT"}, "suppressed": "active"}
    ]}
    resp = c.post("/wardline/scan-results",
                  json={"cell": "surface_only", "agent_id": "a", "scan": scan})
    assert resp.status_code == 422
    assert c.get("/overrides").json() == []


def test_scan_results_rejects_oversized_finding_batch_without_writing(tmp_path):
    c = _client(tmp_path)
    finding = {"rule_id": "R", "message": "m", "severity": "INFO", "kind": "defect",
               "fingerprint": "fp", "qualname": "m.f", "properties": {},
               "suppressed": "active"}
    scan = {"findings": [{**finding, "fingerprint": f"fp-{i}"} for i in range(501)]}
    resp = c.post("/wardline/scan-results",
                  json={"cell": "surface_only", "agent_id": "a", "scan": scan})
    assert resp.status_code == 422
    assert c.get("/overrides").json() == []


def test_scan_results_server_owned_routing_rejects_request_routing(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_WARDLINE_CELL", "surface_only")
    c = _client(tmp_path)
    body = {"cell": "surface_override", "agent_id": "a", "scan": {"findings": [
        {"rule_id": "R", "message": "m", "severity": "INFO", "kind": "defect",
         "fingerprint": "fp", "qualname": "m.f", "properties": {}, "suppressed": "active"}
    ]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 403
    assert "server-owned" in resp.json()["detail"]


def test_scan_results_default_rejects_request_owned_routing(tmp_path, monkeypatch):
    monkeypatch.delenv("LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING", raising=False)
    c = _client(tmp_path)
    body = {"cell": "surface_only", "agent_id": "a", "scan": {"findings": [
        {"rule_id": "R", "message": "m", "severity": "INFO", "kind": "defect",
         "fingerprint": "fp", "qualname": "m.f", "properties": {}, "suppressed": "active"}
    ]}}

    resp = c.post("/wardline/scan-results", json=body)

    assert resp.status_code == 403
    assert "server-owned" in resp.json()["detail"]


def test_scan_results_can_use_server_owned_single_cell(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_WARDLINE_CELL", "surface_only")
    c = _client(tmp_path)
    body = {"agent_id": "a", "scan": {"findings": [
        {"rule_id": "R", "message": "m", "severity": "INFO", "kind": "defect",
         "fingerprint": "fp", "qualname": "m.f", "properties": {}, "suppressed": "active"}
    ]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 200
    assert resp.json()["routed"][0]["mode"] == "surface_only"


def _signed_wardline_scan(scan, key=b"wardline-key"):
    return {
        **scan,
        "artifact_signature": sign(wardline_artifact_fields(scan), key),
    }


def test_scan_results_requires_signed_artifact_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_WARDLINE_ARTIFACT_KEY", "wardline-key")
    c = _client(tmp_path)
    scan = {
        "scanner_identity": "wardline@1",
        "rule_set_version": "rules@abc123",
        "commit_sha": "a" * 40,
        "tree_sha": "b" * 40,
        "findings": [
            {"rule_id": "R", "message": "m", "severity": "INFO", "kind": "defect",
             "fingerprint": "fp", "qualname": "m.f", "properties": {}, "suppressed": "active"}
        ],
    }

    resp = c.post("/wardline/scan-results",
                  json={"cell": "surface_only", "agent_id": "a", "scan": scan})

    assert resp.status_code == 422
    assert "artifact signature" in resp.json()["detail"]
    assert c.get("/overrides").json() == []


def test_scan_results_records_verified_artifact_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_WARDLINE_ARTIFACT_KEY", "wardline-key")
    c = _client(tmp_path)
    scan = _signed_wardline_scan({
        "scanner_identity": "wardline@1",
        "rule_set_version": "rules@abc123",
        "commit_sha": "a" * 40,
        "tree_sha": "b" * 40,
        "findings": [
            {"rule_id": "R", "message": "m", "severity": "INFO", "kind": "defect",
             "fingerprint": "fp", "qualname": "m.f", "properties": {}, "suppressed": "active"}
        ],
    })

    resp = c.post("/wardline/scan-results",
                  json={"cell": "surface_only", "agent_id": "a", "scan": scan})

    assert resp.status_code == 200
    wardline = c.get("/overrides").json()[0]["extensions"]["wardline"]
    assert wardline["artifact_status"] == "verified"
    assert wardline["scanner_identity"] == "wardline@1"
    assert wardline["rule_set_version"] == "rules@abc123"
    assert wardline["commit_sha"] == "a" * 40
    assert wardline["tree_sha"] == "b" * 40
    assert wardline["artifact_signature"].startswith("hmac-sha256:v2:")


def test_scan_results_single_cell_still_works(tmp_path):
    c = _client(tmp_path)
    body = {"cell": "surface_override", "agent_id": "agent-1", "scan": {"findings": [
        {"rule_id": "PY-WL-101", "message": "m", "severity": "ERROR", "kind": "defect",
         "fingerprint": "fp1", "qualname": "m.f", "properties": {}, "suppressed": "active"}]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 200
    assert resp.json()["routed"][0]["mode"] == "surface_override"
