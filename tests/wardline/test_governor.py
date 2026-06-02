from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.signoff import SignoffGate
from legis.enforcement.verdict import SignoffState
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore
from legis.wardline.governor import WardlineCellPolicy, route_findings
from legis.wardline.ingest import WardlineSeverity, active_defects


def _scan():
    return {"findings": [
        {"rule_id": "PY-WL-101", "message": "untrusted reaches trusted",
         "severity": "ERROR", "kind": "defect", "fingerprint": "fp1",
         "qualname": "m.f", "properties": {"actual_return": "UNKNOWN_RAW"},
         "suppressed": "active"},
    ]}


def _engine(tmp_path):
    return EnforcementEngine(AuditStore(f"sqlite:///{tmp_path / 'g.db'}"),
                             FixedClock("2026-06-02T12:00:00+00:00"))


def test_surface_override_cell_records_an_override(tmp_path):
    eng = _engine(tmp_path)
    results = route_findings(
        active_defects(_scan()),
        policy=WardlineCellPolicy.SURFACE_OVERRIDE,
        agent_id="agent-1",
        resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}),
        engine=eng,
    )
    assert len(results) == 1 and results[0]["mode"] == "surface_override"
    trail = eng.trail()
    assert trail[0]["policy"] == "PY-WL-101"             # Wardline rule_id is the policy
    assert trail[0]["entity_key"]["value"] == "m.f"      # routed on the finding's qualname
    assert "untrusted reaches trusted" in trail[0]["rationale"]


def test_surface_override_captures_clarion_lineage_alongside_wardline(tmp_path):
    # A SEI-keyed wardline-routed override must carry the REQ-L-01 clarion
    # lineage snapshot (alive/content_hash/lineage_snapshot) merged ALONGSIDE the
    # wardline ext — same as the same override taken via /overrides.
    eng = _engine(tmp_path)
    clarion_ext = {"clarion": {"alive": True, "content_hash": "h",
                               "lineage_snapshot": {"length": 1, "hash": "z"}}}
    results = route_findings(
        active_defects(_scan()),
        policy=WardlineCellPolicy.SURFACE_OVERRIDE,
        agent_id="agent-1",
        resolve=lambda q: (EntityKey.from_sei("clarion:eid:x"), clarion_ext),
        engine=eng,
    )
    assert results[0]["mode"] == "surface_override"
    ext = eng.trail()[0]["extensions"]
    assert ext["clarion"] == clarion_ext["clarion"]      # lineage snapshot captured
    assert ext["wardline"]["fingerprint"] == "fp1"       # wardline ext still present


def test_block_escalate_cell_opens_a_signoff_request(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'g.db'}")
    gate = SignoffGate(store, FixedClock("2026-06-02T12:00:00+00:00"))
    results = route_findings(
        active_defects(_scan()),
        policy=WardlineCellPolicy.BLOCK_ESCALATE,
        agent_id="agent-1",
        resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}),
        signoff=gate,
    )
    assert results[0]["mode"] == "block_escalate"
    assert results[0]["cleared"] is False                # a human must sign off

    # The stored side-effect: a PENDING_SIGNOFF request was written, not cleared.
    req_seq = results[0]["seq"]
    assert gate.is_cleared(req_seq) is False
    record = store.read_all()[req_seq - 1].payload
    assert record["policy"] == "PY-WL-101"
    assert record["entity_key"]["value"] == "m.f"
    assert (
        record["extensions"]["signoff_state"] == SignoffState.PENDING.value
    )


def test_surface_only_records_a_non_gating_event(tmp_path):
    eng = _engine(tmp_path)
    results = route_findings(
        active_defects(_scan()),
        policy=WardlineCellPolicy.SURFACE_ONLY,
        agent_id="agent-1",
        resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}),
        engine=eng,
    )
    assert results[0]["mode"] == "surface_only"
    assert results[0]["surfaced"] is True
    assert "accepted" not in results[0] and "cleared" not in results[0]
    trail = eng.trail()
    assert trail[0]["kind"] == "wardline_surfaced"
    assert trail[0]["policy"] == "PY-WL-101"
    assert trail[0]["extensions"]["wardline"]["fingerprint"] == "fp1"


def test_surface_only_needs_no_signoff_gate(tmp_path):
    eng = _engine(tmp_path)
    results = route_findings(
        active_defects(_scan()), policy=WardlineCellPolicy.SURFACE_ONLY,
        agent_id="a", resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}),
        engine=eng, signoff=None)
    assert results[0]["mode"] == "surface_only"


def _mixed_scan():
    def fnd(rule, sev, fp):
        return {"rule_id": rule, "message": "m", "severity": sev, "kind": "defect",
                "fingerprint": fp, "qualname": "m.f", "properties": {}, "suppressed": "active"}
    return {"findings": [fnd("R-CRIT", "CRITICAL", "c"),
                         fnd("R-WARN", "WARN", "w"),
                         fnd("R-INFO", "INFO", "i")]}


def test_cell_map_routes_each_finding_by_severity(tmp_path):
    eng = _engine(tmp_path)
    gate = SignoffGate(AuditStore(f"sqlite:///{tmp_path / 's.db'}"),
                       FixedClock("2026-06-02T12:00:00+00:00"))
    cell_map = {
        WardlineSeverity.CRITICAL: WardlineCellPolicy.BLOCK_ESCALATE,
        WardlineSeverity.WARN: WardlineCellPolicy.SURFACE_OVERRIDE,
        WardlineSeverity.INFO: WardlineCellPolicy.SURFACE_ONLY,
    }
    results = route_findings(
        active_defects(_mixed_scan()), cell_map=cell_map, agent_id="a",
        resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}),
        engine=eng, signoff=gate)
    by_fp = {r["fingerprint"]: r["mode"] for r in results}
    assert by_fp == {"c": "block_escalate", "w": "surface_override", "i": "surface_only"}


def test_unmapped_severity_falls_back_to_surface_override(tmp_path):
    eng = _engine(tmp_path)
    cell_map = {WardlineSeverity.CRITICAL: WardlineCellPolicy.SURFACE_ONLY}
    results = route_findings(  # _scan() finding is ERROR — not in the map → fallback
        active_defects(_scan()), cell_map=cell_map, agent_id="a",
        resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}), engine=eng)
    assert results[0]["mode"] == "surface_override"


def test_exactly_one_of_policy_or_cell_map(tmp_path):
    import pytest
    eng = _engine(tmp_path)
    # neither given → raises
    with pytest.raises(ValueError, match="exactly one"):
        route_findings(active_defects(_scan()), agent_id="a",
                       resolve=lambda q: (EntityKey.from_locator("x"), {}), engine=eng)
    # both given → raises
    with pytest.raises(ValueError, match="exactly one"):
        route_findings(active_defects(_scan()), policy=WardlineCellPolicy.SURFACE_OVERRIDE,
                       cell_map={WardlineSeverity.CRITICAL: WardlineCellPolicy.SURFACE_ONLY},
                       agent_id="a",
                       resolve=lambda q: (EntityKey.from_locator("x"), {}), engine=eng)


def test_pre_loop_guard_prevents_partial_application(tmp_path):
    # A heterogeneous cell_map needing block_escalate with no signoff gate must
    # raise BEFORE any finding is written — no partial batch in the ledger.
    import pytest
    eng = _engine(tmp_path)
    cell_map = {
        WardlineSeverity.WARN: WardlineCellPolicy.SURFACE_OVERRIDE,
        WardlineSeverity.CRITICAL: WardlineCellPolicy.BLOCK_ESCALATE,
    }
    with pytest.raises(ValueError, match="block_escalate cell requires a signoff gate"):
        route_findings(active_defects(_mixed_scan()), cell_map=cell_map, agent_id="a",
                       resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}),
                       engine=eng, signoff=None)
    assert eng.trail() == []  # nothing written
